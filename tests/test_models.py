import pytest
from pydantic import ValidationError

from app.models import (
    BoundingBox,
    DocumentJob,
    ExportResult,
    ExportType,
    ExportedFile,
    ImageOverlay,
    JobStatus,
    Placement,
)


def test_document_job_accepts_known_status(tmp_path):
    job = DocumentJob(
        job_id="job_1",
        filename="document.pdf",
        source_path=tmp_path / "document.pdf",
        status=JobStatus.UPLOADED,
    )

    assert job.status == JobStatus.UPLOADED
    assert job.options.place_signature is True


def test_document_job_rejects_unknown_status(tmp_path):
    with pytest.raises(ValidationError):
        DocumentJob(
            job_id="job_1",
            filename="document.pdf",
            source_path=tmp_path / "document.pdf",
            status="unknown",
        )


def test_bounding_box_rejects_reversed_coordinates():
    with pytest.raises(ValidationError):
        BoundingBox(x0=100, y0=80, x1=10, y1=20)


def test_placement_requires_at_least_one_overlay():
    with pytest.raises(ValidationError):
        Placement(
            placement_id="placement_1",
            page_number=1,
            confidence=0.5,
        )


def test_export_result_requires_files(tmp_path):
    with pytest.raises(ValidationError):
        ExportResult(export_id="export_1", type=ExportType.PDF, path=tmp_path / "x.pdf")


def test_valid_placement_with_signature():
    placement = Placement(
        placement_id="placement_1",
        page_number=1,
        signature=ImageOverlay(bbox=BoundingBox(x0=10, y0=20, x1=100, y1=80)),
        confidence=0.9,
        needs_manual_review=False,
        source="manual",
    )

    assert placement.signature is not None
    assert placement.signature.bbox.as_list() == [10, 20, 100, 80]


def test_valid_export_result(tmp_path):
    result = ExportResult(
        export_id="export_1",
        type=ExportType.PDF,
        path=tmp_path / "signed.pdf",
        files=[
            ExportedFile(
                job_id="job_1",
                output_filename="signed.pdf",
            )
        ],
    )

    assert result.files[0].job_id == "job_1"
