import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai_analysis import (
    MAX_AI_PAGES,
    apply_ai_review_decisions,
    build_ai_context,
    build_crop_inputs,
    parse_ai_response,
    should_include_visual_context,
    should_request_ai_review,
)
from app.config import Settings
from app.local_analysis import analyze_pdf
from app.main import create_app
from app.models import (
    AIPlacementDecision,
    AIPlacementDecisions,
    BoundingBox,
    JobStatus,
    SignatureTarget,
)
from tests.pdf_factory import make_signature_pdf_bytes


def make_client(tmp_path, *, with_ai: bool = False):
    app = create_app(
        Settings(
            WORKDIR=tmp_path,
            OPENAI_API_KEY="test-key" if with_ai else None,
            OPENAI_MODEL="test-model" if with_ai else None,
        )
    )
    return TestClient(app), app


def upload_pdf(client: TestClient, content: bytes) -> str:
    response = client.post(
        "/upload",
        files={"files": ("ai.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()["jobs"][0]["job_id"]


def test_parse_ai_response_validates_structured_json():
    response = parse_ai_response(
        json.dumps(
            {
                "decisions": [
                    {
                        "candidate_id": "target_1",
                        "page_number": 1,
                        "verdict": "adjust_local",
                        "should_sign": True,
                        "should_stamp": True,
                        "should_add_name": False,
                        "name_text": None,
                        "signature_bbox": {"x0": 120, "y0": 610, "x1": 330, "y1": 690},
                        "stamp_bbox": {"x0": 310, "y0": 585, "x1": 430, "y1": 705},
                        "name_bbox": None,
                        "confidence": 0.91,
                        "needs_manual_review": False,
                        "reason": "real signature line near signer",
                    }
                ]
            }
        )
    )

    assert len(response.decisions) == 1
    assert response.decisions[0].candidate_id == "target_1"
    assert response.decisions[0].verdict == "adjust_local"
    assert response.decisions[0].signature_bbox.x0 == 120


def test_parse_ai_response_rejects_invalid_json():
    with pytest.raises((json.JSONDecodeError, ValidationError)):
        parse_ai_response('{"decisions":[{"candidate_id":"missing_fields"}]}')


def test_ai_context_and_crops_include_only_candidate_context(tmp_path):
    pdf_path = tmp_path / "ai_context.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")

    context = build_ai_context(analyses=analyses, options=app_options())
    crops = build_crop_inputs(source_path=pdf_path, analyses=analyses)

    assert context["pages"][0]["candidates"][0]["anchor"] == "Венедиктов Р.В."
    assert context["pages"][0]["lines"]
    assert context["pages"][0]["words"]
    assert crops
    assert crops[0]["type"] == "input_image"
    assert crops[0]["image_url"].startswith("data:image/png;base64,")


def test_ai_context_limits_large_documents_to_relevant_pages(tmp_path):
    pdf_path = tmp_path / "large_ai_context.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    base_analysis = analyze_pdf(pdf_path, ocr_languages="eng")[0]
    analyses = [
        base_analysis.model_copy(
            update={
                "page_number": page_number,
                "candidates": [] if page_number != 10 else [
                    candidate.model_copy(update={"page_number": page_number})
                    for candidate in base_analysis.candidates
                ],
                "warnings": ["ocr_no_words"] if page_number == 12 else [],
            },
            deep=True,
        )
        for page_number in range(1, 21)
    ]

    context = build_ai_context(analyses=analyses, options=app_options())
    included_pages = context["document_summary"]["included_pages"]

    assert len(context["pages"]) <= MAX_AI_PAGES
    assert 10 in included_pages
    assert 12 in included_pages
    assert context["document_summary"]["total_pages"] == 20
    assert context["document_summary"]["omitted_pages_count"] >= 14
    assert len(context["pages"][0]["ocr_text"]) <= 900


def test_analyze_applies_ai_adjust_local_when_ai_succeeds(tmp_path, monkeypatch):
    client, app = make_client(tmp_path, with_ai=True)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    def fake_run_ai_analysis(**kwargs):
        return AIPlacementDecisions(
            decisions=[
                AIPlacementDecision(
                    candidate_id="target_ai",
                    page_number=1,
                    verdict="adjust_local",
                    should_sign=True,
                    should_stamp=True,
                    should_add_name=True,
                    name_text="Венедиктов Р.В.",
                    signature_bbox=BoundingBox(x0=120, y0=685, x1=320, y1=750),
                    stamp_bbox=BoundingBox(x0=80, y0=665, x1=180, y1=765),
                    name_bbox=BoundingBox(x0=390, y0=725, x1=540, y1=750),
                    confidence=0.93,
                    needs_manual_review=False,
                    reason="AI selected real signing zone",
                )
            ]
        )

    monkeypatch.setattr("app.main.run_ai_analysis", fake_run_ai_analysis)
    monkeypatch.setattr("app.main.should_request_ai_review", lambda **kwargs: True)

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    assert response.json()["jobs"][0]["status"] == JobStatus.READY
    placement = app.state.jobs.get(job_id).placements[0]
    assert placement.source == "ai"
    assert placement.signature.bbox.x0 == 120
    assert placement.name.text == "Венедиктов Р.В."


def test_ai_accept_local_keeps_local_placement(tmp_path):
    pdf_path = tmp_path / "accept.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")
    from app.auto_placement import create_auto_placements

    local_placements = create_auto_placements(analyses, app_options())

    placements, verdict, warnings = apply_ai_review_decisions(
        decisions=AIPlacementDecisions(
            decisions=[
                AIPlacementDecision(
                    candidate_id="target_1",
                    page_number=1,
                    verdict="accept_local",
                    confidence=0.95,
                    needs_manual_review=False,
                    reason="local placement is correct",
                )
            ]
        ),
        analyses=analyses,
        local_placements=local_placements,
    )

    assert verdict == "accept_local"
    assert placements == local_placements
    assert "ai_accept_local" in warnings


def test_ai_reject_auto_clears_placements_for_review(tmp_path):
    pdf_path = tmp_path / "reject.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")

    placements, verdict, warnings = apply_ai_review_decisions(
        decisions=AIPlacementDecisions(
            decisions=[
                AIPlacementDecision(
                    candidate_id="target_1",
                    page_number=1,
                    verdict="reject_auto",
                    confidence=0.9,
                    needs_manual_review=True,
                    reason="name appears in non-signing context",
                )
            ]
        ),
        analyses=analyses,
        local_placements=[],
    )

    assert verdict == "reject_auto"
    assert placements == []
    assert "ai_rejected_auto" in warnings


def test_ai_manual_review_keeps_local_placement_as_review_suggestion(tmp_path):
    pdf_path = tmp_path / "manual_review.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")
    from app.auto_placement import create_auto_placements

    local_placements = create_auto_placements(analyses, app_options())

    placements, verdict, warnings = apply_ai_review_decisions(
        decisions=AIPlacementDecisions(
            decisions=[
                AIPlacementDecision(
                    candidate_id="target_1",
                    page_number=1,
                    verdict="manual_review",
                    confidence=0.82,
                    needs_manual_review=True,
                    reason="ambiguous placement, keep local suggestion for preview",
                )
            ]
        ),
        analyses=analyses,
        local_placements=local_placements,
    )

    assert verdict == "manual_review"
    assert len(placements) == len(local_placements)
    assert placements[0].needs_manual_review
    assert "ai_needs_manual_review" in warnings


def test_ai_adjust_local_rejects_invalid_stamp_bbox(tmp_path):
    pdf_path = tmp_path / "invalid.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")

    placements, verdict, warnings = apply_ai_review_decisions(
        decisions=AIPlacementDecisions(
            decisions=[
                AIPlacementDecision(
                    candidate_id="target_1",
                    page_number=1,
                    verdict="adjust_local",
                    should_sign=True,
                    should_stamp=True,
                    signature_bbox=BoundingBox(x0=120, y0=685, x1=320, y1=750),
                    stamp_bbox=BoundingBox(x0=260, y0=665, x1=390, y1=795),
                    confidence=0.9,
                    needs_manual_review=False,
                    reason="invalid stamp too wide and too far right",
                )
            ]
        ),
        analyses=analyses,
        local_placements=[],
    )

    assert verdict == "manual_review"
    assert placements == []
    assert any("ai_invalid_decision" in warning for warning in warnings)


def test_analyze_falls_back_to_local_placements_when_ai_fails(tmp_path, monkeypatch):
    client, app = make_client(tmp_path, with_ai=True)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    def fake_run_ai_analysis(**kwargs):
        raise RuntimeError("api timeout")

    monkeypatch.setattr("app.main.run_ai_analysis", fake_run_ai_analysis)
    monkeypatch.setattr("app.main.should_request_ai_review", lambda **kwargs: True)

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    job = app.state.jobs.get(job_id)
    assert job.placements[0].source == "auto"
    assert any(warning.startswith("ai_fallback:") for warning in job.warnings)


def test_analyze_falls_back_to_local_placements_when_ai_times_out(
    tmp_path,
    monkeypatch,
):
    client, app = make_client(tmp_path, with_ai=True)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    def fake_run_ai_analysis(**kwargs):
        raise TimeoutError("responses timeout")

    monkeypatch.setattr("app.main.run_ai_analysis", fake_run_ai_analysis)
    monkeypatch.setattr("app.main.should_request_ai_review", lambda **kwargs: True)

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    job = app.state.jobs.get(job_id)
    assert job.placements[0].source == "auto"
    assert any("responses timeout" in warning for warning in job.warnings)


def test_analyze_does_not_request_ai_for_obvious_local_document(tmp_path):
    client, app = make_client(tmp_path, with_ai=False)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    assert app.state.jobs.get(job_id).placements[0].source == "auto"
    assert "ai_skipped:not_configured" not in response.json()["jobs"][0]["warnings"]


def test_should_request_ai_review_for_structured_documents(tmp_path):
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")
    from app.auto_placement import create_auto_placements

    local_placements = create_auto_placements(analyses, app_options())

    assert should_request_ai_review(
        filename="Счет №27.pdf",
        analyses=analyses,
        local_placements=local_placements,
    )


def test_should_not_request_ai_for_confident_local_with_weak_extra_fio_hits(tmp_path):
    pdf_path = tmp_path / "education_program.pdf"
    pdf_path.write_bytes(
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        )
    )
    analyses = analyze_pdf(pdf_path, ocr_languages="eng")
    strong_candidate = analyses[0].candidates[0]
    weak_fio_candidate = SignatureTarget(
        candidate_id="weak_fio",
        page_number=2,
        line_bbox=None,
        context_bbox=strong_candidate.context_bbox,
        anchor="ФИО",
        reason="anchor_without_line:fio_word",
        confidence=0.25,
        warnings=["line_not_found", "needs_manual_review"],
    )
    analyses.append(
        analyses[0].model_copy(
            update={
                "page_number": 2,
                "candidates": [weak_fio_candidate],
                "warnings": [],
            },
            deep=True,
        )
    )
    from app.auto_placement import create_auto_placements

    local_placements = create_auto_placements(analyses, app_options())

    assert not should_request_ai_review(
        filename="Education_Programs_INNOPROG.pdf",
        analyses=analyses,
        local_placements=local_placements,
    )
    assert not should_include_visual_context(
        filename="Education_Programs_INNOPROG.pdf",
        analyses=analyses,
        local_placements=local_placements,
    )


def app_options():
    from app.models import ProcessingOptions

    return ProcessingOptions()
