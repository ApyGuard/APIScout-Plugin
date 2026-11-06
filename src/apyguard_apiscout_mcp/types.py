from __future__ import annotations

from typing import Any, Literal, TypedDict


class AuthStatus(TypedDict):
    authenticated: bool
    login_required: bool
    login_pending: bool


class LoginStartReport(TypedDict):
    authenticated: bool
    login_url: str
    user_code: str
    expires_in: int
    instructions: str


class LoginCompletionReport(TypedDict):
    authenticated: bool
    message: str


class SignOutReport(TypedDict):
    authenticated: bool
    credential_removed: bool
    token_revoked: bool


class ScanReport(TypedDict):
    project_path: str
    framework: str | None
    operation_count: int
    finding_count: int
    findings: list[dict[str, Any]]
    undocumented_endpoint_count: int
    openapi: dict[str, Any]


class AssetCreationReport(TypedDict):
    project_path: str
    # already_imported: ApyGuard holds this documentation already, and imported nothing.
    operation: Literal["created", "imported", "already_imported"]
    asset_name: str
    analyzed_api_url: str
    asset_id: str
    asset_status: str
    visibility_verified: bool
    response: Any


class ApyGuardCollection(TypedDict):
    id: str
    name: str
    url: str
    status: str | None
    detail: str | None
    endpoint_count: int | None
    risk_score: str | None
    created_at: str | None
    updated_at: str | None
    last_import_date: str | None
    last_scan_date: str | None


class ApyGuardAsset(TypedDict):
    id: str
    name: str
    api_url: str
    status: str
    detail: str | None
    endpoint_count: int | None
    risk_score: str | None
    created_at: str | None
    updated_at: str | None
    last_import_date: str | None
    last_scan_date: str | None
    collections: list[ApyGuardCollection]


class AssetListingReport(TypedDict):
    project_path: str
    codex_project_id: str
    asset_count: int
    collection_count: int
    assets: list[ApyGuardAsset]


ProgressState = Literal["not_started", "running", "completed", "failed", "stopped", "unknown"]


class OperationProgress(TypedDict):
    state: ProgressState
    # ApyGuard's own word for it, which unknown passes on.
    status: str | None
    progress: int | None
    detail: str | None
    updated_at: str | None


class ProgressReport(TypedDict):
    project_path: str
    asset_linked: bool
    asset_id: str | None
    asset_name: str | None
    import_progress: OperationProgress
    scan_progress: OperationProgress


class AssetFormContext(TypedDict):
    project_path: str


class FindingsReport(TypedDict):
    project_path: str
    finding_count: int
    findings: list[dict[str, Any]]


class SourceLocation(TypedDict):
    file_path: str
    start_line: int
    end_line: int
    symbol: str


class EndpointContext(TypedDict):
    project_path: str
    method: str
    path: str
    framework: str | None
    endpoint: dict[str, Any]
    related_models: dict[str, dict[str, Any]]
    findings: list[dict[str, Any]]
    review_context: str | None
    source_excerpt: str | None
    source_location: SourceLocation | None


class ApiReviewItem(TypedDict):
    finding: dict[str, Any]
    endpoint_context: EndpointContext


class ApiReviewReport(TypedDict):
    project_path: str
    framework: str | None
    operation_count: int
    finding_count: int
    reviewed_finding_count: int
    findings: list[dict[str, Any]]
    review_items: list[ApiReviewItem]


class ExportReport(TypedDict):
    project_path: str
    output_path: str
    output_format: str
    operation_count: int
    overwritten: bool


class ApiComparisonReport(TypedDict):
    previous_document_path: str
    current_document_path: str
    changes: list[dict[str, Any]]
    added_endpoint_count: int
    removed_endpoint_count: int
    added_parameter_count: int
    removed_parameter_count: int
    changed_parameter_count: int
    added_body_property_count: int
    removed_body_property_count: int
    changed_body_property_count: int
    added_response_status_count: int
    removed_response_status_count: int
    changed_security_count: int
