from app.auto_placement import (
    STAMP_SIZE_POINTS,
    calculate_name_bbox,
    calculate_signature_bbox,
    calculate_stamp_bbox,
    create_auto_placements,
)
from app.local_analysis import analyze_pdf
from app.models import BoundingBox, PageSize, ProcessingOptions
from tests.pdf_factory import make_signature_pdf_bytes


def test_signature_bbox_scales_from_line_width():
    page_size = PageSize(width=595, height=842)
    line = BoundingBox(x0=210, y0=720, x1=430, y1=721)

    bbox = calculate_signature_bbox(line, page_size)

    assert 180 <= bbox.x1 - bbox.x0 <= 190
    assert bbox.y0 < line.y0 < bbox.y1
    assert bbox.x0 >= 0
    assert bbox.x1 <= page_size.width


def test_signature_bbox_is_larger_and_lower_for_short_approval_line():
    page_size = PageSize(width=595, height=842)
    line = BoundingBox(x0=354, y0=208, x1=445, y1=209)

    bbox = calculate_signature_bbox(line, page_size)

    assert bbox.x1 - bbox.x0 >= 165
    assert bbox.y0 < line.y0 < bbox.y1
    assert bbox.y1 - line.y1 > 20


def test_stamp_bbox_is_40mm_and_overlaps_signature():
    page_size = PageSize(width=595, height=842)
    signature = BoundingBox(x0=330, y0=610, x1=540, y1=690)
    line = BoundingBox(x0=360, y0=650, x1=500, y1=651)

    stamp = calculate_stamp_bbox(signature, page_size, line_bbox=line)

    assert abs((stamp.x1 - stamp.x0) - STAMP_SIZE_POINTS) < 0.01
    assert abs((stamp.y1 - stamp.y0) - STAMP_SIZE_POINTS) < 0.01
    assert stamp.x0 < signature.x0
    assert signature.x0 < stamp.x1 < signature.x1
    assert stamp.y0 < signature.y1 and stamp.y1 > signature.y0
    assert stamp.y0 > (signature.y0 + signature.y1) / 2


def test_stamp_moves_right_for_left_side_signature_line():
    page_size = PageSize(width=595, height=842)
    signature = BoundingBox(x0=55, y0=518, x1=220, y1=571)
    line = BoundingBox(x0=92, y0=547, x1=182, y1=548)

    stamp = calculate_stamp_bbox(signature, page_size, line_bbox=line)

    assert signature.x0 < stamp.x0 < signature.x1
    assert stamp.x1 > signature.x1
    assert stamp.y0 < signature.y1 and stamp.y1 > signature.y0
    assert stamp.y0 > (signature.y0 + signature.y1) / 2


def test_stamp_sits_lower_left_for_written_full_name_signature_line():
    page_size = PageSize(width=595.2, height=841.92)
    line = BoundingBox(x0=201.9, y0=569.4, x1=363.8, y1=570.4)

    signature = calculate_signature_bbox(line, page_size)
    stamp = calculate_stamp_bbox(signature, page_size, line_bbox=line)

    assert stamp.x0 < signature.x0
    assert signature.x0 < stamp.x1 < signature.x1
    assert stamp.y0 > (signature.y0 + signature.y1) / 2
    assert stamp.y0 < signature.y1


def test_name_bbox_is_large_enough_for_default_name():
    page_size = PageSize(width=595, height=842)
    signature = BoundingBox(x0=245, y0=679, x1=415, y1=734)

    name = calculate_name_bbox(signature, page_size)

    assert name.x1 - name.x0 >= 180
    assert name.y1 - name.y0 >= 28
    assert name.x1 <= page_size.width


def test_create_auto_placement_adds_name_when_full_name_missing(tmp_path):
    pdf_path = tmp_path / "director.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Генеральный директор",
            line=(230, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path)

    placements = create_auto_placements(
        analyses,
        ProcessingOptions(
            place_signature=True,
            place_stamp=True,
            add_name_if_missing=True,
        ),
    )

    assert len(placements) == 1
    placement = placements[0]
    assert placement.source == "auto"
    assert placement.signature is not None
    assert placement.stamp is not None
    assert placement.name is not None
    assert placement.name.text == "Венедиктов Р.В."


def test_create_auto_placement_does_not_duplicate_existing_full_name(tmp_path):
    pdf_path = tmp_path / "venediktov.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path)

    placements = create_auto_placements(
        analyses,
        ProcessingOptions(add_name_if_missing=True),
    )

    assert len(placements) == 1
    assert placements[0].name is None


def test_create_auto_placement_does_not_add_initials_when_full_written_name_exists(tmp_path):
    pdf_path = tmp_path / "full_written_name.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Генеральный директор Венедиктов Рафаил Владимирович",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path)

    placements = create_auto_placements(
        analyses,
        ProcessingOptions(add_name_if_missing=True),
    )

    assert len(placements) == 1
    assert placements[0].name is None


def test_create_auto_placement_uses_lower_signer_line_with_written_full_name(tmp_path):
    pdf_path = tmp_path / "enrollment_order.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="/ Венедиктов Рафаил Владимирович",
            text_position=(365, 570),
            line=(202, 570, 364, 570),
            extra_lines=[
                (85, 301, 421, 301),
                (91, 363, 505, 363),
                (85, 465, 194, 465),
            ],
        )
    )
    analyses = analyze_pdf(pdf_path)

    placements = create_auto_placements(
        analyses,
        ProcessingOptions(add_name_if_missing=True),
    )

    assert len(placements) == 1
    placement = placements[0]
    assert placement.name is None
    assert placement.signature is not None
    assert placement.stamp is not None
    assert 190 <= placement.signature.bbox.x0 <= 230
    assert 540 <= placement.signature.bbox.y0 <= 565
    assert placement.stamp.bbox.x0 < placement.signature.bbox.x0
    assert placement.stamp.bbox.y0 > (placement.signature.bbox.y0 + placement.signature.bbox.y1) / 2


def test_create_auto_placement_respects_signature_and_stamp_options(tmp_path):
    pdf_path = tmp_path / "options.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path)

    placements = create_auto_placements(
        analyses,
        ProcessingOptions(
            place_signature=False,
            place_stamp=True,
            add_name_if_missing=False,
        ),
    )

    assert len(placements) == 1
    assert placements[0].signature is None
    assert placements[0].stamp is not None
    assert placements[0].name is None
