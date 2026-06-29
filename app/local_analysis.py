from dataclasses import dataclass
from math import inf
from pathlib import Path
from re import sub
from uuid import uuid4

import fitz

from .models import (
    BoundingBox,
    DetectedLine,
    PageAnalysis,
    PageSize,
    SignatureTarget,
    WordBox,
)
from .ocr import extract_ocr_words


ANCHOR_PATTERNS = [
    ("venediktov_full_name", "Венедиктов Р.В.", ["венедиктов", "рв"]),
    ("venediktov", "Венедиктов", ["венедиктов"]),
    ("general_director", "Генеральный директор", ["генеральный", "директор"]),
    ("signature_word", "подпись", ["подпись"]),
    ("fio_word", "ФИО", ["фио"]),
]


@dataclass(frozen=True)
class AnchorHit:
    kind: str
    label: str
    bbox: BoundingBox
    word_indexes: tuple[int, ...]


def analyze_pdf(source_path: Path, *, ocr_languages: str = "rus+eng") -> list[PageAnalysis]:
    analyses: list[PageAnalysis] = []
    with fitz.open(source_path) as document:
        for page_number, page in enumerate(document, start=1):
            page_size = PageSize(width=page.rect.width, height=page.rect.height)
            warnings: list[str] = []
            words = extract_words(page)
            text = page.get_text("text") or ""
            text_quality = "pdf_text_layer" if words else "no_text_layer"
            if not words:
                words, text, ocr_warnings = extract_ocr_words(
                    page,
                    languages=ocr_languages,
                )
                warnings.extend(ocr_warnings)
                text_quality = "ocr" if words else "ocr_failed"

            lines = extract_horizontal_lines(page)
            text_lines = extract_text_underscore_lines(words)
            for text_line in text_lines:
                if not overlaps_existing_line(text_line, lines):
                    lines.append(text_line)
            if not lines:
                lines = extract_raster_horizontal_lines(page)
                if lines:
                    warnings.append("raster_lines_used")

            anchors = find_anchor_hits(words)
            candidates = build_signature_targets(
                page_number=page_number,
                page_size=page_size,
                anchors=anchors,
                lines=lines,
            )

            analyses.append(
                PageAnalysis(
                    page_number=page_number,
                    page_size=page_size,
                    text_quality=text_quality,
                    ocr_text=text,
                    words=words,
                    lines=lines,
                    candidates=candidates,
                    warnings=sorted(set(warnings)),
                )
            )
    return analyses


def extract_words(page: fitz.Page) -> list[WordBox]:
    extracted: list[WordBox] = []
    for item in page.get_text("words"):
        x0, y0, x1, y1, text = item[:5]
        if not str(text).strip():
            continue
        extracted.append(
            WordBox(
                text=str(text),
                bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
            )
        )
    return extracted


def extract_horizontal_lines(page: fitz.Page) -> list[DetectedLine]:
    lines: list[DetectedLine] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if not item or item[0] != "l":
                continue
            p1 = item[1]
            p2 = item[2]
            if abs(p1.y - p2.y) > 1.5:
                continue
            width = abs(p2.x - p1.x)
            if width < 45:
                continue
            x0 = min(p1.x, p2.x)
            x1 = max(p1.x, p2.x)
            y = (p1.y + p2.y) / 2
            lines.append(
                DetectedLine(
                    bbox=BoundingBox(x0=x0, y0=max(0, y - 0.5), x1=x1, y1=y + 0.5),
                    width=width,
                    type="horizontal",
                )
            )
    return lines


def extract_text_underscore_lines(words: list[WordBox]) -> list[DetectedLine]:
    lines: list[DetectedLine] = []
    for word in words:
        text = word.text.strip()
        if len(text) < 8:
            continue
        if text.count("_") / len(text) < 0.75:
            continue
        width = word.bbox.x1 - word.bbox.x0
        if width < 45:
            continue
        y = word.bbox.y1 - 0.5
        lines.append(
            DetectedLine(
                bbox=BoundingBox(
                    x0=word.bbox.x0,
                    y0=max(0, y - 0.5),
                    x1=word.bbox.x1,
                    y1=y + 0.5,
                ),
                width=width,
                type="text_underscore",
            )
        )
    return lines


def extract_raster_horizontal_lines(
    page: fitz.Page,
    *,
    dpi: int = 144,
    darkness_threshold: int = 210,
) -> list[DetectedLine]:
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    width = pixmap.width
    height = pixmap.height
    channels = pixmap.n
    samples = pixmap.samples
    x_scale = page.rect.width / width
    y_scale = page.rect.height / height
    min_run_px = max(40, int(45 / x_scale))
    lines: list[DetectedLine] = []

    for y in range(0, height, 2):
        runs = dark_runs_in_row(
            samples,
            width,
            channels,
            y,
            min_run_px,
            darkness_threshold=darkness_threshold,
        )
        for x0, x1 in runs:
            pdf_x0 = x0 * x_scale
            pdf_x1 = x1 * x_scale
            pdf_y = y * y_scale
            candidate = DetectedLine(
                bbox=BoundingBox(
                    x0=pdf_x0,
                    y0=max(0, pdf_y - y_scale),
                    x1=pdf_x1,
                    y1=min(page.rect.height, pdf_y + y_scale),
                ),
                width=pdf_x1 - pdf_x0,
                type="horizontal_raster",
            )
            if not overlaps_existing_line(candidate, lines):
                lines.append(candidate)

    return lines


def dark_runs_in_row(
    samples: bytes,
    width: int,
    channels: int,
    y: int,
    min_run_px: int,
    *,
    darkness_threshold: int = 210,
) -> list[tuple[int, int]]:
    row_offset = y * width * channels
    runs: list[tuple[int, int]] = []
    run_start: int | None = None

    for x in range(width):
        offset = row_offset + x * channels
        r = samples[offset]
        g = samples[offset + 1]
        b = samples[offset + 2]
        is_dark = (int(r) + int(g) + int(b)) / 3 < darkness_threshold
        if is_dark and run_start is None:
            run_start = x
        elif not is_dark and run_start is not None:
            if x - run_start >= min_run_px:
                runs.append((run_start, x))
            run_start = None

    if run_start is not None and width - run_start >= min_run_px:
        runs.append((run_start, width))
    return runs


def overlaps_existing_line(candidate: DetectedLine, lines: list[DetectedLine]) -> bool:
    candidate_y = (candidate.bbox.y0 + candidate.bbox.y1) / 2
    for line in lines:
        line_y = (line.bbox.y0 + line.bbox.y1) / 2
        if abs(candidate_y - line_y) < 3 and not (
            candidate.bbox.x1 < line.bbox.x0 or candidate.bbox.x0 > line.bbox.x1
        ):
            return True
    return False


def find_anchor_hits(words: list[WordBox]) -> list[AnchorHit]:
    normalized_words = [normalize_token(word.text) for word in words]
    hits: list[AnchorHit] = []

    for kind, label, pattern in ANCHOR_PATTERNS:
        pattern_len = len(pattern)
        for start in range(0, len(words) - pattern_len + 1):
            window = normalized_words[start : start + pattern_len]
            if token_window_matches(window, pattern):
                indexes = tuple(range(start, start + pattern_len))
                hits.append(
                    AnchorHit(
                        kind=kind,
                        label=label,
                        bbox=union_bbox([words[index].bbox for index in indexes]),
                        word_indexes=indexes,
                    )
                )

    return deduplicate_anchor_hits(hits)


def build_signature_targets(
    *,
    page_number: int,
    page_size: PageSize,
    anchors: list[AnchorHit],
    lines: list[DetectedLine],
) -> list[SignatureTarget]:
    candidates: list[SignatureTarget] = []
    for anchor in anchors:
        line, distance = nearest_line(anchor, lines)
        if line is None:
            candidates.append(
                SignatureTarget(
                    candidate_id=f"target_{uuid4().hex}",
                    page_number=page_number,
                    line_bbox=None,
                    context_bbox=expand_bbox(anchor.bbox, page_size, 80),
                    anchor=anchor.label,
                    reason=f"anchor_without_line:{anchor.kind}",
                    confidence=confidence_for_anchor(anchor.kind, has_line=False),
                    warnings=["line_not_found", "needs_manual_review"],
                )
            )
            continue

        confidence = confidence_for_anchor(anchor.kind, has_line=True)
        if distance > 90:
            confidence = min(confidence, 0.55)

        warnings: list[str] = []
        if distance > 90:
            warnings.append("line_far_from_anchor")
        if len(lines) > 3:
            warnings.append("ambiguous_multiple_lines")
        if is_likely_table_border(line, page_size):
            confidence = min(confidence, 0.45)
            warnings.append("likely_table_line")

        reason = reason_for(anchor.kind)
        candidates.append(
            SignatureTarget(
                candidate_id=f"target_{uuid4().hex}",
                page_number=page_number,
                line_bbox=line.bbox,
                context_bbox=expand_bbox(union_bbox([anchor.bbox, line.bbox]), page_size, 50),
                anchor=anchor.label,
                reason=reason,
                confidence=confidence,
                warnings=warnings,
            )
        )

    return suppress_name_targets_when_signature_line_exists(
        deduplicate_targets_by_line(candidates)
    )


def normalize_token(value: str) -> str:
    lowered = value.casefold().replace("ё", "е")
    lowered = lowered.translate(
        str.maketrans(
            {
                "0": "о",
                "o": "о",
                "p": "р",
                "b": "в",
                "c": "с",
                "e": "е",
                "a": "а",
                "x": "х",
                "y": "у",
            }
        )
    )
    return sub(r"[^a-zа-я0-9]+", "", lowered)


def token_window_matches(window: list[str], pattern: list[str]) -> bool:
    return all(token_matches(actual, expected) for actual, expected in zip(window, pattern))


def token_matches(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if len(expected) <= 3:
        return levenshtein_distance(actual, expected) <= 1
    return levenshtein_distance(actual, expected) <= 2


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def nearest_line(
    anchor: AnchorHit, lines: list[DetectedLine]
) -> tuple[DetectedLine | None, float]:
    best_line: DetectedLine | None = None
    best_distance = inf
    anchor_center_x = (anchor.bbox.x0 + anchor.bbox.x1) / 2
    anchor_center_y = (anchor.bbox.y0 + anchor.bbox.y1) / 2

    for line in lines:
        line_center_y = (line.bbox.y0 + line.bbox.y1) / 2
        vertical_distance = abs(line_center_y - anchor_center_y)
        horizontal_gap = 0
        if anchor_center_x < line.bbox.x0:
            horizontal_gap = line.bbox.x0 - anchor_center_x
        elif anchor_center_x > line.bbox.x1:
            horizontal_gap = anchor_center_x - line.bbox.x1

        score = vertical_distance + horizontal_gap * 0.25
        if score < best_distance:
            best_distance = score
            best_line = line

    if best_line is None:
        return None, inf
    return best_line, best_distance


def confidence_for_anchor(kind: str, *, has_line: bool) -> float:
    if not has_line:
        return {
            "venediktov_full_name": 0.45,
            "venediktov": 0.4,
            "general_director": 0.35,
            "signature_word": 0.25,
            "fio_word": 0.25,
        }.get(kind, 0.2)

    return {
        "venediktov_full_name": 0.9,
        "venediktov": 0.82,
        "general_director": 0.76,
        "signature_word": 0.78,
        "fio_word": 0.58,
    }.get(kind, 0.5)


def reason_for(kind: str) -> str:
    return {
        "venediktov_full_name": "line_near_venediktov",
        "venediktov": "line_near_venediktov",
        "general_director": "line_near_general_director",
        "signature_word": "line_near_signature_word",
        "fio_word": "line_near_fio_word",
    }.get(kind, "line_near_anchor")


def union_bbox(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def expand_bbox(bbox: BoundingBox, page_size: PageSize, padding: float) -> BoundingBox:
    return BoundingBox(
        x0=max(0, bbox.x0 - padding),
        y0=max(0, bbox.y0 - padding),
        x1=min(page_size.width, bbox.x1 + padding),
        y1=min(page_size.height, bbox.y1 + padding),
    )


def deduplicate_anchor_hits(hits: list[AnchorHit]) -> list[AnchorHit]:
    deduped: list[AnchorHit] = []
    occupied_indexes: set[int] = set()
    for hit in sorted(hits, key=lambda item: len(item.word_indexes), reverse=True):
        if any(index in occupied_indexes for index in hit.word_indexes):
            continue
        deduped.append(hit)
        occupied_indexes.update(hit.word_indexes)
    return deduped


def is_likely_table_border(line: DetectedLine, page_size: PageSize) -> bool:
    return line.type == "horizontal_raster" and line.width / page_size.width > 0.7


def deduplicate_targets_by_line(candidates: list[SignatureTarget]) -> list[SignatureTarget]:
    grouped: dict[tuple[int, int, int, int] | str, SignatureTarget] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (item.confidence, anchor_priority(item.anchor)),
        reverse=True,
    ):
        if candidate.line_bbox is None:
            key: tuple[int, int, int, int] | str = f"no-line:{candidate.anchor}:{candidate.page_number}"
        else:
            key = (
                round(candidate.line_bbox.x0),
                round(candidate.line_bbox.y0),
                round(candidate.line_bbox.x1),
                round(candidate.line_bbox.y1),
            )
        grouped.setdefault(key, candidate)
    return sorted(grouped.values(), key=lambda item: item.confidence, reverse=True)


def suppress_name_targets_when_signature_line_exists(
    candidates: list[SignatureTarget],
) -> list[SignatureTarget]:
    signature_line_targets = [
        candidate
        for candidate in candidates
        if candidate.anchor == "подпись" and candidate.line_bbox is not None
    ]
    if not signature_line_targets:
        return candidates

    filtered: list[SignatureTarget] = []
    for candidate in candidates:
        if candidate.line_bbox is None or candidate.anchor not in {"Венедиктов", "Венедиктов Р.В."}:
            filtered.append(candidate)
            continue
        name_line_y = line_center_y(candidate.line_bbox)
        has_better_signature_line = any(
            is_separate_signature_line_on_same_row(candidate.line_bbox, signature_target.line_bbox)
            and line_center_y(signature_target.line_bbox) - 12 <= name_line_y <= line_center_y(signature_target.line_bbox) + 12
            for signature_target in signature_line_targets
            if signature_target.line_bbox is not None
        )
        if not has_better_signature_line:
            filtered.append(candidate)
    return filtered


def is_separate_signature_line_on_same_row(
    name_line: BoundingBox,
    signature_line: BoundingBox,
) -> bool:
    horizontal_gap = signature_line.x0 - name_line.x1
    return 0 <= horizontal_gap <= 80


def line_center_y(line: BoundingBox) -> float:
    return (line.y0 + line.y1) / 2


def anchor_priority(anchor: str) -> int:
    if anchor == "Венедиктов Р.В.":
        return 5
    if anchor == "Венедиктов":
        return 4
    if anchor == "Генеральный директор":
        return 3
    if anchor == "подпись":
        return 2
    if anchor == "ФИО":
        return 1
    return 0
