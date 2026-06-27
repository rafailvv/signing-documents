from app.coordinates import pdf_to_preview_bbox, preview_to_pdf_bbox
from app.models import BoundingBox, PageSize


def test_pdf_preview_coordinate_conversion_round_trip():
    page_size = PageSize(width=595, height=842)
    preview_width = 1190
    preview_height = 1684
    original = BoundingBox(x0=10, y0=20, x1=180, y1=90)

    preview = pdf_to_preview_bbox(
        original,
        page_size,
        preview_width,
        preview_height,
    )
    converted = preview_to_pdf_bbox(
        preview,
        page_size,
        preview_width,
        preview_height,
    )

    assert converted.x0 == original.x0
    assert converted.y0 == original.y0
    assert converted.x1 == original.x1
    assert converted.y1 == original.y1
