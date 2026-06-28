from fastapi.testclient import TestClient
import fitz

from app.config import Settings
from app.local_analysis import (
    AnchorHit,
    analyze_pdf,
    build_signature_targets,
    find_anchor_hits,
    normalize_token,
)
from app.pdf_export import find_unicode_font
from app.main import create_app
from app.models import BoundingBox, DetectedLine, JobStatus, PageSize, WordBox
from tests.pdf_factory import make_scanned_line_pdf_bytes, make_signature_pdf_bytes


def write_pdf(tmp_path, content: bytes, name: str = "analysis.pdf"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def make_client(tmp_path):
    app = create_app(Settings(WORKDIR=tmp_path))
    return TestClient(app), app


def upload_pdf(client: TestClient, content: bytes) -> str:
    response = client.post(
        "/upload",
        files={"files": ("analysis.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()["jobs"][0]["job_id"]


def test_analyze_pdf_finds_venediktov_full_name_near_line(tmp_path):
    path = write_pdf(
        tmp_path,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    analyses = analyze_pdf(path)

    assert len(analyses) == 1
    analysis = analyses[0]
    assert analysis.text_quality == "pdf_text_layer"
    assert any(word.text == "Венедиктов" for word in analysis.words)
    assert len(analysis.lines) == 1
    assert len(analysis.candidates) == 1
    candidate = analysis.candidates[0]
    assert candidate.anchor == "Венедиктов Р.В."
    assert candidate.reason == "line_near_venediktov"
    assert candidate.confidence >= 0.7
    assert candidate.line_bbox is not None


def test_analyze_pdf_marks_anchor_without_line_as_low_confidence(tmp_path):
    path = write_pdf(
        tmp_path,
        make_signature_pdf_bytes(text="Венедиктов Р.В. указан в тексте договора"),
    )

    analysis = analyze_pdf(path)[0]

    assert len(analysis.lines) == 0
    assert len(analysis.candidates) == 1
    candidate = analysis.candidates[0]
    assert candidate.anchor == "Венедиктов Р.В."
    assert candidate.line_bbox is None
    assert candidate.confidence < 0.7
    assert "needs_manual_review" in candidate.warnings


def test_analyze_pdf_finds_general_director_near_line(tmp_path):
    path = write_pdf(
        tmp_path,
        make_signature_pdf_bytes(
            text="Генеральный директор",
            line=(230, 720, 430, 720),
        ),
    )

    candidate = analyze_pdf(path)[0].candidates[0]

    assert candidate.anchor == "Генеральный директор"
    assert candidate.reason == "line_near_general_director"
    assert candidate.confidence >= 0.7


def test_analyze_pdf_uses_text_underscore_signature_line(tmp_path):
    path = tmp_path / "underscore_line.pdf"
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    font_path = find_unicode_font()
    kwargs = {"fontfile": str(font_path), "fontname": "testfont"} if font_path else {}
    page.insert_text((202, 570), "___________________________", fontsize=12, **kwargs)
    page.insert_text((367, 570), "/ Венедиктов Рафаил Владимирович", fontsize=12, **kwargs)
    document.save(path)
    document.close()

    analysis = analyze_pdf(path)[0]

    assert any(line.type == "text_underscore" for line in analysis.lines)
    assert len(analysis.candidates) == 1
    candidate = analysis.candidates[0]
    assert candidate.anchor == "Венедиктов"
    assert candidate.confidence >= 0.7
    assert candidate.line_bbox is not None
    assert candidate.line_bbox.x0 < 210
    assert candidate.line_bbox.x1 > 350


def test_analyze_endpoint_saves_results_and_sets_ready_status(tmp_path):
    client, app = make_client(tmp_path)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
        ),
    )

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"][0]["status"] == JobStatus.READY
    assert body["jobs"][0]["page_analyses"][0]["candidates"][0]["confidence"] >= 0.7
    job = app.state.jobs.get(job_id)
    assert job.status == JobStatus.READY
    assert job.analyses
    assert job.placements
    assert job.placements[0].source == "auto"


def test_preview_returns_auto_placements_after_analyze(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Генеральный директор",
            line=(230, 720, 430, 720),
        ),
    )
    client.post("/analyze", json={"job_ids": [job_id]})

    response = client.get(f"/preview/{job_id}")

    assert response.status_code == 200
    placements = response.json()["placements"]
    assert len(placements) == 1
    assert placements[0]["source"] == "auto"
    assert placements[0]["signature"] is not None
    assert placements[0]["stamp"] is not None
    assert placements[0]["name"]["text"] == "Венедиктов Р.В."


def test_analyze_endpoint_marks_ambiguous_multiple_lines_needs_review(tmp_path):
    client, app = make_client(tmp_path)
    job_id = upload_pdf(
        client,
        make_signature_pdf_bytes(
            text="Венедиктов Р.В.",
            line=(210, 720, 430, 720),
            extra_lines=[
                (60, 100, 520, 100),
                (60, 130, 520, 130),
                (60, 160, 520, 160),
                (60, 190, 520, 190),
            ],
        ),
    )

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"][0]["status"] == JobStatus.NEEDS_REVIEW
    assert "ambiguous_multiple_lines" in body["jobs"][0]["warnings"]
    assert app.state.jobs.get(job_id).status == JobStatus.NEEDS_REVIEW


def test_analyze_endpoint_unknown_job_returns_failed_result(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post("/analyze", json={"job_ids": ["job_missing"]})

    assert response.status_code == 200
    assert response.json()["jobs"][0]["status"] == JobStatus.FAILED
    assert response.json()["jobs"][0]["errors"] == ["job not found"]


def test_fuzzy_matching_handles_common_ocr_errors():
    assert normalize_token("Bенедикт0в") == "венедиктов"
    assert normalize_token("P.B.") == "рв"

    words = [
        WordBox(text="Bенедикт0в", bbox=BoundingBox(x0=72, y0=700, x1=150, y1=720)),
        WordBox(text="P.B.", bbox=BoundingBox(x0=155, y0=700, x1=185, y1=720)),
    ]

    hits = find_anchor_hits(words)

    assert len(hits) == 1
    assert hits[0].label == "Венедиктов Р.В."


def test_analyze_pdf_uses_ocr_words_and_raster_lines_for_scanned_pdf(
    tmp_path,
    monkeypatch,
):
    path = write_pdf(tmp_path, make_scanned_line_pdf_bytes(), "scanned.pdf")

    def fake_extract_ocr_words(page, *, languages):
        return (
            [
                WordBox(
                    text="Bенедикт0в",
                    bbox=BoundingBox(x0=72, y0=707, x1=150, y1=723),
                ),
                WordBox(
                    text="P.B.",
                    bbox=BoundingBox(x0=154, y0=707, x1=185, y1=723),
                ),
            ],
            "Bенедикт0в P.B.",
            ["ocr_languages:rus+eng"],
        )

    monkeypatch.setattr("app.local_analysis.extract_ocr_words", fake_extract_ocr_words)

    analysis = analyze_pdf(path)[0]

    assert analysis.text_quality == "ocr"
    assert "raster_lines_used" in analysis.warnings
    assert len(analysis.lines) >= 1
    assert len(analysis.candidates) == 1
    assert analysis.candidates[0].anchor == "Венедиктов Р.В."
    assert analysis.candidates[0].confidence >= 0.7


def test_analyze_endpoint_marks_bad_ocr_as_needs_review(tmp_path, monkeypatch):
    client, app = make_client(tmp_path)
    job_id = upload_pdf(client, make_scanned_line_pdf_bytes())

    def fake_extract_ocr_words(page, *, languages):
        return [], "", ["ocr_failed:test"]

    monkeypatch.setattr("app.local_analysis.extract_ocr_words", fake_extract_ocr_words)

    response = client.post("/analyze", json={"job_ids": [job_id]})

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"][0]["status"] == JobStatus.NEEDS_REVIEW
    assert "signature_candidates_not_found" in body["jobs"][0]["warnings"]
    assert "ocr_failed:test" in body["jobs"][0]["warnings"]
    assert app.state.jobs.get(job_id).status == JobStatus.NEEDS_REVIEW


def test_long_raster_table_border_does_not_create_confident_candidate():
    page_size = PageSize(width=595, height=842)
    anchor = AnchorHit(
        kind="venediktov",
        label="Венедиктов",
        bbox=BoundingBox(x0=330, y0=625, x1=410, y1=638),
        word_indexes=(0,),
    )
    line = DetectedLine(
        bbox=BoundingBox(x0=66, y0=618, x1=519, y1=619),
        width=453,
        type="horizontal_raster",
    )

    candidates = build_signature_targets(
        page_number=1,
        page_size=page_size,
        anchors=[anchor],
        lines=[line],
    )

    assert len(candidates) == 1
    assert candidates[0].confidence < 0.7
    assert "likely_table_line" in candidates[0].warnings


def test_candidates_on_same_line_are_deduplicated_by_stronger_anchor():
    page_size = PageSize(width=595, height=842)
    line = DetectedLine(
        bbox=BoundingBox(x0=210, y0=720, x1=430, y1=721),
        width=220,
        type="horizontal",
    )
    anchors = [
        AnchorHit(
            kind="general_director",
            label="Генеральный директор",
            bbox=BoundingBox(x0=70, y0=710, x1=175, y1=725),
            word_indexes=(0, 1),
        ),
        AnchorHit(
            kind="venediktov",
            label="Венедиктов",
            bbox=BoundingBox(x0=450, y0=710, x1=520, y1=725),
            word_indexes=(2,),
        ),
    ]

    candidates = build_signature_targets(
        page_number=1,
        page_size=page_size,
        anchors=anchors,
        lines=[line],
    )

    assert len(candidates) == 1
    assert candidates[0].anchor == "Венедиктов"
