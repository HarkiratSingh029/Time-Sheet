"""The metrics engine.

Every figure is checked against a fixture worked out by hand, because a metric that agrees
with its own implementation proves nothing. The numbers below were calculated on paper
first and are stated in the docstrings.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import metrics
from backend.app.config import Settings
from backend.app.metrics import Health, for_project
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectMember,
    ProjectStatus,
    Task,
    TimeNote,
    TimeNoteState,
    User,
    utcnow,
)
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

START = date(2026, 1, 1)
END = date(2026, 12, 31)
TODAY = date(2026, 3, 1)


@pytest.fixture
def factory(client: TestClient):
    return client.app.state.session_factory


def make_project(session, owner_id: int, **overrides) -> Project:
    values = {
        "name": "Ledger migration",
        "code": f"P{overrides.pop('n', 1)}",
        "owner_id": owner_id,
        "start_date": START,
        "end_date": END,
        "status": ProjectStatus.ACTIVE,
        "billable_hours_budget": 100,
        "cost": 50_000,
        "required_approvals": 1,
    }
    values.update(overrides)
    project = Project(**values)
    session.add(project)
    session.flush()
    return project


def make_person(session, email: str) -> User:
    person = User(
        email=email,
        full_name=email.split("@")[0].title(),
        password_hash=hash_password("a-properly-long-password"),
        role_id=ensure_member_role(session).id,
    )
    session.add(person)
    session.flush()
    return person


def log(
    session,
    project: Project,
    task: Task,
    user: User,
    day: date,
    hours: float,
    state: TimeNoteState = TimeNoteState.DRAFT,
    submitted_days_ago: int | None = None,
) -> TimeNote:
    note = TimeNote(
        project_id=project.id,
        task_id=task.id,
        user_id=user.id,
        work_date=day,
        duration_minutes=int(hours * 60),
        state=state,
    )
    if submitted_days_ago is not None:
        note.submitted_at = utcnow() - timedelta(days=submitted_days_ago)
    session.add(note)
    session.flush()
    return note


def log_total(
    session,
    project: Project,
    task: Task,
    user: User,
    total_hours: float,
    first_day: date = date(2026, 2, 2),
    state: TimeNoteState = TimeNoteState.DRAFT,
) -> None:
    """Spread hours over consecutive days.

    A day holds at most 24 hours — the CHECK constraint from #15 — so a fixture wanting
    120 hours has to mean twelve ten-hour days, which is also what it would mean in life.
    """
    remaining, day = total_hours, first_day
    while remaining > 0:
        today_hours = min(8, remaining)
        log(session, project, task, user, day, today_hours, state)
        remaining -= today_hours
        day += timedelta(days=1)


# --- the hand-worked fixture ----------------------------------------------------------


@pytest.fixture
def worked_example(factory, administrator, settings):
    """A project whose every figure is calculable on paper.

    Budget 100 hours, cost 50,000, running 1 Jan to 31 Dec 2026, today 1 March.

        Priya  billable   8h  draft
        Priya  billable  16h  approved
        Sam    billable   4h  submitted   (waiting 10 days)
        Sam    non-bill   2h  approved
        Priya  billable   1h  rejected

    logged  = 8 + 16 + 4 + 2 + 1        = 31h
    approved= 16 + 2                    = 18h
    billable= 8 + 16 + 4 + 1            = 29h
    budget  = 31 / 100                  = 0.31
    schedule= (1 Mar - 1 Jan) = 59 days / 364 planned = 0.1621
    burn    = 50,000 x 0.31             = 15,500
    """
    with factory() as session:
        priya = make_person(session, "priya@example.com")
        sam = make_person(session, "sam@example.com")
        project = make_project(session, administrator.id)

        billable = Task(project_id=project.id, name="Delivery", is_billable=True)
        overhead = Task(project_id=project.id, name="Admin", is_billable=False)
        session.add_all([billable, overhead])
        session.flush()

        log(session, project, billable, priya, date(2026, 2, 2), 8, TimeNoteState.DRAFT)
        log(session, project, billable, priya, date(2026, 2, 3), 16, TimeNoteState.APPROVED)
        log(
            session,
            project,
            billable,
            sam,
            date(2026, 2, 4),
            4,
            TimeNoteState.SUBMITTED,
            submitted_days_ago=10,
        )
        log(session, project, overhead, sam, date(2026, 2, 5), 2, TimeNoteState.APPROVED)
        log(session, project, billable, priya, date(2026, 2, 6), 1, TimeNoteState.REJECTED)
        session.commit()
        return project.id


def test_every_figure_matches_the_hand_worked_example(factory, worked_example, settings) -> None:
    with factory() as session:
        project = session.get(Project, worked_example)
        result = for_project(session, project, settings, TODAY)

    assert result.hours.draft == 8.0
    assert result.hours.approved == 18.0
    assert result.hours.submitted == 4.0
    assert result.hours.rejected == 1.0
    assert result.hours.billable == 29.0
    assert result.hours.non_billable == 2.0
    assert result.hours.logged == 31.0

    assert result.consultants == 2
    assert result.budget_hours == 100
    assert result.budget_ratio == 0.31
    assert result.days_elapsed == 59
    assert result.days_planned == 364
    assert result.schedule_ratio == pytest.approx(0.1621, abs=0.0001)
    assert result.cost == 50_000
    assert result.cost_burn == 15_500.0


# --- approved and unapproved stay apart -----------------------------------------------


def test_approved_and_unapproved_hours_are_never_merged(factory, worked_example, settings) -> None:
    with factory() as session:
        project = session.get(Project, worked_example)
        result = for_project(session, project, settings, TODAY)

    assert result.hours.approved == 18.0
    assert result.hours.awaiting_decision == 4.0
    assert result.hours.approved != result.hours.logged, (
        "a figure that mixes signed work with work under review looks authoritative and is not"
    )


def test_no_public_function_returns_their_sum_alone() -> None:
    """`logged` is everything recorded, and is documented as not standing in for `approved`."""
    from backend.app.metrics import Hours

    hours = Hours(draft=1, submitted=2, approved=4, rejected=8)
    assert hours.logged == 15
    assert not any(
        name for name in dir(Hours) if "approved_and" in name or name == "total_approved"
    )


# --- health is derived ----------------------------------------------------------------


def test_a_healthy_project_is_green(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=2)
        session.commit()
        result = for_project(session, project, settings, TODAY)

    assert result.health is Health.GREEN
    assert result.health_reasons == ()


@pytest.mark.parametrize(
    ("budget", "logged_hours", "expected"),
    [(100, 50, Health.GREEN), (100, 92, Health.AMBER), (100, 120, Health.RED)],
)
def test_budget_drives_health(
    factory, administrator, settings, budget: int, logged_hours: float, expected: Health
) -> None:
    with factory() as session:
        person = make_person(session, f"b{logged_hours}@example.com")
        project = make_project(session, administrator.id, n=3, billable_hours_budget=budget)
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_total(session, project, task, person, logged_hours)
        session.commit()

        assert for_project(session, project, settings, TODAY).health is expected


def test_being_past_the_end_date_is_red(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=4, end_date=date(2026, 2, 1))
        session.commit()
        result = for_project(session, project, settings, TODAY)

    assert result.health is Health.RED
    assert result.is_overdue
    assert "Past the end date" in " ".join(result.health_reasons)


@pytest.mark.parametrize(
    ("waiting_days", "expected"),
    [(3, Health.GREEN), (8, Health.AMBER), (20, Health.RED)],
)
def test_ageing_approvals_drive_health(
    factory, administrator, settings, waiting_days: int, expected: Health
) -> None:
    with factory() as session:
        person = make_person(session, f"w{waiting_days}@example.com")
        project = make_project(session, administrator.id, n=5, billable_hours_budget=None)
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        note = log(
            session,
            project,
            task,
            person,
            date(2026, 2, 2),
            8,
            TimeNoteState.SUBMITTED,
            submitted_days_ago=waiting_days,
        )
        session.add(
            Approval(
                time_note_id=note.id,
                approver_id=administrator.id,
                sequence=1,
                decision=ApprovalDecision.PENDING,
            )
        )
        session.commit()

        assert for_project(session, project, settings, date.today()).health is expected


def test_health_cannot_be_set(factory, worked_example, settings) -> None:
    with factory() as session:
        project = session.get(Project, worked_example)
        result = for_project(session, project, settings, TODAY)

    with pytest.raises((AttributeError, TypeError)):
        result.health = Health.GREEN  # type: ignore[misc]

    assert not hasattr(Project, "health"), "health is derived; storing it invites the two to differ"


def test_a_closed_project_is_green_whatever_its_numbers(factory, administrator, settings) -> None:
    with factory() as session:
        person = make_person(session, "closed@example.com")
        project = make_project(
            session,
            administrator.id,
            n=6,
            end_date=date(2026, 2, 1),
            status=ProjectStatus.COMPLETED,
            billable_hours_budget=10,
        )
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_total(session, project, task, person, 80, date(2026, 1, 5))
        session.commit()

        result = for_project(session, project, settings, TODAY)

    assert result.health is Health.GREEN, "a finished project is not failing"
    assert result.health_reasons == ("Project is closed.",)


def test_the_thresholds_are_configurable_but_the_meaning_is_not(
    factory, administrator, settings: Settings
) -> None:
    strict = settings.model_copy(update={"health_amber_ratio": 0.5})

    with factory() as session:
        person = make_person(session, "threshold@example.com")
        project = make_project(session, administrator.id, n=7, billable_hours_budget=100)
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_total(session, project, task, person, 60)
        session.commit()

        assert for_project(session, project, settings, TODAY).health is Health.GREEN
        assert for_project(session, project, strict, TODAY).health is Health.AMBER


def test_health_always_explains_itself(factory, administrator, settings) -> None:
    with factory() as session:
        person = make_person(session, "reasons@example.com")
        project = make_project(session, administrator.id, n=8, billable_hours_budget=10)
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_total(session, project, task, person, 12)
        session.commit()

        result = for_project(session, project, settings, TODAY)

    assert result.health is Health.RED
    assert result.health_reasons, "a colour nobody can explain is not a signal"
    assert "120%" in " ".join(result.health_reasons)


# --- the awkward edges ----------------------------------------------------------------


def test_a_project_with_no_time_returns_zeroes(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=9)
        session.commit()
        result = for_project(session, project, settings, TODAY)

    assert result.hours.logged == 0.0
    assert result.hours.approved == 0.0
    assert result.consultants == 0
    assert result.budget_ratio == 0.0
    assert result.cost_burn == 0.0
    assert result.oldest_pending_days == 0
    assert result.health is Health.GREEN


def test_an_open_ended_project_is_never_overdue(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=10, end_date=None)
        session.commit()
        result = for_project(session, project, settings, date(2030, 1, 1))

    assert result.schedule_ratio is None
    assert result.days_planned is None
    assert result.is_overdue is False
    assert result.health is Health.GREEN, "no end date means no schedule to be behind"


def test_a_project_without_a_budget_has_no_budget_ratio(factory, administrator, settings) -> None:
    with factory() as session:
        person = make_person(session, "nobudget@example.com")
        project = make_project(
            session, administrator.id, n=11, billable_hours_budget=None, cost=None
        )
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_total(session, project, task, person, 500)
        session.commit()

        result = for_project(session, project, settings, TODAY)

    assert result.budget_ratio is None
    assert result.cost_burn is None
    assert result.is_over_budget is False
    assert result.health is Health.GREEN, "you cannot be over a budget nobody set"


def test_a_zero_budget_does_not_divide_by_zero(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=12, billable_hours_budget=0)
        session.commit()
        result = for_project(session, project, settings, TODAY)

    assert result.budget_ratio is None


def test_a_project_starting_today_has_not_elapsed(factory, administrator, settings) -> None:
    with factory() as session:
        project = make_project(session, administrator.id, n=13, start_date=TODAY)
        session.commit()
        result = for_project(session, project, settings, TODAY)

    assert result.days_elapsed == 0
    assert result.schedule_ratio == 0.0


def test_metrics_count_only_their_own_project(factory, administrator, settings) -> None:
    with factory() as session:
        person = make_person(session, "shared@example.com")
        first = make_project(session, administrator.id, n=14)
        second = make_project(session, administrator.id, n=15)
        for project, hours in ((first, 10), (second, 40)):
            task = Task(project_id=project.id, name="Delivery")
            session.add(task)
            session.flush()
            log_total(session, project, task, person, hours)
        session.commit()

        assert for_project(session, first, settings, TODAY).hours.logged == 10.0
        assert for_project(session, second, settings, TODAY).hours.logged == 40.0


# --- scale ----------------------------------------------------------------------------


def test_fifty_projects_with_a_year_of_entries_stay_quick(factory, administrator, settings) -> None:
    with factory() as session:
        people = [make_person(session, f"scale{index}@example.com") for index in range(5)]
        projects = []
        for index in range(50):
            project = make_project(session, administrator.id, n=100 + index)
            task = Task(project_id=project.id, name="Delivery")
            session.add(task)
            session.flush()
            for offset in range(52):
                log(
                    session,
                    project,
                    task,
                    people[offset % len(people)],
                    START + timedelta(days=offset * 7),
                    8,
                )
            projects.append(project)
        session.add_all(
            ProjectMember(project_id=project.id, user_id=people[0].id) for project in projects
        )
        session.commit()

        started = time.perf_counter()
        results = metrics.for_projects(session, projects, settings, TODAY)
        elapsed_ms = (time.perf_counter() - started) * 1000

    assert len(results) == 50
    assert all(result.hours.logged == 416.0 for result in results)
    assert elapsed_ms < 200, f"metrics for 50 projects took {elapsed_ms:.0f}ms"
