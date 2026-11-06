from __future__ import annotations

import errno
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from apyguard_apiscout_mcp import services
from apyguard_apiscout_mcp.auth import API_URL_ENVIRONMENT_VARIABLE, CLIENT_ENVIRONMENT_VARIABLE
from apyguard_apiscout_mcp.errors import (
    AssetCreationRefusedError,
    AssetCreationServiceError,
    AssetImportRunningError,
    AssetListingServiceError,
)
from apyguard_apiscout_mcp.services import (
    ASSET_REQUEST_ATTEMPTS,
    IMPORT_RUNNING_MESSAGE,
    _get_existing_asset_record,
    _get_json,
    _post_asset_request,
    _serialize_collection,
    create_apyguard_asset,
    list_apyguard_assets,
)

TOKEN = "apg_cdx_" + "t" * 43
ASSET_NOT_FOUND = (404, {"status": "failure", "response": "Asset not found."})


class FakeApyGuard(ThreadingHTTPServer):
    """An ApyGuard that answers every asset request the same way, and counts them."""

    def __init__(self, status_code: int, payload: object) -> None:
        super().__init__(("127.0.0.1", 0), FakeApyGuardHandler)
        self.status_code = status_code
        self.payload = payload
        self.requests = 0

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def url(self) -> str:
        return f"{self.base_url}/api/api_discovery/integrations/codex/discovery_results"


class FakeApyGuardHandler(BaseHTTPRequestHandler):
    server: FakeApyGuard

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._answer()

    def do_GET(self) -> None:
        self._answer()

    def _answer(self) -> None:
        self.server.requests += 1
        body = json.dumps(self.server.payload).encode("utf-8")
        self.send_response(self.server.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "ASSET_REQUEST_RETRY_DELAYS_SECONDS", (0.0, 0.0))


@pytest.fixture(autouse=True)
def _codex_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")


@pytest.fixture
def apyguard(request: pytest.FixtureRequest) -> Iterator[FakeApyGuard]:
    status_code, payload = request.param
    server = FakeApyGuard(status_code, payload)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)


def _post(apyguard: FakeApyGuard) -> Any:
    return _post_asset_request(url=apyguard.url, access_token=TOKEN, request_body={"openapi_json": "{}"})


@pytest.mark.parametrize(
    "apyguard",
    [(409, {"status": "failure", "response": {
        "message": "Another import is already in progress for this asset.",
        "import_running": True,
        "asset_id": "asset-1",
    }})],
    indirect=True,
)
def test_a_request_refused_while_an_import_runs_is_sent_once(apyguard: FakeApyGuard) -> None:
    with pytest.raises(AssetImportRunningError) as refused:
        _post(apyguard)

    assert str(refused.value) == IMPORT_RUNNING_MESSAGE
    assert apyguard.requests == 1


@pytest.mark.parametrize(
    ("apyguard", "reason"),
    [
        (
            (400, {"status": "failure", "response": {
                "message": "Used 99/100 API endpoints. Update subscription to add more API endpoints.",
            }}),
            "Used 99/100 API endpoints. Update subscription to add more API endpoints.",
        ),
        ((400, {"error": "openapi_json is not valid: bad"}), "openapi_json is not valid: bad"),
    ],
    indirect=["apyguard"],
)
def test_a_refused_request_is_sent_once_with_apyguards_reason(apyguard: FakeApyGuard, reason: str) -> None:
    with pytest.raises(AssetCreationRefusedError, match="status=400") as refused:
        _post(apyguard)

    assert not isinstance(refused.value, AssetImportRunningError)
    assert repr(reason) in str(refused.value)
    assert apyguard.requests == 1


@pytest.mark.parametrize("apyguard", [(500, {"error": "Unexpected error"})], indirect=True)
def test_a_server_error_is_tried_again(apyguard: FakeApyGuard) -> None:
    with pytest.raises(AssetCreationServiceError) as failed:
        _post(apyguard)

    assert not isinstance(failed.value, AssetCreationRefusedError)
    assert apyguard.requests == ASSET_REQUEST_ATTEMPTS


class UnresponsiveServer:
    """Accepts each connection, then stays silent or hangs up, and counts the connections."""

    def __init__(self, hang_up: bool) -> None:
        self.connections = 0
        self._hang_up = hang_up
        self._held: list[socket.socket] = []
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._listener.getsockname()[:2]
        return f"http://{host}:{port}/resource"

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            self.connections += 1
            if self._hang_up:
                connection.close()
            else:
                self._held.append(connection)

    def close(self) -> None:
        # Closing does not wake a blocked accept() on every platform, so shut it down first.
        try:
            self._listener.shutdown(socket.SHUT_RDWR)
        except OSError as error:
            if error.errno != errno.ENOTCONN:
                raise
        self._listener.close()
        self._thread.join(timeout=5.0)
        for connection in self._held:
            connection.close()


@pytest.fixture(params=[False, True], ids=["never_answers", "hangs_up"])
def unresponsive_server(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[UnresponsiveServer]:
    monkeypatch.setattr(services, "ASSET_REQUEST_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(services, "ASSET_REQUEST_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    server = UnresponsiveServer(hang_up=request.param)
    try:
        yield server
    finally:
        server.close()


def test_an_unanswered_post_is_tried_again_and_reported(unresponsive_server: UnresponsiveServer) -> None:
    with pytest.raises(AssetCreationServiceError, match="url="):
        _post_asset_request(url=unresponsive_server.url, access_token=TOKEN, request_body={})

    assert unresponsive_server.connections == ASSET_REQUEST_ATTEMPTS


def test_an_unanswered_get_is_tried_again_and_reported(unresponsive_server: UnresponsiveServer) -> None:
    with pytest.raises(AssetListingServiceError, match="url="):
        _get_json(url=unresponsive_server.url, access_token=TOKEN)

    assert unresponsive_server.connections == ASSET_REQUEST_ATTEMPTS


def _list_assets(apyguard: FakeApyGuard, project_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, apyguard.base_url)
    monkeypatch.setattr(services, "get_authenticated_apyguard_token", lambda: TOKEN)
    return list_apyguard_assets(project_path=str(project_path))


@pytest.mark.parametrize("apyguard", [ASSET_NOT_FOUND], indirect=True)
def test_a_project_without_an_asset_is_listed_as_empty_after_one_request(
    apyguard: FakeApyGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _list_assets(apyguard, tmp_path, monkeypatch)

    assert report["asset_count"] == 0
    assert report["collection_count"] == 0
    assert report["assets"] == []
    assert apyguard.requests == 1


@pytest.mark.parametrize("apyguard", [(500, {"error": "Unexpected error"})], indirect=True)
def test_a_listing_server_error_is_tried_again_and_reported(
    apyguard: FakeApyGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssetListingServiceError, match="status=500"):
        _list_assets(apyguard, tmp_path, monkeypatch)

    assert apyguard.requests == ASSET_REQUEST_ATTEMPTS


@pytest.mark.parametrize("apyguard", [ASSET_NOT_FOUND], indirect=True)
def test_asset_creation_finds_no_existing_asset_after_one_request(
    apyguard: FakeApyGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, apyguard.base_url)

    assert _get_existing_asset_record(project_id="project-1", access_token=TOKEN) is None
    assert apyguard.requests == 1


def test_listed_collection_shows_why_its_import_failed() -> None:
    collection = _serialize_collection(
        record={"id": "collection-1", "name": "orders", "import_status": "Error", "import_error_detail": "The document could not be parsed."}
    )

    assert collection["status"] == "Error"
    assert collection["detail"] == "The document could not be parsed."


NEW_ASSET_FIELDS = {
    "asset_name": "orders",
    "analyzed_api_url": "https://orders.example.test",
    "description": "Orders API",
    "environment": "Test",
}


def _stub_asset_flow(monkeypatch: pytest.MonkeyPatch, existing_asset: dict[str, str] | None) -> list[Any]:
    """Runs asset requests against a stubbed ApyGuard and returns the bodies it receives."""
    sent_bodies: list[Any] = []

    def answer(url: str, access_token: str, request_body: Any) -> Any:
        sent_bodies.append(request_body)
        return {"status": "success", "response": {"asset_id": "asset-1"}}

    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    monkeypatch.setattr(services, "scan_api_project", lambda project_path: {"openapi": {"openapi": "3.0.0"}})
    monkeypatch.setattr(services, "get_authenticated_apyguard_token", lambda: TOKEN)
    monkeypatch.setattr(services, "_get_existing_asset_record", lambda project_id, access_token: existing_asset)
    monkeypatch.setattr(services, "_post_asset_request", answer)
    monkeypatch.setattr(
        services, "_verify_created_asset", lambda project_id, asset_id, access_token: ("Import Completed", True)
    )
    return sent_bodies


def test_import_into_an_existing_asset_needs_only_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_bodies = _stub_asset_flow(
        monkeypatch,
        existing_asset={"id": "asset-1", "name": "orders", "target": "https://orders.example.test"},
    )

    report = create_apyguard_asset(project_path=str(tmp_path))

    assert report["operation"] == "imported"
    assert report["asset_name"] == "orders"
    assert report["analyzed_api_url"] == "https://orders.example.test"
    assert sorted(sent_bodies[0]) == ["codex_project_id", "openapi_json"]


@pytest.mark.parametrize("missing_field", sorted(NEW_ASSET_FIELDS))
def test_creating_an_asset_requires_each_of_its_fields(
    missing_field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_bodies = _stub_asset_flow(monkeypatch, existing_asset=None)

    with pytest.raises(ValueError, match=f'{missing_field}=""'):
        create_apyguard_asset(project_path=str(tmp_path), **{**NEW_ASSET_FIELDS, missing_field: "  "})

    assert sent_bodies == []


@pytest.mark.parametrize(
    "local_url",
    [
        "http://localhost:8000",
        "HTTP://LOCALHOST",
        "localhost:8000",
        "http://api.localhost/v1",
        "http://127.0.0.1:8000",
        "127.0.0.1:8000",
        "http://[::1]:8000",
        "http://0.0.0.0:8000",
    ],
)
def test_a_new_asset_cannot_point_at_a_local_address(
    local_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_bodies = _stub_asset_flow(monkeypatch, existing_asset=None)

    with pytest.raises(ValueError, match="local address"):
        create_apyguard_asset(
            project_path=str(tmp_path),
            **{**NEW_ASSET_FIELDS, "analyzed_api_url": local_url},
        )

    assert sent_bodies == []


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://staging.shop.example",
        "https://localhost.example.com",
        "http://10.0.0.5:8000",
        "api.shop.example:8443",
    ],
)
def test_a_new_asset_can_point_at_a_host_apyguard_may_reach(
    remote_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_bodies = _stub_asset_flow(monkeypatch, existing_asset=None)

    report = create_apyguard_asset(
        project_path=str(tmp_path),
        **{**NEW_ASSET_FIELDS, "analyzed_api_url": remote_url},
    )

    assert report["operation"] == "created"
    assert sent_bodies[0]["url"] == remote_url


def test_an_unreadable_url_is_rejected_with_its_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent_bodies = _stub_asset_flow(monkeypatch, existing_asset=None)

    with pytest.raises(ValueError, match=r"not a valid URL: analyzed_api_url=\"http://\[::1\""):
        create_apyguard_asset(
            project_path=str(tmp_path),
            **{**NEW_ASSET_FIELDS, "analyzed_api_url": "http://[::1"},
        )

    assert sent_bodies == []


def test_documentation_apyguard_holds_already_is_reported_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = {"status": "success", "response": {
        "message": "This documentation is already imported.",
        "asset_id": "asset-1",
        "api_collection_id": "collection-1",
        "already_imported": True,
    }}
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    monkeypatch.setattr(services, "scan_api_project", lambda project_path: {"openapi": {"openapi": "3.0.0"}})
    monkeypatch.setattr(services, "get_authenticated_apyguard_token", lambda: TOKEN)
    monkeypatch.setattr(
        services,
        "_get_existing_asset_record",
        lambda project_id, access_token: {"id": "asset-1", "name": "orders", "target": "https://orders.example.test"},
    )
    monkeypatch.setattr(services, "_post_asset_request", lambda url, access_token, request_body: answer)
    monkeypatch.setattr(
        services, "_verify_created_asset", lambda project_id, asset_id, access_token: ("Import Completed", True)
    )

    report = create_apyguard_asset(
        project_path=str(tmp_path),
        asset_name="orders",
        analyzed_api_url="https://orders.example.test",
        description="Orders API",
        environment="Test",
        tags=[],
    )

    assert report["operation"] == "already_imported"
    assert report["asset_id"] == "asset-1"
