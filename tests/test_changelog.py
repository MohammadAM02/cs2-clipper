"""CHANGELOG.md: the notes of each release, which the release workflow publishes and the app shows when it offers
the update (clipper.update)."""
from __future__ import annotations

import tomllib
from pathlib import Path

from clipper import update

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def section(text: str, version: str) -> str:
    """What the release workflow publishes for `version` (its awk): the lines after `## <version>`, up to the next
    `## ` heading."""
    lines: list[str] = []
    found = False
    for line in text.splitlines():
        if line.startswith("## "):
            if found:
                break
            found = line == f"## {version}"
            continue
        if found:
            lines.append(line)
    return "\n".join(lines).strip()


def test_the_version_being_built_has_notes():
    with (ROOT / "pyproject.toml").open("rb") as file:
        version = tomllib.load(file)["project"]["version"]
    assert section(CHANGELOG, version), f"CHANGELOG.md has no notes under '## {version}'"


def test_a_section_ends_where_the_next_version_begins():
    text = "# Changelog\n\nAbout it.\n\n## 0.3.0\n\n### Fixed\n- a\n\n## 0.2.0\n- b\n"
    assert section(text, "0.3.0") == "### Fixed\n- a"
    assert section(text, "0.2.0") == "- b"
    assert section(text, "0.1.0") == ""


def test_every_version_in_the_changelog_is_one_the_app_can_compare():
    versions = [line.removeprefix("## ") for line in CHANGELOG.splitlines() if line.startswith("## ")]
    assert versions and all(update.version_key(version) for version in versions)
    assert versions == sorted(versions, key=update.version_key, reverse=True)      # the newest first


def test_the_release_workflow_publishes_the_versions_section_as_the_notes():
    assert "CHANGELOG.md > notes.md" in WORKFLOW and "--notes-file notes.md" in WORKFLOW
    assert "--generate-notes" not in WORKFLOW
