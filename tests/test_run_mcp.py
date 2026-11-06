from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


RUN_MCP = Path(__file__).resolve().parent.parent / "scripts" / "run-mcp"
SYSTEM_PATH = "/usr/bin:/bin"
# Prints what the launcher handed to uv instead of starting a server.
FAKE_UV = '#!/bin/sh\nprintf "%s\\n" "$UV_PROJECT_ENVIRONMENT" "${UV_CACHE_DIR-unset}"\n'


def _write_fake_uv(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fake_uv = directory / "uv"
    fake_uv.write_text(FAKE_UV, encoding="utf-8")
    fake_uv.chmod(0o755)


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(RUN_MCP), "--client", "codex"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def launcher_env(tmp_path: Path) -> dict[str, str]:
    """A user with uv on PATH, whose TMPDIR stands in for a /tmp shared with other users."""
    _write_fake_uv(tmp_path / "fake-bin")
    return {
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "shared-tmp"),
        "PATH": f"{tmp_path / 'fake-bin'}:{SYSTEM_PATH}",
    }


def test_runtime_environment_is_kept_in_the_users_own_cache(launcher_env: dict[str, str]) -> None:
    result = _run(env=launcher_env)

    assert result.returncode == 0, result.stderr
    environment_path = result.stdout.splitlines()[0]
    assert environment_path.startswith(f"{launcher_env['HOME']}/.cache/apyguard/apiscout/runtime-")
    assert not environment_path.startswith(launcher_env["TMPDIR"])


def test_uv_cache_is_not_moved_to_a_shared_directory(launcher_env: dict[str, str]) -> None:
    result = _run(env=launcher_env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == "unset"


def test_an_absolute_xdg_cache_home_is_used(launcher_env: dict[str, str], tmp_path: Path) -> None:
    result = _run(env={**launcher_env, "XDG_CACHE_HOME": str(tmp_path / "xdg")})

    assert result.stdout.splitlines()[0].startswith(f"{tmp_path / 'xdg'}/apyguard/apiscout/")


def test_a_relative_xdg_cache_home_is_ignored(launcher_env: dict[str, str]) -> None:
    result = _run(env={**launcher_env, "XDG_CACHE_HOME": "relative/cache"})

    assert result.stdout.splitlines()[0].startswith(f"{launcher_env['HOME']}/.cache/")


def test_no_cache_location_is_reported(launcher_env: dict[str, str]) -> None:
    environment = {name: value for name, value in launcher_env.items() if name != "HOME"}

    result = _run(env=environment)

    assert result.returncode == 78
    assert "HOME" in result.stderr


def test_missing_uv_says_how_to_install_it(tmp_path: Path) -> None:
    result = _run(env={"HOME": str(tmp_path / "home"), "PATH": SYSTEM_PATH})

    assert result.returncode == 69
    assert "docs.astral.sh/uv" in result.stderr


@pytest.mark.parametrize("installer_directory", [".local/bin", ".cargo/bin"])
def test_uv_is_found_where_its_installer_puts_it(tmp_path: Path, installer_directory: str) -> None:
    home = tmp_path / "home"
    _write_fake_uv(home / installer_directory)

    result = _run(env={"HOME": str(home), "PATH": SYSTEM_PATH})

    assert result.returncode == 0, result.stderr


def test_uv_on_path_wins_over_the_installer_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"
    installer_uv = home / ".local/bin/uv"
    installer_uv.parent.mkdir(parents=True)
    installer_uv.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    installer_uv.chmod(0o755)
    _write_fake_uv(tmp_path / "fake-bin")

    result = _run(env={"HOME": str(home), "PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{SYSTEM_PATH}"})

    assert result.returncode == 0, result.stderr
