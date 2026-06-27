from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class JobStatus(StrEnum):
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    EXPORTED = "exported"
    FAILED = "failed"


class ExportType(StrEnum):
    PDF = "pdf"
    ZIP = "zip"


class PageSize(BaseModel):
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class BoundingBox(BaseModel):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox coordinates must satisfy x1 > x0 and y1 > y0")
        return self

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class ProcessingOptions(BaseModel):
    place_signature: bool = True
    place_stamp: bool = True
    add_name_if_missing: bool = True
    use_ai: bool = True
    require_manual_confirmation: bool = True


class DocumentJob(BaseModel):
    job_id: str = Field(min_length=1)
    user_id: int = 0
    filename: str = Field(min_length=1)
    source_path: Path
    status: JobStatus = JobStatus.UPLOADED
    options: ProcessingOptions = Field(default_factory=ProcessingOptions)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confirmed_by_user: bool = False
    placements: list["Placement"] = Field(default_factory=list)
    analyses: list["PageAnalysis"] = Field(default_factory=list)


class DocumentJobSummary(BaseModel):
    job_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    status: JobStatus
    confirmed_by_user: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class JobsResponse(BaseModel):
    jobs: list[DocumentJobSummary] = Field(default_factory=list)


class UploadJobResult(BaseModel):
    filename: str = Field(min_length=1)
    status: JobStatus
    job_id: str | None = None
    errors: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    jobs: list[UploadJobResult] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1)
    options: ProcessingOptions = Field(default_factory=ProcessingOptions)


class AnalyzeJobResult(BaseModel):
    job_id: str = Field(min_length=1)
    status: JobStatus
    page_analyses: list["PageAnalysis"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    jobs: list[AnalyzeJobResult] = Field(default_factory=list)


class CoordinateScale(BaseModel):
    x: float = Field(gt=0)
    y: float = Field(gt=0)


class PreviewPage(BaseModel):
    page_number: int = Field(ge=1)
    page_size: PageSize
    image_url: str = Field(min_length=1)
    preview_width: int = Field(gt=0)
    preview_height: int = Field(gt=0)
    scale: CoordinateScale


class PreviewResponse(BaseModel):
    job_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    page_count: int = Field(ge=0)
    pages: list[PreviewPage] = Field(default_factory=list)
    placements: list["Placement"] = Field(default_factory=list)


class PlacementUpdateRequest(BaseModel):
    placements: list["Placement"] = Field(default_factory=list)
    confirmed_by_user: bool = True


class PlacementUpdateResponse(BaseModel):
    job_id: str = Field(min_length=1)
    status: JobStatus
    confirmed_by_user: bool
    placements: list["Placement"] = Field(default_factory=list)


class WordBox(BaseModel):
    text: str = Field(min_length=1)
    bbox: BoundingBox


class DetectedLine(BaseModel):
    bbox: BoundingBox
    width: float = Field(gt=0)
    type: str = "horizontal"


class SignatureTarget(BaseModel):
    candidate_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    line_bbox: BoundingBox | None = None
    context_bbox: BoundingBox
    anchor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class PageAnalysis(BaseModel):
    page_number: int = Field(ge=1)
    page_size: PageSize
    text_quality: str = "unknown"
    ocr_text: str = ""
    words: list[WordBox] = Field(default_factory=list)
    lines: list[DetectedLine] = Field(default_factory=list)
    candidates: list[SignatureTarget] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AIPlacementDecision(BaseModel):
    candidate_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    verdict: str = Field(
        default="adjust_local",
        pattern="^(accept_local|adjust_local|reject_auto|manual_review)$",
    )
    should_sign: bool = False
    should_stamp: bool = False
    should_add_name: bool = False
    name_text: str | None = None
    signature_bbox: BoundingBox | None = None
    stamp_bbox: BoundingBox | None = None
    name_bbox: BoundingBox | None = None
    confidence: float = Field(ge=0, le=1)
    needs_manual_review: bool = True
    reason: str = Field(min_length=1)


class AIPlacementDecisions(BaseModel):
    decisions: list[AIPlacementDecision] = Field(default_factory=list)


class ImageOverlay(BaseModel):
    enabled: bool = True
    bbox: BoundingBox
    rotation: float = 0


class NameOverlay(BaseModel):
    enabled: bool = True
    text: str = "Венедиктов Р.В."
    bbox: BoundingBox


class Placement(BaseModel):
    placement_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    signature: ImageOverlay | None = None
    stamp: ImageOverlay | None = None
    name: NameOverlay | None = None
    confidence: float = Field(ge=0, le=1)
    needs_manual_review: bool = True
    source: str = "manual"

    @model_validator(mode="after")
    def validate_has_action(self) -> "Placement":
        if not any((self.signature, self.stamp, self.name)):
            raise ValueError("placement must include signature, stamp, or name")
        return self


class ExportedFile(BaseModel):
    job_id: str = Field(min_length=1)
    output_filename: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class ExportResult(BaseModel):
    export_id: str = Field(min_length=1)
    user_id: int = 0
    type: ExportType
    path: Path
    files: list[ExportedFile] = Field(min_length=1)


class ExportRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1)


class ExportResponse(BaseModel):
    export_id: str = Field(min_length=1)
    type: ExportType
    download_url: str = Field(min_length=1)
    files: list[ExportedFile] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class ProcessJobReport(BaseModel):
    job_id: str | None = None
    filename: str = Field(min_length=1)
    status: JobStatus
    signed: bool = False
    stamped: bool = False
    name_added: bool = False
    placements_count: int = 0
    needs_manual_review: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProcessResponse(BaseModel):
    export_id: str | None = None
    type: ExportType | None = None
    download_url: str | None = None
    files: list[ExportedFile] = Field(default_factory=list)
    jobs: list[ProcessJobReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AuthRequest(BaseModel):
    login: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=200)


class RegisterRequest(AuthRequest):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password_repeat: str = Field(min_length=6, max_length=200)
    accept_offer: bool = False
    accept_privacy: bool = False
    accept_personal_data: bool = False
    accept_ai_analysis: bool = False
    accept_usage_rules: bool = False
    accept_marketing: bool = False

    @model_validator(mode="after")
    def validate_password_repeat(self) -> "RegisterRequest":
        if self.password != self.password_repeat:
            raise ValueError("passwords do not match")
        if not all(
            [
                self.accept_offer,
                self.accept_privacy,
                self.accept_personal_data,
                self.accept_ai_analysis,
                self.accept_usage_rules,
            ]
        ):
            raise ValueError("required legal consents are missing")
        return self


class AuthUser(BaseModel):
    id: int
    login: str
    email: str | None = None
    has_signature: bool = False
    has_stamp: bool = False


class AuthResponse(BaseModel):
    access_token: str = Field(min_length=1)
    token_type: str = "bearer"
    user: AuthUser
