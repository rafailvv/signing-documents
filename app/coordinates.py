from .models import BoundingBox, PageSize


def pdf_to_preview_bbox(
    bbox: BoundingBox,
    page_size: PageSize,
    preview_width: int,
    preview_height: int,
) -> BoundingBox:
    x_scale = preview_width / page_size.width
    y_scale = preview_height / page_size.height
    return BoundingBox(
        x0=bbox.x0 * x_scale,
        y0=bbox.y0 * y_scale,
        x1=bbox.x1 * x_scale,
        y1=bbox.y1 * y_scale,
    )


def preview_to_pdf_bbox(
    bbox: BoundingBox,
    page_size: PageSize,
    preview_width: int,
    preview_height: int,
) -> BoundingBox:
    x_scale = page_size.width / preview_width
    y_scale = page_size.height / preview_height
    return BoundingBox(
        x0=bbox.x0 * x_scale,
        y0=bbox.y0 * y_scale,
        x1=bbox.x1 * x_scale,
        y1=bbox.y1 * y_scale,
    )
