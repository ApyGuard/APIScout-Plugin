from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tomllib
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENGINE_WHEELS = {
    "darwin-arm64": "apyguard_engine-1.0.8-cp313-cp313-macosx_11_0_arm64.whl",
    "darwin-x64": "apyguard_engine-1.0.8-cp313-cp313-macosx_11_0_x64.whl",
    "linux-x64": "apyguard_engine-1.0.8-cp313-cp313-linux_x86_64.whl",
    "win32-x64": "apyguard_engine-1.0.8-cp313-cp313-win_amd64.whl",
}
PLUGIN_WHEEL = "apyguard_apiscout_plugin-0.1.2-py3-none-any.whl"


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPOSITORY_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump_version = _load_script("bump_version")
assemble_release = _load_script("assemble_release")
sync_version = _load_script("sync_version")


def _write_wheels(directory: Path, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(name.encode("utf-8"))


@pytest.mark.parametrize(
    ("part", "expected"),
    [("patch", "0.1.2"), ("minor", "0.2.0"), ("major", "1.0.0")],
)
def test_bump_increases_the_requested_part(part: str, expected: str) -> None:
    assert bump_version.bump(version="0.1.1", part=part) == expected


def test_bump_rejects_unknown_part() -> None:
    with pytest.raises(ValueError, match="Version part"):
        bump_version.bump(version="0.1.1", part="build")


def test_bump_rejects_non_semver_version() -> None:
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        bump_version.bump(version="0.1.1+engine.1.0.8", part="patch")


def test_pyproject_version_is_replaced() -> None:
    source = '[project]\nname = "apyguard-apiscout-plugin"\nversion = "0.1.1"\n'

    assert 'version = "0.1.2"' in bump_version.replace_pyproject_version(source=source, version="0.1.2")


def test_pyproject_without_version_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="found 0"):
        bump_version.replace_pyproject_version(source="[project]\n", version="0.1.2")


def test_changelog_release_takes_the_unreleased_entries() -> None:
    source = "# Changelog\n\n## Unreleased\n\n- A fix.\n\n## 0.1.1 - 2026-01-01\n\n- Older.\n"

    updated = bump_version.release_changelog(source=source, version="0.1.2", released_on="2026-02-03")

    assert updated == (
        "# Changelog\n\n## Unreleased\n\n## 0.1.2 - 2026-02-03\n\n- A fix.\n\n## 0.1.1 - 2026-01-01\n\n- Older.\n"
    )


def test_changelog_without_an_unreleased_section_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="found 0"):
        bump_version.release_changelog(source="# Changelog\n", version="0.1.2", released_on="2026-02-03")


def test_repository_changelog_can_be_released() -> None:
    source = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "## 9.9.9 - 2026-02-03" in bump_version.release_changelog(
        source=source, version="9.9.9", released_on="2026-02-03"
    )


def test_marketplace_is_pinned_to_the_release_tag() -> None:
    marketplace = {
        "plugins": [{"name": "apyguard-apiscout-plugin", "source": {"source": "url", "url": "u", "ref": "main"}}]
    }

    pinned = sync_version.pin_marketplace_to_release(marketplace=marketplace, version="0.1.7")

    assert pinned["plugins"][0]["source"]["ref"] == "plugin-v0.1.7"


def test_marketplace_without_the_plugin_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="found 0"):
        sync_version.pin_marketplace_to_release(marketplace={"plugins": []}, version="0.1.7")


def test_committed_marketplace_points_at_the_current_release() -> None:
    version = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    marketplace = json.loads((REPOSITORY_ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))

    assert marketplace["plugins"][0]["source"]["ref"] == f"plugin-v{version}"


def test_engine_wheels_map_to_every_target(tmp_path: Path) -> None:
    _write_wheels(tmp_path / "downloads", list(ENGINE_WHEELS.values()))

    wheels = assemble_release.find_engine_wheels(tmp_path / "downloads")

    assert {target: wheel.name for target, wheel in wheels.items()} == ENGINE_WHEELS


def test_engine_wheels_are_found_in_artifact_subdirectories(tmp_path: Path) -> None:
    for target, name in ENGINE_WHEELS.items():
        _write_wheels(tmp_path / f"apyguard-engine-{target}" / "wheels", [name])

    assert set(assemble_release.find_engine_wheels(tmp_path)) == set(ENGINE_WHEELS)


def test_missing_engine_target_is_rejected(tmp_path: Path) -> None:
    _write_wheels(tmp_path, [name for target, name in ENGINE_WHEELS.items() if target != "win32-x64"])

    with pytest.raises(ValueError, match="missing for: win32-x64"):
        assemble_release.find_engine_wheels(tmp_path)


def test_duplicate_engine_target_is_rejected(tmp_path: Path) -> None:
    _write_wheels(
        tmp_path,
        [*ENGINE_WHEELS.values(), "apyguard_engine-1.0.8-cp313-cp313-manylinux_2_17_x86_64.whl"],
    )

    with pytest.raises(ValueError, match="Two engine wheels for target linux-x64"):
        assemble_release.find_engine_wheels(tmp_path)


def test_mixed_engine_versions_are_rejected(tmp_path: Path) -> None:
    names = dict(ENGINE_WHEELS)
    names["win32-x64"] = "apyguard_engine-1.0.7-cp313-cp313-win_amd64.whl"
    _write_wheels(tmp_path, list(names.values()))

    with pytest.raises(ValueError, match="differ in version"):
        assemble_release.find_engine_wheels(tmp_path)


def test_unknown_engine_platform_is_rejected(tmp_path: Path) -> None:
    _write_wheels(tmp_path, ["apyguard_engine-1.0.8-cp313-cp313-linux_aarch64.whl"])

    with pytest.raises(ValueError, match="matches no release target"):
        assemble_release.find_engine_wheels(tmp_path)


def test_runtime_pyproject_matches_the_committed_release() -> None:
    runtime_dir = REPOSITORY_ROOT / "releases" / "darwin-arm64"
    vendor_names = sorted(path.name for path in (runtime_dir / "vendor").glob("*.whl"))
    plugin_wheel = next(name for name in vendor_names if name.startswith("apyguard_apiscout_plugin-"))
    engine_wheel = next(name for name in vendor_names if name.startswith("apyguard_engine-"))

    rendered = assemble_release.render_runtime_pyproject(
        plugin_version=assemble_release.plugin_version_of(Path(plugin_wheel)),
        plugin_wheel_name=plugin_wheel,
        engine_wheel_name=engine_wheel,
    )

    assert rendered == (runtime_dir / "runtime" / "pyproject.toml").read_text(encoding="utf-8")


# The package metadata and both client manifests must match the proprietary LICENSE file.
@pytest.mark.parametrize("manifest", [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"])
def test_manifest_declares_the_proprietary_license(manifest: str) -> None:
    declared = json.loads((REPOSITORY_ROOT / manifest).read_text(encoding="utf-8"))["license"]

    assert declared == "LicenseRef-ApyGuard-Proprietary"


def test_package_declares_the_proprietary_license() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["license"] == "LicenseRef-ApyGuard-Proprietary"


def test_assemble_replaces_wheels_and_runtime_for_every_target(tmp_path: Path) -> None:
    releases_root = tmp_path / "releases"
    _write_wheels(tmp_path / "engine", list(ENGINE_WHEELS.values()))
    _write_wheels(tmp_path / "plugin", [PLUGIN_WHEEL])
    for target in ENGINE_WHEELS:
        _write_wheels(
            releases_root / target / "vendor",
            ["apyguard_apiscout_plugin-0.1.1-py3-none-any.whl", "apyguard_engine-1.0.7-old.whl"],
        )

    assemble_release.assemble(
        releases_root=releases_root,
        plugin_wheel=tmp_path / "plugin" / PLUGIN_WHEEL,
        engine_wheels=assemble_release.find_engine_wheels(tmp_path / "engine"),
    )

    for target, engine_wheel in ENGINE_WHEELS.items():
        vendor = sorted(path.name for path in (releases_root / target / "vendor").iterdir())
        assert vendor == sorted([PLUGIN_WHEEL, engine_wheel])
        runtime = (releases_root / target / "runtime" / "pyproject.toml").read_text(encoding="utf-8")
        assert '"apyguard-apiscout-plugin==0.1.2"' in runtime
        assert '"apyguard-engine==1.0.8"' in runtime
        assert f'path = "../vendor/{engine_wheel}"' in runtime


def test_assemble_rejects_a_non_plugin_wheel(tmp_path: Path) -> None:
    _write_wheels(tmp_path / "engine", list(ENGINE_WHEELS.values()))

    with pytest.raises(ValueError, match="Not an APIScout plugin wheel"):
        assemble_release.assemble(
            releases_root=tmp_path / "releases",
            plugin_wheel=tmp_path / "engine" / ENGINE_WHEELS["linux-x64"],
            engine_wheels=assemble_release.find_engine_wheels(tmp_path / "engine"),
        )


def test_main_rejects_unexpected_engine_version(tmp_path: Path) -> None:
    _write_wheels(tmp_path / "engine", list(ENGINE_WHEELS.values()))
    _write_wheels(tmp_path / "plugin", [PLUGIN_WHEEL])

    with pytest.raises(ValueError, match="version 1.0.8, expected 1.0.9"):
        assemble_release.main([
            "--engine-wheels", str(tmp_path / "engine"),
            "--plugin-wheel", str(tmp_path / "plugin" / PLUGIN_WHEEL),
            "--expected-engine-version", "1.0.9",
            "--releases-root", str(tmp_path / "releases"),
        ])
    assert not (tmp_path / "releases").exists()
