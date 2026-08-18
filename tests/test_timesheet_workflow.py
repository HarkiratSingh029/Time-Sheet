"""Logging time: the calendar, the entry, and the draft → submitted transition.

The rules that matter here are the ones a wrong answer makes dangerous rather than
untidy — a note logged for someone else, or a submitted day quietly edited after the fact.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.calendar_month import build_month, default_month
from backend.app.models import Project, Task, TimeNote, TimeNoteState

START = date(2026, 1, 1)
END = date(2026, 6, 30)
INSIDE = date(2026, 2, 3)
BEFORE = date(2025, 12, 31)
AFTER = date(2026, 7, 1)


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    consultant = make_person("consultant@example.com", "Priya Consultant")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger migration",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "600",
            "proposed_duration_days": "",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery", "is_billable": "true"})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Retired"})

    with client.app.state.session_factory() as session:
        tasks = session.scalars(select(Task).where(Task.project_id == project_id)).all()
        task_id = next(task.id for task in tasks if task.name == "Discovery")
        retired_id = next(task.id for task in tasks if task.name == "Retired")

    client.post(f"/projects/{project_id}/tasks/{retired_id}/archive", data={"archived": "true"})

    return {
        "project_id": project_id,
        "task_id": task_id,
        "retired_task_id": retired_id,
        "administrator": administrator,
        "consultant": consultant,
        "outsider": outsider,
    }


def log(client: TestClient, world, *, day: date = INSIDE, hours: str = "7.5", **overrides):
    payload = {
        "work_date": day.isoformat(),
        "task_id": str(world["task_id"]),
        "hours": hours,
        "detail": "Reviewed the legacy schema",
        "month": "2026-02",
    }
    payload.update(overrides)
    return client.post(f"/projects/{world['project_id']}/notes", data=payload)


def notes_of(client: TestClient, project_id: int) -> list[TimeNote]:
    with client.app.state.session_factory() as session:
        return list(
            session.scalars(select(TimeNote).where(TimeNote.project_id == project_id)).all()
        )


# --- the calendar ---------------------------------------------------------------------


def test_the_calendar_opens_for_a_member(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.get(f"/projects/{world['project_id']}/calendar?month=2026-02")

    assert response.status_code == 200
    assert "February 2026" in response.text
    assert "Double-click a day to log time" in response.text


def test_days_are_buttons_inside_the_window_and_inert_outside(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    january = client.get(f"/projects/{world['project_id']}/calendar?month=2026-01").text

    # 1 January is the project start; 31 December sits outside it.
    assert 'data-day="2026-01-01"' in january
    assert 'data-day="2025-12-31"' not in january
    assert "calendar__day--inert" in january


def test_navigation_stops_at_the_project_window(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    first = client.get(f"/projects/{world['project_id']}/calendar?month=2026-01").text
    last = client.get(f"/projects/{world['project_id']}/calendar?month=2026-06").text

    assert "?month=2025-12" not in first, "there is no project before it starts"
    assert "?month=2026-02" in first
    assert "?month=2026-07" not in last, "there is no project after it ends"


def test_an_outsider_cannot_open_the_calendar(client: TestClient, world, sign_in) -> None:
    sign_in(world["outsider"])
    assert client.get(f"/projects/{world['project_id']}/calendar").status_code == 404


# --- logging --------------------------------------------------------------------------


def test_a_member_logs_a_day_as_a_draft_in_minutes(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = log(client, world, hours="7.5")

    assert response.status_code == 200
    notes = notes_of(client, world["project_id"])
    assert len(notes) == 1
    assert notes[0].duration_minutes == 450, "hours in, minutes stored"
    assert notes[0].state is TimeNoteState.DRAFT
    assert notes[0].user_id == world["consultant"].id


def test_the_note_belongs_to_the_signed_in_user_whatever_the_form_says(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    log(client, world, user_id=str(world["administrator"].id))

    notes = notes_of(client, world["project_id"])
    assert notes[0].user_id == world["consultant"].id, "nobody logs time for someone else"


@pytest.mark.parametrize("hours", ["0", "-3", "25", "not-a-number"])
def test_an_impossible_duration_is_refused(client: TestClient, world, sign_in, hours: str) -> None:
    sign_in(world["consultant"])
    response = log(client, world, hours=hours)

    assert response.status_code == 200
    assert notes_of(client, world["project_id"]) == []
    assert "duration" in response.text.lower() or "hours" in response.text.lower()


@pytest.mark.parametrize("day", [BEFORE, AFTER])
def test_a_day_outside_the_project_window_is_refused(
    client: TestClient, world, sign_in, day: date
) -> None:
    sign_in(world["consultant"])
    response = log(client, world, day=day)

    assert notes_of(client, world["project_id"]) == []
    assert "project" in response.text.lower()


def test_an_archived_task_cannot_be_logged_against(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = log(client, world, task_id=str(world["retired_task_id"]))

    assert notes_of(client, world["project_id"]) == []
    assert "belongs to this project" in response.text


def test_an_outsider_cannot_log_time_on_the_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["outsider"])
    assert log(client, world).status_code == 404
    assert notes_of(client, world["project_id"]) == []


# --- editing --------------------------------------------------------------------------


def test_a_draft_can_be_edited_and_deleted_by_its_author(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    log(client, world, hours="4")
    note_id = notes_of(client, world["project_id"])[0].id
    project_id = world["project_id"]

    client.post(
        f"/projects/{project_id}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "6", "detail": "Corrected"},
    )
    assert notes_of(client, project_id)[0].duration_minutes == 360

    client.post(f"/projects/{project_id}/notes/{note_id}/delete")
    assert notes_of(client, project_id) == []


def test_one_persons_note_is_invisible_to_another(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    log(client, world)
    note_id = notes_of(client, world["project_id"])[0].id

    sign_in(world["administrator"])
    project_id = world["project_id"]
    edit = client.post(
        f"/projects/{project_id}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "1", "detail": "Meddling"},
    )

    assert edit.status_code == 404, "even an administrator does not edit someone's timesheet here"
    assert notes_of(client, project_id)[0].duration_minutes == 450


# --- submitting -----------------------------------------------------------------------


def test_submitting_freezes_the_day_against_its_author(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    project_id = world["project_id"]
    log(client, world)
    note_id = notes_of(client, project_id)[0].id

    submitted = client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": INSIDE.isoformat(), "month": "2026-02"},
    )
    assert "Submitted 1 entry." in submitted.text
    assert notes_of(client, project_id)[0].state is TimeNoteState.SUBMITTED

    blocked = client.post(
        f"/projects/{project_id}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "1", "detail": "Second thoughts"},
    )
    assert "no longer editable" in blocked.text
    assert notes_of(client, project_id)[0].duration_minutes == 450

    removal = client.post(f"/projects/{project_id}/notes/{note_id}/delete")
    assert "no longer editable" in removal.text
    assert len(notes_of(client, project_id)) == 1


def test_a_range_submits_every_draft_it_covers(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    project_id = world["project_id"]

    for day in (date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 20)):
        log(client, world, day=day, hours="4")

    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": "2026-02-01", "to_date": "2026-02-10", "month": "2026-02"},
    )

    by_date = {note.work_date: note.state for note in notes_of(client, project_id)}
    assert by_date[date(2026, 2, 2)] is TimeNoteState.SUBMITTED
    assert by_date[date(2026, 2, 3)] is TimeNoteState.SUBMITTED
    assert by_date[date(2026, 2, 20)] is TimeNoteState.DRAFT, "outside the range, untouched"


def test_submitting_nothing_says_so_rather_than_pretending(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={"from_date": INSIDE.isoformat(), "month": "2026-02"},
    )

    assert "Nothing to submit" in response.text


def test_submitting_never_touches_another_persons_drafts(
    client: TestClient, world, sign_in
) -> None:
    project_id = world["project_id"]

    sign_in(world["consultant"])
    log(client, world)

    sign_in(world["administrator"])
    log(client, world)
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": "2026-02-01", "to_date": "2026-02-28", "month": "2026-02"},
    )

    states = {note.user_id: note.state for note in notes_of(client, project_id)}
    assert states[world["administrator"].id] is TimeNoteState.SUBMITTED
    assert states[world["consultant"].id] is TimeNoteState.DRAFT


# --- the month grid -------------------------------------------------------------------


def test_the_grid_marks_the_window_weekends_and_totals(client: TestClient, world) -> None:
    with client.app.state.session_factory() as session:
        project = session.get(Project, world["project_id"])
        month = build_month(project, 2026, 1, [], date(2026, 1, 15))

    days = [day for week in month.weeks for day in week]
    by_date = {day.date: day for day in days}

    assert by_date[date(2025, 12, 31)].in_window is False
    assert by_date[date(2026, 1, 1)].in_window is True
    assert by_date[date(2026, 1, 3)].is_weekend is True
    assert by_date[date(2026, 1, 3)].loggable is True, "people do work weekends"
    assert by_date[date(2026, 1, 15)].is_today is True
    assert month.label == "January 2026"


def test_the_calendar_opens_on_a_month_the_project_covers(client: TestClient, world) -> None:
    with client.app.state.session_factory() as session:
        project = session.get(Project, world["project_id"])

    assert default_month(project, date(2020, 1, 1)) == START, "before the project: its first day"
    assert default_month(project, date(2030, 1, 1)) == END, "after it: its last day"
    assert default_month(project, INSIDE) == INSIDE, "during it: today"
