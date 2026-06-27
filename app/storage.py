from pathlib import Path
from re import sub
from shutil import rmtree
from uuid import uuid4

from .config import Settings


class LocalStorage:
    """Filesystem layout for uploaded, preview, and exported artifacts."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.workdir
        self.uploads_dir = self.root / "uploads"
        self.previews_dir = self.root / "previews"
        self.exports_dir = self.root / "exports"

    def ensure(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def clear_runtime(self) -> None:
        for directory in (self.uploads_dir, self.previews_dir, self.exports_dir):
            if directory.exists():
                rmtree(directory)
        self.ensure()

    def clear_jobs(self, job_ids: list[str]) -> None:
        for job_id in job_ids:
            for directory in (self.job_dir(job_id), self.preview_dir(job_id)):
                if directory.exists():
                    rmtree(directory)
        self.ensure()

    def clear_exports(self, export_ids: list[str]) -> None:
        for export_id in export_ids:
            export_dir = self.export_dir(export_id)
            if export_dir.exists():
                rmtree(export_dir)
        self.ensure()

    def new_job_id(self) -> str:
        return f"job_{uuid4().hex}"

    def new_export_id(self) -> str:
        return f"export_{uuid4().hex}"

    def export_dir(self, export_id: str) -> Path:
        return self.exports_dir / export_id

    def prepare_export_dir(self, export_id: str) -> Path:
        export_dir = self.export_dir(export_id)
        export_dir.mkdir(parents=True, exist_ok=False)
        return export_dir

    def job_dir(self, job_id: str) -> Path:
        return self.uploads_dir / job_id

    def source_path(self, job_id: str, filename: str) -> Path:
        return self.job_dir(job_id) / filename

    def preview_dir(self, job_id: str) -> Path:
        return self.previews_dir / job_id

    def preview_page_path(self, job_id: str, page_number: int) -> Path:
        return self.preview_dir(job_id) / f"page-{page_number}.png"

    def prepare_job_source_path(self, job_id: str, filename: str) -> Path:
        safe_name = sanitize_filename(filename)
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        return job_dir / safe_name


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = sub(r"[\x00-\x1f/\\:]+", "_", name)
    return name or "document.pdf"


def signed_filename(filename: str) -> str:
    path = Path(sanitize_filename(filename))
    stem = path.stem or "document"
    return f"{stem}_signed.pdf"


def get_storage(settings: Settings) -> LocalStorage:
    storage = LocalStorage(settings)
    storage.ensure()
    return storage
