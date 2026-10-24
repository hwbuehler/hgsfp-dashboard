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


def seeded_lecture(session_factory, survey_id: int, slot: int = 0) -> str:
    """The lecture the template seeded into a slot.

    Tests used to name "Ollefs" directly; the template now seeds a single
    example per slot, so they ask the database instead.
    """
    with session_factory() as session:
        return session.get(Survey, survey_id).slots[slot].choices[0].value


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
            "ml_title": seeded_lecture(session_factory, survey_id),
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
    assert len(values) == 2 and values[-1] == "DnA"


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


@pytest.mark.parametrize("which", ["existing", "reserved"])
def test_conflicting_choice_keys_are_refused(auth_client, survey, session_factory, which):
    survey_id, _ = survey
    with session_factory() as session:
        slot_id = session.get(Survey, survey_id).slots[0].id
    value = seeded_lecture(session_factory, survey_id) if which == "existing" else "DnA"

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
        slot_id = session.get(Survey, survey_id).slots[0].id
    # The template seeds one example, so a second lecture has to be added first.
    auth_client.post(
        f"/surveys/{survey_id}/slots/{slot_id}/choices",
        data={"text": "Second lecture", "value": "SecondKey"},
    )
    with session_factory() as session:
        slot = session.get(Survey, survey_id).slots[0]
        first, second = slot.choices[0].id, slot.choices[1].value

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


# --- Creating a survey from the template --------------------------------------


def test_template_seeds_one_example_per_slot(auth_client, survey):
    """Seeding last run's full line-up meant deleting five wrong rows first."""
    survey_id, _ = survey
    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    for page in definition["pages"][:-1]:
        values = [c["value"] for c in page["elements"][0]["choices"]]
        assert len(values) == 2, f"{page['name']}: one example plus DnA expected, got {values}"
        assert values[-1] == "DnA"


def test_file_import_still_seeds_every_lecture(auth_client):
    """The one-example rule applies to the template only, never to real data."""
    response = auth_client.post(
        "/surveys/from-file",
        data={"title": "Archive", "template_id": 1},
        files={"file": ("s.json", _sample_bytes(), "application/json")},
    )
    survey_id = int(response.headers["location"].split("/")[2])
    definition = auth_client.get(f"/surveys/{survey_id}/definition").json()
    morning = [c["value"] for c in definition["pages"][0]["elements"][0]["choices"]]
    assert morning == ["Blekman", "Erdmenger", "Groß", "Ollefs", "Peacock", "DnA"]


# --- The invitation slide -----------------------------------------------------


def test_slide_is_a_landscape_pdf(auth_client, survey):
    survey_id, _ = survey
    response = auth_client.get(f"/surveys/{survey_id}/slide.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert "qr-slide.pdf" in response.headers["content-disposition"]


def test_slide_requires_login(auth_client, survey):
    survey_id, _ = survey
    anonymous = TestClient(app, follow_redirects=False)
    response = anonymous.get(f"/surveys/{survey_id}/slide.pdf")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_qr_png_still_served_for_the_page_preview(auth_client, survey):
    survey_id, _ = survey
    response = auth_client.get(f"/surveys/{survey_id}/qr.png")
    assert response.status_code == 200
    assert response.content[:4] == b"\x89PNG"


# --- Survey titles must stay distinguishable ---------------------------------


def test_second_survey_cannot_reuse_a_title(auth_client, survey, session_factory):
    """Two runs with the same name are indistinguishable in every list and export."""
    response = auth_client.post("/surveys", data={"title": "Test Run", "template_id": 1})
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with session_factory() as session:
        assert len(session.scalars(select(Survey)).all()) == 1


def test_duplicate_title_is_matched_regardless_of_case_and_spacing(auth_client, survey):
    response = auth_client.post(
        "/surveys", data={"title": "  test run  ", "template_id": 1}
    )
    assert "error=" in response.headers["location"]


def test_file_import_also_refuses_a_taken_title(auth_client, survey, session_factory):
    response = auth_client.post(
        "/surveys/from-file",
        data={"title": "Test Run", "template_id": 1},
        files={"file": ("s.json", _sample_bytes(), "application/json")},
    )
    assert "error=" in response.headers["location"]
    with session_factory() as session:
        # Neither the survey nor its responses may be created.
        assert len(session.scalars(select(Survey)).all()) == 1
        assert session.scalars(select(ResponseRow)).all() == []


def test_renaming_to_a_taken_title_is_refused(auth_client, survey, session_factory):
    survey_id, _ = survey
    auth_client.post("/surveys", data={"title": "Second Run", "template_id": 1})

    response = auth_client.post(
        f"/surveys/{survey_id}/title", data={"title": "Second Run", "intro_text": ""}
    )
    assert "is-error" in response.text
    with session_factory() as session:
        assert session.get(Survey, survey_id).title == "Test Run"


def test_a_survey_may_keep_its_own_title(auth_client, survey):
    """Saving the title form unchanged must not trip the uniqueness check."""
    survey_id, _ = survey
    response = auth_client.post(
        f"/surveys/{survey_id}/title", data={"title": "Test Run", "intro_text": "Hallo"}
    )
    assert "is-error" not in response.text


def test_empty_title_is_refused_with_a_sentence(auth_client, survey):
    survey_id, _ = survey
    response = auth_client.post(
        f"/surveys/{survey_id}/title", data={"title": "   ", "intro_text": ""}
    )
    assert response.status_code == 200
    assert "is-error" in response.text


# --- Load behaviour and input limits ------------------------------------------


def test_malformed_json_is_a_bad_request_not_a_crash(auth_client, survey):
    """A decode error used to escape as a 500, losing the answer silently."""
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")

    response = auth_client.post(
        f"/s/{token}/responses",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]


def test_oversized_submission_is_refused(auth_client, survey, session_factory):
    """One request must not be able to write an unbounded blob to the database."""
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")

    from app.config import settings

    huge = "x" * (settings.max_submission_bytes + 1024)
    response = auth_client.post(
        f"/s/{token}/responses", json={"ml_title": "DnA", "sugg_topics": huge}
    )
    assert response.status_code == 413
    with session_factory() as session:
        assert session.scalars(select(ResponseRow)).all() == []


def test_long_free_text_is_truncated_not_dropped(auth_client, survey, session_factory):
    """A participant who pastes too much still gets their ratings counted."""
    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")

    from app.config import settings

    long_text = "y" * (settings.max_free_text_chars * 2)
    response = auth_client.post(
        f"/s/{token}/responses",
        json={
            "ml_title": seeded_lecture(session_factory, survey_id),
            "ml_interesting": 4,
            "sugg_topics": long_text,
        },
    )
    assert response.status_code == 200

    with session_factory() as session:
        payload = session.scalars(select(ResponseRow)).one().payload_json
        assert len(payload["sugg_topics"]) == settings.max_free_text_chars
        assert payload["ml_interesting"] == 4


def test_oversized_import_is_refused(auth_client, survey):
    survey_id, _ = survey
    from app.config import settings

    blob = b"x" * (settings.max_upload_bytes + 1024)
    response = auth_client.post(
        f"/surveys/{survey_id}/import",
        files={"file": ("huge.json", blob, "application/json")},
    )
    assert response.status_code == 413


def test_submitting_loads_the_survey_in_one_query(auth_client, survey, session_factory):
    """Lazy loading cost one query per slot, exactly when the hall submits at once."""
    from sqlalchemy import event

    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")

    engine = session_factory.kw["bind"]
    seen: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    try:
        response = auth_client.post(
            f"/s/{token}/responses",
            json={"ml_title": "DnA", "al_title": "DnA", "il_title": "DnA"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert response.status_code == 200
    # One for the survey, one for its slots, one for all choices at once.
    assert len(seen) <= 3, f"expected a bounded number of SELECTs, got {len(seen)}"


def test_concurrent_submissions_all_land(auth_client, survey, session_factory):
    """The burst a lecture hall produces must not drop or corrupt responses."""
    from concurrent.futures import ThreadPoolExecutor

    survey_id, token = survey
    auth_client.post(f"/surveys/{survey_id}/publish")
    lecture = seeded_lecture(session_factory, survey_id)

    def submit(n: int) -> int:
        return auth_client.post(
            f"/s/{token}/responses",
            json={"ml_title": lecture, "ml_interesting": (n % 5) + 1},
        ).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(submit, range(40)))

    assert codes == [200] * 40
    with session_factory() as session:
        assert len(session.scalars(select(ResponseRow)).all()) == 40


def test_analysis_job_completes_and_releases_its_transaction(survey, session_factory):
    """The job must finish, and must not hold a read transaction while it runs.

    In WAL mode an open read transaction pins the WAL file, so one left open for
    the length of a model run makes the WAL grow with every response submitted
    meanwhile and never checkpoints it back.
    """
    from app.analysis.pipeline import run_analysis
    from app.models import AnalysisRun, AnalysisStatus

    survey_id, _ = survey
    raw = (PROJECT_ROOT / "reference" / "sample-survey-results.json").read_bytes()
    with session_factory() as session:
        from app.analysis.legacy_import import import_into

        import_into(session, session.get(Survey, survey_id), raw)

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


def test_analysis_job_records_a_failure(survey, session_factory, monkeypatch):
    """A crash in the model must land in the dashboard, not vanish."""
    from app.analysis import pipeline
    from app.models import AnalysisRun, AnalysisStatus

    survey_id, _ = survey

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
