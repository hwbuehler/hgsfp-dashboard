"""Route-level behaviour: the auth boundary, the public form, and deletion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.blueprint import load_reference_blueprint
from app.config import PROJECT_ROOT
from app.db import get_session
from app.main import app
from app.models import (
    Base,
    Response as ResponseRow,
    SlotChoice,
    Survey,
    SurveyStatus,
    SurveyTemplate,
    User,
)
from app.security import hash_password


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
        session.add(
            SurveyTemplate(
                name="Graduate Days Feedback",
                blueprint_json=load_reference_blueprint(
                    PROJECT_ROOT / "reference" / "surveyjs-original.json"
                ),
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
def survey(auth_client, session_factory):
    with session_factory() as session:
        template_id = session.scalars(select(SurveyTemplate)).first().id
    response = auth_client.post(
        "/surveys", data={"title": "Test Run", "template_id": template_id}
    )
    survey_id = int(response.headers["location"].split("/")[2])
    with session_factory() as session:
        token = session.get(Survey, survey_id).public_token
    return survey_id, token


# --- Auth boundary ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/dashboard", "/surveys/1/edit", "/surveys/1/report", "/surveys/1/data", "/surveys/1/qr.png"],
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


def test_logout_clears_the_session(auth_client):
    assert auth_client.get("/dashboard").status_code == 200
    auth_client.post("/logout")
    assert auth_client.get("/dashboard").status_code == 303


# --- Public participation -----------------------------------------------------


def test_draft_survey_does_not_accept_responses(client, survey):
    _, token = survey
    assert client.get(f"/s/{token}").status_code == 410
    assert client.post(f"/s/{token}/responses", json={"ml_title": "Ollefs"}).status_code == 410


def test_published_survey_is_public(auth_client, survey):
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")

    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get(f"/s/{token}").status_code == 200
    assert anonymous.get(f"/s/{token}/definition.json").status_code == 200


def test_unknown_token_is_not_found(client):
    assert client.get("/s/does-not-exist").status_code == 404


def test_response_is_stored_without_identifying_metadata(auth_client, survey, session_factory):
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")
    auth_client.post(
        f"/s/{token}/responses",
        json={
            "ml_title": "Ollefs",
            "ml_interesting": 4,
            "al_title": "DnA",
            "il_title": "DnA",
            "client_ip": "1.2.3.4",
        },
    )
    with session_factory() as session:
        row = session.scalars(select(ResponseRow)).one()
        assert "client_ip" not in row.payload_json
        assert not hasattr(row.submitted_on, "hour"), "a time would weaken anonymity"
        # A UUID rather than a sequence number, so order cannot be reconstructed.
        assert len(row.id) == 36


def test_unpublish_closes_the_link(auth_client, survey):
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")
    assert auth_client.get(f"/s/{token}").status_code == 200
    auth_client.post(f"/surveys/{survey_id}/unpublish")
    assert auth_client.get(f"/s/{token}").status_code == 410


def test_publish_is_refused_while_a_slot_is_empty(auth_client, survey, session_factory):
    survey_id, token = survey
    with session_factory() as session:
        loaded = session.get(Survey, survey_id)
        for choice in list(loaded.slots[0].choices):
            session.delete(choice)
        session.commit()

    auth_client.post(f"/surveys/{survey_id}/publish")
    assert auth_client.get(f"/s/{token}").status_code == 410


# --- Import, export and erasure ----------------------------------------------


def test_legacy_import_and_export_round_trip(auth_client, survey):
    survey_id, _ = survey
    raw = (PROJECT_ROOT / "reference" / "sample-survey-results.json").read_bytes()

    response = auth_client.post(
        f"/surveys/{survey_id}/import", files={"file": ("s.json", raw, "application/json")}
    )
    assert "imported=111" in response.headers["location"]

    exported = auth_client.get(f"/surveys/{survey_id}/export.json").json()
    assert exported["ResultCount"] == 111
    assert all("HappendAt" not in entry for entry in exported["Data"])
    assert all("InstanceId" not in entry for entry in exported["Data"])


def test_malformed_import_is_reported_not_raised(auth_client, survey):
    survey_id, _ = survey
    response = auth_client.post(
        f"/surveys/{survey_id}/import", files={"file": ("s.json", b"not json", "application/json")}
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_deleting_responses_keeps_the_survey(auth_client, survey, session_factory):
    survey_id, _ = survey
    raw = (PROJECT_ROOT / "reference" / "sample-survey-results.json").read_bytes()
    auth_client.post(f"/surveys/{survey_id}/import", files={"file": ("s.json", raw, "application/json")})

    auth_client.post(f"/surveys/{survey_id}/responses/delete")

    with session_factory() as session:
        assert session.scalars(select(ResponseRow)).all() == []
        assert session.get(Survey, survey_id) is not None


def test_deleting_a_survey_removes_its_responses(auth_client, survey, session_factory):
    survey_id, _ = survey
    raw = (PROJECT_ROOT / "reference" / "sample-survey-results.json").read_bytes()
    auth_client.post(f"/surveys/{survey_id}/import", files={"file": ("s.json", raw, "application/json")})

    auth_client.post(f"/surveys/{survey_id}/delete")

    with session_factory() as session:
        assert session.get(Survey, survey_id) is None
        assert session.scalars(select(ResponseRow)).all() == []


# --- Editor -------------------------------------------------------------------


def test_new_survey_is_seeded_from_the_template(auth_client, survey):
    survey_id, _ = survey
    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    assert [page["name"] for page in definition["pages"]] == ["ml", "al", "il", "sugg"]
    values = [c["value"] for c in definition["pages"][0]["elements"][0]["choices"]]
    assert "Ollefs" in values and values[-1] == "DnA"


def test_added_choice_reaches_the_definition(auth_client, survey, session_factory):
    survey_id, _ = survey
    with session_factory() as session:
        slot_id = session.get(Survey, survey_id).slots[0].id

    auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices",
        data={"text": "Brand New Lecture", "value": "Neumann"},
    )
    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    assert "Neumann" in json.dumps(definition)


@pytest.mark.parametrize("value", ["Ollefs", "DnA"])
def test_conflicting_choice_keys_are_refused(auth_client, survey, session_factory, value):
    survey_id, _ = survey
    with session_factory() as session:
        slot_id = session.get(Survey, survey_id).slots[0].id

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices", data={"text": "X", "value": value}
    )
    assert response.status_code == 400


def test_analysis_requires_a_completed_run(auth_client, survey):
    survey_id, _ = survey
    assert auth_client.get(f"/surveys/{survey_id}/report/overall").status_code == 409


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


# --- Creating a survey from a results file ------------------------------------


def _sample_bytes() -> bytes:
    return (PROJECT_ROOT / "reference" / "sample-survey-results.json").read_bytes()


def test_survey_can_be_created_from_a_results_file(auth_client, session_factory):
    response = auth_client.post(
        "/surveys/from-file",
        data={"title": "Archive 56th", "template_id": 1},
        files={"file": ("s.json", _sample_bytes(), "application/json")},
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "created=111" in location

    survey_id = int(location.split("/")[2])
    with session_factory() as session:
        survey = session.get(Survey, survey_id)
        assert survey.title == "Archive 56th"
        # Archived data must not be collecting new responses.
        assert survey.status is SurveyStatus.closed
        assert len(session.scalars(select(ResponseRow)).all()) == 111


def test_created_survey_takes_its_lectures_from_the_file(auth_client, session_factory):
    response = auth_client.post(
        "/surveys/from-file",
        data={"title": "Archive", "template_id": 1},
        files={"file": ("s.json", _sample_bytes(), "application/json")},
    )
    survey_id = int(response.headers["location"].split("/")[2])

    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    morning = [c["value"] for c in definition["pages"][0]["elements"][0]["choices"]]
    # Exactly the lectures present in the data, plus the automatic DnA entry.
    assert morning == ["Blekman", "Erdmenger", "Groß", "Ollefs", "Peacock", "DnA"]


def test_malformed_file_does_not_create_a_survey(auth_client, session_factory):
    response = auth_client.post(
        "/surveys/from-file",
        data={"title": "Broken", "template_id": 1},
        files={"file": ("s.json", b"{]", "application/json")},
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with session_factory() as session:
        assert session.scalars(select(Survey)).all() == []


# --- Editor save feedback -----------------------------------------------------


def test_saving_the_title_confirms_with_a_timestamp(auth_client, survey):
    survey_id, _ = survey
    response = auth_client.post(
        f"/surveys/{survey_id}/title", data={"title": "New", "intro_text": ""}
    )
    assert response.status_code == 200
    assert "save-state" in response.text
    assert "Gespeichert" in response.text


def test_adding_a_choice_names_what_was_stored(auth_client, survey, session_factory):
    survey_id, _ = survey
    with session_factory() as session:
        slot_id = session.get(Survey, survey_id).slots[0].id

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices",
        data={"text": "A New Lecture", "value": "Neumann"},
    )
    assert "A New Lecture" in response.text and "Neumann" in response.text
    assert "gespeichert" in response.text
    # The freshly written row is marked so it can be spotted in a long list.
    assert "is-just-saved" in response.text


def test_slot_autosave_does_not_replace_the_form(auth_client, survey, session_factory):
    """The status line is swapped, not the fragment — otherwise typing loses focus."""
    survey_id, _ = survey
    with session_factory() as session:
        slot_id = session.get(Survey, survey_id).slots[0].id

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}",
        data={"page_title": "Morning", "key_prefix": "ml"},
    )
    assert "save-state" in response.text
    assert "<form" not in response.text
    assert "ml_title" in response.text


# --- Editing an existing lecture in place -------------------------------------


def _first_choice(session_factory, survey_id):
    with session_factory() as session:
        slot = session.get(Survey, survey_id).slots[0]
        return slot.id, slot.choices[0].id, slot.choices[0].value


def test_lecture_text_and_key_can_be_edited(auth_client, survey, session_factory):
    survey_id, _ = survey
    slot_id, choice_id, _ = _first_choice(session_factory, survey_id)

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
        data={"text": "Renamed Lecture", "value": "Neumann"},
    )
    assert response.status_code == 200
    assert "save-state" in response.text and "is-error" not in response.text

    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    choices = definition["pages"][0]["elements"][0]["choices"]
    assert {"value": "Neumann", "text": "Renamed Lecture"} in choices


def test_editing_keeps_the_position(auth_client, survey, session_factory):
    """Delete-and-recreate moved a lecture to the end of the list; editing must not."""
    survey_id, _ = survey
    with session_factory() as session:
        slot = session.get(Survey, survey_id).slots[0]
        slot_id = slot.id
        first_id = slot.choices[0].id
        original_order = [c.value for c in slot.choices]

    auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{first_id}",
        data={"text": "Still First", "value": "StillFirst"},
    )
    with session_factory() as session:
        slot = session.get(Survey, survey_id).slots[0]
        assert [c.value for c in slot.choices][1:] == original_order[1:]
        assert slot.choices[0].value == "StillFirst"


def test_renaming_a_key_migrates_existing_responses(auth_client, survey, session_factory):
    """The key lives inside every response; leaving it behind orphans the data."""
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")
    slot_id, choice_id, old_value = _first_choice(session_factory, survey_id)

    for _ in range(3):
        auth_client.post(
            f"/s/{token}/responses",
            json={
                "ml_title": old_value,
                "ml_interesting": 4,
                "al_title": "DnA",
                "il_title": "DnA",
            },
        )

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
        data={"text": "Renamed", "value": "BrandNewKey"},
    )
    assert "3 vorhandene Antworten" in response.text

    with session_factory() as session:
        stored = [r.payload_json["ml_title"] for r in session.scalars(select(ResponseRow))]
        assert stored == ["BrandNewKey"] * 3


def test_editing_only_the_text_leaves_responses_alone(auth_client, survey, session_factory):
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")
    slot_id, choice_id, old_value = _first_choice(session_factory, survey_id)
    auth_client.post(
        f"/s/{token}/responses",
        json={"ml_title": old_value, "al_title": "DnA", "il_title": "DnA"},
    )

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
        data={"text": "A Better Title", "value": old_value},
    )
    assert "Antworten" not in response.text
    with session_factory() as session:
        assert session.scalars(select(ResponseRow)).one().payload_json["ml_title"] == old_value


@pytest.mark.parametrize("bad_value", ["DnA", ""])
def test_invalid_edit_is_refused_without_changing_anything(
    auth_client, survey, session_factory, bad_value
):
    survey_id, _ = survey
    slot_id, choice_id, old_value = _first_choice(session_factory, survey_id)

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
        data={"text": "X", "value": bad_value},
    )
    # 200 with a visible reason, so htmx puts the message next to the field.
    assert response.status_code == 200
    assert "is-error" in response.text
    with session_factory() as session:
        assert session.get(SlotChoice, choice_id).value == old_value


def test_edit_cannot_duplicate_another_key(auth_client, survey, session_factory):
    survey_id, _ = survey
    with session_factory() as session:
        slot = session.get(Survey, survey_id).slots[0]
        slot_id, first, second = slot.id, slot.choices[0].id, slot.choices[1].value

    response = auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{first}",
        data={"text": "X", "value": second},
    )
    assert "is-error" in response.text
    assert "bereits vergeben" in response.text


def test_editing_marks_the_survey_as_edited(auth_client, survey, session_factory):
    """Choice edits write to another table, so updated_at needs an explicit bump."""
    survey_id, _ = survey
    with session_factory() as session:
        before = session.get(Survey, survey_id).updated_at
    slot_id, choice_id, _ = _first_choice(session_factory, survey_id)

    auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices/{choice_id}",
        data={"text": "Touched", "value": "Touched"},
    )
    with session_factory() as session:
        assert session.get(Survey, survey_id).updated_at > before
