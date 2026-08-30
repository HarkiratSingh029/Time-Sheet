"""Search.

The rule that shapes everything: **search must never become the way to see a project you
cannot open.** A non-member searching an exact project code should get nothing — not the
name, not a count, not the fact that it exists. Most of what follows is that rule, checked
from several angles.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import search as finder
from backend.app.models import Project, Task, TimeNote, TimeNoteState, User
from backend.app.queue import Filters
from backend.app.search import MIN_QUERY_LENGTH, escape_like
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

TODAY = date.today()
START = TODAY - timedelta(days=60)
DAY = TODAY - timedelta(days=5)


def flat(response) -> str:
    return " ".join(response.text.split())


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    """Alice is on LEDGER. Bob is on PORTAL. Neither can see the other's project."""
    alice = make_person("alice@example.com", "Alice Consultant")
    bob = make_person("bob@example.com", "Bob Bystander")

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
                "cost": "",
                "billable_hours_budget": "",
                "proposed_duration_days": "",
                "start_date": START.isoformat(),
                "end_date": (TODAY + timedelta(days=60)).isoformat(),
                "required_approvals": "1",
            },
        )
        with client.app.state.session_factory() as session:
            project_id = session.scalar(select(Project.id).where(Project.code == code.upper()))
        client.post(f"/projects/{project_id}/tasks", data={"name": "Delivery"})
        with client.app.state.session_factory() as session:
            task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        projects[code] = {"id": project_id, "task_id": task_id}

    client.post(f"/projects/{projects['ledger']['id']}/members", data={"user_id": str(alice.id)})
    client.post(f"/projects/{projects['portal']['id']}/members", data={"user_id": str(bob.id)})

    return {"projects": projects, "administrator": administrator, "alice": alice, "bob": bob}


def log(client: TestClient, world, code: str, person, detail: str, day: date = DAY) -> int:
    project = world["projects"][code]
    with client.app.state.session_factory() as session:
        note = TimeNote(
            project_id=project["id"],
            task_id=project["task_id"],
            user_id=person.id,
            work_date=day,
            duration_minutes=480,
            detail=detail,
            state=TimeNoteState.DRAFT,
        )
        session.add(note)
        session.commit()
        return note.id


def search(client: TestClient, term: str, extra: str = "") -> str:
    return flat(client.get(f"/search?q={term}{extra}"))


def results_of(client: TestClient, term: str, extra: str = "") -> str:
    """Only the results, not the filter dropdowns — which list every project the searcher
    can already reach, on purpose, so they can switch filters without clearing one first."""
    page = search(client, term, extra)
    marker = "data-search-results"
    return page[page.index(marker) :] if marker in page else ""


# --- it finds things ------------------------------------------------------------------


def test_a_project_code_finds_the_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    page = search(client, "LEDGER")

    assert "Ledger migration" in page
    assert f'/projects/{world["projects"]["ledger"]["id"]}"' in page


def test_a_project_name_finds_the_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    assert "LEDGER" in search(client, "migration")


def test_a_persons_name_finds_the_person(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Some work")
    sign_in(world["administrator"])
    page = search(client, "Alice")

    assert "alice@example.com" in page
    assert "People" in page


def test_a_word_from_a_time_note_finds_the_note(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Reconciled the opening balances")
    sign_in(world["alice"])
    page = search(client, "balances")

    assert "Time notes" in page
    assert "Reconciled the opening balances" in page


def test_a_task_name_finds_its_notes(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Nothing distinctive here")
    sign_in(world["alice"])

    assert "Nothing distinctive here" in search(client, "Delivery")


def test_search_is_case_insensitive(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    assert "Ledger migration" in search(client, "lEdGeR")


def test_a_note_links_to_its_history(client: TestClient, world, sign_in) -> None:
    note_id = log(client, world, "ledger", world["alice"], "Traceable work")
    sign_in(world["alice"])
    page = search(client, "Traceable")

    assert f"/projects/{world['projects']['ledger']['id']}/notes/{note_id}/history" in page


# --- it never widens what you can see -------------------------------------------------


def test_a_non_member_searching_an_exact_code_gets_nothing(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["bob"])
    page = search(client, "LEDGER")

    assert "Ledger migration" not in page, "not the name"
    assert "Nothing matched" in page, "not a count, and not the fact it exists"
    assert f'/projects/{world["projects"]["ledger"]["id"]}"' not in page


def test_a_non_member_cannot_find_another_projects_time_notes(
    client: TestClient, world, sign_in
) -> None:
    log(client, world, "ledger", world["alice"], "Confidential reconciliation")

    sign_in(world["bob"])
    assert "Confidential reconciliation" not in search(client, "reconciliation")


def test_a_filter_is_not_a_way_past_the_scoping(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Confidential reconciliation")

    sign_in(world["bob"])
    page = search(client, "reconciliation", f"&project_id={world['projects']['ledger']['id']}")

    assert "Confidential reconciliation" not in page


def test_a_consultant_does_not_get_a_staff_directory(
    client: TestClient, world, make_person, sign_in
) -> None:
    """People are searchable by those who could already meet them, not by everyone."""
    make_person("stranger@example.com", "Stranger Danger")

    sign_in(world["alice"])
    page = search(client, "Stranger")

    assert "stranger@example.com" not in page, (
        "a search box must not quietly become a company directory"
    )


def test_an_administrator_can_find_anybody(client: TestClient, world, make_person, sign_in) -> None:
    make_person("stranger@example.com", "Stranger Danger")

    sign_in(world["administrator"])
    assert "stranger@example.com" in search(client, "Stranger")


def test_people_you_share_a_project_with_are_findable(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    assert "alice@example.com" in search(client, "Alice"), "you can always find yourself"


def test_search_requires_a_session(client: TestClient, world) -> None:
    client.cookies.clear()
    response = client.get("/search?q=ledger", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- a user typed this ----------------------------------------------------------------


@pytest.mark.parametrize("wildcard", ["%", "_", "%%", "a%b", "_x_"])
def test_sql_wildcards_are_escaped_rather_than_matching_everything(
    client: TestClient, world, sign_in, wildcard: str
) -> None:
    log(client, world, "ledger", world["alice"], "Ordinary work")

    sign_in(world["alice"])
    page = search(client, wildcard.replace("%", "%25").replace("_", "%5F"))

    assert "Ordinary work" not in page, (
        "an unescaped wildcard would match every row, which is a way to dump the table"
    )


def test_escape_like_makes_text_mean_itself() -> None:
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("back\\slash") == "back\\\\slash"
    assert escape_like("ordinary") == "ordinary"


def test_a_literal_percent_finds_a_literal_percent(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Completed 50% of the migration")
    log(client, world, "ledger", world["alice"], "No numbers here", day=DAY - timedelta(days=1))

    sign_in(world["alice"])
    page = search(client, "50%25")

    assert "Completed 50% of the migration" in page
    assert "No numbers here" not in page, "the percent is a character, not a wildcard"


def test_a_quote_does_not_break_the_query(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "O'Brien's ledger review")

    sign_in(world["alice"])
    page = results_of(client, "O%27Brien")

    # The apostrophe comes back HTML-escaped, which is the template doing its job — so
    # assert on the escaped form rather than loosening the search assertion.
    assert "O&#39;Brien&#39;s ledger review" in page
    assert "ledger review" in page


def test_markup_in_a_time_note_is_escaped_not_rendered(client: TestClient, world, sign_in) -> None:
    """Search puts user text on a page, so the page had better not execute it."""
    log(client, world, "ledger", world["alice"], "<script>alert('x')</script> review")

    sign_in(world["alice"])
    page = results_of(client, "review")

    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_a_drop_table_attempt_is_just_text(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    response = client.get("/search?q=%27%3B+DROP+TABLE+time_notes%3B--")

    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Project).limit(1)) is not None, "the schema is intact"


# --- the empty and awkward cases ------------------------------------------------------


def test_an_empty_query_returns_the_form_not_the_database(
    client: TestClient, world, sign_in
) -> None:
    log(client, world, "ledger", world["alice"], "Should not be listed")

    sign_in(world["alice"])
    page = search(client, "")

    assert "Should not be listed" not in page
    assert "data-search-results" not in page, "no results block at all, not an empty one"
    assert 'name="q"' in page, "the form is still there to type into"


@pytest.mark.parametrize("term", ["", " ", "a", "  x  "[:3]])
def test_a_short_query_returns_nothing_from_the_search_itself(
    client: TestClient, world, term: str
) -> None:
    """Tested against `search()` rather than the page.

    The template only renders results when the query is non-empty, so asserting through it
    would pass even if the guard were removed — which is exactly what a mutation run showed.
    """
    log(client, world, "ledger", world["alice"], "Should not be listed")

    with client.app.state.session_factory() as session:
        person = session.get(User, world["alice"].id)
        results = finder.search(session, person, term, Filters())

    assert results.is_empty, "a blank box is a question nobody asked"
    assert results.notes == [] and results.projects == [] and results.people == []


def test_a_one_character_query_asks_for_more(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    page = search(client, "a")

    assert f"at least {MIN_QUERY_LENGTH} characters" in page
    assert "data-search-results" not in page


def test_nothing_matching_says_so_and_explains_the_scope(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["alice"])
    page = search(client, "kumquat")

    assert "Nothing matched" in page
    assert "only covers what you can already open" in page


def test_the_query_and_filters_survive_a_reload(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    url = (
        f"/search?q=ledger&project_id={world['projects']['ledger']['id']}&since={START.isoformat()}"
    )

    first = flat(client.get(url))
    assert first == flat(client.get(url)), "the same URL gives the same page"
    assert 'value="ledger"' in first
    assert START.isoformat() in first


def test_filters_compose_with_the_query(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], "Recent reconciliation", day=DAY)
    log(
        client,
        world,
        "ledger",
        world["alice"],
        "Older reconciliation",
        day=START + timedelta(days=1),
    )

    sign_in(world["alice"])
    page = search(client, "reconciliation", f"&since={(DAY - timedelta(days=1)).isoformat()}")

    assert "Recent reconciliation" in page
    assert "Older reconciliation" not in page


def test_the_masthead_offers_search_to_a_signed_in_user(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    page = flat(client.get("/projects"))

    assert 'action="/search"' in page
    assert 'type="search"' in page


# --- scale ----------------------------------------------------------------------------


def test_search_stays_quick_across_a_year_of_entries(
    client: TestClient, administrator, sign_in
) -> None:
    with client.app.state.session_factory() as session:
        role_id = ensure_member_role(session).id
        people = []
        for index in range(5):
            person = User(
                email=f"scale{index}@example.com",
                full_name=f"Scale {index}",
                password_hash=hash_password("a-properly-long-password"),
                role_id=role_id,
            )
            session.add(person)
            session.flush()
            people.append(person.id)

        for index in range(50):
            project = Project(
                name=f"Project {index}",
                code=f"S{index:03d}",
                owner_id=administrator.id,
                start_date=TODAY - timedelta(days=400),
                end_date=TODAY + timedelta(days=30),
            )
            session.add(project)
            session.flush()
            task = Task(project_id=project.id, name="Delivery")
            session.add(task)
            session.flush()
            for week in range(52):
                session.add(
                    TimeNote(
                        project_id=project.id,
                        task_id=task.id,
                        user_id=people[week % 5],
                        work_date=TODAY - timedelta(days=week * 7),
                        duration_minutes=480,
                        detail=f"Week {week} on project {index}",
                        state=TimeNoteState.APPROVED,
                    )
                )
        session.commit()

        person = session.get(User, administrator.id)
        started = time.perf_counter()
        results = finder.search(session, person, "Week 12", Filters())
        elapsed_ms = (time.perf_counter() - started) * 1000

    print(f"\n  searched 2,600 entries across 50 projects in {elapsed_ms:.0f}ms")
    assert results.notes, "and it found something"
    assert elapsed_ms < 300, f"search took {elapsed_ms:.0f}ms"


def test_results_are_capped_and_say_so(client: TestClient, world, sign_in) -> None:
    for index in range(finder.RESULT_LIMIT + 5):
        log(
            client,
            world,
            "ledger",
            world["alice"],
            f"Bulk entry {index}",
            day=START + timedelta(days=index),
        )

    sign_in(world["alice"])
    page = search(client, "Bulk")

    assert f"showing the first {finder.RESULT_LIMIT}" in page, (
        "a silent cap reads as 'that is everything', which it is not"
    )
