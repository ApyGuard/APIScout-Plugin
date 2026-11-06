from __future__ import annotations

import base64
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from apyguard_apiscout_mcp import __version__
from apyguard_apiscout_mcp.auth import (
    complete_apyguard_login as complete_apyguard_login_service,
    get_apyguard_auth_status as get_apyguard_auth_status_service,
    get_client_type,
    require_apyguard_authentication,
    sign_out_apyguard as sign_out_apyguard_service,
    start_apyguard_login as start_apyguard_login_service,
)
from apyguard_apiscout_mcp.paths import resolve_project_path
from apyguard_apiscout_mcp.services import (
    compare_openapi as compare_openapi_service,
    create_apyguard_asset as create_apyguard_asset_service,
    export_openapi as export_openapi_service,
    get_apyguard_progress as get_apyguard_progress_service,
    get_endpoint_context as get_endpoint_context_service,
    list_api_findings as list_api_findings_service,
    list_apyguard_assets as list_apyguard_assets_service,
    review_api_project as review_api_project_service,
    scan_api_project as scan_api_project_service,
)
from apyguard_apiscout_mcp.types import (
    ApiComparisonReport,
    AssetCreationReport,
    AssetFormContext,
    AssetListingReport,
    ApiReviewReport,
    AuthStatus,
    EndpointContext,
    ExportReport,
    FindingsReport,
    LoginStartReport,
    LoginCompletionReport,
    ProgressReport,
    ScanReport,
    SignOutReport,
)


SERVER_INSTRUCTIONS = (
    "Reply entirely in the language of the current user request, including every "
    "user-facing progress update and the final summary. Keep tool names and code "
    "identifiers unchanged. "
    "APIScout quick start prompts: 'Scan this project with APIScout and summarize "
    "the API findings.'; 'Open the APIScout Workbench for this project.'; "
    "'Show high-sensitivity API findings in this project.'; 'Explain the GET "
    "/users/{user_id} endpoint using source evidence.'; 'Export this project's "
    "OpenAPI document to openapi.json.'; and 'Compare /absolute/path/previous.json "
    "with /absolute/path/current.json.'; 'List the ApyGuard APIScout assets and "
    "their API collections for this project.' Use the user's actual project path and "
    "route when substituting examples. Check get_apyguard_auth_status before APIScout operations. If login is "
    "required, call start_apyguard_login, present its login_url as a link together with its "
    "user_code, and ask the user to approve that code in the browser. After the user confirms "
    "the approval, call complete_apyguard_login, then repeat the requested APIScout operation. "
    "Use scan_api_project before summarizing a project's APIs. The APIScout Workbench "
    "shows the raw scan JSON and provides a form to create an ApyGuard asset. "
    "Use create_apyguard_asset when that form is submitted. "
    "Use review_api_project when the user asks for an evidence-backed AI review of "
    "the scan; it packages high and medium findings with endpoint source context. "
    "When the user wants findings fixed in source, follow the review-and-fix skill. "
    "When the user wants static review through a live ApyGuard scan, or has live scan "
    "findings to map to source, follow the full-pipeline skill. "
    "Use get_apyguard_progress when the user asks whether an import or a live scan has "
    "finished, and after create_apyguard_asset imports documentation. Tell the user for "
    "each whether it is still running or has finished, with the percentage when there is "
    "one and the reason when it failed. It reads once: when something is still running, "
    "say so and offer to check again instead of calling it in a loop. "
    "Separate facts from inference in the response. Use get_endpoint_context before explaining an endpoint and base the explanation "
    "only on returned evidence. export_openapi overwrites an existing target."
)

mcp = FastMCP(
    name="apyguard-apiscout",
    instructions=SERVER_INSTRUCTIONS,
)

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    openWorldHint=False,
)
WORKBENCH_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
AUTH_START_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    openWorldHint=True,
)
# Tools that talk to ApyGuard, unlike the scans and exports, which stay on this computer.
REMOTE_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=True,
)
REMOTE_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    openWorldHint=True,
)
WORKBENCH_RESOURCE_URI = "ui://apyguard-apiscout/workbench-v2.html"
# Where the Workbench's follow-up prompts ask the agent to surface a source line.
# Codex has a code panel; Claude Code shows file references in the conversation.
SOURCE_LINK_INSTRUCTIONS = {
    "codex": "in the Codex code panel",
    "claude_code": "as a clickable file_path:line reference",
}
ASSET_FORM_RESOURCE_URI = "ui://apyguard-apiscout/asset-form-v2.html"
WORKBENCH_RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
    },
}
WORKBENCH_TOOL_META = {
    "ui": {
        "resourceUri": WORKBENCH_RESOURCE_URI,
    },
    # Keep the legacy key for Codex hosts that have not migrated to nested UI metadata.
    "ui/resourceUri": WORKBENCH_RESOURCE_URI,
    # Codex compatibility alias for binding a tool result to its UI resource.
    "openai/outputTemplate": WORKBENCH_RESOURCE_URI,
}
ASSET_FORM_TOOL_META = {
    "ui": {
        "resourceUri": ASSET_FORM_RESOURCE_URI,
    },
    "ui/resourceUri": ASSET_FORM_RESOURCE_URI,
    "openai/outputTemplate": ASSET_FORM_RESOURCE_URI,
}


@mcp.resource(
    uri=WORKBENCH_RESOURCE_URI,
    name="apyguard-apiscout-workbench",
    title="APIScout Workbench",
    description="Interactive API inventory, findings, and endpoint review workbench.",
    mime_type="text/html;profile=mcp-app",
    meta=WORKBENCH_RESOURCE_META,
)
def get_workbench_resource() -> str:
    return render_html_resource("workbench.html")


@mcp.resource(
    uri=ASSET_FORM_RESOURCE_URI,
    name="apyguard-asset-creation-form",
    title="Create ApyGuard Asset",
    description="Collect ApyGuard asset metadata and create an asset from a local API project.",
    mime_type="text/html;profile=mcp-app",
    meta=WORKBENCH_RESOURCE_META,
)
def get_asset_form_resource() -> str:
    return render_html_resource("asset_form.html")


def render_html_resource(resource_name: str) -> str:
    resource_path = Path(__file__).with_name(resource_name)
    logo_path = Path(__file__).with_name("apyguard-logo.png")
    logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    html = resource_path.read_text(encoding="utf-8")
    return (
        html.replace("__APYGUARD_LOGO_DATA_URI__", f"data:image/png;base64,{logo_data}")
        .replace("__APYGUARD_PLUGIN_VERSION__", __version__)
        .replace(
            "__APYGUARD_SOURCE_LINK_INSTRUCTION__",
            SOURCE_LINK_INSTRUCTIONS[get_client_type()],
        )
    )


@mcp.tool(
    name="get_apyguard_auth_status",
    title="Get ApyGuard authentication status",
    description=(
        "Check whether this local APIScout installation has completed the required "
        "ApyGuard sign-in, and whether one is waiting for approval. This does not return "
        "the access token."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_apyguard_auth_status() -> AuthStatus:
    return get_apyguard_auth_status_service()


@mcp.tool(
    name="start_apyguard_login",
    title="Start ApyGuard login",
    description=(
        "Start an ApyGuard sign-in and return the login URL and the code the user "
        "approves in the browser. Show both to the user."
    ),
    annotations=AUTH_START_ANNOTATIONS,
    structured_output=True,
)
def start_apyguard_login() -> LoginStartReport:
    return start_apyguard_login_service()


@mcp.tool(
    name="complete_apyguard_login",
    title="Complete ApyGuard login",
    description=(
        "Finish the ApyGuard sign-in once the user has approved the code in the browser, "
        "and store this Codex or Claude Code installation's access token. Call this after "
        "the user confirms the approval."
    ),
    annotations=AUTH_START_ANNOTATIONS,
    structured_output=True,
)
def complete_apyguard_login() -> LoginCompletionReport:
    return complete_apyguard_login_service()


@mcp.tool(
    name="sign_out_apyguard",
    title="Sign out of ApyGuard",
    description="Revoke this installation's ApyGuard access token and remove it from this computer.",
    annotations=REMOTE_WRITE_ANNOTATIONS,
    structured_output=True,
)
def sign_out_apyguard() -> SignOutReport:
    return sign_out_apyguard_service()


@mcp.tool(
    name="scan_api_project",
    title="Scan API project",
    description=(
        "Scan a local backend project and return its framework, OpenAPI document, "
        "endpoint count, source locations, and API inventory findings."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def scan_api_project(project_path: str) -> ScanReport:
    require_apyguard_authentication()
    return scan_api_project_service(project_path=project_path)


@mcp.tool(
    name="open_api_workbench",
    title="Open APIScout Workbench",
    description=(
        "Scan a local backend project and render an interactive APIScout workbench "
        "with summary metrics, inventory findings, endpoint evidence actions, raw "
        "JSON, OpenAPI export, and an ApyGuard Create Asset form. Use after "
        "authentication."
    ),
    annotations=WORKBENCH_ANNOTATIONS,
    meta=WORKBENCH_TOOL_META,
    structured_output=True,
)
def open_api_workbench(project_path: str) -> ScanReport:
    require_apyguard_authentication()
    return scan_api_project_service(project_path=project_path)


@mcp.tool(
    name="open_apyguard_asset_form",
    title="Open ApyGuard asset creation form",
    description=(
        "Open the ApyGuard asset creation form for a local backend project. The form "
        "collects asset metadata and sends the generated OpenAPI document to ApyGuard."
    ),
    annotations=WORKBENCH_ANNOTATIONS,
    meta=ASSET_FORM_TOOL_META,
    structured_output=True,
)
def open_apyguard_asset_form(project_path: str) -> AssetFormContext:
    require_apyguard_authentication()
    return {"project_path": str(resolve_project_path(project_path=project_path))}


@mcp.tool(
    name="create_apyguard_asset",
    title="Create ApyGuard asset",
    description=(
        "Scan a local backend project and upload its generated OpenAPI JSON to ApyGuard. "
        "Creates the project asset only once; when an asset already exists for this "
        "project, imports the new documentation into it instead of creating a duplicate. "
        "operation already_imported means ApyGuard already holds this documentation and "
        "imported nothing new. asset_name, analyzed_api_url, description, and environment "
        "are required only when the project has no asset yet. analyzed_api_url cannot be a "
        "local address, because ApyGuard scans the API remotely."
    ),
    annotations=REMOTE_WRITE_ANNOTATIONS,
    structured_output=True,
)
def create_apyguard_asset(
    project_path: str,
    asset_name: str = "",
    analyzed_api_url: str = "",
    description: str = "",
    environment: str = "",
    tags: list[str] | None = None,
) -> AssetCreationReport:
    require_apyguard_authentication()
    return create_apyguard_asset_service(
        project_path=project_path,
        asset_name=asset_name,
        analyzed_api_url=analyzed_api_url,
        description=description,
        environment=environment,
        tags=tags or [],
    )


@mcp.tool(
    name="list_apyguard_assets",
    title="List ApyGuard assets and API collections",
    description=(
        "List the ApyGuard APIScout asset associated with a local project and the "
        "API collections underneath it. Requires authentication and uses the same "
        "project identity as asset creation."
    ),
    annotations=REMOTE_READ_ANNOTATIONS,
    structured_output=True,
)
def list_apyguard_assets(project_path: str) -> AssetListingReport:
    require_apyguard_authentication()
    return list_apyguard_assets_service(project_path=project_path)


@mcp.tool(
    name="get_apyguard_progress",
    title="Get ApyGuard import and scan progress",
    description=(
        "Read once how the latest ApyGuard import and live scan of a local project stand. "
        "Each has a state: running, completed, failed, stopped, not_started, or unknown, "
        "which means ApyGuard reported a status this plugin does not know; status holds "
        "ApyGuard's own word for it. progress is a percentage when ApyGuard reports one, "
        "and detail the reason when it failed. asset_linked false means the project has no "
        "asset yet. This does not start an import or a scan."
    ),
    annotations=REMOTE_READ_ANNOTATIONS,
    structured_output=True,
)
def get_apyguard_progress(project_path: str) -> ProgressReport:
    require_apyguard_authentication()
    return get_apyguard_progress_service(project_path=project_path)


@mcp.tool(
    name="list_api_findings",
    title="List API findings",
    description=(
        "Scan a local backend project and list inventory findings filtered by "
        "sensitivity and category. Pass empty lists to include all values; for "
        "example, use sensitivity_levels=[\"high\"] and categories=[]. Endpoints missing "
        "from the project's OpenAPI document are listed only when categories includes "
        "\"undocumented_endpoint\"."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def list_api_findings(
    project_path: str,
    sensitivity_levels: list[str],
    categories: list[str],
) -> FindingsReport:
    require_apyguard_authentication()
    return list_api_findings_service(
        project_path=project_path,
        sensitivity_levels=sensitivity_levels,
        categories=categories,
    )


@mcp.tool(
    name="review_api_project",
    title="Prepare AI API review context",
    description=(
        "Scan a local backend project and return source-backed review context for "
        "the active model. Includes all findings and endpoint metadata, related "
        "models, source excerpts, and high/medium finding context. This tool does "
        "not call an external AI provider; the active model interprets the evidence."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def review_api_project(project_path: str) -> ApiReviewReport:
    require_apyguard_authentication()
    return review_api_project_service(project_path=project_path)


@mcp.tool(
    name="get_endpoint_context",
    title="Get endpoint context",
    description=(
        "Return source-backed endpoint evidence for the active model to explain. "
        "Pass the exact HTTP method and route path. Use null review_context when "
        "no specific documentation gap is requested."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_endpoint_context(
    project_path: str,
    method: str,
    path: str,
    review_context: str | None,
) -> EndpointContext:
    require_apyguard_authentication()
    return get_endpoint_context_service(
        project_path=project_path,
        method=method,
        endpoint_path=path,
        review_context=review_context,
    )


@mcp.tool(
    name="export_openapi",
    title="Export OpenAPI",
    description=(
        "Scan a local project and write JSON or YAML OpenAPI inside that project. "
        "Use an output path ending in .json, .yaml, or .yml. This tool overwrites "
        "the target when it already exists."
    ),
    annotations=WRITE_ANNOTATIONS,
    structured_output=True,
)
def export_openapi(
    project_path: str,
    output_path: str,
    output_format: str,
) -> ExportReport:
    require_apyguard_authentication()
    return export_openapi_service(
        project_path=project_path,
        output_path=output_path,
        output_format=output_format,
    )


@mcp.tool(
    name="compare_openapi",
    title="Compare OpenAPI documents",
    description=(
        "Compare two local OpenAPI documents and return deterministic endpoint, "
        "parameter, request and response body, response status, and security changes."
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def compare_openapi(
    previous_document_path: str,
    current_document_path: str,
) -> ApiComparisonReport:
    require_apyguard_authentication()
    return compare_openapi_service(
        previous_document_path=previous_document_path,
        current_document_path=current_document_path,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
