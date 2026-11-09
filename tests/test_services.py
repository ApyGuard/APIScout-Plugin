from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from apyguard_apiscout_mcp.errors import (
    EndpointNotFoundError,
    InvalidOpenApiDocumentError,
    InvalidProjectPathError,
    PathOutsideProjectError,
    UnsupportedOutputFormatError,
)
from apyguard_apiscout_mcp.services import (
    compare_openapi,
    export_openapi,
    get_endpoint_context,
    list_api_findings,
    review_api_project,
    scan_api_project,
)


@pytest.fixture
def project_with_a_latin5_route(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_bytes(
        (
            "# Yönetici uçları\n"
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            '@app.get("/admin/users")\n'
            "def admin_users():\n"
            "    return []\n"
        ).encode("iso-8859-9")
    )
    return tmp_path


def test_endpoint_context_reads_a_source_file_that_is_not_utf8(project_with_a_latin5_route: Path) -> None:
    context = get_endpoint_context(
        project_path=str(project_with_a_latin5_route),
        method="GET",
        endpoint_path="/admin/users",
        review_context=None,
    )

    assert context["source_location"] is not None
    assert context["source_location"]["start_line"] == 7
    assert "def admin_users" in (context["source_excerpt"] or "")


def test_project_review_survives_a_source_file_that_is_not_utf8(project_with_a_latin5_route: Path) -> None:
    report = review_api_project(project_path=str(project_with_a_latin5_route))

    assert report["reviewed_finding_count"] == 1
    assert report["review_items"][0]["endpoint_context"]["source_excerpt"] is not None


@pytest.fixture
def ai_provider_set_in_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[Any, ...]]:
    """A user whose shell enables the engine's AI explanations, for example for another ApyGuard tool.

    Returns every attempt to resolve a host or open a connection; each attempt is refused.
    """
    monkeypatch.setenv("APYGUARD_AI_ENABLED", "true")
    monkeypatch.setenv("APYGUARD_AI_PROVIDER", "openai")
    monkeypatch.setenv("APYGUARD_AI_API_KEY", "test-key")
    monkeypatch.setenv("APYGUARD_AI_MODEL", "test-model")
    monkeypatch.setenv("APYGUARD_AI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("APYGUARD_AI_CACHE_DIR", str(tmp_path / "ai-cache"))
    attempts: list[tuple[Any, ...]] = []

    def refuse(*args: Any, **kwargs: Any) -> None:
        attempts.append(args)
        raise OSError("this test allows no network access")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return attempts


def test_scan_never_calls_an_ai_provider_set_in_the_environment(
    ai_provider_set_in_the_environment: list[tuple[Any, ...]],
) -> None:
    scan_api_project(project_path=str(Path(__file__).parent / "fixtures" / "fastapi-app"))

    assert ai_provider_set_in_the_environment == []


def test_tools_run_against_real_fastapi_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "fastapi-app"
    report = scan_api_project(project_path=str(fixture_path))
    assert report["framework"] == "fastapi"
    assert report["operation_count"] == 2

    findings = list_api_findings(
        project_path=str(fixture_path),
        sensitivity_levels=[],
        categories=[],
    )
    assert findings["finding_count"] == len(findings["findings"])

    context = get_endpoint_context(
        project_path=str(fixture_path),
        method="GET",
        endpoint_path="/users/{user_id}",
        review_context=None,
    )
    assert context["endpoint"]["function_name"] == "get_user"
    assert context["source_excerpt"] is not None
    assert context["source_location"] == {
        "file_path": str(fixture_path / "main.py"),
        "start_line": 13,
        "end_line": 13,
        "symbol": "get_user",
    }

    export_path = fixture_path / "generated-openapi.json"
    try:
        first_export = export_openapi(
            project_path=str(fixture_path),
            output_path=str(export_path),
            output_format="json",
        )
        second_export = export_openapi(
            project_path=str(fixture_path),
            output_path=str(export_path),
            output_format="json",
        )
        assert first_export["overwritten"] is False
        assert second_export["overwritten"] is True
    finally:
        export_path.unlink(missing_ok=True)


def _copy_fixture_with_spec_for_get_user_only(tmp_path: Path) -> Path:
    project_path = tmp_path / "project"
    shutil.copytree(Path(__file__).parent / "fixtures" / "fastapi-app", project_path)
    (project_path / "docs").mkdir()
    (project_path / "docs" / "api.yaml").write_text(
        "openapi: 3.0.3\n"
        "info: {title: Users, version: '1'}\n"
        "paths:\n"
        "  /users/{user_id}:\n"
        "    get:\n"
        "      responses: {'200': {description: OK}}\n",
        encoding="utf-8",
    )
    return project_path


def test_scan_reports_how_many_endpoints_are_undocumented(tmp_path: Path) -> None:
    project_path = _copy_fixture_with_spec_for_get_user_only(tmp_path)

    report = scan_api_project(project_path=str(project_path))

    assert report["undocumented_endpoint_count"] == 1
    assert report["finding_count"] == len(report["findings"])
    assert not any("undocumented_endpoint" in finding["categories"] for finding in report["findings"])


def test_undocumented_endpoints_are_listed_when_their_category_is_requested(tmp_path: Path) -> None:
    project_path = _copy_fixture_with_spec_for_get_user_only(tmp_path)

    findings = list_api_findings(
        project_path=str(project_path),
        sensitivity_levels=[],
        categories=["undocumented_endpoint"],
    )

    assert [(finding["method"], finding["path"]) for finding in findings["findings"]] == [("POST", "/users")]
    assert findings["finding_count"] == 1


def test_undocumented_endpoints_are_left_out_of_an_unfiltered_listing(tmp_path: Path) -> None:
    project_path = _copy_fixture_with_spec_for_get_user_only(tmp_path)

    findings = list_api_findings(project_path=str(project_path), sensitivity_levels=[], categories=[])

    assert not any("undocumented_endpoint" in finding["categories"] for finding in findings["findings"])


def test_compare_openapi_matches_existing_extension_behavior(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    previous_path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users/{userId}": {
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "get": {
                            "parameters": [
                                {
                                    "name": "verbose",
                                    "in": "query",
                                    "schema": {"type": "boolean"},
                                },
                                {
                                    "name": "locale",
                                    "in": "header",
                                    "schema": {"type": "string"},
                                },
                            ]
                        },
                        "delete": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users/{userId}": {
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "get": {
                            "parameters": [
                                {
                                    "name": "locale",
                                    "in": "header",
                                    "schema": {"type": "string"},
                                },
                                {
                                    "name": "limit",
                                    "in": "query",
                                    "schema": {"type": "integer"},
                                },
                            ]
                        },
                    },
                    "/users": {"post": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    comparison = compare_openapi(
        previous_document_path=str(previous_path),
        current_document_path=str(current_path),
    )
    assert comparison == {
        "previous_document_path": str(previous_path),
        "current_document_path": str(current_path),
        "changes": [
            {
                "kind": "parameterChanged",
                "method": "GET",
                "path": "/users/{userId}",
                "parameter_name": "userId",
                "parameter_location": "path",
            },
            {
                "kind": "parameterAdded",
                "method": "GET",
                "path": "/users/{userId}",
                "parameter_name": "limit",
                "parameter_location": "query",
            },
            {
                "kind": "parameterRemoved",
                "method": "GET",
                "path": "/users/{userId}",
                "parameter_name": "verbose",
                "parameter_location": "query",
            },
            {
                "kind": "endpointAdded",
                "method": "POST",
                "path": "/users",
            },
            {
                "kind": "endpointRemoved",
                "method": "DELETE",
                "path": "/users/{userId}",
            },
        ],
        "added_endpoint_count": 1,
        "removed_endpoint_count": 1,
        "added_parameter_count": 1,
        "removed_parameter_count": 1,
        "changed_parameter_count": 1,
        "added_body_property_count": 0,
        "removed_body_property_count": 0,
        "changed_body_property_count": 0,
        "added_response_status_count": 0,
        "removed_response_status_count": 0,
        "changed_security_count": 0,
    }


def test_compare_openapi_reports_body_status_and_security_changes(tmp_path: Path) -> None:
    def document(order_properties: dict[str, Any], security: list[Any], delete_status: str) -> dict[str, Any]:
        order = {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}
        return {
            "openapi": "3.1.0",
            "paths": {
                "/orders/{id}": {
                    "get": {"security": security, "responses": {"200": {"description": "OK", "content": order}}},
                    "delete": {"responses": {delete_status: {"description": "Done"}}},
                }
            },
            "components": {
                "schemas": {"Order": {"type": "object", "properties": order_properties, "required": ["id"]}}
            },
        }

    previous_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    previous_path.write_text(
        json.dumps(document({"id": {"type": "string"}, "email": {"type": "string"}}, [], "200")),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(document({"id": {"type": "integer"}, "total": {"type": "number"}}, [{"bearerAuth": []}], "204")),
        encoding="utf-8",
    )

    comparison = compare_openapi(
        previous_document_path=str(previous_path),
        current_document_path=str(current_path),
    )

    response = {"method": "GET", "path": "/orders/{id}", "body": "response", "status_code": "200"}
    assert comparison["changes"] == [
        {"kind": "bodyPropertyTypeChanged", **response, "property_path": "id", "previous_type": "string", "current_type": "integer"},
        {"kind": "bodyPropertyAdded", **response, "property_path": "total", "required": False},
        {"kind": "bodyPropertyRemoved", **response, "property_path": "email"},
        {
            "kind": "securityChanged",
            "method": "GET",
            "path": "/orders/{id}",
            "previous_security": [],
            "current_security": [{"bearerAuth": []}],
        },
        {"kind": "responseStatusAdded", "method": "DELETE", "path": "/orders/{id}", "status_code": "204"},
        {"kind": "responseStatusRemoved", "method": "DELETE", "path": "/orders/{id}", "status_code": "200"},
    ]
    assert comparison["added_body_property_count"] == 1
    assert comparison["removed_body_property_count"] == 1
    assert comparison["changed_body_property_count"] == 1
    assert comparison["added_response_status_count"] == 1
    assert comparison["removed_response_status_count"] == 1
    assert comparison["changed_security_count"] == 1


def test_actionable_validation_errors(tmp_path: Path) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "fastapi-app"
    with pytest.raises(InvalidProjectPathError):
        scan_api_project(project_path=str(tmp_path / "missing"))
    with pytest.raises(EndpointNotFoundError):
        get_endpoint_context(
            project_path=str(fixture_path),
            method="GET",
            endpoint_path="/missing",
            review_context=None,
        )
    with pytest.raises(UnsupportedOutputFormatError):
        export_openapi(
            project_path=str(fixture_path),
            output_path=str(fixture_path / "openapi.xml"),
            output_format="xml",
        )
    with pytest.raises(PathOutsideProjectError):
        export_openapi(
            project_path=str(fixture_path),
            output_path=str(tmp_path / "openapi.json"),
            output_format="json",
        )


def test_invalid_openapi_is_rejected(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.yaml"
    valid_path = tmp_path / "valid.json"
    invalid_path.write_text("paths: [", encoding="utf-8")
    valid_path.write_text('{"openapi":"3.1.0","paths":{}}', encoding="utf-8")
    with pytest.raises(InvalidOpenApiDocumentError):
        compare_openapi(
            previous_document_path=str(invalid_path),
            current_document_path=str(valid_path),
        )
