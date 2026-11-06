from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from apyguard_apiscout_mcp import services
from apyguard_apiscout_mcp.auth import API_URL_ENVIRONMENT_VARIABLE, CLIENT_ENVIRONMENT_VARIABLE
from apyguard_apiscout_mcp.errors import AssetListingServiceError
from apyguard_apiscout_mcp.services import get_apyguard_progress

TOKEN = "apg_cdx_" + "t" * 43
ASSET = (200, {"status": "success", "response": {"id": "asset-1", "name": "Orders", "target": "https://orders.example"}})
NOT_FOUND = (404, {"status": "failure", "response": "Asset not found."})


class ProgressApyGuard(ThreadingHTTPServer):
    """Answers the asset lookup and the status request separately, and records each path."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ProgressHandler)
        self.asset_answer: tuple[int, object] = ASSET
        self.status_answer: tuple[int, object] = (500, {"error": "Not set"})
        self.paths: list[str] = []

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class ProgressHandler(BaseHTTPRequestHandler):
    server: ProgressApyGuard

    def do_GET(self) -> None:
        self.server.paths.append(self.path)
        status_code, payload = self.server.status_answer if self.path.endswith("/status") else self.server.asset_answer
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def apyguard(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProgressApyGuard]:
    server = ProgressApyGuard()
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    server_thread.start()
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, server.base_url)
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    monkeypatch.setattr(services, "get_authenticated_apyguard_token", lambda: TOKEN)
    monkeypatch.setattr(services, "ASSET_REQUEST_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)


def _status(imported: object = None, scan: object = None) -> tuple[int, object]:
    return (200, {"status": "success", "response": {"import": imported, "scan": scan}})


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("Import Requested", "running"),
        ("Import Started", "running"),
        ("Importing", "running"),
        ("Stopping", "running"),
        ("Import Completed", "completed"),
        ("Error", "failed"),
        ("Stopped", "stopped"),
        ("Cancelled", "stopped"),
    ],
)
def test_import_status_is_reported_as_its_state(
    status: str, state: str, apyguard: ProgressApyGuard, tmp_path: Path
) -> None:
    apyguard.status_answer = _status(imported={"status": status, "progress": 40})

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report["import_progress"]["state"] == state
    assert report["import_progress"]["status"] == status


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("Queued", "running"),
        ("Running", "running"),
        ("Stopping", "running"),
        ("Completed", "completed"),
        ("Failed", "failed"),
        ("Stopped", "stopped"),
        ("Cancelled", "stopped"),
    ],
)
def test_scan_status_is_reported_as_its_state(
    status: str, state: str, apyguard: ProgressApyGuard, tmp_path: Path
) -> None:
    apyguard.status_answer = _status(scan={"status": status, "progress": "45"})

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report["scan_progress"]["state"] == state
    assert report["scan_progress"]["status"] == status


def test_report_names_the_asset_and_what_apyguard_said(apyguard: ProgressApyGuard, tmp_path: Path) -> None:
    apyguard.status_answer = _status(
        imported={"status": "Error", "progress": 10, "detail": "The document could not be parsed.", "updated_at": "2026-11-04T09:00:00Z"},
        scan={"status": "Completed", "progress": "100", "detail": None, "updated_at": "2026-11-04T10:00:00Z", "completed_at": "2026-11-04T10:00:00Z"},
    )

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report == {
        "project_path": str(tmp_path.resolve()),
        "asset_linked": True,
        "asset_id": "asset-1",
        "asset_name": "Orders",
        "import_progress": {
            "state": "failed",
            "status": "Error",
            "progress": 10,
            "detail": "The document could not be parsed.",
            "updated_at": "2026-11-04T09:00:00Z",
        },
        "scan_progress": {
            "state": "completed",
            "status": "Completed",
            "progress": 100,
            "detail": None,
            "updated_at": "2026-11-04T10:00:00Z",
        },
    }


def test_operation_that_never_ran_is_reported_as_not_started(apyguard: ProgressApyGuard, tmp_path: Path) -> None:
    apyguard.status_answer = _status()

    report = get_apyguard_progress(project_path=str(tmp_path))

    not_started = {"state": "not_started", "status": None, "progress": None, "detail": None, "updated_at": None}
    assert report["asset_linked"] is True
    assert report["import_progress"] == not_started
    assert report["scan_progress"] == not_started


def test_status_apyguard_adds_later_is_passed_on_as_unknown(apyguard: ProgressApyGuard, tmp_path: Path) -> None:
    apyguard.status_answer = _status(imported={"status": "waiting", "progress": 0})

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report["import_progress"]["state"] == "unknown"
    assert report["import_progress"]["status"] == "waiting"


@pytest.mark.parametrize(
    ("reported", "progress"),
    [(45, 45), ("45", 45), ("45.6", 46), ("45%", 45), (" 7 ", 7), ("0", 0), (150, 100), (-3, 0), ("soon", None), ("", None), (None, None), (True, None)],
)
def test_progress_is_a_percentage_or_nothing(
    reported: object, progress: int | None, apyguard: ProgressApyGuard, tmp_path: Path
) -> None:
    apyguard.status_answer = _status(scan={"status": "Running", "progress": reported})

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report["scan_progress"]["progress"] == progress


def test_project_without_an_asset_is_reported_without_asking_for_its_status(
    apyguard: ProgressApyGuard, tmp_path: Path
) -> None:
    apyguard.asset_answer = NOT_FOUND

    report = get_apyguard_progress(project_path=str(tmp_path))

    assert report["asset_linked"] is False
    assert report["asset_id"] is None
    assert report["import_progress"]["state"] == "not_started"
    assert report["scan_progress"]["state"] == "not_started"
    assert len(apyguard.paths) == 1


def test_apyguard_without_the_status_route_is_not_reported_as_no_asset(
    apyguard: ProgressApyGuard, tmp_path: Path
) -> None:
    apyguard.status_answer = NOT_FOUND

    with pytest.raises(AssetListingServiceError, match='does not report progress.*asset_id="asset-1"'):
        get_apyguard_progress(project_path=str(tmp_path))

    assert len([path for path in apyguard.paths if path.endswith("/status")]) == 1


def test_status_answer_without_a_report_is_rejected(apyguard: ProgressApyGuard, tmp_path: Path) -> None:
    apyguard.status_answer = (200, {"status": "success", "response": "nothing here"})

    with pytest.raises(AssetListingServiceError, match="progress answer was not an object"):
        get_apyguard_progress(project_path=str(tmp_path))
