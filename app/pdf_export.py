from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fitz

from .config import Settings
from .models import DocumentJob, ExportResult, ExportType, ExportedFile, JobStatus
from .storage import LocalStorage, signed_filename


def export_jobs(
    *,
    jobs: list[DocumentJob],
    storage: LocalStorage,
    settings: Settings,
    user_id: int = 0,
    signature_png: bytes | None = None,
    stamp_png: bytes | None = None,
) -> ExportResult:
    export_id = storage.new_export_id()
    export_dir = storage.prepare_export_dir(export_id)
    exported_files: list[ExportedFile] = []
    output_paths: list[Path] = []

    for job in jobs:
        output_filename = signed_filename(job.filename)
        output_path = export_dir / output_filename
        export_single_pdf(
            job=job,
            output_path=output_path,
            signature_image_path=settings.signature_image_path,
            stamp_image_path=settings.stamp_image_path,
            signature_png=signature_png,
            stamp_png=stamp_png,
        )
        job.status = JobStatus.EXPORTED
        output_paths.append(output_path)
        exported_files.append(
            ExportedFile(
                job_id=job.job_id,
                output_filename=output_filename,
                warnings=job.warnings,
            )
        )

    if len(output_paths) == 1:
        return ExportResult(
            export_id=export_id,
            user_id=user_id,
            type=ExportType.PDF,
            path=output_paths[0],
            files=exported_files,
        )

    zip_path = export_dir / "signed_documents.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for output_path in output_paths:
            archive.write(output_path, arcname=output_path.name)

    return ExportResult(
        export_id=export_id,
        user_id=user_id,
        type=ExportType.ZIP,
        path=zip_path,
        files=exported_files,
    )


def export_single_pdf(
    *,
    job: DocumentJob,
    output_path: Path,
    signature_image_path: Path,
    stamp_image_path: Path,
    signature_png: bytes | None = None,
    stamp_png: bytes | None = None,
) -> None:
    with fitz.open(job.source_path) as document:
        name_font_path = find_unicode_font()
        for placement in job.placements:
            page = document[placement.page_number - 1]

            if placement.signature and placement.signature.enabled:
                insert_image(
                    page=page,
                    image_path=signature_image_path,
                    image_bytes=signature_png,
                    bbox=placement.signature.bbox.as_list(),
                )

            if placement.stamp and placement.stamp.enabled:
                insert_image(
                    page=page,
                    image_path=stamp_image_path,
                    image_bytes=stamp_png,
                    bbox=placement.stamp.bbox.as_list(),
                )

            if placement.name and placement.name.enabled:
                rect = fitz.Rect(placement.name.bbox.as_list())
                insert_name_text(
                    page=page,
                    rect=rect,
                    text=placement.name.text,
                    font_path=name_font_path,
                )

        document.save(output_path)


def insert_image(*, page: fitz.Page, image_path: Path, bbox: list[float], image_bytes: bytes | None = None) -> None:
    kwargs = {
        "rect": fitz.Rect(bbox),
        "keep_proportion": True,
        "overlay": True,
    }
    if image_bytes:
        kwargs["stream"] = image_bytes
    else:
        kwargs["filename"] = str(image_path)
    page.insert_image(**kwargs)


def insert_name_text(
    *,
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_path: Path | None,
) -> None:
    kwargs = {
        "fontsize": max(8, min(14, rect.height * 0.7)),
        "color": (0, 0, 0),
        "align": fitz.TEXT_ALIGN_LEFT,
        "overlay": True,
    }
    if font_path is not None:
        kwargs["fontfile"] = str(font_path)
        kwargs["fontname"] = "namefont"

    page.insert_textbox(rect, text, **kwargs)


def find_unicode_font() -> Path | None:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    return next((path for path in candidates if path.exists()), None)
