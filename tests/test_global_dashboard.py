"""The portfolio view: every project's health on one screen, and who is busy."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import metrics
from backend.app.metrics import Health, month_bounds, working_days_between
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectStatus,
    Task,
    TimeNote,
    TimeNoteState,
    User,
    utcnow,
)
from backend.app.permissions import PROJECT_VIEW_ANY
from backend.app.queue import AGEING_DAYS
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

TODAY = date.today()
MONTH_FIRST, MONTH_LAST = month_bounds(TODAY)


def flat(response) -> str:
    return " ".join(response.text.split())


def make_project(session, owner_id: int, code: str, **overrides) -> Project:
    values = {
        "name": f"Project {code}",
        "code": code,
        "owner_id": owner_id,
        "start_date": TODAY - timedelta(days=60),
        "end_date": TODAY + timedelta(days=30),
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


def add_person(session, email: str, name: str) -> User:
    person = User(
        email=email,
        full_name=name,
        password_hash=hash_password("a-properly-long-password"),
        role_id=ensure_member_role(session).id,
    )
    session.add(person)
    session.flush()
    return person


def log_hours(session, project, task, user, total: float, first_day: date, state=None) -> None:
    """Spread over consecutive weekdays; a day holds at most 24 hours."""
    remaining, day = total, first_day
    while remaining > 0:
        if day.weekday() < 5:
            note = TimeNote(
                project_id=project.id,
                task_id=task.id,
                user_id=user.id,
                work_date=day,
                duration_minutes=int(min(8, remaining) * 60),
                state=state or TimeNoteState.DRAFT,
            )
            if note.state is TimeNoteState.SUBMITTED:
                note.submitted_at = utcnow()
            session.add(note)
            remaining -= min(8, remaining)
        day += timedelta(days=1)
    session.flush()


@pytest.fixture
def portfolio(client: TestClient, administrator, settings):
    """Three projects: one healthy, one nearly out of budget, one overdue."""
    with client.app.state.session_factory() as session:
        alice = add_person(session, "alice@example.com", "Alice Consultant")
        bob = add_person(session, "bob@example.com", "Bob Consultant")

        healthy = make_project(session, administrator.id, "GREEN")
        tight = make_project(session, administrator.id, "AMBER", billable_hours_budget=20)
        late = make_project(session, administrator.id, "RED", end_date=TODAY - timedelta(days=5))

        for project, person, hours in ((healthy, alice, 16), (tight, bob, 19)):
            task = Task(project_id=project.id, name="Delivery")
            session.add(task)
            session.flush()
            log_hours(session, project, task, person, hours, MONTH_FIRST)

        session.commit()
        return {
            "healthy": healthy.id,
            "tight": tight.id,
            "late": late.id,
            "alice": alice.id,
            "bob": bob.id,
        }


def dashboard(client: TestClient) -> str:
    return flat(client.get("/dashboard"))


# --- health distribution --------------------------------------------------------------


def test_health_counts_sum_to_every_project_visible(
    client: TestClient, portfolio, administrator, settings, sign_in
) -> None:
    with client.app.state.session_factory() as session:
        projects = list(session.scalars(select(Project)).all())
        view = metrics.portfolio(session, projects, settings)

    assert view.total == 3
    assert sum(view.counts.values()) == view.total, "no project may go uncounted"
    assert view.counts[Health.GREEN] == 1
    assert view.counts[Health.AMBER] == 1
    assert view.counts[Health.RED] == 1


def test_the_counts_appear_on_the_page(
    client: TestClient, portfolio, administrator, sign_in
) -> None:
    sign_in(administrator)
    page = dashboard(client)

    assert "Project health" in page
    assert "Green" in page and "Amber" in page and "Red" in page
    assert "Total" in page


def test_a_project_lands_in_exactly_one_bucket(client: TestClient, portfolio, settings) -> None:
    """Overdue *and* over budget is still one project, counted once, in the worse bucket."""
    with client.app.state.session_factory() as session:
        person = session.get(User, portfolio["alice"])
        project = session.get(Project, portfolio["late"])
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()
        log_hours(session, project, task, person, 200, project.start_date)
        session.commit()

        projects = list(session.scalars(select(Project)).all())
        view = metrics.portfolio(session, projects, settings)

    assert sum(view.counts.values()) == view.total == 3
    assert view.counts[Health.RED] == 1


# --- deadlines ------------------------------------------------------------------------


def test_deadlines_are_nearest_first_and_skip_closed_projects(
    client: TestClient, portfolio, administrator, settings, sign_in
) -> None:
    with client.app.state.session_factory() as session:
        open_ended = make_project(session, administrator.id, "OPEN", end_date=None)
        done = make_project(session, administrator.id, "DONE", status=ProjectStatus.COMPLETED)
        session.commit()
        assert open_ended.id and done.id

        projects = list(session.scalars(select(Project)).all())
        view = metrics.portfolio(session, projects, settings)

    codes = [item.project.code for item in view.deadlines]

    assert "OPEN" not in codes, "a project with no end date has no deadline"
    assert "DONE" not in codes, "a finished project is not a deadline"
    assert (
        codes == sorted(codes, key=lambda code: {"RED": 0, "GREEN": 1, "AMBER": 1}[code])
        or codes[0] == "RED"
    ), "the nearest deadline comes first"


def test_the_deadline_table_links_to_each_project(
    client: TestClient, portfolio, administrator, sign_in
) -> None:
    sign_in(administrator)
    page = dashboard(client)

    assert "Upcoming deadlines" in page
    for project_id in (portfolio["healthy"], portfolio["tight"]):
        assert f'/projects/{project_id}"' in page, "every figure links to what explains it"


# --- ageing approvals -----------------------------------------------------------------


def test_ageing_approvals_use_the_same_threshold_as_the_queue(
    client: TestClient, portfolio, administrator, settings, sign_in
) -> None:
    with client.app.state.session_factory() as session:
        project = session.get(Project, portfolio["healthy"])
        person = session.get(User, portfolio["alice"])
        task = session.scalar(select(Task).where(Task.project_id == project.id))

        note = TimeNote(
            project_id=project.id,
            task_id=task.id,
            user_id=person.id,
            work_date=MONTH_FIRST,
            duration_minutes=480,
            state=TimeNoteState.SUBMITTED,
            submitted_at=utcnow() - timedelta(days=AGEING_DAYS + 2),
        )
        session.add(note)
        session.flush()
        session.add(
            Approval(
                time_note_id=note.id,
                approver_id=administrator.id,
                sequence=1,
                decision=ApprovalDecision.PENDING,
            )
        )
        session.commit()

    sign_in(administrator)
    page = dashboard(client)

    assert "Approvals waiting" in page
    assert f"{AGEING_DAYS + 2}d" in page
    assert f"/approvals?project_id={portfolio['healthy']}" in page, (
        "an ageing queue must link to the queue that clears it"
    )


def test_nothing_waiting_says_so(client: TestClient, portfolio, administrator, sign_in) -> None:
    sign_in(administrator)
    assert "Nothing is waiting on an approver." in dashboard(client)


# --- utilisation ----------------------------------------------------------------------


def test_utilisation_is_hours_against_working_days(client: TestClient, portfolio, settings) -> None:
    """Alice logged 16h this month; available is the month's weekdays x 8."""
    available = working_days_between(MONTH_FIRST, MONTH_LAST) * 8

    with client.app.state.session_factory() as session:
        rows = metrics.utilisation_between(session, MONTH_FIRST, MONTH_LAST)

    by_name = {row.person.full_name: row for row in rows}
    assert by_name["Alice Consultant"].hours.logged == 16.0
    assert by_name["Alice Consultant"].available_hours == available
    assert by_name["Alice Consultant"].ratio == round(16 / available, 4)


def test_utilisation_counts_across_every_project(client: TestClient, portfolio, settings) -> None:
    with client.app.state.session_factory() as session:
        alice = session.get(User, portfolio["alice"])
        second = session.get(Project, portfolio["tight"])
        task = session.scalar(select(Task).where(Task.project_id == second.id))
        log_hours(session, second, task, alice, 8, MONTH_FIRST)
        session.commit()

        rows = metrics.utilisation_between(session, MONTH_FIRST, MONTH_LAST)

    by_name = {row.person.full_name: row for row in rows}
    assert by_name["Alice Consultant"].hours.logged == 24.0, "16h on one project plus 8h on another"


def test_utilisation_ignores_time_outside_the_period(
    client: TestClient, portfolio, settings
) -> None:
    with client.app.state.session_factory() as session:
        alice = session.get(User, portfolio["alice"])
        project = session.get(Project, portfolio["healthy"])
        task = session.scalar(select(Task).where(Task.project_id == project.id))
        log_hours(session, project, task, alice, 8, MONTH_FIRST - timedelta(days=20))
        session.commit()

        rows = metrics.utilisation_between(session, MONTH_FIRST, MONTH_LAST)

    by_name = {row.person.full_name: row for row in rows}
    assert by_name["Alice Consultant"].hours.logged == 16.0, "last month is not this month"


def test_working_days_exclude_weekends() -> None:
    monday = date(2026, 2, 2)
    assert working_days_between(monday, monday + timedelta(days=4)) == 5
    assert working_days_between(monday, monday + timedelta(days=6)) == 5
    assert working_days_between(monday, monday + timedelta(days=13)) == 10
    assert working_days_between(monday + timedelta(days=1), monday) == 0


def test_utilisation_appears_on_the_page(
    client: TestClient, portfolio, administrator, sign_in
) -> None:
    sign_in(administrator)
    page = dashboard(client)

    assert "Utilisation" in page
    assert "Alice Consultant" in page
    assert "public holidays are not modelled" in page, (
        "say what the number does not account for, rather than implying precision"
    )


# --- access ---------------------------------------------------------------------------


def test_someone_without_view_any_cannot_reach_the_dashboard(
    client: TestClient, portfolio, make_person, sign_in
) -> None:
    consultant = make_person("nobody@example.com", "No Body")
    sign_in(consultant)

    assert client.get("/dashboard").status_code == 404, (
        "how many projects exist is itself information"
    )


def test_the_nav_never_offers_a_link_that_404s(
    client: TestClient, portfolio, make_person, sign_in
) -> None:
    consultant = make_person("navcheck@example.com", "Nav Check")
    sign_in(consultant)
    page = flat(client.get("/"))

    assert '/dashboard"' not in page, "a consultant is not shown a door they cannot open"
    assert client.get("/").status_code == 200, "and their own overview still works"


def test_a_portfolio_viewer_lands_on_the_dashboard(
    client: TestClient, portfolio, administrator, sign_in
) -> None:
    sign_in(administrator)
    landing = client.get("/", follow_redirects=False)

    assert landing.status_code == 303
    assert landing.headers["location"] == "/dashboard"


def test_a_consultant_keeps_their_own_overview(
    client: TestClient, portfolio, make_person, sign_in
) -> None:
    sign_in(make_person("stays@example.com", "Stays Put"))
    landing = client.get("/", follow_redirects=False)

    assert landing.status_code == 200


def test_the_dashboard_requires_a_session(client: TestClient) -> None:
    client.cookies.clear()
    response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_a_custom_role_with_view_any_may_see_it(
    client: TestClient, portfolio, administrator, make_person, sign_in
) -> None:
    """It is the permission that opens this door, not being an administrator."""
    from backend.app.models import Role
    from backend.app.seed import ensure_permissions

    reviewer = make_person("reviewer@example.com", "Rae Reviewer")
    with client.app.state.session_factory() as session:
        catalogue = ensure_permissions(session)
        role = Role(name="finance reviewer", description="Reads every project.")
        role.permissions.append(catalogue[PROJECT_VIEW_ANY])
        session.add(role)
        session.flush()
        session.get(User, reviewer.id).role_id = role.id
        session.commit()

    sign_in(reviewer)
    assert client.get("/dashboard").status_code == 200


# --- empty and at scale ---------------------------------------------------------------


def test_an_empty_portfolio_says_so(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    page = dashboard(client)

    assert "No projects yet" in page
    assert "/projects/new" in page


def test_fifty_projects_with_a_year_of_entries_stay_quick(
    client: TestClient, administrator, settings, sign_in
) -> None:
    with client.app.state.session_factory() as session:
        people = [add_person(session, f"s{i}@example.com", f"S {i}") for i in range(5)]
        for index in range(50):
            # A year of entries needs a project window a year wide; the work_date invariant
            # from #15 refuses anything else, and rightly.
            project = make_project(
                session,
                administrator.id,
                f"S{index:03d}",
                start_date=TODAY - timedelta(days=400),
            )
            task = Task(project_id=project.id, name="Delivery")
            session.add(task)
            session.flush()
            for week in range(52):
                session.add(
                    TimeNote(
                        project_id=project.id,
                        task_id=task.id,
                        user_id=people[week % 5].id,
                        work_date=TODAY - timedelta(days=week * 7),
                        duration_minutes=480,
                        state=TimeNoteState.APPROVED,
                    )
                )
        session.commit()

    sign_in(administrator)
    started = time.perf_counter()
    response = client.get("/dashboard")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    print(f"\n  dashboard rendered 50 projects x 52 weeks in {elapsed_ms:.0f}ms")
    assert elapsed_ms < 300, f"the dashboard took {elapsed_ms:.0f}ms across 50 projects"
