from __future__ import annotations

import asyncio
import os
from tempfile import TemporaryDirectory
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

from apyguard_apiscout_mcp.auth import (
    AUTH_FILE_ENVIRONMENT_VARIABLE,
    CLIENT_ENVIRONMENT_VARIABLE,
    DEFAULT_API_URL,
    StoredAuthState,
    save_auth_state,
)


async def run_smoke_test(auth_file_path: Path) -> None:
    repository_root = Path(__file__).resolve().parent.parent
    fixture_path = repository_root / "tests" / "fixtures" / "fastapi-app"
    server_parameters = StdioServerParameters(
        command=str(repository_root / "scripts" / "run-mcp"),
        args=["--client", "codex"],
        cwd=str(repository_root),
        env={
            **os.environ,
            AUTH_FILE_ENVIRONMENT_VARIABLE: str(auth_file_path),
            CLIENT_ENVIRONMENT_VARIABLE: "codex",
        },
    )
    async with stdio_client(server_parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            expected_names = {
                "get_apyguard_auth_status",
                "scan_api_project",
                "open_api_workbench",
                "open_apyguard_asset_form",
                "create_apyguard_asset",
                "list_apyguard_assets",
                "get_apyguard_progress",
                "list_api_findings",
                "review_api_project",
                "get_endpoint_context",
                "export_openapi",
                "compare_openapi",
                "sign_out_apyguard",
                "start_apyguard_login",
                "complete_apyguard_login",
            }
            if tool_names != expected_names:
                raise RuntimeError(
                    f"Unexpected MCP tools: actual={sorted(tool_names)} "
                    f"expected={sorted(expected_names)}."
                )
            resources = await session.list_resources()
            resource_uris = {str(resource.uri) for resource in resources.resources}
            workbench_uri = "ui://apyguard-apiscout/workbench-v2.html"
            if workbench_uri not in resource_uris:
                raise RuntimeError(
                    f"APIScout workbench resource was not registered: resources={sorted(resource_uris)}."
                )
            workbench_resource = await session.read_resource(AnyUrl(workbench_uri))
            workbench_contents = workbench_resource.contents[0]
            if (
                workbench_contents.mimeType != "text/html;profile=mcp-app"
                or "APIScout Workbench" not in workbench_contents.text
            ):
                raise RuntimeError("APIScout workbench resource content is invalid.")
            asset_form_uri = "ui://apyguard-apiscout/asset-form-v2.html"
            if asset_form_uri not in resource_uris:
                raise RuntimeError(
                    f"ApyGuard asset form resource was not registered: resources={sorted(resource_uris)}."
                )
            asset_form_resource = await session.read_resource(AnyUrl(asset_form_uri))
            asset_form_contents = asset_form_resource.contents[0]
            if (
                asset_form_contents.mimeType != "text/html;profile=mcp-app"
                or "Create ApyGuard Asset" not in asset_form_contents.text
            ):
                raise RuntimeError("ApyGuard asset form resource content is invalid.")
            blocked_result = await session.call_tool(
                "scan_api_project",
                {"project_path": str(fixture_path)},
            )
            if not blocked_result.isError:
                raise RuntimeError("scan_api_project did not require ApyGuard login.")
            save_auth_state(
                auth_file_path=auth_file_path,
                state=StoredAuthState(
                    client_type="codex",
                    installation_id="smoke-installation",
                    access_token="apg_cdx_smoke-token",
                    # The server runs with the default API, which the token is for.
                    api_url=DEFAULT_API_URL,
                ),
            )
            result = await session.call_tool(
                "scan_api_project",
                {"project_path": str(fixture_path)},
            )
            if result.isError:
                raise RuntimeError(f"scan_api_project failed: content={result.content}.")
            workbench_result = await session.call_tool(
                "open_api_workbench",
                {"project_path": str(fixture_path)},
            )
            if workbench_result.isError:
                raise RuntimeError(
                    f"open_api_workbench failed: content={workbench_result.content}."
                )
            structured_content = workbench_result.structuredContent
            if not isinstance(structured_content, dict):
                raise RuntimeError(
                    "open_api_workbench did not return structuredContent for the UI."
                )
            required_workbench_fields = {
                "project_path",
                "framework",
                "operation_count",
                "finding_count",
                "findings",
                "undocumented_endpoint_count",
                "openapi",
            }
            if set(structured_content) != required_workbench_fields:
                raise RuntimeError(
                    "open_api_workbench returned unexpected structuredContent fields: "
                    f"actual={sorted(structured_content)} "
                    f"expected={sorted(required_workbench_fields)}."
                )


def main() -> None:
    with TemporaryDirectory(prefix="apyguard-mcp-smoke-") as temporary_directory:
        asyncio.run(
            run_smoke_test(
                auth_file_path=Path(temporary_directory) / "auth.json",
            )
        )


if __name__ == "__main__":
    main()
