from pathlib import Path
from uuid import uuid4

import fitz

from .models import CoordinateScale, PageSize, PreviewPage, PreviewResponse
from .storage import LocalStorage


DEFAULT_PREVIEW_DPI = 144


def preview_page_dimensions(page: fitz.Page, dpi: int = DEFAULT_PREVIEW_DPI) -> tuple[int, int]:
    scale = dpi / 72
    return (
        max(1, round(page.rect.width * scale)),
        max(1, round(page.rect.height * scale)),
    )


def render_preview(
    *,
    job_id: str,
    filename: str,
    source_path: Path,
    storage: LocalStorage,
    placements: list | None = None,
    dpi: int = DEFAULT_PREVIEW_DPI,
) -> PreviewResponse:
    preview_dir = storage.preview_dir(job_id)
    preview_dir.mkdir(parents=True, exist_ok=True)

    pages: list[PreviewPage] = []
    with fitz.open(source_path) as document:
        for index, page in enumerate(document, start=1):
            page_rect = page.rect
            page_size = PageSize(width=page_rect.width, height=page_rect.height)
            preview_path = storage.preview_page_path(job_id, index)
            preview_width, preview_height = preview_page_dimensions(page, dpi)

            pages.append(
                PreviewPage(
                    page_number=index,
                    page_size=page_size,
                    image_url=f"/previews/{job_id}/{preview_path.name}",
                    preview_width=preview_width,
                    preview_height=preview_height,
                    scale=CoordinateScale(
                        x=preview_width / page_size.width,
                        y=preview_height / page_size.height,
                    ),
                )
            )

        return PreviewResponse(
            job_id=job_id,
            filename=filename,
            page_count=document.page_count,
            pages=pages,
            placements=placements or [],
        )


def render_preview_page(
    *,
    job_id: str,
    source_path: Path,
    storage: LocalStorage,
    page_number: int,
    dpi: int = DEFAULT_PREVIEW_DPI,
) -> Path:
    if page_number < 1:
        raise IndexError("page number must be positive")

    preview_dir = storage.preview_dir(job_id)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = storage.preview_page_path(job_id, page_number)
    if preview_path.exists():
        return preview_path

    tmp_path = preview_path.with_name(f"{preview_path.stem}.{uuid4().hex}.tmp.png")
    try:
        with fitz.open(source_path) as document:
            if page_number > document.page_count:
                raise IndexError("page number out of range")
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(tmp_path)
        tmp_path.replace(preview_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return preview_path
