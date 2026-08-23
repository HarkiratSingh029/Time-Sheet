"""The week grid and the two bulk moves that fill it.

The rule that matters most: the week view must enforce exactly what the month view does.
A second entry path would be a second set of bugs, so several tests here assert the two
behave identically rather than merely that the week view behaves reasonably.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.calendar_week import build_week, week_start
from backend.app.models import Project, Task, TimeNote, TimeNoteState
from backend.app.routes.timesheet import EntryError, _minutes_from_hours

MONDAY = date(2026, 2, 2)
FRIDAY = MONDAY + timedelta(days=4)
SATURDAY = MONDAY + timedelta(days=5)


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    consultant = make_person("consultant@example.com", "Priya Consultant")
    approver = make_person("approver@example.com", "Ade Approver")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": "2026-02-02",
            "end_date": "2026-02-27",
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(approver.id), "is_approver": "true", "approval_order": "1"},
    )
    for name in ("Discovery", "Delivery"):
        client.post(f"/projects/{project_id}/tasks", data={"name": name, "is_billable": "true"})

    with client.app.state.session_factory() as session:
        tasks = session.scalars(select(Task).where(Task.project_id == project_id)).all()
        ids = {task.name: task.id for task in tasks}

    return {
        "project_id": project_id,
        "tasks": ids,
        "administrator": administrator,
        "consultant": consultant,
        "approver": approver,
    }


def cell(client: TestClient, world, day: date, task: str, hours: str, week: date = MONDAY):
    return client.post(
        f"/projects/{world['project_id']}/week/cells",
        data={
            "work_date": day.isoformat(),
            "task_id": str(world["tasks"][task]),
            "hours": hours,
            "week": week.isoformat(),
        },
    )


def notes(client: TestClient, world) -> list[TimeNote]:
    with client.app.state.session_factory() as session:
        return list(
            session.scalars(
                select(TimeNote)
                .where(TimeNote.project_id == world["project_id"])
                .order_by(TimeNote.work_date, TimeNote.task_id)
            ).all()
        )


def flat(response) -> str:
    return " ".join(response.text.split())


# --- the grid -------------------------------------------------------------------------


def test_the_week_shows_tasks_down_and_days_across(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    page = flat(client.get(f"/projects/{world['project_id']}/week?week={MONDAY}"))

    assert "02 – 08 February 2026" in page
    for name in ("Discovery", "Delivery"):
        assert name in page
    for weekday in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert weekday in page


def test_a_cell_writes_a_draft_in_minutes(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "7.5")

    logged = notes(client, world)
    assert len(logged) == 1
    assert logged[0].duration_minutes == 450
    assert logged[0].state is TimeNoteState.DRAFT
    assert logged[0].user_id == world["consultant"].id


def test_clearing_a_cell_removes_the_entry(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    assert len(notes(client, world)) == 1

    cell(client, world, MONDAY, "Discovery", "")
    assert notes(client, world) == []


def test_editing_a_cell_replaces_rather_than_duplicates(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    cell(client, world, MONDAY, "Discovery", "6")

    logged = notes(client, world)
    assert len(logged) == 1
    assert logged[0].duration_minutes == 360


def test_the_grid_totals_by_row_day_and_week(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    cell(client, world, MONDAY, "Delivery", "2")
    cell(client, world, FRIDAY, "Discovery", "4")

    with client.app.state.session_factory() as session:
        project = session.get(Project, world["project_id"])
        tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all()
        logged = session.scalars(select(TimeNote)).all()
        week = build_week(project, MONDAY, list(tasks), list(logged), MONDAY)

    assert week.total_minutes == 14 * 60
    assert week.daily_minutes(MONDAY) == 10 * 60
    assert week.daily_minutes(FRIDAY) == 4 * 60


# --- the same rules as the month view -------------------------------------------------


@pytest.mark.parametrize("hours", ["0", "-3", "25", "banana"])
def test_the_week_refuses_exactly_what_the_month_refuses(
    client: TestClient, world, sign_in, hours: str
) -> None:
    sign_in(world["consultant"])

    via_week = cell(client, world, MONDAY, "Discovery", hours)
    via_month = client.post(
        f"/projects/{world['project_id']}/notes",
        data={
            "work_date": MONDAY.isoformat(),
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": hours,
            "detail": "",
            "month": "2026-02",
        },
    )

    assert notes(client, world) == [], "neither view may write an impossible duration"

    # The same helper rejects both, so the message must be identical — comparing pages for
    # a loose keyword would pass on unrelated copy that happens to contain it.
    with pytest.raises(EntryError) as refusal:
        _minutes_from_hours(hours)
    assert str(refusal.value) in via_week.text
    assert str(refusal.value) in via_month.text


def test_a_day_outside_the_project_cannot_be_written_from_the_grid(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    outside = date(2026, 3, 2)  # the project ends 27 February

    response = cell(client, world, outside, "Discovery", "8", week=outside)
    assert notes(client, world) == []
    assert "project" in response.text.lower()


def test_a_submitted_cell_is_frozen_in_the_grid_too(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={"from_date": MONDAY.isoformat(), "month": "2026-02"},
    )

    response = cell(client, world, MONDAY, "Discovery", "2")
    assert "no longer editable" in response.text
    assert notes(client, world)[0].duration_minutes == 480

    cleared = cell(client, world, MONDAY, "Discovery", "")
    assert "no longer editable" in cleared.text
    assert len(notes(client, world)) == 1


def test_nobody_writes_into_another_persons_grid(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")

    sign_in(world["approver"])
    cell(client, world, MONDAY, "Discovery", "1")

    logged = notes(client, world)
    assert len(logged) == 2, "the approver wrote their own entry, not over Priya's"
    assert {note.user_id for note in logged} == {
        world["consultant"].id,
        world["approver"].id,
    }


# --- copy last week -------------------------------------------------------------------


def test_copy_last_week_reproduces_drafts_shifted_seven_days(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    cell(client, world, FRIDAY, "Delivery", "4")

    next_monday = MONDAY + timedelta(days=7)
    response = client.post(
        f"/projects/{world['project_id']}/week/copy-last-week",
        data={"week": next_monday.isoformat()},
    )
    assert "Created 2 entries" in response.text

    by_date = {note.work_date: note for note in notes(client, world)}
    assert by_date[next_monday].duration_minutes == 480
    assert by_date[FRIDAY + timedelta(days=7)].duration_minutes == 240


def test_copy_last_week_never_overwrites_an_existing_entry(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")

    next_monday = MONDAY + timedelta(days=7)
    cell(client, world, next_monday, "Delivery", "3", week=next_monday)

    response = client.post(
        f"/projects/{world['project_id']}/week/copy-last-week",
        data={"week": next_monday.isoformat()},
    )
    assert "already had time logged" in response.text

    by_date = {note.work_date: note for note in notes(client, world)}
    assert by_date[next_monday].duration_minutes == 180, "the day that was there is untouched"


def test_copy_last_week_ignores_submitted_and_approved_days(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Discovery", "8")
    cell(client, world, FRIDAY, "Delivery", "4")
    client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={"from_date": MONDAY.isoformat(), "month": "2026-02"},
    )

    next_monday = MONDAY + timedelta(days=7)
    response = client.post(
        f"/projects/{world['project_id']}/week/copy-last-week",
        data={"week": next_monday.isoformat()},
    )

    assert "Created 1 entry" in response.text, "only the remaining draft was copied"
    copied = [note for note in notes(client, world) if note.work_date > FRIDAY]
    assert len(copied) == 1
    assert copied[0].work_date == FRIDAY + timedelta(days=7)


def test_copy_last_week_cannot_push_a_day_outside_the_project(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    last_week = date(2026, 2, 23)  # the project ends Friday 27 February
    cell(client, world, last_week, "Discovery", "8", week=last_week)

    beyond = last_week + timedelta(days=7)
    response = client.post(
        f"/projects/{world['project_id']}/week/copy-last-week",
        data={"week": beyond.isoformat()},
    )

    assert "fell outside the project" in response.text
    assert all(note.work_date <= date(2026, 2, 27) for note in notes(client, world))


def test_copying_an_empty_week_says_so(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/week/copy-last-week",
        data={"week": (MONDAY + timedelta(days=7)).isoformat()},
    )
    assert "no drafts in the previous week" in response.text


# --- fill a range ---------------------------------------------------------------------


def test_fill_creates_a_day_for_each_working_day(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "7.5",
            "from_date": MONDAY.isoformat(),
            "to_date": (MONDAY + timedelta(days=6)).isoformat(),
            "week": MONDAY.isoformat(),
        },
    )

    assert "Created 5 entries" in response.text, "weekends are skipped unless asked for"
    logged = notes(client, world)
    assert len(logged) == 5
    assert all(note.duration_minutes == 450 for note in logged)
    assert all(note.work_date.weekday() < 5 for note in logged)


def test_fill_can_include_weekends_when_asked(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "4",
            "from_date": SATURDAY.isoformat(),
            "to_date": SATURDAY.isoformat(),
            "include_weekends": "true",
            "week": MONDAY.isoformat(),
        },
    )
    assert [note.work_date for note in notes(client, world)] == [SATURDAY]


def test_fill_skips_days_that_already_have_time_and_reports_it(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    cell(client, world, MONDAY, "Delivery", "3")

    response = client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "8",
            "from_date": MONDAY.isoformat(),
            "to_date": FRIDAY.isoformat(),
            "week": MONDAY.isoformat(),
        },
    )

    assert "Created 4 entries" in response.text
    assert "1 day already had time logged" in response.text

    by_date = {note.work_date: note for note in notes(client, world)}
    assert by_date[MONDAY].duration_minutes == 180, "the existing entry stands"


def test_fill_cannot_place_a_day_outside_the_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "8",
            "from_date": "2026-02-23",
            "to_date": "2026-03-06",
            "week": "2026-02-23",
        },
    )

    assert "fell outside the project" in response.text
    assert all(note.work_date <= date(2026, 2, 27) for note in notes(client, world))


def test_fill_refuses_an_impossible_duration(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "0",
            "from_date": MONDAY.isoformat(),
            "to_date": FRIDAY.isoformat(),
            "week": MONDAY.isoformat(),
        },
    )
    assert "more than zero" in response.text
    assert notes(client, world) == []


def test_fill_refuses_a_backwards_range(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "8",
            "from_date": FRIDAY.isoformat(),
            "to_date": MONDAY.isoformat(),
            "week": MONDAY.isoformat(),
        },
    )
    assert "ends before it starts" in response.text
    assert notes(client, world) == []


# --- submitting and remembering the view ----------------------------------------------


def test_the_week_submits_in_one_action(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    client.post(
        f"/projects/{world['project_id']}/week/fill",
        data={
            "task_id": str(world["tasks"]["Discovery"]),
            "hours": "8",
            "from_date": MONDAY.isoformat(),
            "to_date": FRIDAY.isoformat(),
            "week": MONDAY.isoformat(),
        },
    )

    client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={
            "from_date": MONDAY.isoformat(),
            "to_date": (MONDAY + timedelta(days=6)).isoformat(),
            "month": "2026-02",
        },
    )

    assert all(note.state is TimeNoteState.SUBMITTED for note in notes(client, world))


def test_the_view_choice_is_remembered(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    project_id = world["project_id"]

    client.get(f"/projects/{project_id}/week?week={MONDAY}")
    assert client.cookies.get("ts_calendar_view") == "week"

    client.get(f"/projects/{project_id}/calendar?month=2026-02")
    assert client.cookies.get("ts_calendar_view") == "month"


def test_each_view_links_to_the_other(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    project_id = world["project_id"]

    week_page = client.get(f"/projects/{project_id}/week?week={MONDAY}").text
    assert f"/projects/{project_id}/calendar" in week_page

    month_page = client.get(f"/projects/{project_id}/calendar?month=2026-02").text
    assert f"/projects/{project_id}/week" in month_page


def test_an_outsider_cannot_open_the_week(client: TestClient, world, make_person, sign_in) -> None:
    outsider = make_person("outsider@example.com", "Ozzy Outsider")
    sign_in(outsider)

    assert client.get(f"/projects/{world['project_id']}/week").status_code == 404
    assert cell(client, world, MONDAY, "Discovery", "8").status_code == 404


def test_week_start_is_always_monday() -> None:
    for offset in range(7):
        assert week_start(MONDAY + timedelta(days=offset)) == MONDAY
