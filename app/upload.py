from pathlib import Path

from fastapi import UploadFile


PDF_MAGIC = b"%PDF-"


async def validate_pdf_upload(file: UploadFile) -> bytes:
    content = await file.read()
    errors = get_pdf_validation_errors(file.filename or "", file.content_type, content)
    if errors:
        raise ValueError("; ".join(errors))
    return content


def get_pdf_validation_errors(
    filename: str, content_type: str | None, content: bytes
) -> list[str]:
    errors: list[str] = []

    if Path(filename).suffix.lower() != ".pdf":
        errors.append("file extension must be .pdf")

    if content_type and content_type not in {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    }:
        errors.append(f"unsupported content type: {content_type}")

    if not content.startswith(PDF_MAGIC):
        errors.append("file content is not a PDF")

    return errors
