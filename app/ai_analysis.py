from base64 import b64encode
from json import dumps, loads
from pathlib import Path
from typing import Any

import fitz
from openai import OpenAI

from .models import (
    AIPlacementDecision,
    AIPlacementDecisions,
    AnalyzeJobResult,
    BoundingBox,
    ImageOverlay,
    NameOverlay,
    PageAnalysis,
    PageSize,
    Placement,
    ProcessingOptions,
)


MAX_AI_PAGES = 4
MAX_WORDS_PER_PAGE = 60
MAX_TEXT_CHARS_PER_PAGE = 700
MAX_LINES_PER_PAGE = 40
MAX_CANDIDATES_PER_PAGE = 6
MAX_LOCAL_PLACEMENTS = 6
MAX_CROPS = 2
HIGH_CONFIDENCE_CANDIDATE = 0.7
AI_CONFIDENT_LOCAL_THRESHOLD = 0.85
POINTS_PER_MM = 72 / 25.4
MIN_STAMP_SIZE = 35 * POINTS_PER_MM
MAX_STAMP_SIZE = 45 * POINTS_PER_MM


AI_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "page_number": {"type": "integer", "minimum": 1},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "accept_local",
                            "adjust_local",
                            "reject_auto",
                            "manual_review",
                        ],
                    },
                    "should_sign": {"type": "boolean"},
                    "should_stamp": {"type": "boolean"},
                    "should_add_name": {"type": "boolean"},
                    "name_text": {"type": ["string", "null"]},
                    "signature_bbox": {
                        "anyOf": [{"$ref": "#/$defs/bbox"}, {"type": "null"}]
                    },
                    "stamp_bbox": {
                        "anyOf": [{"$ref": "#/$defs/bbox"}, {"type": "null"}]
                    },
                    "name_bbox": {
                        "anyOf": [{"$ref": "#/$defs/bbox"}, {"type": "null"}]
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "needs_manual_review": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "candidate_id",
                    "page_number",
                    "verdict",
                    "should_sign",
                    "should_stamp",
                    "should_add_name",
                    "name_text",
                    "signature_bbox",
                    "stamp_bbox",
                    "name_bbox",
                    "confidence",
                    "needs_manual_review",
                    "reason",
                ],
            },
        }
    },
    "required": ["decisions"],
    "$defs": {
        "bbox": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "x0": {"type": "number", "minimum": 0},
                "y0": {"type": "number", "minimum": 0},
                "x1": {"type": "number", "minimum": 0},
                "y1": {"type": "number", "minimum": 0},
            },
            "required": ["x0", "y0", "x1", "y1"],
        }
    },
}


SYSTEM_PROMPT = """You review local placement decisions for a handwritten signature, company stamp, and optional signer name on Russian PDF documents.

Return decisions only for real signing zones. Do not sign a document just because a name appears in body text.
Use candidates, word coordinates, line coordinates, and cropped images. Coordinates are PDF points.
Mark needs_manual_review=true when uncertain, when multiple zones are plausible, when the person differs, or when the document already appears signed/stamped.

Use verdict:
- accept_local: local placement is correct; leave coordinates unchanged.
- adjust_local: local placement is real but your returned coordinates are better.
- reject_auto: local placement is wrong; clear automatic placement.
- manual_review: document is ambiguous; require human review.

Distinguish:
- "Венедиктов Р.В." near a signature line: likely signable.
- "Венедиктов Р.В." only in ordinary paragraph text: not automatically signable.
- "Генеральный директор" near an empty signature line: signable for default signer.
- signature and stamp may be separate if "М.П." or "печать" indicates a stamp area.
- if full name is missing near the signature and default signer is required, add "Венедиктов Р.В.".
"""


def ai_configured(*, api_key: str | None, model: str | None) -> bool:
    return bool(api_key and model)


def run_ai_analysis(
    *,
    source_path: Path,
    analyses: list[PageAnalysis],
    options: ProcessingOptions,
    local_placements: list[Placement],
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> AIPlacementDecisions:
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
    )
    ai_analyses = select_ai_analyses(
        analyses=analyses,
        local_placements=local_placements,
    )
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": dumps(
                build_ai_context(
                    analyses=ai_analyses,
                    options=options,
                    local_placements=local_placements,
                    total_pages=len(analyses),
                ),
                ensure_ascii=False,
            ),
        }
    ]
    if should_include_visual_context(
        filename=source_path.name,
        analyses=analyses,
        local_placements=local_placements,
    ):
        content.extend(build_crop_inputs(source_path=source_path, analyses=ai_analyses))

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {"role": "user", "content": content},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "signature_placement_decisions",
                "strict": True,
                "schema": AI_DECISION_SCHEMA,
            }
        },
        max_output_tokens=1200,
        store=False,
        truncation="auto",
    )
    return parse_ai_response(response.output_text)


def build_ai_context(
    *,
    analyses: list[PageAnalysis],
    options: ProcessingOptions,
    local_placements: list[Placement] | None = None,
    total_pages: int | None = None,
) -> dict[str, Any]:
    selected_analyses = select_ai_analyses(
        analyses=analyses,
        local_placements=local_placements or [],
    )
    included_pages = [analysis.page_number for analysis in selected_analyses]
    total = total_pages or len(analyses)
    return {
        "task": "Review local signature/stamp/name placements.",
        "default_signer_name": "Венедиктов Р.В.",
        "document_summary": {
            "total_pages": total,
            "included_pages": included_pages,
            "omitted_pages_count": max(0, total - len(included_pages)),
        },
        "options": options.model_dump(),
        "local_placements": [
            placement.model_dump(mode="json")
            for placement in (local_placements or [])[:MAX_LOCAL_PLACEMENTS]
        ],
        "pages": [serialize_page_analysis(analysis) for analysis in selected_analyses],
    }


def serialize_page_analysis(analysis: PageAnalysis) -> dict[str, Any]:
    candidate_boxes = [candidate.context_bbox for candidate in analysis.candidates]
    words = [
        word
        for word in analysis.words
        if not candidate_boxes or any(is_near(word.bbox, box, padding=120) for box in candidate_boxes)
    ][:MAX_WORDS_PER_PAGE]
    lines = [
        line
        for line in analysis.lines
        if not candidate_boxes or any(is_near(line.bbox, box, padding=160) for box in candidate_boxes)
    ][:MAX_LINES_PER_PAGE]
    text_excerpt = build_text_excerpt(analysis=analysis, words=words)

    return {
        "page_number": analysis.page_number,
        "page_size": analysis.page_size.model_dump(),
        "text_quality": analysis.text_quality,
        "ocr_text": text_excerpt,
        "warnings": analysis.warnings,
        "words": [
            {"text": word.text, "bbox": word.bbox.model_dump()} for word in words
        ],
        "lines": [
            {
                "bbox": line.bbox.model_dump(),
                "width": line.width,
                "type": line.type,
            }
            for line in lines
        ],
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "page_number": candidate.page_number,
                "line_bbox": candidate.line_bbox.model_dump()
                if candidate.line_bbox
                else None,
                "context_bbox": candidate.context_bbox.model_dump(),
                "anchor": candidate.anchor,
                "reason": candidate.reason,
                "confidence": candidate.confidence,
                "warnings": candidate.warnings,
            }
            for candidate in analysis.candidates[:MAX_CANDIDATES_PER_PAGE]
        ],
    }


def select_ai_analyses(
    *,
    analyses: list[PageAnalysis],
    local_placements: list[Placement],
) -> list[PageAnalysis]:
    if len(analyses) <= MAX_AI_PAGES:
        return analyses

    placement_pages = {placement.page_number for placement in local_placements}
    candidate_pages = {
        analysis.page_number
        for analysis in analyses
        if any(candidate.confidence >= HIGH_CONFIDENCE_CANDIDATE for candidate in analysis.candidates)
    }
    warning_pages = {
        analysis.page_number
        for analysis in analyses
        if has_risky_warnings(analysis.warnings)
        or any(has_risky_warnings(candidate.warnings) for candidate in analysis.candidates)
    }
    first_pages = {analysis.page_number for analysis in analyses[:2]}

    prioritized: list[int] = []
    for page_group in (placement_pages, candidate_pages, warning_pages, first_pages):
        for page_number in sorted(page_group):
            if page_number not in prioritized:
                prioritized.append(page_number)

    if not prioritized:
        prioritized = [analysis.page_number for analysis in analyses[:MAX_AI_PAGES]]

    selected_pages = set(prioritized[:MAX_AI_PAGES])
    return [analysis for analysis in analyses if analysis.page_number in selected_pages]


def build_text_excerpt(*, analysis: PageAnalysis, words: list) -> str:
    if words:
        text = " ".join(word.text for word in words)
    else:
        text = analysis.ocr_text
    return text[:MAX_TEXT_CHARS_PER_PAGE]


def build_crop_inputs(
    *,
    source_path: Path,
    analyses: list[PageAnalysis],
) -> list[dict[str, Any]]:
    image_inputs: list[dict[str, Any]] = []
    with fitz.open(source_path) as document:
        for analysis in analyses:
            page = document[analysis.page_number - 1]
            for candidate in analysis.candidates:
                if len(image_inputs) >= MAX_CROPS:
                    return image_inputs
                rect = fitz.Rect(expand_bbox(candidate.context_bbox, analysis.page_size, 20).as_list())
                pixmap = page.get_pixmap(clip=rect, dpi=150, alpha=False)
                data_url = "data:image/png;base64," + b64encode(
                    pixmap.tobytes("png")
                ).decode("ascii")
                image_inputs.append(
                    {
                        "type": "input_image",
                        "image_url": data_url,
                    }
                )
    return image_inputs


def parse_ai_response(output_text: str) -> AIPlacementDecisions:
    return AIPlacementDecisions.model_validate(loads(output_text))


def placements_from_ai_decisions(
    decisions: AIPlacementDecisions,
) -> list[Placement]:
    placements: list[Placement] = []
    for decision in decisions.decisions:
        placement = placement_from_decision(decision)
        if placement is not None:
            placements.append(placement)
    return placements


def apply_ai_review_decisions(
    *,
    decisions: AIPlacementDecisions,
    analyses: list[PageAnalysis],
    local_placements: list[Placement],
) -> tuple[list[Placement], str, list[str]]:
    if not decisions.decisions:
        return local_placements, "manual_review", ["ai_no_decisions"]

    warnings: list[str] = []
    verdicts = {decision.verdict for decision in decisions.decisions}
    if "manual_review" in verdicts:
        reviewed = [
            placement.model_copy(update={"needs_manual_review": True})
            for placement in local_placements
        ]
        return reviewed, "manual_review", ["ai_needs_manual_review"]
    if "reject_auto" in verdicts:
        return [], "reject_auto", ["ai_rejected_auto"]
    if verdicts == {"accept_local"}:
        return local_placements, "accept_local", ["ai_accept_local"]

    placements: list[Placement] = []
    for decision in decisions.decisions:
        if decision.verdict == "accept_local":
            placements.extend(local_placements)
            continue
        if decision.verdict != "adjust_local":
            continue
        placement = placement_from_decision(decision)
        if placement is None:
            warnings.append(f"ai_invalid_decision:{decision.candidate_id}:empty")
            continue
        validation_error = validate_ai_placement(placement, analyses)
        if validation_error is not None:
            warnings.append(f"ai_invalid_decision:{decision.candidate_id}:{validation_error}")
            continue
        placements.append(placement)

    if not placements:
        return local_placements, "manual_review", warnings or ["ai_no_valid_placements"]
    if any(placement.needs_manual_review for placement in placements):
        warnings.append("ai_needs_manual_review")
        return placements, "manual_review", warnings
    warnings.append("ai_adjusted_local")
    return placements, "adjust_local", warnings


def should_request_ai_review(
    *,
    filename: str,
    analyses: list[PageAnalysis],
    local_placements: list[Placement],
) -> bool:
    candidates = [candidate for analysis in analyses for candidate in analysis.candidates]
    strong_candidates = [
        candidate for candidate in candidates if candidate.confidence >= HIGH_CONFIDENCE_CANDIDATE
    ]
    if not candidates:
        return True
    if len(strong_candidates) != 1:
        return True
    if len(local_placements) != 1:
        return True

    candidate = strong_candidates[0]
    warnings = {
        warning
        for analysis in analyses
        for warning in analysis.warnings
    } | set(candidate.warnings)
    if has_risky_warnings(warnings):
        return True
    if candidate.confidence < AI_CONFIDENT_LOCAL_THRESHOLD:
        return True

    lowered = filename.casefold()
    return any(keyword in lowered for keyword in ("упд", "счет", "спецификация"))


def should_include_visual_context(
    *,
    filename: str,
    analyses: list[PageAnalysis],
    local_placements: list[Placement],
) -> bool:
    """Use image crops only when visual inspection can change the AI verdict."""
    lowered = filename.casefold()
    if any(keyword in lowered for keyword in ("упд", "счет", "спецификация")):
        return True
    if len(local_placements) != 1:
        return True

    candidates = [candidate for analysis in analyses for candidate in analysis.candidates]
    strong_candidates = [
        candidate for candidate in candidates if candidate.confidence >= HIGH_CONFIDENCE_CANDIDATE
    ]
    if len(strong_candidates) != 1:
        return True
    if has_risky_warnings(
        warning for analysis in analyses for warning in analysis.warnings
    ):
        return True
    return any(has_risky_warnings(candidate.warnings) for candidate in strong_candidates)


def has_risky_warnings(warnings) -> bool:
    return any(
        warning in {"ambiguous_multiple_lines", "likely_table_line"}
        or str(warning).startswith(("ocr_failed", "ocr_no_words", "low_ocr_confidence"))
        for warning in warnings
    )


def placement_from_decision(decision: AIPlacementDecision) -> Placement | None:
    signature = None
    stamp = None
    name = None

    if decision.should_sign and decision.signature_bbox is not None:
        signature = ImageOverlay(enabled=True, bbox=decision.signature_bbox)
    if decision.should_stamp and decision.stamp_bbox is not None:
        stamp = ImageOverlay(enabled=True, bbox=decision.stamp_bbox)
    if decision.should_add_name and decision.name_bbox is not None:
        name = NameOverlay(
            enabled=True,
            text=decision.name_text or "Венедиктов Р.В.",
            bbox=decision.name_bbox,
        )

    if not any((signature, stamp, name)):
        return None

    return Placement(
        placement_id=f"ai_{decision.candidate_id}",
        page_number=decision.page_number,
        signature=signature,
        stamp=stamp,
        name=name,
        confidence=decision.confidence,
        needs_manual_review=decision.needs_manual_review,
        source="ai",
    )


def validate_ai_placement(
    placement: Placement,
    analyses: list[PageAnalysis],
) -> str | None:
    analysis = next(
        (item for item in analyses if item.page_number == placement.page_number),
        None,
    )
    if analysis is None:
        return "page_not_found"

    for bbox in placement_bboxes(placement):
        if not bbox_inside_page(bbox, analysis.page_size):
            return "bbox_outside_page"

    if placement.signature is not None:
        if not any(
            is_near(placement.signature.bbox, candidate.context_bbox, padding=80)
            for candidate in analysis.candidates
        ):
            return "signature_far_from_candidates"

    if placement.stamp is not None:
        stamp = placement.stamp.bbox
        width = stamp.x1 - stamp.x0
        height = stamp.y1 - stamp.y0
        if not (MIN_STAMP_SIZE <= width <= MAX_STAMP_SIZE):
            return "stamp_width_out_of_range"
        if abs(width - height) > 6:
            return "stamp_not_square"

        if placement.signature is not None:
            signature = placement.signature.bbox
            overlap_width = min(stamp.x1, signature.x1) - max(stamp.x0, signature.x0)
            if overlap_width < 5:
                return "stamp_not_overlapping_signature"

        if placement.name is not None:
            overlap = overlap_area(stamp, placement.name.bbox)
            name_area = area(placement.name.bbox)
            if name_area and overlap / name_area > 0.1:
                return "stamp_covers_name"

    return None


def placement_bboxes(placement: Placement) -> list[BoundingBox]:
    bboxes: list[BoundingBox] = []
    if placement.signature is not None:
        bboxes.append(placement.signature.bbox)
    if placement.stamp is not None:
        bboxes.append(placement.stamp.bbox)
    if placement.name is not None:
        bboxes.append(placement.name.bbox)
    return bboxes


def bbox_inside_page(bbox: BoundingBox, page_size: PageSize) -> bool:
    return bbox.x0 >= 0 and bbox.y0 >= 0 and bbox.x1 <= page_size.width and bbox.y1 <= page_size.height


def area(bbox: BoundingBox) -> float:
    return max(0, bbox.x1 - bbox.x0) * max(0, bbox.y1 - bbox.y0)


def overlap_area(left: BoundingBox, right: BoundingBox) -> float:
    x0 = max(left.x0, right.x0)
    y0 = max(left.y0, right.y0)
    x1 = min(left.x1, right.x1)
    y1 = min(left.y1, right.y1)
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def is_near(a: BoundingBox, b: BoundingBox, padding: float = 0) -> bool:
    return not (
        a.x1 < b.x0 - padding
        or a.x0 > b.x1 + padding
        or a.y1 < b.y0 - padding
        or a.y0 > b.y1 + padding
    )


def expand_bbox(bbox: BoundingBox, page_size: PageSize, padding: float) -> BoundingBox:
    return BoundingBox(
        x0=max(0, bbox.x0 - padding),
        y0=max(0, bbox.y0 - padding),
        x1=min(page_size.width, bbox.x1 + padding),
        y1=min(page_size.height, bbox.y1 + padding),
    )
