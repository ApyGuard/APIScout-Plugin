from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import sys


ENGINE_WHEEL_PATTERN = re.compile(
    r"^apyguard_engine-(?P<version>[^-]+)-(?P<python>cp(?P<major>\d)(?P<minor>\d+))-cp\d+-(?P<platform>.+)\.whl$"
)
PLUGIN_WHEEL_PATTERN = re.compile(r"^apyguard_apiscout_plugin-(?P<version>[^-]+)-py3-none-any\.whl$")
# Release targets and the engine wheel platform tags that belong to them. The engine retags
# macOS wheels with its own architecture names, so darwin-x64 accepts both x64 and x86_64.
TARGET_PLATFORMS = {
    "darwin-arm64": re.compile(r"^macosx_\d+_\d+_arm64$"),
    "darwin-x64": re.compile(r"^macosx_\d+_\d+_(x64|x86_64)$"),
    "linux-x64": re.compile(r"^(linux|manylinux\w*)_x86_64$"),
    "win32-x64": re.compile(r"^win_amd64$"),
}


def plugin_version_of(plugin_wheel: Path) -> str:
    match = PLUGIN_WHEEL_PATTERN.match(plugin_wheel.name)
    if match is None:
        raise ValueError(f'Not an APIScout plugin wheel: file="{plugin_wheel.name}".')
    return match.group("version")


def find_engine_wheels(engine_wheel_dir: Path) -> dict[str, Path]:
    wheels: dict[str, Path] = {}
    for wheel in sorted(engine_wheel_dir.rglob("apyguard_engine-*.whl")):
        match = ENGINE_WHEEL_PATTERN.match(wheel.name)
        if match is None:
            raise ValueError(f'Engine wheel name is not recognized: file="{wheel.name}".')
        targets = [
            target for target, pattern in TARGET_PLATFORMS.items()
            if pattern.match(match.group("platform"))
        ]
        if len(targets) != 1:
            raise ValueError(f'Engine wheel matches no release target: file="{wheel.name}".')
        if targets[0] in wheels:
            raise ValueError(
                f'Two engine wheels for target {targets[0]}: '
                f'files="{wheels[targets[0]].name}", "{wheel.name}".'
            )
        wheels[targets[0]] = wheel

    missing = sorted(set(TARGET_PLATFORMS) - set(wheels))
    if missing:
        raise ValueError(f'Engine wheels are missing for: {", ".join(missing)} (dir="{engine_wheel_dir}").')

    builds = {
        (match.group("version"), match.group("python"))
        for match in (ENGINE_WHEEL_PATTERN.match(wheel.name) for wheel in wheels.values())
        if match is not None
    }
    if len(builds) != 1:
        raise ValueError(f"Engine wheels differ in version or Python tag: {sorted(builds)}.")
    return wheels


def render_runtime_pyproject(
    plugin_version: str,
    plugin_wheel_name: str,
    engine_wheel_name: str,
) -> str:
    match = ENGINE_WHEEL_PATTERN.match(engine_wheel_name)
    if match is None:
        raise ValueError(f'Engine wheel name is not recognized: file="{engine_wheel_name}".')
    major, minor = int(match.group("major")), int(match.group("minor"))
    return f'''[project]
name = "apyguard-apiscout-runtime"
version = "{plugin_version}"
requires-python = ">={major}.{minor},<{major}.{minor + 1}"
dependencies = [
  "apyguard-apiscout-plugin=={plugin_version}",
  "apyguard-engine=={match.group("version")}",
]

[tool.uv]
package = false

[tool.uv.sources]
apyguard-apiscout-plugin = {{ path = "../vendor/{plugin_wheel_name}" }}
apyguard-engine = {{ path = "../vendor/{engine_wheel_name}" }}
'''


def assemble(releases_root: Path, plugin_wheel: Path, engine_wheels: dict[str, Path]) -> None:
    plugin_version = plugin_version_of(plugin_wheel)
    for target, engine_wheel in engine_wheels.items():
        vendor_dir = releases_root / target / "vendor"
        runtime_dir = releases_root / target / "runtime"
        vendor_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for old_wheel in vendor_dir.glob("*.whl"):
            old_wheel.unlink()
        shutil.copy2(plugin_wheel, vendor_dir / plugin_wheel.name)
        shutil.copy2(engine_wheel, vendor_dir / engine_wheel.name)
        (runtime_dir / "pyproject.toml").write_text(
            render_runtime_pyproject(
                plugin_version=plugin_version,
                plugin_wheel_name=plugin_wheel.name,
                engine_wheel_name=engine_wheel.name,
            ),
            encoding="utf-8",
        )


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Place the plugin wheel and per-platform engine wheels under releases/."
    )
    parser.add_argument("--engine-wheels", type=Path, required=True)
    parser.add_argument("--plugin-wheel", type=Path, required=True)
    parser.add_argument("--expected-engine-version", default="")
    parser.add_argument(
        "--releases-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "releases",
    )
    arguments = parser.parse_args(argv)

    if not arguments.plugin_wheel.is_file():
        raise ValueError(f'Plugin wheel not found: path="{arguments.plugin_wheel}".')
    engine_wheels = find_engine_wheels(arguments.engine_wheels)
    engine_version = ENGINE_WHEEL_PATTERN.match(next(iter(engine_wheels.values())).name).group("version")
    expected = arguments.expected_engine_version.strip()
    if expected and expected != engine_version:
        raise ValueError(f"Engine wheels are version {engine_version}, expected {expected}.")

    assemble(
        releases_root=arguments.releases_root,
        plugin_wheel=arguments.plugin_wheel,
        engine_wheels=engine_wheels,
    )
    print(
        f"Assembled {', '.join(sorted(engine_wheels))} with plugin "
        f"{plugin_version_of(arguments.plugin_wheel)} and engine {engine_version}",
        file=sys.stderr,
    )
    # stdout carries only key=value lines so a workflow can append it to $GITHUB_OUTPUT.
    print(f"engine_version={engine_version}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, ValueError) as error:
        print(f"Release assembly failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
