"""Project files.

A filename from a browser is user input, so most of what follows is about not trusting it:
the name on disk is generated, the extension is allow-listed, the size ceiling bites while
reading, and every download goes through the app so object-level access still applies.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import storage
from backend.app.models import Project, ProjectFile
from backend.app.storage import UploadError, display_name_for, generated_name

TODAY = date.today()


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    member = make_person("member@example.com", "Mo Member")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

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
            "start_date": (TODAY - timedelta(days=30)).isoformat(),
            "end_date": (TODAY + timedelta(days=30)).isoformat(),
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))
    client.post(f"/projects/{project_id}/members", data={"user_id": str(member.id)})

    return {
        "project_id": project_id,
        "administrator": administrator,
        "member": member,
        "outsider": outsider,
    }


def upload(client: TestClient, world, name: str = "statement.pdf", content: bytes = b"a" * 64):
    return client.post(
        f"/projects/{world['project_id']}/files",
        files={"upload": (name, BytesIO(content), "application/pdf")},
    )


def files(client: TestClient, world) -> list[ProjectFile]:
    with client.app.state.session_factory() as session:
        return list(
            session.scalars(
                select(ProjectFile).where(ProjectFile.project_id == world["project_id"])
            ).all()
        )


def upload_dir(client: TestClient, world):
    return storage.project_directory(client.app.state.settings, world["project_id"])


# --- the round trip -------------------------------------------------------------------


def test_a_member_uploads_lists_and_downloads(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    assert "Attached statement.pdf" in upload(client, world, content=b"hello world").text

    listing = client.get(f"/projects/{world['project_id']}").text
    assert "statement.pdf" in listing
    assert "Mo Member" in listing

    record = files(client, world)[0]
    download = client.get(f"/projects/{world['project_id']}/files/{record.id}")

    assert download.status_code == 200
    assert download.content == b"hello world"
    assert "statement.pdf" in download.headers["content-disposition"]


def test_the_metadata_records_what_was_uploaded(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world, content=b"x" * 2048)

    record = files(client, world)[0]
    assert record.filename == "statement.pdf"
    assert record.size_bytes == 2048
    assert record.content_type == "application/pdf"
    assert record.uploaded_by_id == world["member"].id


def test_the_file_lands_on_the_volume_under_its_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world)

    record = files(client, world)[0]
    path = upload_dir(client, world) / record.stored_name

    assert path.is_file()
    assert path.parent.name == str(world["project_id"])
    assert path.read_bytes() == b"a" * 64


# --- the filename is not trusted ------------------------------------------------------


def test_a_traversing_filename_lands_harmlessly_inside_the_project(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world, name="../../etc/passwd.pdf")

    record = files(client, world)[0]
    path = upload_dir(client, world) / record.stored_name

    assert record.filename == "passwd.pdf", "the display name keeps only the last segment"
    assert "/" not in record.stored_name and ".." not in record.stored_name
    assert path.resolve().parent == upload_dir(client, world).resolve()
    assert path.is_file()


def test_the_stored_name_is_generated_not_the_uploaded_one(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world, name="statement.pdf")

    record = files(client, world)[0]
    assert record.stored_name != "statement.pdf"
    assert record.stored_name.endswith(".pdf")
    assert len(record.stored_name) > 20, "an unguessable stem, so the volume cannot be browsed"


def test_two_uploads_of_the_same_name_do_not_collide(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world, name="report.pdf", content=b"first")
    upload(client, world, name="report.pdf", content=b"second")

    records = files(client, world)
    assert len({record.stored_name for record in records}) == 2
    assert {record.filename for record in records} == {"report.pdf"}

    contents = {(upload_dir(client, world) / record.stored_name).read_bytes() for record in records}
    assert contents == {b"first", b"second"}, "neither upload overwrote the other"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\cmd.exe", "cmd.exe"),
        ("....", "file"),
        ("", "file"),
        ("re;port&.pdf", "re_port_.pdf"),
    ],
)
def test_display_names_are_reduced_to_something_safe(given: str, expected: str) -> None:
    assert display_name_for(given) == expected


def test_a_generated_name_never_carries_a_path(client: TestClient) -> None:
    for attempt in ("../../x.pdf", "a/b/c.pdf", "..\\x.pdf"):
        name = generated_name(attempt)
        assert "/" not in name and "\\" not in name and ".." not in name


# --- what is refused ------------------------------------------------------------------


def test_a_type_outside_the_allow_list_is_refused(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    response = upload(client, world, name="payload.exe")

    assert "not a file type this deployment accepts" in response.text
    assert files(client, world) == []
    assert (
        not any(upload_dir(client, world).glob("*")) if upload_dir(client, world).exists() else True
    )


def test_a_file_over_the_ceiling_is_refused_and_nothing_is_written(
    client: TestClient, world, sign_in, settings
) -> None:
    sign_in(world["member"])
    oversized = b"x" * (settings.max_upload_bytes + 1024)

    response = upload(client, world, content=oversized)

    assert f"larger than the {settings.max_upload_mb}MB limit" in response.text
    assert files(client, world) == []
    directory = upload_dir(client, world)
    assert not directory.exists() or list(directory.iterdir()) == [], (
        "a refused upload must not leave a half-written file behind"
    )


def test_an_empty_file_is_refused(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    response = upload(client, world, content=b"")

    assert "empty" in response.text
    assert files(client, world) == []


def test_the_ceiling_is_enforced_while_reading(settings) -> None:
    """Not after: an oversized upload must never be held whole in memory."""
    small = settings.model_copy(update={"max_upload_mb": 1})
    stream = BytesIO(b"x" * (2 * 1024 * 1024))

    with pytest.raises(UploadError, match="larger than the 1MB limit"):
        storage.save(stream, "big.pdf", "application/pdf", small, project_id=1)

    assert stream.tell() < 2 * 1024 * 1024, "it stopped reading rather than consuming the lot"


def test_resolve_within_refuses_to_escape(settings, tmp_path) -> None:
    with pytest.raises(UploadError):
        storage.resolve_within(tmp_path, "../escape.pdf")


# --- who may reach a file -------------------------------------------------------------


def test_an_outsider_gets_404_on_upload_list_and_download(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]

    sign_in(world["outsider"])
    assert client.get(f"/projects/{world['project_id']}").status_code == 404
    assert upload(client, world).status_code == 404
    assert client.get(f"/projects/{world['project_id']}/files/{record.id}").status_code == 404


def test_a_file_id_from_another_project_is_not_reachable(
    client: TestClient, world, administrator, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Other",
            "code": "other",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": TODAY.isoformat(),
            "end_date": "",
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        other_id = session.scalar(select(Project.id).where(Project.code == "OTHER"))

    assert client.get(f"/projects/{other_id}/files/{record.id}").status_code == 404, (
        "a file belongs to its project, not to whoever knows its id"
    )


def test_downloads_require_a_session(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]

    client.cookies.clear()
    response = client.get(
        f"/projects/{world['project_id']}/files/{record.id}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- removing -------------------------------------------------------------------------


def test_deleting_removes_the_row_and_the_bytes(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]
    path = upload_dir(client, world) / record.stored_name
    assert path.is_file()

    sign_in(world["administrator"])
    client.post(f"/projects/{world['project_id']}/files/{record.id}/delete")

    assert files(client, world) == []
    assert not path.exists(), "the row and the bytes go together"


def test_a_plain_member_cannot_delete(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]

    response = client.post(f"/projects/{world['project_id']}/files/{record.id}/delete")

    assert response.status_code == 403
    assert len(files(client, world)) == 1


def test_a_missing_file_on_disk_does_not_break_the_listing(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]
    (upload_dir(client, world) / record.stored_name).unlink()

    listing = client.get(f"/projects/{world['project_id']}")
    assert listing.status_code == 200, "a vanished file is a broken link, not a broken page"
    assert "statement.pdf" in listing.text

    download = client.get(f"/projects/{world['project_id']}/files/{record.id}")
    assert download.status_code == 404


def test_deleting_a_file_whose_bytes_are_already_gone_still_works(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["member"])
    upload(client, world)
    record = files(client, world)[0]
    (upload_dir(client, world) / record.stored_name).unlink()

    sign_in(world["administrator"])
    client.post(f"/projects/{world['project_id']}/files/{record.id}/delete")

    assert files(client, world) == [], "the row goes even if the bytes went first"


# --- presentation ---------------------------------------------------------------------


def test_sizes_are_shown_in_something_readable() -> None:
    assert storage.human_size(512) == "512B"
    assert storage.human_size(2048) == "2.0KB"
    assert storage.human_size(5 * 1024 * 1024) == "5.0MB"


def test_an_empty_project_says_nothing_is_attached(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    assert "Nothing attached yet." in client.get(f"/projects/{world['project_id']}").text


def test_a_cross_project_file_id_is_refused_by_the_guard_not_by_luck(
    client: TestClient, world, administrator, sign_in
) -> None:
    """Plant identical bytes in the other project so a missing path cannot do the refusing.

    Without the `record.project_id != project.id` check the route would happily serve this,
    because the stored name resolves inside the *requested* project's directory. Asserting
    only "404" would pass on an accident; asserting the bytes never appear does not.
    """
    sign_in(world["member"])
    upload(client, world, content=b"confidential")
    record = files(client, world)[0]

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Decoy",
            "code": "decoy",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": TODAY.isoformat(),
            "end_date": "",
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        decoy_id = session.scalar(select(Project.id).where(Project.code == "DECOY"))

    decoy_dir = storage.project_directory(client.app.state.settings, decoy_id)
    decoy_dir.mkdir(parents=True, exist_ok=True)
    (decoy_dir / record.stored_name).write_bytes(b"planted")

    response = client.get(f"/projects/{decoy_id}/files/{record.id}")

    assert response.status_code == 404
    assert b"planted" not in response.content
    assert b"confidential" not in response.content
