from io import BytesIO
from math import ceil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
from PIL import Image

from .config import Settings
from .models import DocumentJob, ExportResult, ExportType, ExportedFile, JobStatus
from .storage import LocalStorage, sanitize_filename


def export_jobs(
    *,
    jobs: list[DocumentJob],
    storage: LocalStorage,
    settings: Settings,
    user_id: int = 0,
    signature_png: bytes | None = None,
    stamp_png: bytes | None = None,
    force_zip: bool = False,
) -> ExportResult:
    export_id = storage.new_export_id()
    export_dir = storage.prepare_export_dir(export_id)
    exported_files: list[ExportedFile] = []
    output_paths: list[Path] = []
    used_output_filenames: set[str] = set()

    for job in jobs:
        output_filename = unique_output_filename(
            sanitize_filename(job.filename),
            used_output_filenames,
        )
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

    if len(output_paths) == 1 and not force_zip:
        return ExportResult(
            export_id=export_id,
            user_id=user_id,
            type=ExportType.PDF,
            path=output_paths[0],
            files=exported_files,
        )

    archive_name = f"signed_documents_{export_id.rsplit('_', 1)[-1][:8]}"
    zip_path = export_dir / f"{archive_name}.zip"
    archive_dir = zip_path.stem
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for output_path in output_paths:
            archive.write(output_path, arcname=f"{archive_dir}/{output_path.name}")

    return ExportResult(
        export_id=export_id,
        user_id=user_id,
        type=ExportType.ZIP,
        path=zip_path,
        files=exported_files,
    )


def unique_output_filename(filename: str, used_filenames: set[str]) -> str:
    """Return a ZIP-safe filename without overwriting another exported PDF."""
    path = Path(filename)
    stem = path.stem
    suffix = path.suffix
    candidate = filename
    number = 2

    while candidate.casefold() in used_filenames:
        candidate = f"{stem}_{number}{suffix}"
        number += 1

    used_filenames.add(candidate.casefold())
    return candidate


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
        signature_boxes = [
            placement.signature.bbox.as_list()
            for placement in job.placements
            if placement.signature and placement.signature.enabled
        ]
        stamp_boxes = [
            placement.stamp.bbox.as_list()
            for placement in job.placements
            if placement.stamp and placement.stamp.enabled
        ]
        prepared_signature = prepare_overlay_image(signature_image_path, signature_png, signature_boxes)
        prepared_stamp = prepare_overlay_image(stamp_image_path, stamp_png, stamp_boxes)
        signature_xref = 0
        stamp_xref = 0
        for placement in job.placements:
            page = document[placement.page_number - 1]

            if placement.signature and placement.signature.enabled:
                signature_xref = insert_image(
                    page=page,
                    image_path=signature_image_path,
                    image_bytes=prepared_signature,
                    bbox=placement.signature.bbox.as_list(),
                    xref=signature_xref,
                )

            if placement.stamp and placement.stamp.enabled:
                stamp_xref = insert_image(
                    page=page,
                    image_path=stamp_image_path,
                    image_bytes=prepared_stamp,
                    bbox=placement.stamp.bbox.as_list(),
                    xref=stamp_xref,
                )

            if placement.name and placement.name.enabled:
                rect = fitz.Rect(placement.name.bbox.as_list())
                insert_name_text(
                    page=page,
                    rect=rect,
                    text=placement.name.text,
                    font_path=name_font_path,
                )

        document.save(output_path, garbage=4, deflate=True, deflate_images=True)


def prepare_overlay_image(image_path: Path, image_bytes: bytes | None, boxes: list[list[float]]) -> bytes | None:
    if not boxes:
        return None

    original = image_bytes if image_bytes is not None else image_path.read_bytes()
    # PDF coordinates are points (72 per inch). 300 DPI keeps printed overlays sharp
    # without embedding a multi-megapixel PNG in a small signature/stamp rectangle.
    max_width = max(1, ceil(max(box[2] - box[0] for box in boxes) * 300 / 72))
    max_height = max(1, ceil(max(box[3] - box[1] for box in boxes) * 300 / 72))
    with Image.open(BytesIO(original)) as image:
        if image.width <= max_width and image.height <= max_height:
            return original
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def insert_image(
    *,
    page: fitz.Page,
    image_path: Path,
    bbox: list[float],
    image_bytes: bytes | None = None,
    xref: int = 0,
) -> int:
    kwargs = {
        "rect": fitz.Rect(bbox),
        "keep_proportion": True,
        "overlay": True,
    }
    if xref:
        kwargs["xref"] = xref
    elif image_bytes is not None:
        kwargs["stream"] = image_bytes
    else:
        kwargs["filename"] = str(image_path)
    return page.insert_image(**kwargs)


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
