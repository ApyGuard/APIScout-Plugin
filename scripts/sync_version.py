from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any


PLUGIN_NAME = "apyguard-apiscout-plugin"


def pin_marketplace_to_release(marketplace: dict[str, Any], version: str) -> dict[str, Any]:
    """Point new installs at the release tag, so what is merged later reaches nobody before its release."""
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == PLUGIN_NAME]
    if len(entries) != 1:
        raise RuntimeError(f"Expected one {PLUGIN_NAME} entry in the marketplace, found {len(entries)}.")
    entries[0]["source"]["ref"] = f"plugin-v{version}"
    return marketplace


def main() -> None:
    plugin_root = Path(__file__).resolve().parent.parent
    package_version = tomllib.loads(
        (plugin_root / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]

    for manifest_name in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        manifest_path = plugin_root / manifest_name
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = package_version
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    marketplace_path = plugin_root / ".claude-plugin/marketplace.json"
    marketplace = pin_marketplace_to_release(
        marketplace=json.loads(marketplace_path.read_text(encoding="utf-8")),
        version=package_version,
    )
    marketplace_path.write_text(
        json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    version_path = plugin_root / "src/apyguard_apiscout_mcp/__init__.py"
    version_source = version_path.read_text(encoding="utf-8")
    updated_source, replacements = re.subn(
        r'(?m)^__version__ = "[^"]+"$',
        f'__version__ = "{package_version}"',
        version_source,
    )
    if replacements != 1:
        raise RuntimeError(
            f"Expected one __version__ declaration in {version_path}, found {replacements}."
        )
    version_path.write_text(updated_source, encoding="utf-8")
    print(f"Synchronized plugin version: {package_version}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"Version synchronization failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
