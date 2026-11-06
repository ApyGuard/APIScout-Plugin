from __future__ import annotations

from dataclasses import asdict
import ipaddress
import json
import logging
import math
import ssl
import time
from http.client import HTTPException
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

import certifi
from yaml import YAMLError

from apyguard_apiscout_mcp import __version__
from apyguard_engine.analysis.api_changes import ApiChange, compare_api_documents
from apyguard_engine.exporters.documentation_merge import load_openapi_document
from apyguard_engine.exporters.openapi import (
    build_openapi_document,
    inventory_finding_to_dict,
    to_json,
    to_yaml,
)
from apyguard_engine.models.types import Endpoint, InventoryFinding, ModelSchema, ScanResult
from apyguard_engine.parsers.scanner import scan_project

from apyguard_apiscout_mcp.errors import (
    AssetCreationRefusedError,
    AssetCreationServiceError,
    AssetImportRunningError,
    AssetListingServiceError,
    AssetNotFoundError,
    AuthenticationRequiredError,
    EndpointNotFoundError,
    InvalidOpenApiDocumentError,
    UnsupportedOutputFormatError,
)
from apyguard_apiscout_mcp.auth import (
    discard_rejected_token,
    get_api_url,
    get_authenticated_apyguard_token,
    get_client_type,
)
from apyguard_apiscout_mcp.paths import (
    resolve_existing_file,
    resolve_project_file,
    resolve_project_path,
)
from apyguard_apiscout_mcp.types import (
    ApiComparisonReport,
    ApiReviewItem,
    ApiReviewReport,
    EndpointContext,
    ExportReport,
    FindingsReport,
    OperationProgress,
    ProgressReport,
    ProgressState,
    ScanReport,
    SourceLocation,
    AssetCreationReport,
    AssetListingReport,
    ApyGuardAsset,
    ApyGuardCollection,
)


VALID_SENSITIVITY_LEVELS = frozenset({"low", "medium", "high"})
VALID_FINDING_CATEGORIES = frozenset(
    {
        "deprecated_version",
        "duplicate_route",
        "administrative",
        "authentication",
        "pii_data",
        "undocumented_endpoint",
    }
)
VALID_OUTPUT_FORMATS = frozenset({"json", "yaml", "yml"})
SOURCE_EXCERPT_CONTEXT_LINES = 35
SOURCE_EXCERPT_MAX_CHARACTERS = 12_000
# Always under the ApyGuard API URL, because these requests carry the access token.
DISCOVERY_RESULTS_PATH = "/api/api_discovery/integrations/{client_type}/discovery_results"
ASSETS_PATH = "/api/api_discovery/integrations/{client_type}/assets"
REJECTED_TOKEN_MESSAGE = (
    "ApyGuard refused this installation's sign-in: it was revoked or has expired. Call "
    "start_apyguard_login to sign in again."
)
ASSET_REQUEST_TIMEOUT_SECONDS = 30.0
ASSET_REQUEST_ATTEMPTS = 3
ASSET_REQUEST_RETRY_DELAYS_SECONDS = (0.5, 1.0)
ASSET_VISIBILITY_ATTEMPTS = 3
ASSET_VISIBILITY_DELAY_SECONDS = 1.0
MAX_ASSET_ERROR_RESPONSE_CHARACTERS = 4_096
IMPORT_RUNNING_MESSAGE = (
    "ApyGuard is already importing this project's asset, so this scan was not sent. "
    "Wait until that import finishes, then import again."
)
# What ApyGuard's statuses mean to someone waiting on them (APICollection and APIScan.STATUS_CHOICES).
IMPORT_PROGRESS_STATES: Mapping[str, ProgressState] = {
    "Import Requested": "running",
    "Import Started": "running",
    "Importing": "running",
    "Stopping": "running",
    "Import Completed": "completed",
    "Error": "failed",
    "Stopped": "stopped",
    "Cancelled": "stopped",
}
SCAN_PROGRESS_STATES: Mapping[str, ProgressState] = {
    "Queued": "running",
    "Running": "running",
    "Stopping": "running",
    "Completed": "completed",
    "Failed": "failed",
    "Stopped": "stopped",
    "Cancelled": "stopped",
}
ASSET_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger(__name__)


def _scan(project_root: Path) -> ScanResult:
    return scan_project(str(project_root), inference_config=None)


def _serialize_findings(
    findings: Sequence[InventoryFinding],
) -> list[dict[str, Any]]:
    return [inventory_finding_to_dict(finding=finding) for finding in findings]


def scan_api_project(project_path: str) -> ScanReport:
    project_root = resolve_project_path(project_path=project_path)
    scan = _scan(project_root=project_root)
    findings = _serialize_findings(findings=scan.analysis.findings)
    return {
        "project_path": str(project_root),
        "framework": scan.framework,
        "operation_count": len(scan.endpoints),
        "finding_count": len(findings),
        "findings": findings,
        "undocumented_endpoint_count": len(scan.analysis.undocumented_endpoints),
        "openapi": build_openapi_document(scan=scan),
    }


def _get_discovery_results_url() -> str:
    return f"{get_api_url()}{DISCOVERY_RESULTS_PATH.format(client_type=get_client_type())}"


def _build_asset_request_body(
    project_root: Path,
    report: ScanReport,
    asset_name: str,
    analyzed_api_url: str,
    description: str,
    environment: str,
    tags: Sequence[str],
) -> dict[str, Any]:
    codex_project_id = str(uuid5(NAMESPACE_URL, f"apyguard-apiscout:{project_root}"))
    return {
        "url": analyzed_api_url,
        "api_url": analyzed_api_url,
        "codex_project_id": codex_project_id,
        "asset_name": asset_name,
        "description": description,
        "environment": environment,
        "tags": list(tags),
        "openapi_json": json.dumps(report["openapi"], ensure_ascii=False),
    }


def _build_import_request_body(
    project_root: Path,
    report: ScanReport,
) -> dict[str, Any]:
    codex_project_id = str(uuid5(NAMESPACE_URL, f"apyguard-apiscout:{project_root}"))
    return {
        "codex_project_id": codex_project_id,
        "openapi_json": json.dumps(report["openapi"], ensure_ascii=False),
    }


def _post_asset_request_once(
    url: str,
    access_token: str,
    request_body: Mapping[str, Any],
) -> Any:
    encoded_body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=encoded_body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"ApyGuard-APIScout-MCP/{__version__}",
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urlopen(
            request,
            timeout=ASSET_REQUEST_TIMEOUT_SECONDS,
            context=ASSET_SSL_CONTEXT,
        ) as response:
            response_body = response.read()
            status_code = response.status
    except HTTPError as error:
        if error.code == 401:
            # Not retried: the token is revoked or expired, and the user signs in again.
            discard_rejected_token(access_token=access_token)
            raise AuthenticationRequiredError(REJECTED_TOKEN_MESSAGE) from error
        response_body = error.read()[:MAX_ASSET_ERROR_RESPONSE_CHARACTERS]
        response_text = response_body.decode("utf-8", errors="replace")
        if 400 <= error.code < 500:
            raise _refused_request_error(url=url, status_code=error.code, response_text=response_text) from error
        raise AssetCreationServiceError(
            f'ApyGuard asset creation failed: url="{url}" status={error.code} '
            f'response_body="{response_text}".'
        ) from error
    except (OSError, HTTPException) as error:
        # urllib wraps only connection errors in URLError: a read timeout or a dropped connection
        # arrives as it is.
        raise AssetCreationServiceError(
            f'ApyGuard asset creation failed before receiving a complete response: url="{url}" '
            f'error="{error}".'
        ) from error
    try:
        response_payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssetCreationServiceError(
            f'ApyGuard asset creation returned invalid JSON: url="{url}" '
            f'status={status_code} response_body="{response_body.decode("utf-8", errors="replace")}".'
        ) from error
    if status_code < 200 or status_code >= 300:
        raise AssetCreationServiceError(
            f'ApyGuard asset creation failed: url="{url}" status={status_code} '
            f'response_body={response_payload}.'
        )
    return response_payload


def _refused_request_error(url: str, status_code: int, response_text: str) -> AssetCreationRefusedError:
    """The error for an asset request ApyGuard refused: an import already running, or its own reason."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        payload = None
    record = payload.get("response") if isinstance(payload, Mapping) else None
    if status_code == 409 and isinstance(record, Mapping) and record.get("import_running") is True:
        return AssetImportRunningError(IMPORT_RUNNING_MESSAGE)
    if isinstance(record, Mapping):
        reason = _get_string(record, ("message", "error"))
    elif isinstance(record, str) and record.strip():
        reason = record.strip()
    else:
        reason = _get_string(payload, ("error", "message", "detail")) if isinstance(payload, Mapping) else None
    return AssetCreationRefusedError(
        f'ApyGuard refused the asset request: url="{url}" status={status_code} '
        + (f"reason={reason!r}." if reason is not None else f"response_body={response_text!r}.")
    )


def _post_asset_request(
    url: str,
    access_token: str,
    request_body: Mapping[str, Any],
) -> Any:
    last_error: AssetCreationServiceError | None = None
    for attempt in range(1, ASSET_REQUEST_ATTEMPTS + 1):
        try:
            return _post_asset_request_once(
                url=url,
                access_token=access_token,
                request_body=request_body,
            )
        except AssetCreationRefusedError:
            # Not retried: ApyGuard refuses it again the same way.
            raise
        except AssetCreationServiceError as error:
            last_error = error
            logger.warning(
                "ApyGuard asset creation attempt failed",
                extra={"attempt": attempt, "attempts": ASSET_REQUEST_ATTEMPTS, "url": url, "error": str(error)},
            )
            if attempt < ASSET_REQUEST_ATTEMPTS:
                time.sleep(ASSET_REQUEST_RETRY_DELAYS_SECONDS[attempt - 1])
    if last_error is None:
        raise AssetCreationServiceError(
            f'ApyGuard asset creation did not run: url="{url}".'
        )
    raise last_error


def _is_already_imported(response_payload: Any) -> bool:
    """ApyGuard answered with the import that holds this documentation already, and imported nothing."""
    record = response_payload.get("response") if isinstance(response_payload, Mapping) else None
    return isinstance(record, Mapping) and record.get("already_imported") is True


def _get_created_asset_id(response_payload: Any) -> str:
    if not isinstance(response_payload, Mapping):
        raise AssetCreationServiceError(
            f"ApyGuard asset creation response was not an object: response_body={response_payload!r}."
        )
    response_record = response_payload.get("response")
    asset_record = response_record if isinstance(response_record, Mapping) else response_payload
    asset_id = _get_string(asset_record, ("asset_id", "assetId", "id"))
    if asset_id is None:
        message = _get_string(response_payload, ("message", "error", "detail"))
        raise AssetCreationServiceError(
            "ApyGuard returned a successful HTTP response without creating an asset: "
            f"response_body={response_payload!r}"
            f"{f' message={message!r}' if message is not None else ''}."
        )
    return asset_id


def _verify_created_asset(
    project_id: str,
    asset_id: str,
    access_token: str,
) -> tuple[str, bool]:
    last_error: AssetListingServiceError | None = None
    for attempt in range(1, ASSET_VISIBILITY_ATTEMPTS + 1):
        try:
            response_payload = _get_json(
                url=_get_asset_request_url(project_id=project_id),
                access_token=access_token,
            )
            asset_records = _get_records(payload=response_payload, label="assets")
            matching_record = next(
                (
                    record
                    for record in asset_records
                    if _get_string(record, ("id", "asset_id", "assetId")) == asset_id
                ),
                None,
            )
            if matching_record is not None:
                asset_status = _get_string(
                    matching_record,
                    ("status", "import_status", "importStatus"),
                ) or "Unknown"
                normalized_status = asset_status.strip().lower().replace("-", "_").replace(" ", "_")
                if normalized_status in {"failed", "failure", "error", "cancelled", "canceled"}:
                    raise AssetCreationServiceError(
                        f'ApyGuard asset import failed: asset_id="{asset_id}" status="{asset_status}" '
                        f"asset={dict(matching_record)!r}."
                    )
                return asset_status, True
        except AssetCreationServiceError:
            raise
        except AssetListingServiceError as error:
            last_error = error
            logger.warning(
                "ApyGuard created asset visibility check failed",
                extra={
                    "attempt": attempt,
                    "attempts": ASSET_VISIBILITY_ATTEMPTS,
                    "asset_id": asset_id,
                    "error": str(error),
                },
            )
        if attempt < ASSET_VISIBILITY_ATTEMPTS:
            time.sleep(ASSET_VISIBILITY_DELAY_SECONDS)
    return (
        "accepted_not_yet_visible",
        False,
    )


def _is_local_address(url: str) -> bool:
    """Whether a URL points at the machine that opens it."""
    try:
        # Without a scheme, as in localhost:8000, urlparse does not find the host.
        host = urlparse(url if "://" in url else f"//{url}").hostname
    except ValueError as error:
        raise ValueError(f'Analyzed API URL is not a valid URL: analyzed_api_url="{url}".') from error
    if host is None:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A host name rather than an IP address.
        return False
    return address.is_loopback or address.is_unspecified


def _require_new_asset_fields(
    asset_name: str,
    analyzed_api_url: str,
    description: str,
    environment: str,
) -> None:
    if not asset_name.strip():
        raise ValueError('Asset name must not be empty: asset_name="".')
    if not analyzed_api_url.strip():
        raise ValueError('Analyzed API URL must not be empty: analyzed_api_url="".')
    if not description.strip():
        raise ValueError('Asset description must not be empty: description="".')
    if not environment.strip():
        raise ValueError('Asset environment must not be empty: environment="".')
    if _is_local_address(url=analyzed_api_url.strip()):
        raise ValueError(
            "Analyzed API URL cannot be a local address, because ApyGuard scans the API remotely. "
            f'Use a staging URL or port forwarding: analyzed_api_url="{analyzed_api_url.strip()}".'
        )


def create_apyguard_asset(
    project_path: str,
    asset_name: str = "",
    analyzed_api_url: str = "",
    description: str = "",
    environment: str = "",
    tags: Sequence[str] = (),
) -> AssetCreationReport:
    """Create a project asset once, then import later scans into that asset.

    An import sends only the documentation, so the asset's fields apply to creation alone.
    """
    project_root = resolve_project_path(project_path=project_path)
    access_token = get_authenticated_apyguard_token()
    project_id = str(uuid5(NAMESPACE_URL, f"apyguard-apiscout:{project_root}"))
    existing_asset_record = _get_existing_asset_record(
        project_id=project_id,
        access_token=access_token,
    )
    if existing_asset_record is None:
        _require_new_asset_fields(
            asset_name=asset_name,
            analyzed_api_url=analyzed_api_url,
            description=description,
            environment=environment,
        )
    report = scan_api_project(project_path=str(project_root))
    if existing_asset_record is None:
        request_body = _build_asset_request_body(
            project_root=project_root,
            report=report,
            asset_name=asset_name.strip(),
            analyzed_api_url=analyzed_api_url.strip(),
            description=description.strip(),
            environment=environment.strip(),
            tags=tags,
        )
        operation = "created"
        existing_asset_id = None
    else:
        request_body = _build_import_request_body(
            project_root=project_root,
            report=report,
        )
        operation = "imported"
        existing_asset_id = _get_string(
            existing_asset_record,
            ("id", "asset_id", "assetId"),
        )
        if existing_asset_id is None:
            raise AssetCreationServiceError(
                "ApyGuard returned an existing project asset without an asset id: "
                f"asset={dict(existing_asset_record)!r}."
            )
    response_payload = _post_asset_request(
        url=_get_discovery_results_url(),
        access_token=access_token,
        request_body=request_body,
    )
    if _is_already_imported(response_payload=response_payload):
        operation = "already_imported"
    asset_id = (
        existing_asset_id
        if existing_asset_id is not None
        else _get_created_asset_id(response_payload=response_payload)
    )
    asset_status, visibility_verified = _verify_created_asset(
        project_id=project_id,
        asset_id=asset_id,
        access_token=access_token,
    )
    existing_asset_name = _get_string(
        existing_asset_record or {},
        ("name", "asset_name", "assetName", "title"),
    )
    existing_api_url = _get_string(
        existing_asset_record or {},
        ("api_url", "apiUrl", "url", "base_url", "target"),
    )
    return {
        "project_path": str(project_root),
        "operation": operation,
        "asset_name": existing_asset_name or asset_name.strip(),
        "analyzed_api_url": existing_api_url or analyzed_api_url.strip(),
        "asset_id": asset_id,
        "asset_status": asset_status,
        "visibility_verified": visibility_verified,
        "response": response_payload,
    }


def _get_assets_url() -> str:
    return f"{get_api_url()}{ASSETS_PATH.format(client_type=get_client_type())}"


def _get_string(record: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _get_int(record: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                continue
    return None


def _get_records(payload: Any, label: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, Mapping):
        response = payload.get("response")
        if isinstance(response, list):
            records = response
        elif isinstance(response, Mapping):
            records = _get_records(response, label)
        elif isinstance(payload.get("data"), Mapping):
            records = _get_records(cast(Mapping[str, Any], payload["data"]), label)
        else:
            records = next(
                (
                    payload.get(key)
                    for key in ("assets", "api_collections", "collections", "items", "results", "data")
                    if isinstance(payload.get(key), list)
                ),
                None,
            )
            if records is None:
                records = [payload] if _get_string(payload, ("id", "asset_id", "api_collection_id")) else []
    else:
        records = []
    if not all(isinstance(record, Mapping) for record in records):
        raise AssetListingServiceError(
            f"ApyGuard {label} response included a non-object record: response_body={payload!r}."
        )
    return [cast(Mapping[str, Any], record) for record in records]


def _get_optional_string(record: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    return _get_string(record, keys)


def _serialize_collection(record: Mapping[str, Any]) -> ApyGuardCollection:
    collection_id = _get_string(record, ("id", "api_collection_id", "apiCollectionId"))
    name = _get_string(record, ("name", "title"))
    if collection_id is None or name is None:
        raise AssetListingServiceError(
            f"ApyGuard API collection response omitted id or name: collection={dict(record)!r}."
        )
    return {
        "id": collection_id,
        "name": name,
        "url": _get_string(record, ("url", "api_url", "apiUrl")) or "",
        "status": _get_optional_string(record, ("status", "import_status", "importStatus")),
        "detail": _get_optional_string(record, ("import_error_detail", "message", "detail", "description")),
        "endpoint_count": _get_int(record, ("endpoint_count", "endpointCount")),
        "risk_score": _get_optional_string(record, ("risk_score", "riskScore")),
        "created_at": _get_optional_string(record, ("created_at", "createdAt", "created")),
        "updated_at": _get_optional_string(record, ("updated_at", "updatedAt", "updated")),
        "last_import_date": _get_optional_string(record, ("last_import_date", "lastImportDate")),
        "last_scan_date": _get_optional_string(record, ("last_scan_date", "lastScanDate")),
    }


def _serialize_asset(record: Mapping[str, Any], collections: list[ApyGuardCollection]) -> ApyGuardAsset:
    asset_id = _get_string(record, ("id", "asset_id", "assetId"))
    name = _get_string(record, ("name", "asset_name", "assetName", "title"))
    if asset_id is None or name is None:
        raise AssetListingServiceError(
            f"ApyGuard asset response omitted id or name: asset={dict(record)!r}."
        )
    return {
        "id": asset_id,
        "name": name,
        "api_url": _get_string(record, ("api_url", "apiUrl", "url", "base_url", "target")) or "",
        "status": _get_string(record, ("status", "import_status", "importStatus")) or "Unknown",
        "detail": _get_optional_string(record, ("message", "detail", "description")),
        "endpoint_count": _get_int(record, ("endpoint_count", "endpointCount")),
        "risk_score": _get_optional_string(record, ("risk_score", "riskScore")),
        "created_at": _get_optional_string(record, ("created_at", "createdAt", "created")),
        "updated_at": _get_optional_string(record, ("updated_at", "updatedAt", "updated")),
        "last_import_date": _get_optional_string(record, ("last_import_date", "lastImportDate")),
        "last_scan_date": _get_optional_string(record, ("last_scan_date", "lastScanDate")),
        "collections": collections,
    }


def _get_asset_request_url(project_id: str) -> str:
    return f"{_get_assets_url().rstrip('/')}/{quote(project_id, safe='')}"


def _get_collection_request_url(project_id: str) -> str:
    return f"{_get_asset_request_url(project_id=project_id)}/api_collections"


def _get_asset_records(project_id: str, access_token: str) -> list[Mapping[str, Any]]:
    """The project's assets: ApyGuard answers 404 when the project has none."""
    try:
        return _get_records(
            payload=_get_json(url=_get_asset_request_url(project_id=project_id), access_token=access_token),
            label="assets",
        )
    except AssetNotFoundError:
        return []


def _get_existing_asset_record(
    project_id: str,
    access_token: str,
) -> Mapping[str, Any] | None:
    asset_records = _get_asset_records(project_id=project_id, access_token=access_token)
    return asset_records[0] if asset_records else None


def _get_json_once(url: str, access_token: str) -> Any:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": f"ApyGuard-APIScout-MCP/{__version__}",
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urlopen(
            request,
            timeout=ASSET_REQUEST_TIMEOUT_SECONDS,
            context=ASSET_SSL_CONTEXT,
        ) as response:
            response_body = response.read()
            status_code = response.status
    except HTTPError as error:
        if error.code == 401:
            # Not retried: the token is revoked or expired, and the user signs in again.
            discard_rejected_token(access_token=access_token)
            raise AuthenticationRequiredError(REJECTED_TOKEN_MESSAGE) from error
        response_body = error.read()[:MAX_ASSET_ERROR_RESPONSE_CHARACTERS]
        error_type = AssetNotFoundError if error.code == 404 else AssetListingServiceError
        raise error_type(
            f'ApyGuard asset listing failed: url="{url}" status={error.code} '
            f'response_body="{response_body.decode("utf-8", errors="replace")}".'
        ) from error
    except (OSError, HTTPException) as error:
        raise AssetListingServiceError(
            f'ApyGuard asset listing failed before receiving a complete response: url="{url}" error="{error}".'
        ) from error
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssetListingServiceError(
            f'ApyGuard asset listing returned invalid JSON: url="{url}" status={status_code} '
            f'response_body="{response_body.decode("utf-8", errors="replace")}".'
        ) from error
    if status_code < 200 or status_code >= 300:
        raise AssetListingServiceError(
            f"ApyGuard asset listing failed: url=\"{url}\" status={status_code} response_body={payload!r}."
        )
    return payload


def _get_json(url: str, access_token: str) -> Any:
    last_error: AssetListingServiceError | None = None
    for attempt in range(1, ASSET_REQUEST_ATTEMPTS + 1):
        try:
            return _get_json_once(url=url, access_token=access_token)
        except AssetNotFoundError:
            # Not retried: ApyGuard answers the same way until the asset exists.
            raise
        except AssetListingServiceError as error:
            last_error = error
            logger.warning(
                "ApyGuard asset listing attempt failed",
                extra={"attempt": attempt, "attempts": ASSET_REQUEST_ATTEMPTS, "url": url, "error": str(error)},
            )
            if attempt < ASSET_REQUEST_ATTEMPTS:
                time.sleep(ASSET_REQUEST_RETRY_DELAYS_SECONDS[attempt - 1])
    if last_error is None:
        raise AssetListingServiceError(f'ApyGuard asset listing did not run: url="{url}".')
    raise last_error


def list_apyguard_assets(project_path: str) -> AssetListingReport:
    """List the Codex project asset and its API collections from ApyGuard."""
    project_root = resolve_project_path(project_path=project_path)
    project_id = str(uuid5(NAMESPACE_URL, f"apyguard-apiscout:{project_root}"))
    access_token = get_authenticated_apyguard_token()
    asset_records = _get_asset_records(project_id=project_id, access_token=access_token)
    collection_records = (
        _get_records(
            payload=_get_json(url=_get_collection_request_url(project_id=project_id), access_token=access_token),
            label="API collections",
        )
        if asset_records
        else []
    )
    collections = [_serialize_collection(record=record) for record in collection_records]
    assets = [
        _serialize_asset(record=record, collections=collections if index == 0 else [])
        for index, record in enumerate(asset_records)
    ]
    return {
        "project_path": str(project_root),
        "codex_project_id": project_id,
        "asset_count": len(assets),
        "collection_count": len(collections),
        "assets": assets,
    }


def _read_progress(value: object) -> int | None:
    """A percentage from 0 to 100, or None: a scan's progress is free text in ApyGuard."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        percentage = float(value.strip().rstrip("%") if isinstance(value, str) else value)
    except ValueError:
        return None
    if not math.isfinite(percentage):
        return None
    return max(0, min(100, math.floor(percentage + 0.5)))


def _read_operation_progress(record: object, states: Mapping[str, ProgressState]) -> OperationProgress:
    if not isinstance(record, Mapping):
        return {"state": "not_started", "status": None, "progress": None, "detail": None, "updated_at": None}
    status = _get_string(record, ("status",))
    return {
        "state": states.get(status, "unknown") if status is not None else "unknown",
        "status": status,
        "progress": _read_progress(record.get("progress")),
        "detail": _get_string(record, ("detail",)),
        "updated_at": _get_string(record, ("updated_at",)),
    }


def get_apyguard_progress(project_path: str) -> ProgressReport:
    """Read once how the project's latest ApyGuard import and live scan stand."""
    project_root = resolve_project_path(project_path=project_path)
    project_id = str(uuid5(NAMESPACE_URL, f"apyguard-apiscout:{project_root}"))
    access_token = get_authenticated_apyguard_token()
    asset_records = _get_asset_records(project_id=project_id, access_token=access_token)
    if not asset_records:
        return {
            "project_path": str(project_root),
            "asset_linked": False,
            "asset_id": None,
            "asset_name": None,
            "import_progress": _read_operation_progress(record=None, states=IMPORT_PROGRESS_STATES),
            "scan_progress": _read_operation_progress(record=None, states=SCAN_PROGRESS_STATES),
        }
    asset_id = _get_string(asset_records[0], ("id", "asset_id", "assetId"))
    status_url = f"{_get_asset_request_url(project_id=project_id)}/status"
    try:
        payload = _get_json(url=status_url, access_token=access_token)
    except AssetNotFoundError as error:
        # The asset was just listed, so it is this route that is missing, not the asset.
        raise AssetListingServiceError(
            f'ApyGuard does not report progress for this asset yet: asset_id="{asset_id}" url="{status_url}".'
        ) from error
    report = payload.get("response") if isinstance(payload, Mapping) else None
    if not isinstance(report, Mapping):
        raise AssetListingServiceError(
            f'ApyGuard progress answer was not an object: url="{status_url}" response_body={payload!r}.'
        )
    return {
        "project_path": str(project_root),
        "asset_linked": True,
        "asset_id": asset_id,
        "asset_name": _get_string(asset_records[0], ("name", "asset_name", "assetName", "title")),
        "import_progress": _read_operation_progress(record=report.get("import"), states=IMPORT_PROGRESS_STATES),
        "scan_progress": _read_operation_progress(record=report.get("scan"), states=SCAN_PROGRESS_STATES),
    }


def _validate_filters(values: Sequence[str], allowed: frozenset[str], label: str) -> None:
    unsupported_values = sorted(set(values) - allowed)
    if unsupported_values:
        raise ValueError(
            f"Unsupported {label}: values={unsupported_values} "
            f"allowed={sorted(allowed)}."
        )


def list_api_findings(
    project_path: str,
    sensitivity_levels: Sequence[str],
    categories: Sequence[str],
) -> FindingsReport:
    _validate_filters(
        values=sensitivity_levels,
        allowed=VALID_SENSITIVITY_LEVELS,
        label="sensitivity levels",
    )
    _validate_filters(
        values=categories,
        allowed=VALID_FINDING_CATEGORIES,
        label="finding categories",
    )
    project_root = resolve_project_path(project_path=project_path)
    scan = _scan(project_root=project_root)
    # Documentation gaps are not security findings, so only their own category lists them.
    candidate_findings = (
        [*scan.analysis.findings, *scan.analysis.undocumented_endpoints]
        if "undocumented_endpoint" in categories
        else scan.analysis.findings
    )
    filtered_findings = [
        finding
        for finding in candidate_findings
        if (
            not sensitivity_levels
            or finding.sensitivity_level in sensitivity_levels
        )
        and (
            not categories
            or any(category in categories for category in finding.categories)
        )
    ]
    serialized_findings = _serialize_findings(findings=filtered_findings)
    return {
        "project_path": str(project_root),
        "finding_count": len(serialized_findings),
        "findings": serialized_findings,
    }


def _find_endpoint(scan: ScanResult, method: str, endpoint_path: str) -> Endpoint:
    normalized_method = method.upper()
    for endpoint in scan.endpoints:
        if endpoint.method.upper() == normalized_method and endpoint.path == endpoint_path:
            return endpoint
    raise EndpointNotFoundError(
        f'Endpoint not found: method="{normalized_method}" path="{endpoint_path}".'
    )


def _serialize_model(model: ModelSchema) -> dict[str, Any]:
    return cast(dict[str, Any], asdict(model))


def _get_related_models(
    scan: ScanResult,
    endpoint: Endpoint,
) -> dict[str, dict[str, Any]]:
    model_names = {
        model_name
        for model_name in (endpoint.request_body_model, endpoint.response_model)
        if model_name is not None and model_name in scan.models
    }
    return {
        model_name: _serialize_model(model=scan.models[model_name])
        for model_name in sorted(model_names)
    }


def _get_source_lines(project_root: Path, endpoint: Endpoint) -> tuple[Path, list[str]] | None:
    source_path = resolve_project_file(
        project_root=project_root,
        file_path=endpoint.file_path,
        label="Endpoint source file",
    )
    if not source_path.exists() or not source_path.is_file():
        return None
    # A file in a legacy encoding still has its route: only the excerpt's other characters are lost.
    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    return source_path, source_text.splitlines()


def _find_source_symbol_line(source_lines: list[str], endpoint: Endpoint) -> int | None:
    symbol_names = tuple(
        name
        for name in (
            endpoint.source_member_name,
            endpoint.source_symbol_name,
            endpoint.function_name,
        )
        if name is not None and name
    )
    matching_line_index = next(
        (
            index
            for index, line in enumerate(source_lines)
            if any(symbol_name in line for symbol_name in symbol_names)
        ),
        None,
    )
    return matching_line_index


def _get_source_location(
    project_root: Path,
    endpoint: Endpoint,
) -> SourceLocation | None:
    source_data = _get_source_lines(project_root=project_root, endpoint=endpoint)
    if source_data is None:
        return None
    source_path, source_lines = source_data
    matching_line_index = _find_source_symbol_line(
        source_lines=source_lines,
        endpoint=endpoint,
    )
    if matching_line_index is None:
        return None
    return {
        "file_path": str(source_path),
        "start_line": matching_line_index + 1,
        "end_line": matching_line_index + 1,
        "symbol": endpoint.function_name,
    }


def _get_source_excerpt(project_root: Path, endpoint: Endpoint) -> str | None:
    source_data = _get_source_lines(project_root=project_root, endpoint=endpoint)
    if source_data is None:
        return None
    _, source_lines = source_data
    matching_line_index = _find_source_symbol_line(
        source_lines=source_lines,
        endpoint=endpoint,
    )
    if matching_line_index is None:
        return None
    start_index = max(0, matching_line_index - SOURCE_EXCERPT_CONTEXT_LINES)
    end_index = min(
        len(source_lines),
        matching_line_index + SOURCE_EXCERPT_CONTEXT_LINES + 1,
    )
    excerpt = "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(
            source_lines[start_index:end_index],
            start=start_index + 1,
        )
    )
    return excerpt[:SOURCE_EXCERPT_MAX_CHARACTERS]


def get_endpoint_context(
    project_path: str,
    method: str,
    endpoint_path: str,
    review_context: str | None,
) -> EndpointContext:
    project_root = resolve_project_path(project_path=project_path)
    scan = _scan(project_root=project_root)
    endpoint = _find_endpoint(
        scan=scan,
        method=method,
        endpoint_path=endpoint_path,
    )
    return _build_endpoint_context(
        project_root=project_root,
        scan=scan,
        endpoint=endpoint,
        review_context=review_context,
    )


def _build_endpoint_context(
    project_root: Path,
    scan: ScanResult,
    endpoint: Endpoint,
    review_context: str | None,
) -> EndpointContext:
    endpoint_findings = [
        finding
        for finding in scan.analysis.findings
        if finding.path == endpoint.path
        and (finding.method is None or finding.method.upper() == endpoint.method.upper())
    ]
    return {
        "project_path": str(project_root),
        "method": endpoint.method.upper(),
        "path": endpoint.path,
        "framework": scan.framework,
        "endpoint": cast(dict[str, Any], asdict(endpoint)),
        "related_models": _get_related_models(scan=scan, endpoint=endpoint),
        "findings": _serialize_findings(findings=endpoint_findings),
        "review_context": review_context,
        "source_excerpt": _get_source_excerpt(
            project_root=project_root,
            endpoint=endpoint,
        ),
        "source_location": _get_source_location(
            project_root=project_root,
            endpoint=endpoint,
        ),
    }


def review_api_project(project_path: str) -> ApiReviewReport:
    """Prepare source-backed context for model-assisted API review."""
    project_root = resolve_project_path(project_path=project_path)
    scan = _scan(project_root=project_root)
    findings = _serialize_findings(findings=scan.analysis.findings)
    review_items: list[ApiReviewItem] = []
    reviewed_endpoints: set[tuple[str, str]] = set()
    for finding, serialized_finding in zip(scan.analysis.findings, findings):
        if finding.sensitivity_level not in {"high", "medium"}:
            continue
        if finding.method is None or finding.path is None:
            continue
        endpoint_key = (finding.method.upper(), finding.path)
        if endpoint_key in reviewed_endpoints:
            continue
        endpoint = _find_endpoint(
            scan=scan,
            method=finding.method,
            endpoint_path=finding.path,
        )
        reviewed_endpoints.add(endpoint_key)
        review_items.append(
            {
                "finding": serialized_finding,
                "endpoint_context": _build_endpoint_context(
                    project_root=project_root,
                    scan=scan,
                    endpoint=endpoint,
                    review_context=(
                        "Produce an evidence-backed security review. Separate facts from "
                        "inference and identify any missing evidence."
                    ),
                ),
            }
        )
    return {
        "project_path": str(project_root),
        "framework": scan.framework,
        "operation_count": len(scan.endpoints),
        "finding_count": len(findings),
        "reviewed_finding_count": len(review_items),
        "findings": findings,
        "review_items": review_items,
    }


def export_openapi(
    project_path: str,
    output_path: str,
    output_format: str,
) -> ExportReport:
    normalized_format = output_format.lower()
    if normalized_format not in VALID_OUTPUT_FORMATS:
        raise UnsupportedOutputFormatError(
            f'Unsupported output format: output_format="{output_format}" '
            f'allowed={sorted(VALID_OUTPUT_FORMATS)}.'
        )
    project_root = resolve_project_path(project_path=project_path)
    resolved_output_path = resolve_project_file(
        project_root=project_root,
        file_path=output_path,
        label="OpenAPI output path",
    )
    expected_suffixes = (
        {".json"} if normalized_format == "json" else {".yaml", ".yml"}
    )
    if resolved_output_path.suffix.lower() not in expected_suffixes:
        raise UnsupportedOutputFormatError(
            f'Output extension does not match format: output_path="{resolved_output_path}" '
            f'output_format="{normalized_format}" expected_extensions={sorted(expected_suffixes)}.'
        )
    output_existed = resolved_output_path.exists()
    scan = _scan(project_root=project_root)
    content = to_json(scan=scan) if normalized_format == "json" else to_yaml(scan=scan)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(content, encoding="utf-8")
    return {
        "project_path": str(project_root),
        "output_path": str(resolved_output_path),
        "output_format": "json" if normalized_format == "json" else "yaml",
        "operation_count": len(scan.endpoints),
        "overwritten": output_existed,
    }


def _load_document(file_path: str, label: str) -> tuple[Path, Mapping[str, Any]]:
    resolved_path = resolve_existing_file(file_path=file_path, label=label)
    try:
        document = load_openapi_document(path=resolved_path)
    except (OSError, TypeError, ValueError, YAMLError) as error:
        raise InvalidOpenApiDocumentError(
            f'{label} is not a valid OpenAPI document: path="{resolved_path}" '
            f'error="{error}".'
        ) from error
    if not isinstance(document, Mapping):
        raise InvalidOpenApiDocumentError(
            f'{label} must contain an OpenAPI object: path="{resolved_path}".'
        )
    return resolved_path, document


def _serialize_api_change(change: ApiChange) -> dict[str, Any]:
    details = {
        "parameter_name": change.parameter_name,
        "parameter_location": change.parameter_location,
        "body": change.body,
        "status_code": change.status_code,
        "property_path": change.property_path,
        "required": change.required,
        "previous_type": change.previous_type,
        "current_type": change.current_type,
    }
    report = {
        "kind": change.kind,
        "method": change.method,
        "path": change.path,
        **{name: value for name, value in details.items() if value is not None},
    }
    if change.kind == "securityChanged":
        # None says no security was declared, so it stays in the report.
        report["previous_security"] = change.previous_security
        report["current_security"] = change.current_security
    return report


def compare_openapi(
    previous_document_path: str,
    current_document_path: str,
) -> ApiComparisonReport:
    previous_path, previous_document = _load_document(
        file_path=previous_document_path,
        label="Previous OpenAPI document",
    )
    current_path, current_document = _load_document(
        file_path=current_document_path,
        label="Current OpenAPI document",
    )
    comparison = compare_api_documents(
        previous_document=previous_document,
        current_document=current_document,
    )
    return {
        "previous_document_path": str(previous_path),
        "current_document_path": str(current_path),
        "changes": [_serialize_api_change(change=change) for change in comparison.changes],
        "added_endpoint_count": comparison.added_endpoint_count,
        "removed_endpoint_count": comparison.removed_endpoint_count,
        "added_parameter_count": comparison.added_parameter_count,
        "removed_parameter_count": comparison.removed_parameter_count,
        "changed_parameter_count": comparison.changed_parameter_count,
        "added_body_property_count": comparison.added_body_property_count,
        "removed_body_property_count": comparison.removed_body_property_count,
        "changed_body_property_count": comparison.changed_body_property_count,
        "added_response_status_count": comparison.added_response_status_count,
        "removed_response_status_count": comparison.removed_response_status_count,
        "changed_security_count": comparison.changed_security_count,
    }
