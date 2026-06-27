from pathlib import Path

import fitz

from .models import CoordinateScale, PageSize, PreviewPage, PreviewResponse
from .storage import LocalStorage


DEFAULT_PREVIEW_DPI = 144


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

            if not preview_path.exists():
                pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                pixmap.save(preview_path)
            else:
                pixmap = fitz.Pixmap(preview_path)

            pages.append(
                PreviewPage(
                    page_number=index,
                    page_size=page_size,
                    image_url=f"/previews/{job_id}/{preview_path.name}",
                    preview_width=pixmap.width,
                    preview_height=pixmap.height,
                    scale=CoordinateScale(
                        x=pixmap.width / page_size.width,
                        y=pixmap.height / page_size.height,
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
