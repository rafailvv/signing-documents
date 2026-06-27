from app.config import Settings
from app.storage import get_storage


def test_storage_creates_runtime_directories(tmp_path):
    storage = get_storage(Settings(WORKDIR=tmp_path))

    assert storage.uploads_dir.exists()
    assert storage.previews_dir.exists()
    assert storage.exports_dir.exists()


def test_storage_generates_ids_and_paths(tmp_path):
    storage = get_storage(Settings(WORKDIR=tmp_path))
    job_id = storage.new_job_id()
    export_id = storage.new_export_id()

    assert job_id.startswith("job_")
    assert export_id.startswith("export_")
    assert storage.source_path(job_id, "document.pdf").name == "document.pdf"
