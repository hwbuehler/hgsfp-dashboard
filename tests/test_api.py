"""Route-level behaviour for the analysis-only dashboard.

Covers the auth boundary, the import workflow (upload JSON → assign lecturer
names → run analysis → export), data management, and report rendering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.config import PROJECT_ROOT
from app.db import get_session
from app.main import app
from app.models import (
    Base,
    Response as ResponseRow,
    SlotChoice,
    Survey,
    SurveySlot,
    User,
)
from app.security import hash_password

SAMPLE_FILE = PROJECT_ROOT / "reference" / "sample-survey-results.json"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        session.add(
            User(
                username="tester",
                password_hash=hash_password("correct-horse-battery"),
                display_name="Tester",
            )
        )
        session.commit()
    return factory


@pytest.fixture
def client(session_factory):
    def override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client):
    client.post(
        "/login",
        data={"username": "tester", "password": "correct-horse-battery", "next": "/dashboard"},
    )
    return client


@pytest.fixture
def imported_survey(auth_client):
    """A survey created by uploading the reference sample file via POST /imports."""
    raw = SAMPLE_FILE.read_bytes()
    response = auth_client.post(
        "/imports",
        data={"title": "Test Import"},
        files={"file": ("sample.json", raw, "application/json")},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    survey_id = int(location.split("/")[2])
    return survey_id


# --- Auth boundary ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/dashboard", "/surveys/1/report", "/surveys/1/data", "/surveys/1/lectures"],
)
def test_dashboard_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"username": "tester", "password": "nope"})
    assert response.status_code == 401


def test_login_next_cannot_leave_the_site(client):
    response = client.post(
        "/login",
        data={
            "username": "tester",
            "password": "correct-horse-battery",
            "next": "//evil.example.com/",
        },
    )
    assert response.headers["location"] == "/dashboard"


def test_login_next_rejects_backslash_bypass(client):
    """Browsers normalise \\ to /, so /\\evil.com becomes //evil.com."""
    response = client.post(
        "/login",
        data={
            "username": "tester",
            "password": "correct-horse-battery",
            "next": "/\\evil.example.com/",
        },
    )
    assert response.headers["location"] == "/dashboard"


def test_logout_clears_the_session(auth_client):
    assert auth_client.get("/dashboard").status_code == 200
    auth_client.post("/logout")
    assert auth_client.get("/dashboard").status_code == 303


# --- Import (POST /imports) ---------------------------------------------------


def test_import_creates_a_dataset(auth_client, session_factory):
    raw = SAMPLE_FILE.read_bytes()
    response = auth_client.post(
        "/imports",
        data={"title": "57th Graduate Days"},
        files={"file": ("results.json", raw, "application/json")},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "created=111" in location

    survey_id = int(location.split("/")[2])
    with session_factory() as session:
        survey = session.get(Survey, survey_id)
        assert survey.title == "57th Graduate Days"
        assert len(survey.slots) == 3  # ml, al, il from the reference data
        assert len(session.scalars(select(ResponseRow)).all()) == 111


def test_import_uses_filename_when_title_is_empty(auth_client, session_factory):
    raw = SAMPLE_FILE.read_bytes()
    response = auth_client.post(
        "/imports",
        data={"title": ""},
        files={"file": ("my-results.json", raw, "application/json")},
    )
    assert response.status_code == 303
    survey_id = int(response.headers["location"].split("/")[2])
    with session_factory() as session:
        assert session.get(Survey, survey_id).title == "my-results"


def test_import_discovers_skill_course_slots(auth_client, session_factory):
    """The importer discovers *_title fields, including sc_ for skill courses."""
    payload = {
        "ResultCount": 2,
        "Data": [
            {"ml_title": "Lecturer A", "ml_interesting": 5, "sc_title": "Course X",
             "sc_interesting": 4, "al_title": "DnA", "il_title": "DnA"},
            {"ml_title": "DnA", "sc_title": "Course X", "sc_interesting": 3,
             "al_title": "DnA", "il_title": "DnA"},
        ],
    }
    response = auth_client.post(
        "/imports",
        data={"title": "With Skills"},
        files={"file": ("r.json", json.dumps(payload).encode(), "application/json")},
    )
    assert response.status_code == 303
    survey_id = int(response.headers["location"].split("/")[2])
    with session_factory() as session:
        survey = session.get(Survey, survey_id)
        prefixes = {s.key_prefix for s in survey.slots}
        assert "sc" in prefixes
        sc_slot = next(s for s in survey.slots if s.key_prefix == "sc")
        assert sc_slot.page_title == "Skill Courses"


def test_import_renames_sk_comment_to_sc_comment(auth_client, session_factory):
    """Survey forms used 'sk_comment' for skill courses but 'sc_' for ratings.

    The import normalises the comment key so the analysis finds the comments.
    """
    payload = {
        "ResultCount": 1,
        "Data": [
            {"sc_title": "Course X", "sc_interesting": 4,
             "sugg_lectures": {"sk_comment": "Great course!"},
             "al_title": "DnA", "il_title": "DnA", "ml_title": "DnA"},
        ],
    }
    response = auth_client.post(
        "/imports",
        data={"title": "Comment Key Fix"},
        files={"file": ("r.json", json.dumps(payload).encode(), "application/json")},
    )
    assert response.status_code == 303
    survey_id = int(response.headers["location"].split("/")[2])
    with session_factory() as session:
        row = session.scalars(select(ResponseRow)).one()
        comments = row.payload_json["sugg_lectures"]
        assert "sc_comment" in comments
        assert "sk_comment" not in comments
        assert comments["sc_comment"] == "Great course!"


def test_malformed_import_is_reported_not_raised(auth_client):
    response = auth_client.post(
        "/imports",
        data={"title": "Broken"},
        files={"file": ("bad.json", b"not json", "application/json")},
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_oversized_import_is_refused(auth_client):
    from app.config import settings

    blob = b"x" * (settings.max_upload_bytes + 1024)
    response = auth_client.post(
        "/imports",
        data={"title": "Huge"},
        files={"file": ("huge.json", blob, "application/json")},
    )
    assert response.status_code == 413


def test_import_discards_identifying_metadata(auth_client, session_factory):
    """HappendAt and InstanceId must not be stored."""
    payload = {
        "ResultCount": 1,
        "Data": [
            {"ml_title": "DnA", "al_title": "DnA", "il_title": "DnA",
             "HappendAt": "2026-01-01T12:00:00Z", "InstanceId": "abc123"},
        ],
    }
    auth_client.post(
        "/imports",
        data={"title": "Privacy"},
        files={"file": ("r.json", json.dumps(payload).encode(), "application/json")},
    )
    with session_factory() as session:
        row = session.scalars(select(ResponseRow)).one()
        assert "HappendAt" not in row.payload_json
        assert "InstanceId" not in row.payload_json


# --- Lecturer details ---------------------------------------------------------


def test_lecture_details_page_lists_all_choices(auth_client, imported_survey, session_factory):
    response = auth_client.get(f"/surveys/{imported_survey}/lectures")
    assert response.status_code == 200
    with session_factory() as session:
        survey = session.get(Survey, imported_survey)
        for slot in survey.slots:
            for choice in slot.choices:
                assert choice.value in response.text


def test_updating_lecture_details_saves_names(auth_client, imported_survey, session_factory):
    with session_factory() as session:
        survey = session.get(Survey, imported_survey)
        form_data = {}
        for slot in survey.slots:
            for choice in slot.choices:
                form_data[f"lecturer_{choice.id}"] = f"Dr. {choice.value}"
                form_data[f"title_{choice.id}"] = f"Lecture: {choice.value}"

    response = auth_client.post(
        f"/surveys/{imported_survey}/lecture-details", data=form_data
    )
    assert response.status_code == 303
    assert "details_updated=1" in response.headers["location"]

    with session_factory() as session:
        survey = session.get(Survey, imported_survey)
        for slot in survey.slots:
            for choice in slot.choices:
                assert choice.lecturer_name == f"Dr. {choice.value}"
                assert choice.text == f"Lecture: {choice.value}"


def test_lecture_details_requires_all_fields(auth_client, imported_survey, session_factory):
    with session_factory() as session:
        survey = session.get(Survey, imported_survey)
        form_data = {}
        for slot in survey.slots:
            for choice in slot.choices:
                form_data[f"lecturer_{choice.id}"] = "Someone"
                form_data[f"title_{choice.id}"] = ""  # missing title

    response = auth_client.post(
        f"/surveys/{imported_survey}/lecture-details", data=form_data
    )
    assert response.status_code == 422


# --- Analysis -----------------------------------------------------------------


def test_analysis_requires_a_completed_run(auth_client, imported_survey):
    assert auth_client.get(f"/surveys/{imported_survey}/report/overall").status_code == 409


def test_analysis_job_completes_and_releases_its_transaction(auth_client, imported_survey, session_factory):
    """The job must finish, and must not hold a read transaction while it runs."""
    from app.analysis.pipeline import run_analysis
    from app.models import AnalysisRun, AnalysisStatus

    survey_id = imported_survey
    with session_factory() as session:
        run = AnalysisRun(survey_id=survey_id, status=AnalysisStatus.pending)
        session.add(run)
        session.commit()
        run_id = run.id

    run_analysis(survey_id, run_id, session_factory)

    with session_factory() as session:
        finished = session.get(AnalysisRun, run_id)
        assert finished.status is AnalysisStatus.done, finished.error_message
        assert finished.response_count == 111
        assert finished.result_json
        assert finished.finished_at is not None


def test_analysis_job_records_a_failure(auth_client, imported_survey, session_factory, monkeypatch):
    """A crash in the model must land in the dashboard, not vanish."""
    from app.analysis import pipeline
    from app.models import AnalysisRun, AnalysisStatus

    survey_id = imported_survey

    def boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(pipeline, "build_context", boom)

    with session_factory() as session:
        run = AnalysisRun(survey_id=survey_id, status=AnalysisStatus.pending)
        session.add(run)
        session.commit()
        run_id = run.id

    pipeline.run_analysis(survey_id, run_id, session_factory)

    with session_factory() as session:
        failed = session.get(AnalysisRun, run_id)
        assert failed.status is AnalysisStatus.failed
        assert "model exploded" in failed.error_message
        assert failed.finished_at is not None


# --- Export and erasure -------------------------------------------------------


def test_export_returns_all_responses(auth_client, imported_survey):
    exported = auth_client.get(f"/surveys/{imported_survey}/export.json").json()
    assert exported["ResultCount"] == 111
    assert all("HappendAt" not in entry for entry in exported["Data"])
    assert all("InstanceId" not in entry for entry in exported["Data"])


def test_deleting_responses_keeps_the_survey(auth_client, imported_survey, session_factory):
    auth_client.post(f"/surveys/{imported_survey}/responses/delete")
    with session_factory() as session:
        assert session.scalars(select(ResponseRow)).all() == []
        assert session.get(Survey, imported_survey) is not None


def test_deleting_a_survey_removes_its_responses(auth_client, imported_survey, session_factory):
    auth_client.post(f"/surveys/{imported_survey}/delete")
    with session_factory() as session:
        assert session.get(Survey, imported_survey) is None
        assert session.scalars(select(ResponseRow)).all() == []


# --- Report rendering ---------------------------------------------------------


def _render(scope: str, screen_actions=None) -> str:
    """Render the report document the way the routes do, without a database."""
    from app.analysis.clustering import ClusteredComments
    from app.analysis.report import ReportContext
    from app.routers.reports import _render_document
    from app.templating import templates  # noqa: F401

    context = ReportContext(
        survey_title="T",
        total_responses=0,
        sections=[],
        organization=ClusteredComments(raw=[], clustered=[]),
        topics=ClusteredComments(raw=[], clustered=[]),
    )
    context.sections = []
    return _render_document(
        None, context, [_dummy_section()], scope, "css/report.css", screen_actions
    )


def _dummy_section():
    from app.analysis.report import ReportSection
    from app.analysis.statistics import compute_stats

    section = ReportSection(
        scope="overall",
        title="Overall Results",
        subtitle="",
        response_count=10,
        dna_count=0,
        stats=compute_stats({q: [3] * 10 for q in ("interesting", "new", "expected",
                                                   "exciting", "structure", "level")}),
    )
    section.render_charts()
    return section


def test_pdf_render_omits_the_screen_controls():
    """The print button must not end up printed into the PDF."""
    assert "screen-actions" not in _render("overall")
    assert "screen-actions" in _render("overall", {"pdf_url": "/x"})


def test_preview_and_pdf_share_one_stylesheet():
    """Two paths, one CSS file — that is what keeps them from drifting."""
    assert 'href="css/report.css"' in _render("overall")
