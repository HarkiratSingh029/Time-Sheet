"""Getting time out of the system.

Two rules carry most of the weight here. **A CSV is a document spreadsheets execute**, so
every field has to leave inert. And an export must never widen what somebody can see —
the rows are exactly the ones they could already read on a page.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import exports, metrics
from backend.app.access import visible_projects_query
from backend.app.exports import neutralise
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    Task,
    TimeNote,
    TimeNoteState,
    User,
    utcnow,
)

TODAY = date.today()
START = TODAY - timedelta(days=40)
DAY = TODAY - timedelta(days=10)


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    """Two projects. Alice is on one, Bob on the other, so scoping is observable."""
    alice = make_person("alice@example.com", "Alice Consultant")
    bob = make_person("bob@example.com", "Bob Consultant")

    sign_in(administrator)
    projects = {}
    for code, name in (("ledger", "Ledger migration"), ("portal", "Client portal")):
        client.post(
            "/projects",
            data={
                "name": name,
                "code": code,
                "description": "",
                "comment": "",
                "owner_id": str(administrator.id),
                "manager_id": "",
                "status": "active",
                "cost": "50000",
                "billable_hours_budget": "100",
                "proposed_duration_days": "",
                "start_date": START.isoformat(),
                "end_date": (TODAY + timedelta(days=40)).isoformat(),
                "required_approvals": "1",
            },
        )
        with client.app.state.session_factory() as session:
            project_id = session.scalar(select(Project.id).where(Project.code == code.upper()))
        client.post(
            f"/projects/{project_id}/tasks", data={"name": "Delivery", "is_billable": "true"}
        )
        client.post(f"/projects/{project_id}/tasks", data={"name": "Admin"})
        with client.app.state.session_factory() as session:
            tasks = {
                task.name: task.id
                for task in session.scalars(select(Task).where(Task.project_id == project_id)).all()
            }
        projects[code] = {"id": project_id, "tasks": tasks}

    client.post(f"/projects/{projects['ledger']['id']}/members", data={"user_id": str(alice.id)})
    client.post(f"/projects/{projects['portal']['id']}/members", data={"user_id": str(bob.id)})

    return {
        "projects": projects,
        "administrator": administrator,
        "alice": alice,
        "bob": bob,
    }


def log(
    client: TestClient,
    world,
    code: str,
    person,
    hours: float,
    day: date = DAY,
    task: str = "Delivery",
    state: TimeNoteState = TimeNoteState.DRAFT,
    detail: str = "Worked on it",
    approver=None,
) -> int:
    project = world["projects"][code]
    with client.app.state.session_factory() as session:
        note = TimeNote(
            project_id=project["id"],
            task_id=project["tasks"][task],
            user_id=person.id,
            work_date=day,
            duration_minutes=int(hours * 60),
            detail=detail,
            state=state,
        )
        if state is not TimeNoteState.DRAFT:
            note.submitted_at = utcnow()
        session.add(note)
        session.flush()
        if approver is not None:
            session.add(
                Approval(
                    time_note_id=note.id,
                    approver_id=approver.id,
                    sequence=1,
                    decision=ApprovalDecision.APPROVED,
                    decided_at=utcnow(),
                )
            )
        session.commit()
        return note.id


def download_csv(client: TestClient, query: str = "") -> str:
    response = client.get(f"/exports/timesheet.csv{query}")
    assert response.status_code == 200
    return response.text


def rows_of(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


# --- the file a spreadsheet opens -----------------------------------------------------


def test_the_csv_opens_cleanly_in_a_spreadsheet(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 7.5)
    sign_in(world["administrator"])

    response = client.get("/exports/timesheet.csv")

    assert response.status_code == 200
    assert response.text.startswith("﻿"), "Excel needs the BOM to read UTF-8"
    assert "text/csv" in response.headers["content-type"]
    assert 'filename="timesheet.csv"' in response.headers["content-disposition"]

    rows = rows_of(response.text)
    assert rows[0] == list(exports.CSV_COLUMNS)
    assert '"Project code"' in response.text, "every field is quoted"


def test_dates_are_iso_and_hours_are_decimal(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 7.5)
    sign_in(world["administrator"])

    row = rows_of(download_csv(client))[1]
    columns = dict(zip(exports.CSV_COLUMNS, row, strict=True))

    assert columns["Date"] == DAY.isoformat()
    assert columns["Hours"] == "7.5", "decimal hours, from the minutes stored"


def test_hours_match_what_the_screen_shows(client: TestClient, world, sign_in) -> None:
    # Distinct days, all comfortably inside the project window.
    for offset, hours in enumerate((0.25, 1, 7.5, 8)):
        log(client, world, "ledger", world["alice"], hours, day=DAY - timedelta(days=offset))

    sign_in(world["administrator"])
    exported = {row[7] for row in rows_of(download_csv(client))[1:]}

    assert exported == {"0.25", "1", "7.5", "8"}


def test_accented_names_survive_the_round_trip(client: TestClient, world, sign_in) -> None:
    with client.app.state.session_factory() as session:
        person = session.get(User, world["alice"].id)
        person.full_name = "Zoë Ångström"
        session.commit()

    log(client, world, "ledger", world["alice"], 4)
    sign_in(world["administrator"])

    assert "Zoë Ångström" in download_csv(client)


# --- a CSV is a document spreadsheets execute -----------------------------------------


@pytest.mark.parametrize(
    "dangerous",
    ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)", "\tmalicious", "\rmalicious"],
)
def test_a_formula_looking_cell_is_neutralised(dangerous: str) -> None:
    assert neutralise(dangerous).startswith("'"), (
        "opening an export must not be able to execute anything"
    )
    assert neutralise(dangerous)[1:] == dangerous, "and the data itself is not changed"


def test_ordinary_text_is_left_alone() -> None:
    for harmless in ("Worked on the ledger", "8 hours", "a-b", "", "2026-01-01"):
        assert neutralise(harmless) == harmless


def test_a_formula_in_a_detail_field_leaves_inert(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 4, detail='=HYPERLINK("http://evil","click")')
    sign_in(world["administrator"])

    row = rows_of(download_csv(client))[1]
    detail = dict(zip(exports.CSV_COLUMNS, row, strict=True))["Detail"]

    assert detail.startswith("'="), "the cell is text, not a formula"


def test_a_formula_in_a_project_name_leaves_inert(client: TestClient, world, sign_in) -> None:
    with client.app.state.session_factory() as session:
        project = session.get(Project, world["projects"]["ledger"]["id"])
        project.name = "=1+1"
        session.commit()

    log(client, world, "ledger", world["alice"], 4)
    sign_in(world["administrator"])

    row = rows_of(download_csv(client))[1]
    assert dict(zip(exports.CSV_COLUMNS, row, strict=True))["Project"] == "'=1+1"


# --- an export sees no more than a page -----------------------------------------------


def test_a_consultant_exports_only_their_own_projects(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8)
    log(client, world, "portal", world["bob"], 8)

    sign_in(world["alice"])
    codes = {row[0] for row in rows_of(download_csv(client))[1:]}

    assert codes == {"LEDGER"}, "an export must not widen what somebody can see"


def test_an_administrator_exports_everything(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8)
    log(client, world, "portal", world["bob"], 8)

    sign_in(world["administrator"])
    codes = {row[0] for row in rows_of(download_csv(client))[1:]}

    assert codes == {"LEDGER", "PORTAL"}


def test_asking_for_another_project_by_id_returns_nothing(
    client: TestClient, world, sign_in
) -> None:
    log(client, world, "portal", world["bob"], 8)

    sign_in(world["alice"])
    rows = rows_of(download_csv(client, f"?project_id={world['projects']['portal']['id']}"))

    assert rows[1:] == [], "a filter is not a way past the scoping"


def test_the_export_requires_a_session(client: TestClient, world) -> None:
    client.cookies.clear()
    response = client.get("/exports/timesheet.csv", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- states stay apart ----------------------------------------------------------------


def test_every_row_carries_its_state(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        day=DAY,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )
    log(
        client,
        world,
        "ledger",
        world["alice"],
        4,
        day=DAY - timedelta(days=1),
        state=TimeNoteState.SUBMITTED,
    )
    log(client, world, "ledger", world["alice"], 2, day=DAY - timedelta(days=2))

    sign_in(world["administrator"])
    states = {row[8] for row in rows_of(download_csv(client))[1:]}

    assert states == {"approved", "submitted", "draft"}, (
        "approved and unapproved must be distinguishable, never silently mixed"
    )


def test_filtering_by_state_narrows_the_file(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )
    log(client, world, "ledger", world["alice"], 4, day=DAY - timedelta(days=1))

    sign_in(world["administrator"])
    rows = rows_of(download_csv(client, "?state=approved"))[1:]

    assert len(rows) == 1
    assert rows[0][8] == "approved"


def test_an_approved_row_names_who_decided_it(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )

    sign_in(world["administrator"])
    row = rows_of(download_csv(client))[1]

    assert (
        dict(zip(exports.CSV_COLUMNS, row, strict=True))["Decided by"]
        == world["administrator"].email
    )


def test_filters_compose(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, day=DAY)
    log(client, world, "ledger", world["alice"], 4, day=DAY - timedelta(days=20))

    sign_in(world["administrator"])
    project_id = world["projects"]["ledger"]["id"]
    since = (DAY - timedelta(days=1)).isoformat()
    query = f"?project_id={project_id}&since={since}"
    rows = rows_of(download_csv(client, query))[1:]

    assert len(rows) == 1
    assert rows[0][6] == DAY.isoformat()


def test_the_filename_names_what_was_exported(client: TestClient, world, sign_in) -> None:
    sign_in(world["administrator"])
    response = client.get(
        f"/exports/timesheet.csv?project_id={world['projects']['ledger']['id']}&since=2026-01-01"
    )

    assert 'filename="timesheet-ledger-2026-01-01.csv"' in response.headers["content-disposition"]


# --- it streams -----------------------------------------------------------------------


def test_the_csv_is_produced_a_row_at_a_time(client: TestClient, world) -> None:
    """The generator must yield before the query has finished, or it is not streaming."""
    for offset in range(30):
        log(client, world, "ledger", world["alice"], 8, day=START + timedelta(days=offset))

    with client.app.state.session_factory() as session:
        person = session.get(User, world["administrator"].id)
        query = exports.timesheet_query(visible_projects_query(person))
        chunks = exports.stream_csv(session, query)

        assert next(chunks) == "﻿", "the BOM arrives before any row is read"
        assert "Project code" in next(chunks)

        produced = sum(1 for _ in chunks)

    assert produced == 30, "one chunk per row, not one chunk for the file"


# --- the report -----------------------------------------------------------------------


def test_the_pdf_names_the_project_and_totals(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )
    log(
        client,
        world,
        "ledger",
        world["alice"],
        4,
        day=DAY - timedelta(days=1),
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )

    sign_in(world["administrator"])
    response = client.get(f"/exports/projects/{world['projects']['ledger']['id']}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert 'filename="ledger-report.pdf"' in response.headers["content-disposition"]
    assert len(response.content) > 1000


def test_the_report_figures_match_the_dashboard(
    client: TestClient, world, settings, sign_in
) -> None:
    """The PDF is built from the same metrics call the dashboard renders."""
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )
    log(client, world, "ledger", world["alice"], 4, day=DAY - timedelta(days=1))

    with client.app.state.session_factory() as session:
        project = session.get(Project, world["projects"]["ledger"]["id"])
        figures = metrics.for_project(session, project, settings)
        notes = list(
            session.scalars(
                exports.timesheet_query(
                    visible_projects_query(session.get(User, world["administrator"].id)),
                    project_id=project.id,
                    state=TimeNoteState.APPROVED,
                )
            ).all()
        )
        document = exports.project_report(figures, notes, settings, TODAY)

    assert figures.hours.approved == 8.0
    assert figures.hours.draft == 4.0
    assert document.startswith(b"%PDF-")
    assert len(notes) == 1, "only approved entries appear in the client-facing report"


def test_a_project_with_no_approved_time_still_produces_a_report(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["administrator"])
    response = client.get(f"/exports/projects/{world['projects']['ledger']['id']}/report.pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


def test_an_outsider_cannot_download_a_report(client: TestClient, world, sign_in) -> None:
    sign_in(world["bob"])
    response = client.get(f"/exports/projects/{world['projects']['ledger']['id']}/report.pdf")

    assert response.status_code == 404, "the report follows the same access as the project"


def test_a_name_outside_latin_1_does_not_break_the_report(
    client: TestClient, world, settings, sign_in
) -> None:
    """The built-in PDF fonts are Latin-1; a Japanese name must degrade, not explode."""
    with client.app.state.session_factory() as session:
        person = session.get(User, world["alice"].id)
        person.full_name = "山田太郎"
        session.commit()

    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        state=TimeNoteState.APPROVED,
        approver=world["administrator"],
    )

    sign_in(world["administrator"])
    response = client.get(f"/exports/projects/{world['projects']['ledger']['id']}/report.pdf")

    assert response.status_code == 200, "month-end must not fail over a font"
    assert response.content.startswith(b"%PDF-")
