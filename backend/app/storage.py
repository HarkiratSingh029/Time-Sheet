"""Files on the local volume.

No S3 (`docs/ARCHITECTURE.md` §1): project files live under `data/uploads/<project_id>/`,
which is the same volume as the database and therefore the same backup.

Everything here exists because **a filename from a browser is user input**:

* the name on disk is **generated**, never the uploaded one — path traversal is the oldest
  trick there is, and `../../etc/passwd` should end up a boring string in a column;
* the extension is checked against an **allow-list**, so a newly dangerous type does not
  become uploadable by default;
* the size ceiling is enforced **while reading**, not after, so an oversized upload is
  refused without ever being held whole in memory or left half-written on disk;
* every resolved path is asserted to sit inside its project directory before anything is
  written or read.
"""

from __future__ import annotations

import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from backend.app.config import Settings

CHUNK_BYTES = 64 * 1024
MAX_DISPLAY_NAME = 255

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadError(ValueError):
    """The upload cannot be accepted, with a reason the uploader can act on."""


@dataclass(frozen=True)
class StoredFile:
    stored_name: str
    display_name: str
    size_bytes: int
    content_type: str


def project_directory(settings: Settings, project_id: int) -> Path:
    return settings.uploads_dir / str(project_id)


def display_name_for(filename: str) -> str:
    """What we show. Never what we write to disk.

    Reduced to its last path segment and stripped of anything exotic, because this string
    ends up in HTML, in a Content-Disposition header, and in a CSV export.
    """
    tail = filename.replace("\\", "/").split("/")[-1].strip()
    cleaned = _UNSAFE.sub("_", tail).strip(". ")
    return (cleaned or "file")[:MAX_DISPLAY_NAME]


def extension_of(filename: str) -> str:
    suffix = Path(display_name_for(filename)).suffix.lower().lstrip(".")
    return suffix if suffix.isalnum() else ""


def generated_name(filename: str) -> str:
    """An unguessable name plus the allow-listed extension.

    Nothing user-supplied reaches the filesystem: the extension has already been checked
    against the allow-list, and the stem is random.
    """
    extension = extension_of(filename)
    token = secrets.token_hex(16)
    return f"{token}.{extension}" if extension else token


def check_extension(filename: str, settings: Settings) -> str:
    extension = extension_of(filename)
    allowed = settings.upload_extensions
    if extension not in allowed:
        readable = ", ".join(sorted(allowed))
        raise UploadError(
            f"{display_name_for(filename)} is not a file type this deployment accepts. "
            f"Allowed: {readable}."
        )
    return extension


def resolve_within(directory: Path, name: str) -> Path:
    """Belt and braces: the resolved path must be inside the directory we intended."""
    directory = directory.resolve()
    candidate = (directory / name).resolve()
    if directory not in candidate.parents:
        raise UploadError("That file is not where it should be.")
    return candidate


def save(
    upload: BinaryIO, filename: str, content_type: str, settings: Settings, project_id: int
) -> StoredFile:
    check_extension(filename, settings)

    directory = project_directory(settings, project_id)
    directory.mkdir(parents=True, exist_ok=True)

    stored_name = generated_name(filename)
    destination = resolve_within(directory, stored_name)

    ceiling = settings.max_upload_bytes
    written = 0

    try:
        with destination.open("wb") as target:
            while chunk := upload.read(CHUNK_BYTES):
                written += len(chunk)
                if written > ceiling:
                    raise UploadError(
                        f"{display_name_for(filename)} is larger than the "
                        f"{settings.max_upload_mb}MB limit."
                    )
                target.write(chunk)
    except UploadError:
        # Nothing half-written survives a refusal.
        destination.unlink(missing_ok=True)
        raise

    if written == 0:
        destination.unlink(missing_ok=True)
        raise UploadError("That file is empty.")

    return StoredFile(
        stored_name=stored_name,
        display_name=display_name_for(filename),
        size_bytes=written,
        content_type=(content_type or "application/octet-stream")[:128],
    )


def path_of(settings: Settings, project_id: int, stored_name: str) -> Path:
    return resolve_within(project_directory(settings, project_id), stored_name)


def delete(settings: Settings, project_id: int, stored_name: str) -> None:
    """Remove the bytes. A file already gone is not an error worth surfacing."""
    try:
        path_of(settings, project_id, stored_name).unlink(missing_ok=True)
    except UploadError:
        return


def remove_project_directory(settings: Settings, project_id: int) -> None:
    shutil.rmtree(project_directory(settings, project_id), ignore_errors=True)


def human_size(size_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024 or unit == "GB":
            return f"{size_bytes:.0f}B" if unit == "B" else f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}GB"
