from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import tomllib


VERSION_PARTS = ("major", "minor", "patch")
SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PYPROJECT_VERSION_PATTERN = re.compile(r'(?m)^version = "[^"]+"$')
CHANGELOG_UNRELEASED_PATTERN = re.compile(r"(?m)^## Unreleased$")


def bump(version: str, part: str) -> str:
    if part not in VERSION_PARTS:
        raise ValueError(f'Version part must be one of {", ".join(VERSION_PARTS)}: part="{part}".')
    match = SEMVER_PATTERN.match(version)
    if match is None:
        raise ValueError(f'Plugin version must look like MAJOR.MINOR.PATCH: version="{version}".')
    major, minor, patch = (int(value) for value in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def replace_pyproject_version(source: str, version: str) -> str:
    updated, replacements = PYPROJECT_VERSION_PATTERN.subn(f'version = "{version}"', source)
    if replacements != 1:
        raise RuntimeError(f"Expected one version line in pyproject.toml, found {replacements}.")
    return updated


def release_changelog(source: str, version: str, released_on: str) -> str:
    """Start the new version's section below the Unreleased heading, so it takes those entries."""
    updated, replacements = CHANGELOG_UNRELEASED_PATTERN.subn(
        f"## Unreleased\n\n## {version} - {released_on}",
        source,
    )
    if replacements != 1:
        raise RuntimeError(f"Expected one Unreleased section in CHANGELOG.md, found {replacements}.")
    return updated


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise ValueError(f"Usage: bump_version.py {{{'|'.join(VERSION_PARTS)}}}")
    plugin_root = Path(__file__).resolve().parent.parent
    pyproject_path = plugin_root / "pyproject.toml"
    changelog_path = plugin_root / "CHANGELOG.md"

    pyproject_source = pyproject_path.read_text(encoding="utf-8")
    current_version = tomllib.loads(pyproject_source)["project"]["version"]
    new_version = bump(version=current_version, part=argv[0])

    pyproject_path.write_text(
        replace_pyproject_version(source=pyproject_source, version=new_version),
        encoding="utf-8",
    )
    changelog_path.write_text(
        release_changelog(
            source=changelog_path.read_text(encoding="utf-8"),
            version=new_version,
            released_on=datetime.now(timezone.utc).date().isoformat(),
        ),
        encoding="utf-8",
    )
    print(f"Bumped plugin version: {current_version} -> {new_version}", file=sys.stderr)
    # stdout carries only key=value lines so a workflow can append it to $GITHUB_OUTPUT.
    print(f"version={new_version}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, RuntimeError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f"Version bump failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
