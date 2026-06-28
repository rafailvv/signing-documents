from uuid import uuid4

from .local_analysis import normalize_token
from .models import (
    BoundingBox,
    ImageOverlay,
    NameOverlay,
    PageAnalysis,
    PageSize,
    Placement,
    ProcessingOptions,
    SignatureTarget,
)


POINTS_PER_MM = 72 / 25.4
STAMP_MIN_SIZE_POINTS = 35 * POINTS_PER_MM
STAMP_SIZE_POINTS = 40 * POINTS_PER_MM


def create_auto_placements(
    analyses: list[PageAnalysis],
    options: ProcessingOptions,
) -> list[Placement]:
    placements: list[Placement] = []

    for analysis in analyses:
        for candidate in analysis.candidates:
            if candidate.confidence < 0.7 or candidate.line_bbox is None:
                continue
            placement = create_placement_from_target(
                analysis=analysis,
                target=candidate,
                options=options,
            )
            if placement is not None:
                placements.append(placement)

    return placements


def create_placement_from_target(
    *,
    analysis: PageAnalysis,
    target: SignatureTarget,
    options: ProcessingOptions,
) -> Placement | None:
    signature = None
    stamp = None
    name = None

    signature_bbox = calculate_signature_bbox(target.line_bbox, analysis.page_size)

    if options.place_signature:
        signature = ImageOverlay(enabled=True, bbox=signature_bbox, rotation=0)

    if options.place_stamp:
        stamp_bbox = calculate_stamp_bbox(
            signature_bbox,
            analysis.page_size,
            line_bbox=target.line_bbox,
        )
        stamp = ImageOverlay(enabled=True, bbox=stamp_bbox, rotation=0)

    if options.add_name_if_missing and not page_has_full_name_near_target(
        analysis=analysis,
        target=target,
    ):
        name = NameOverlay(
            enabled=True,
            text="Венедиктов Р.В.",
            bbox=calculate_name_bbox(signature_bbox, analysis.page_size),
        )

    if not any((signature, stamp, name)):
        return None

    return Placement(
        placement_id=f"auto_{uuid4().hex}",
        page_number=target.page_number,
        signature=signature,
        stamp=stamp,
        name=name,
        confidence=target.confidence,
        needs_manual_review=bool(target.warnings),
        source="auto",
    )


def calculate_signature_bbox(line_bbox: BoundingBox, page_size: PageSize) -> BoundingBox:
    line_width = line_bbox.x1 - line_bbox.x0
    if line_width < 150:
        signature_width = max(line_width * 1.55, 180)
    else:
        signature_width = max(line_width * 1.05, 160)
    signature_width = min(signature_width, min(290, page_size.width * 0.5))
    signature_height = signature_width * 0.32
    center_x = (line_bbox.x0 + line_bbox.x1) / 2
    baseline_y = (line_bbox.y0 + line_bbox.y1) / 2

    x0 = center_x - signature_width / 2
    y1 = baseline_y + signature_height * 0.45
    y0 = y1 - signature_height
    return clamp_bbox(
        BoundingBox(x0=x0, y0=y0, x1=x0 + signature_width, y1=y1),
        page_size,
    )


def calculate_stamp_bbox(
    signature_bbox: BoundingBox,
    page_size: PageSize,
    *,
    line_bbox: BoundingBox | None = None,
) -> BoundingBox:
    signature_width = signature_bbox.x1 - signature_bbox.x0
    signature_height = signature_bbox.y1 - signature_bbox.y0
    size = STAMP_MIN_SIZE_POINTS if signature_width < 180 else STAMP_SIZE_POINTS

    anchor_bbox = line_bbox or signature_bbox
    anchor_center_x = (anchor_bbox.x0 + anchor_bbox.x1) / 2
    if anchor_center_x < page_size.width * 0.42:
        x0 = signature_bbox.x1 - size * 0.55
    else:
        x0 = signature_bbox.x0 - size * 0.35
    y0 = signature_bbox.y1 - size * 0.28
    return clamp_bbox(BoundingBox(x0=x0, y0=y0, x1=x0 + size, y1=y0 + size), page_size)


def calculate_name_bbox(signature_bbox: BoundingBox, page_size: PageSize) -> BoundingBox:
    width = min(190, page_size.width * 0.36)
    height = 30
    x0 = signature_bbox.x1 + 12
    if x0 + width > page_size.width:
        x0 = max(0, signature_bbox.x0)
        y0 = min(page_size.height - height, signature_bbox.y1 + 8)
    else:
        y0 = signature_bbox.y1 - height
    return clamp_bbox(BoundingBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + height), page_size)


def page_has_full_name_near_target(
    *,
    analysis: PageAnalysis,
    target: SignatureTarget,
) -> bool:
    nearby_words = [
        word
        for word in analysis.words
        if bboxes_overlap_or_near(word.bbox, target.context_bbox, padding=20)
    ]
    normalized = [normalize_token(word.text) for word in nearby_words]
    for index in range(0, len(normalized) - 1):
        if normalized[index] == "венедиктов" and normalized[index + 1] == "рв":
            return True
        if (
            normalized[index] == "венедиктов"
            and len(normalized[index + 1]) > 1
            and normalized[index + 1] not in {"подпись", "директор"}
        ):
            return True
    return False


def bboxes_overlap_or_near(a: BoundingBox, b: BoundingBox, padding: float = 0) -> bool:
    return not (
        a.x1 < b.x0 - padding
        or a.x0 > b.x1 + padding
        or a.y1 < b.y0 - padding
        or a.y0 > b.y1 + padding
    )


def clamp_bbox(bbox: BoundingBox, page_size: PageSize) -> BoundingBox:
    width = bbox.x1 - bbox.x0
    height = bbox.y1 - bbox.y0
    x0 = min(max(0, bbox.x0), max(0, page_size.width - width))
    y0 = min(max(0, bbox.y0), max(0, page_size.height - height))
    return BoundingBox(
        x0=x0,
        y0=y0,
        x1=min(page_size.width, x0 + width),
        y1=min(page_size.height, y0 + height),
    )
