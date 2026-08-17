"""Guards on the platform itself.

The application arrives in EPIC 0; until then these keep the scaffolding honest — the
brand palette stays valid, the skills stay loadable, and secrets stay out of git.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

REQUIRED_DOCS = [
    "README.md",
    "CHANGE_MANAGEMENT.md",
    "BRAND_GUIDELINES.md",
    "docs/ARCHITECTURE.md",
    "docs/DATA_MODEL.md",
    "docs/ROADMAP.md",
]

REQUIRED_ENV_KEYS = {
    "TS_SECRET_KEY",
    "TS_DATA_DIR",
    "TS_DATABASE_URL",
    "TS_ADMIN_EMAIL",
    "TS_ADMIN_PASSWORD",
    "TS_ENV",
    "TS_TUNNEL_TOKEN",
}


@pytest.mark.parametrize("relative_path", REQUIRED_DOCS)
def test_required_document_exists_and_is_substantive(relative_path: str) -> None:
    document = REPO_ROOT / relative_path
    assert document.is_file(), f"{relative_path} is missing"
    assert len(document.read_text().split()) > 100, f"{relative_path} is a stub"


def test_brand_palette_builds_and_artifacts_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "brands" / "scripts" / "build_all.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_generated_tokens_cover_both_themes() -> None:
    css = (REPO_ROOT / "brands" / "dist" / "tokens.css").read_text()
    assert '[data-theme="dark"]' in css
    assert "prefers-color-scheme: dark" in css
    for token in ("--color-primary", "--color-state-approved", "--color-health-red"):
        assert token in css, f"{token} missing from generated tokens"


def test_palette_semantic_colours_are_locked_in_both_themes() -> None:
    palette = json.loads((REPO_ROOT / "brands" / "tokens" / "palette.json").read_text())
    assert palette["semantic"]["light"].keys() == palette["semantic"]["dark"].keys()


def test_every_skill_has_frontmatter_with_name_and_description() -> None:
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skills, "no skills found under .claude/skills"

    for skill in skills:
        text = skill.read_text()
        assert text.startswith("---\n"), f"{skill.name} has no frontmatter"
        frontmatter = text.split("---", 2)[1]
        name = re.search(r"^name:\s*(\S+)", frontmatter, re.MULTILINE)
        description = re.search(r"^description:\s*(.+)", frontmatter, re.MULTILINE)
        assert name, f"{skill} frontmatter has no name"
        assert description, f"{skill} frontmatter has no description"
        assert name.group(1) == skill.parent.name, (
            f"{skill} declares name {name.group(1)!r} but lives in {skill.parent.name!r}"
        )


def test_env_example_documents_every_required_key() -> None:
    text = (REPO_ROOT / ".env.example").read_text()
    declared = {line.split("=", 1)[0].strip() for line in text.splitlines() if "=" in line}
    assert declared >= REQUIRED_ENV_KEYS, REQUIRED_ENV_KEYS - declared


def test_gitignore_protects_secrets_and_runtime_state() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text()
    for pattern in (".env", "data/", "__pycache__/", ".DS_Store", "*.db"):
        assert pattern in ignored, f".gitignore does not cover {pattern}"


def test_no_env_file_is_tracked_by_git() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.split()
    assert ".env" not in tracked
    assert not [path for path in tracked if path.endswith(".db")]


def test_shell_scripts_are_executable() -> None:
    for script in (REPO_ROOT / "scripts").glob("*.sh"):
        assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
