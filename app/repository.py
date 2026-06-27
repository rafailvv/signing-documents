from __future__ import annotations

from .models import DocumentJob, ExportResult


class JobRepository:
    """Process-local job registry for the current MVP backend."""

    def __init__(self) -> None:
        self._jobs: dict[str, DocumentJob] = {}
        self._exports: dict[str, ExportResult] = {}

    def add(self, job: DocumentJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str, user_id: int | None = None) -> DocumentJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if user_id is not None and job.user_id != user_id:
            return None
        return job

    def list(self, user_id: int | None = None) -> list[DocumentJob]:
        jobs = list(self._jobs.values())
        if user_id is None:
            return jobs
        return [job for job in jobs if job.user_id == user_id]

    def clear(self, user_id: int | None = None) -> None:
        if user_id is None:
            self._jobs.clear()
            self._exports.clear()
            return
        self._jobs = {job_id: job for job_id, job in self._jobs.items() if job.user_id != user_id}
        self._exports = {
            export_id: export
            for export_id, export in self._exports.items()
            if export.user_id != user_id
        }

    def list_exports(self, user_id: int | None = None) -> list[ExportResult]:
        exports = list(self._exports.values())
        if user_id is None:
            return exports
        return [export for export in exports if export.user_id == user_id]

    def add_export(self, export: ExportResult) -> None:
        self._exports[export.export_id] = export

    def get_export(self, export_id: str, user_id: int | None = None) -> ExportResult | None:
        export = self._exports.get(export_id)
        if export is None:
            return None
        if user_id is not None and export.user_id != user_id:
            return None
        return export
