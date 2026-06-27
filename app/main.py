from base64 import urlsafe_b64encode
from json import dumps
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import DUMMY_PASSWORD_HASH, AuthRateLimiter, auth_rate_limit_key, create_access_token, decode_access_token, ensure_assets, hash_password, make_current_user_dependency, user_to_auth_payload, verify_password
from .config import Settings, get_settings
from .db import init_db, session_dependency
from .db_models import User, utc_now
from .models import (
    AnalyzeJobResult,
    AnalyzeRequest,
    AnalyzeResponse,
    AuthRequest,
    AuthResponse,
    AuthUser,
    DocumentJob,
    DocumentJobSummary,
    ExportRequest,
    ExportResponse,
    JobsResponse,
    JobStatus,
    PlacementUpdateRequest,
    PlacementUpdateResponse,
    ProcessJobReport,
    ProcessingOptions,
    ProcessResponse,
    RegisterRequest,
    UploadJobResult,
    UploadResponse,
)
from .auto_placement import create_auto_placements
from .ai_analysis import (
    apply_ai_review_decisions,
    ai_configured,
    run_ai_analysis,
    should_request_ai_review,
)
from .local_analysis import analyze_pdf
from .pdf_export import export_jobs
from .preview import render_preview
from .repository import JobRepository
from .storage import get_storage
from .upload import validate_pdf_upload


MAX_ASSET_BYTES = 5 * 1024 * 1024

LEGAL_DOCUMENTS = {
    "public-offer.pdf": "Публичная оферта",
    "privacy-policy.pdf": "Политика конфиденциальности и обработки персональных данных",
    "personal-data-consent.pdf": "Согласие на обработку персональных данных",
    "ai-analysis-consent.pdf": "Согласие на AI-анализ через OpenAI API",
    "marketing-consent.pdf": "Согласие на информационную и рекламную рассылку",
    "usage-rules-disclaimer.pdf": "Правила использования и дисклеймер",
    "user-guide.pdf": "Инструкция по эксплуатации сервиса",
    "functional-characteristics.pdf": "Описание функциональных характеристик сервиса",
}

OPENAPI_TAGS = [
    {"name": "Auth", "description": "Регистрация, вход и получение текущего пользователя"},
    {"name": "Assets", "description": "Пользовательские PNG подписи и печати"},
    {"name": "Documents", "description": "Пошаговый UI/API flow: upload, analyze, preview, placements, export"},
    {"name": "Integration API", "description": "Одношаговая обработка PDF для внешних систем"},
    {"name": "System", "description": "Healthcheck и HTML-интерфейс"},
]


async def read_png_upload(file: UploadFile) -> bytes:
    filename = file.filename or "asset.png"
    content = await file.read()
    if len(content) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="PNG file is too large")
    if not filename.lower().endswith(".png"):
        raise HTTPException(status_code=422, detail="file extension must be .png")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=422, detail="file content is not a PNG")
    return content


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    storage = get_storage(app_settings)
    session_factory = init_db(app_settings)
    get_db = session_dependency(session_factory)
    get_current_user = make_current_user_dependency(settings=app_settings, get_db=get_db)
    rate_limiter = AuthRateLimiter()
    ip_rate_limiter = AuthRateLimiter(limit=40)

    app = FastAPI(
        title="Сервис подписания PDF-документов",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        description=(
            "API для авторизации, загрузки пользовательской подписи/печати, "
            "пакетной обработки PDF, автоматической постановки подписи/печати "
            "и скачивания готовых PDF/ZIP. Для защищенных endpoints используйте "
            "Authorize в Swagger: Bearer access_token из /auth/login."
        ),
        openapi_tags=OPENAPI_TAGS,
    )
    app.state.jobs = JobRepository()
    app.mount("/previews", StaticFiles(directory=storage.previews_dir), name="previews")
    legal_dir = Path(__file__).resolve().parent.parent / "frontend" / "legal"

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if app_settings.auth_required and request.url.path.startswith("/previews/"):
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                return Response(status_code=status.HTTP_401_UNAUTHORIZED)
            try:
                user_id = decode_access_token(token, settings=app_settings)
            except HTTPException as exc:
                return Response(status_code=exc.status_code)
            parts = request.url.path.strip("/").split("/")
            job_id = parts[1] if len(parts) > 1 else ""
            if not app.state.jobs.get(job_id, user_id):
                return Response(status_code=status.HTTP_404_NOT_FOUND)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/docs"):
            csp = (
                "default-src 'self' blob: data:; "
                "img-src 'self' blob: data:; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            csp = (
                "default-src 'self' blob: data:; "
                "img-src 'self' blob: data:; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        response.headers.setdefault("Content-Security-Policy", csp)
        return response

    @app.get("/", response_class=HTMLResponse, tags=["System"], summary="Веб-интерфейс")
    def index() -> str:
        index_path = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
        return index_path.read_text(encoding="utf-8")

    @app.get("/docs", include_in_schema=False)
    def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger",
            swagger_favicon_url="/favicon.png",
            swagger_ui_parameters={
                "deepLinking": True,
                "showExtensions": True,
                "showCommonExtensions": True,
                "persistAuthorization": True,
            },
        )

    @app.get("/favicon.png", include_in_schema=False)
    def favicon() -> FileResponse:
        favicon_path = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "favicon.png"
        if not favicon_path.exists():
            raise HTTPException(status_code=404, detail="favicon not found")
        return FileResponse(favicon_path, media_type="image/png")

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/apple-touch-icon.png", include_in_schema=False)
    @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    def favicon_aliases() -> FileResponse:
        return favicon()

    @app.get("/legal", tags=["System"], summary="Список правовых документов")
    def legal_documents() -> list[dict[str, str]]:
        return [
            {"title": title, "url": f"/legal/{filename}", "filename": filename}
            for filename, title in LEGAL_DOCUMENTS.items()
        ]

    @app.get("/legal/{filename}", tags=["System"], summary="Правовой документ PDF")
    def legal_document(filename: str) -> FileResponse:
        if filename not in LEGAL_DOCUMENTS:
            raise HTTPException(status_code=404, detail="legal document not found")
        path = legal_dir / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="legal document file not found")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=filename,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @app.get("/health", tags=["System"], summary="Healthcheck")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "ai-pdf-signing",
            "version": app.version,
            "workdir": str(storage.root),
            "ai_configured": app_settings.ai_enabled_by_config,
            "signature_image_path": str(app_settings.signature_image_path),
            "stamp_image_path": str(app_settings.stamp_image_path),
        }

    @app.post(
        "/auth/register",
        response_model=AuthResponse,
        tags=["Auth"],
        summary="Регистрация по почте, логину и паролю",
        description="Создает аккаунт в PostgreSQL и сразу возвращает Bearer access_token.",
    )
    def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
        login = payload.login.strip()
        email = payload.email.strip().lower()
        if not login:
            raise HTTPException(status_code=422, detail="login is required")
        limit_key = auth_rate_limit_key(request, action="register", login=login)
        ip_limit_key = auth_rate_limit_key(request, action="register-ip", login="")
        ip_rate_limiter.check(ip_limit_key)
        rate_limiter.check(limit_key)
        now = utc_now()
        user = User(
            login=login,
            email=email,
            password_hash=hash_password(payload.password),
            accepted_offer_at=now,
            accepted_privacy_at=now,
            accepted_personal_data_at=now,
            accepted_ai_analysis_at=now,
            accepted_usage_rules_at=now,
            accepted_marketing_at=now if payload.accept_marketing else None,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="login or email already exists") from exc
        db.refresh(user)
        rate_limiter.clear(limit_key)
        token = create_access_token(user_id=user.id, settings=app_settings)
        return AuthResponse(access_token=token, user=user_to_auth_payload(user))

    @app.post(
        "/auth/login",
        response_model=AuthResponse,
        tags=["Auth"],
        summary="Вход по логину и паролю",
        description="Возвращает Bearer access_token для Authorize в Swagger и API-запросов.",
    )
    def login(payload: AuthRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
        limit_key = auth_rate_limit_key(request, action="login", login=payload.login)
        ip_limit_key = auth_rate_limit_key(request, action="login-ip", login="")
        ip_rate_limiter.check(ip_limit_key)
        rate_limiter.check(limit_key)
        user = db.query(User).filter(User.login == payload.login.strip()).first()
        if not user or not verify_password(payload.password, user.password_hash):
            if not user:
                verify_password(payload.password, DUMMY_PASSWORD_HASH)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid login or password")
        rate_limiter.clear(limit_key)
        token = create_access_token(user_id=user.id, settings=app_settings)
        return AuthResponse(access_token=token, user=user_to_auth_payload(user))

    @app.get("/auth/me", response_model=AuthUser, tags=["Auth"], summary="Текущий пользователь")
    def auth_me(current_user: User = Depends(get_current_user)) -> AuthUser:
        return user_to_auth_payload(current_user)

    @app.post("/auth/logout", tags=["Auth"], summary="Выход из аккаунта")
    def logout() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/assets/signature", tags=["Assets"], summary="Дефолтная подпись")
    def signature_asset(_current_user: User = Depends(get_current_user)) -> FileResponse:
        if not app_settings.signature_image_path.exists():
            raise HTTPException(status_code=404, detail="signature image not found")
        return FileResponse(app_settings.signature_image_path)

    @app.get("/assets/stamp", tags=["Assets"], summary="Дефолтная печать")
    def stamp_asset(_current_user: User = Depends(get_current_user)) -> FileResponse:
        if not app_settings.stamp_image_path.exists():
            raise HTTPException(status_code=404, detail="stamp image not found")
        return FileResponse(app_settings.stamp_image_path)

    @app.get("/assets/me/signature", tags=["Assets"], summary="Моя подпись PNG")
    def my_signature_asset(
        current_user: User = Depends(get_current_user),
    ):
        if current_user.assets and current_user.assets.signature_png:
            return Response(content=current_user.assets.signature_png, media_type="image/png")
        return signature_asset(current_user)

    @app.get("/assets/me/stamp", tags=["Assets"], summary="Моя печать PNG")
    def my_stamp_asset(
        current_user: User = Depends(get_current_user),
    ):
        if current_user.assets and current_user.assets.stamp_png:
            return Response(content=current_user.assets.stamp_png, media_type="image/png")
        return stamp_asset(current_user)

    @app.post("/assets/me/signature", response_model=AuthUser, tags=["Assets"], summary="Загрузить мою подпись PNG")
    async def upload_signature_asset(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AuthUser:
        content = await read_png_upload(file)
        assets = ensure_assets(db, current_user)
        assets.signature_png = content
        assets.signature_filename = file.filename or "signature.png"
        db.commit()
        db.refresh(current_user)
        return user_to_auth_payload(current_user)

    @app.post("/assets/me/stamp", response_model=AuthUser, tags=["Assets"], summary="Загрузить мою печать PNG")
    async def upload_stamp_asset(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AuthUser:
        content = await read_png_upload(file)
        assets = ensure_assets(db, current_user)
        assets.stamp_png = content
        assets.stamp_filename = file.filename or "stamp.png"
        db.commit()
        db.refresh(current_user)
        return user_to_auth_payload(current_user)

    @app.post("/upload", response_model=UploadResponse, tags=["Documents"], summary="Загрузить PDF")
    async def upload_documents(
        files: list[UploadFile] = File(...),
        current_user: User = Depends(get_current_user),
    ) -> UploadResponse:
        results: list[UploadJobResult] = []

        for upload in files:
            filename = upload.filename or "document.pdf"
            try:
                content = await validate_pdf_upload(upload)
                job_id = storage.new_job_id()
                source_path = storage.prepare_job_source_path(job_id, filename)
                source_path.write_bytes(content)

                job = DocumentJob(
                    job_id=job_id,
                    user_id=current_user.id,
                    filename=source_path.name,
                    source_path=source_path,
                    status=JobStatus.UPLOADED,
                )
                app.state.jobs.add(job)
                results.append(
                    UploadJobResult(
                        job_id=job.job_id,
                        filename=job.filename,
                        status=job.status,
                    )
                )
            except Exception as exc:
                results.append(
                    UploadJobResult(
                        filename=filename,
                        status=JobStatus.FAILED,
                        errors=[str(exc)],
                    )
                )

        return UploadResponse(jobs=results)

    @app.get("/jobs", response_model=JobsResponse, tags=["Documents"], summary="Список моих документов")
    def list_jobs(current_user: User = Depends(get_current_user)) -> JobsResponse:
        return JobsResponse(
            jobs=[
                DocumentJobSummary(
                    job_id=job.job_id,
                    filename=job.filename,
                    status=job.status,
                    confirmed_by_user=job.confirmed_by_user,
                    errors=job.errors,
                    warnings=job.warnings,
                )
                for job in app.state.jobs.list(current_user.id)
            ]
        )

    @app.post("/reset", tags=["Documents"], summary="Очистить мои временные документы")
    def reset_workspace(current_user: User = Depends(get_current_user)) -> dict[str, str]:
        user_jobs = app.state.jobs.list(current_user.id)
        user_exports = app.state.jobs.list_exports(current_user.id)
        storage.clear_jobs([job.job_id for job in user_jobs])
        storage.clear_exports([export.export_id for export in user_exports])
        app.state.jobs.clear(current_user.id)
        return {"status": "ok"}

    def analyze_job(job: DocumentJob, options: ProcessingOptions) -> AnalyzeJobResult:
        job.status = JobStatus.ANALYZING
        job.options = options
        try:
            analyses = analyze_pdf(
                job.source_path,
                ocr_languages=app_settings.ocr_languages,
            )
            job.analyses = analyses
            candidates = [
                candidate
                for analysis in analyses
                for candidate in analysis.candidates
            ]
            high_confidence_candidates = [
                candidate for candidate in candidates if candidate.confidence >= 0.7
            ]
            job.placements = create_auto_placements(
                analyses=analyses,
                options=options,
            )
            local_placements = list(job.placements)

            if not candidates:
                job.status = JobStatus.NEEDS_REVIEW
                job.warnings.append("signature_candidates_not_found")
            elif len(high_confidence_candidates) != 1:
                job.status = JobStatus.NEEDS_REVIEW
                job.warnings.append("ambiguous_signature_candidates")
            elif high_confidence_candidates[0].warnings:
                job.status = JobStatus.NEEDS_REVIEW
                job.warnings.extend(high_confidence_candidates[0].warnings)
            else:
                job.status = JobStatus.READY

            for analysis in analyses:
                job.warnings.extend(
                    warning
                    for warning in analysis.warnings
                    if warning.startswith(("ocr_failed", "ocr_no_words", "low_ocr_confidence"))
                )
            job.warnings = sorted(set(job.warnings))
            if any(
                warning.startswith(("ocr_failed", "ocr_no_words", "low_ocr_confidence"))
                for warning in job.warnings
            ):
                job.status = JobStatus.NEEDS_REVIEW

            if options.use_ai and should_request_ai_review(
                filename=job.filename,
                analyses=analyses,
                local_placements=local_placements,
            ):
                if ai_configured(
                    api_key=app_settings.openai_api_key,
                    model=app_settings.openai_model,
                ):
                    try:
                        ai_decisions = run_ai_analysis(
                            source_path=job.source_path,
                            analyses=analyses,
                            options=options,
                            local_placements=local_placements,
                            api_key=app_settings.openai_api_key or "",
                            base_url=app_settings.openai_base_url,
                            model=app_settings.openai_model or "",
                            timeout_seconds=app_settings.openai_timeout_seconds,
                        )
                        ai_placements, ai_verdict, ai_warnings = apply_ai_review_decisions(
                            decisions=ai_decisions,
                            analyses=analyses,
                            local_placements=local_placements,
                        )
                        job.placements = ai_placements
                        job.warnings.extend(ai_warnings)
                        if ai_verdict in {"manual_review", "reject_auto"}:
                            job.status = JobStatus.NEEDS_REVIEW
                        elif any(placement.needs_manual_review for placement in ai_placements):
                            job.status = JobStatus.NEEDS_REVIEW
                        elif job.status != JobStatus.FAILED:
                            job.status = JobStatus.READY
                    except Exception as exc:
                        job.placements = local_placements
                        job.warnings.append(f"ai_fallback:{exc}")
                else:
                    job.warnings.append("ai_skipped:not_configured")

            job.warnings = sorted(set(job.warnings))
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.errors.append(f"analysis failed: {exc}")

        return AnalyzeJobResult(
            job_id=job.job_id,
            status=job.status,
            page_analyses=job.analyses,
            warnings=job.warnings,
            errors=job.errors,
        )

    async def create_job_from_upload(upload: UploadFile, current_user: User) -> tuple[DocumentJob | None, ProcessJobReport]:
        filename = upload.filename or "document.pdf"
        try:
            content = await validate_pdf_upload(upload)
            job_id = storage.new_job_id()
            source_path = storage.prepare_job_source_path(job_id, filename)
            source_path.write_bytes(content)
            job = DocumentJob(
                job_id=job_id,
                user_id=current_user.id,
                filename=source_path.name,
                source_path=source_path,
                status=JobStatus.UPLOADED,
            )
            app.state.jobs.add(job)
            return job, process_report_for_job(job)
        except Exception as exc:
            return None, ProcessJobReport(
                filename=filename,
                status=JobStatus.FAILED,
                errors=[str(exc)],
            )

    def process_report_for_job(job: DocumentJob) -> ProcessJobReport:
        signed = any(placement.signature and placement.signature.enabled for placement in job.placements)
        stamped = any(placement.stamp and placement.stamp.enabled for placement in job.placements)
        name_added = any(placement.name and placement.name.enabled for placement in job.placements)
        return ProcessJobReport(
            job_id=job.job_id,
            filename=job.filename,
            status=job.status,
            signed=signed,
            stamped=stamped,
            name_added=name_added,
            placements_count=len(job.placements),
            needs_manual_review=job.status == JobStatus.NEEDS_REVIEW or any(
                placement.needs_manual_review for placement in job.placements
            ),
            warnings=job.warnings,
            errors=job.errors,
        )

    async def process_uploads_for_api(
        *,
        files: list[UploadFile],
        current_user: User,
        place_signature: bool,
        place_stamp: bool,
        add_name_if_missing: bool,
        use_ai: bool,
    ) -> ProcessResponse:
        options = ProcessingOptions(
            place_signature=place_signature,
            place_stamp=place_stamp,
            add_name_if_missing=add_name_if_missing,
            use_ai=use_ai,
            require_manual_confirmation=False,
        )
        jobs: list[DocumentJob] = []
        reports: list[ProcessJobReport] = []
        for upload in files:
            job, report = await create_job_from_upload(upload, current_user)
            if job is None:
                reports.append(report)
                continue
            analyze_job(job, options)
            reports.append(process_report_for_job(job))
            if job.status != JobStatus.FAILED:
                jobs.append(job)

        if not jobs:
            return ProcessResponse(jobs=reports, warnings=["no exportable jobs"])

        result = export_jobs(
            jobs=jobs,
            storage=storage,
            settings=app_settings,
            user_id=current_user.id,
            signature_png=current_user.assets.signature_png if current_user.assets else None,
            stamp_png=current_user.assets.stamp_png if current_user.assets else None,
        )
        app.state.jobs.add_export(result)
        return ProcessResponse(
            export_id=result.export_id,
            type=result.type,
            download_url=f"/download/{result.export_id}",
            files=result.files,
            jobs=reports,
            warnings=[warning for report in reports for warning in report.warnings],
        )

    def encoded_process_report(response: ProcessResponse) -> str:
        payload = response.model_dump(mode="json")
        raw = dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return urlsafe_b64encode(raw).decode("ascii")

    @app.post("/analyze", response_model=AnalyzeResponse, tags=["Documents"], summary="Проанализировать PDF")
    def analyze_documents(
        request: AnalyzeRequest,
        current_user: User = Depends(get_current_user),
    ) -> AnalyzeResponse:
        results: list[AnalyzeJobResult] = []

        for job_id in request.job_ids:
            job = app.state.jobs.get(job_id, current_user.id)
            if job is None:
                results.append(
                    AnalyzeJobResult(
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        errors=["job not found"],
                    )
                )
                continue

            results.append(analyze_job(job, request.options))

        return AnalyzeResponse(jobs=results)

    @app.post(
        "/api/process",
        response_model=ProcessResponse,
        tags=["Integration API"],
        summary="Загрузить PDF, обработать и получить ссылку на результат",
        description=(
            "Одношаговый API для внешних систем. Принимает один или несколько PDF, "
            "выполняет анализ, применяет найденные размещения и экспортирует результат. "
            "Даже если уверенность низкая, endpoint возвращает готовый PDF/ZIP по download_url "
            "и отчет jobs с warnings/needs_manual_review/флагами signed/stamped/name_added."
        ),
    )
    async def process_documents_api(
        files: list[UploadFile] = File(..., description="PDF-файлы для обработки"),
        place_signature: bool = Form(True, description="Поставить подпись, если найдено место"),
        place_stamp: bool = Form(True, description="Поставить печать, если найдено место"),
        add_name_if_missing: bool = Form(True, description="Добавить ФИО, если нужно и оно отсутствует"),
        use_ai: bool = Form(True, description="Использовать AI-проверку спорных случаев"),
        current_user: User = Depends(get_current_user),
    ) -> ProcessResponse:
        response = await process_uploads_for_api(
            files=files,
            current_user=current_user,
            place_signature=place_signature,
            place_stamp=place_stamp,
            add_name_if_missing=add_name_if_missing,
            use_ai=use_ai,
        )
        if response.export_id is None:
            raise HTTPException(status_code=422, detail=response.model_dump(mode="json"))
        return response

    @app.post(
        "/api/process-file",
        tags=["Integration API"],
        summary="Загрузить PDF и сразу скачать готовый PDF/ZIP",
        description=(
            "Одношаговый download endpoint. Возвращает application/pdf для одного файла "
            "или application/zip для нескольких. Краткий отчет доступен в заголовке "
            "X-Signing-Report как base64url JSON."
        ),
        responses={
            200: {
                "content": {
                    "application/pdf": {},
                    "application/zip": {},
                },
                "description": "Готовый PDF или ZIP. Заголовок X-Signing-Report содержит base64url JSON-отчет.",
            }
        },
    )
    async def process_documents_file_api(
        files: list[UploadFile] = File(..., description="PDF-файлы для обработки"),
        place_signature: bool = Form(True),
        place_stamp: bool = Form(True),
        add_name_if_missing: bool = Form(True),
        use_ai: bool = Form(True),
        current_user: User = Depends(get_current_user),
    ) -> FileResponse:
        response = await process_uploads_for_api(
            files=files,
            current_user=current_user,
            place_signature=place_signature,
            place_stamp=place_stamp,
            add_name_if_missing=add_name_if_missing,
            use_ai=use_ai,
        )
        if response.export_id is None:
            raise HTTPException(status_code=422, detail=response.model_dump(mode="json"))
        result = app.state.jobs.get_export(response.export_id, current_user.id)
        if result is None or not result.path.exists():
            raise HTTPException(status_code=404, detail="export file not found")
        media_type = "application/zip" if result.type == "zip" else "application/pdf"
        return FileResponse(
            result.path,
            media_type=media_type,
            filename=result.path.name,
            headers={"X-Signing-Report": encoded_process_report(response)},
        )

    @app.get("/preview/{job_id}", tags=["Documents"], summary="Предпросмотр PDF")
    def preview_document(
        job_id: str,
        current_user: User = Depends(get_current_user),
    ):
        job = app.state.jobs.get(job_id, current_user.id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        try:
            return render_preview(
                job_id=job.job_id,
                filename=job.filename,
                source_path=job.source_path,
                storage=storage,
                placements=job.placements,
            )
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.errors.append(f"preview failed: {exc}")
            raise HTTPException(status_code=422, detail="preview failed") from exc

    @app.post("/placement/{job_id}", response_model=PlacementUpdateResponse, tags=["Documents"], summary="Сохранить placements")
    def save_placements(
        job_id: str,
        request: PlacementUpdateRequest,
        current_user: User = Depends(get_current_user),
    ) -> PlacementUpdateResponse:
        job = app.state.jobs.get(job_id, current_user.id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        job.placements = request.placements
        job.confirmed_by_user = request.confirmed_by_user
        if job.status != JobStatus.FAILED:
            job.status = JobStatus.READY

        return PlacementUpdateResponse(
            job_id=job.job_id,
            status=job.status,
            confirmed_by_user=job.confirmed_by_user,
            placements=job.placements,
        )

    @app.post("/export", response_model=ExportResponse, tags=["Documents"], summary="Экспортировать PDF/ZIP")
    def export_documents(
        request: ExportRequest,
        current_user: User = Depends(get_current_user),
    ) -> ExportResponse:
        jobs: list[DocumentJob] = []
        warnings: list[str] = []

        for job_id in request.job_ids:
            job = app.state.jobs.get(job_id, current_user.id)
            if job is None:
                warnings.append(f"{job_id}: job not found")
                continue
            if job.status == JobStatus.FAILED:
                warnings.append(f"{job_id}: skipped failed job")
                continue
            if job.options.require_manual_confirmation and not job.confirmed_by_user:
                warnings.append(f"{job_id}: manual confirmation required")
                continue
            jobs.append(job)

        if not jobs:
            detail = "no exportable jobs"
            if warnings:
                detail = "; ".join(warnings)
            raise HTTPException(status_code=422, detail=detail)

        try:
            result = export_jobs(
                jobs=jobs,
                storage=storage,
                settings=app_settings,
                user_id=current_user.id,
                signature_png=current_user.assets.signature_png if current_user.assets else None,
                stamp_png=current_user.assets.stamp_png if current_user.assets else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"export failed: {exc}") from exc

        app.state.jobs.add_export(result)
        return ExportResponse(
            export_id=result.export_id,
            type=result.type,
            download_url=f"/download/{result.export_id}",
            files=result.files,
            warnings=warnings,
        )

    @app.get("/download/{export_id}", tags=["Documents"], summary="Скачать экспортированный PDF/ZIP")
    def download_export(
        export_id: str,
        current_user: User = Depends(get_current_user),
    ) -> FileResponse:
        result = app.state.jobs.get_export(export_id, current_user.id)
        if result is None:
            raise HTTPException(status_code=404, detail="export not found")
        if not result.path.exists():
            raise HTTPException(status_code=404, detail="export file not found")

        media_type = "application/pdf"
        if result.type == "zip":
            media_type = "application/zip"

        return FileResponse(
            result.path,
            media_type=media_type,
            filename=result.path.name,
        )

    return app
