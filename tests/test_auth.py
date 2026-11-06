from __future__ import annotations

import json
import socket
import stat
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from apyguard_apiscout_mcp import auth
from apyguard_apiscout_mcp.auth import (
    API_URL_ENVIRONMENT_VARIABLE,
    AUTH_FILE_ENVIRONMENT_VARIABLE,
    CLIENT_ENVIRONMENT_VARIABLE,
    DEVICE_CODE_GRANT_TYPE,
    complete_apyguard_login,
    discard_rejected_token,
    get_apyguard_auth_status,
    get_authenticated_apyguard_token,
    require_apyguard_authentication,
    sign_out_apyguard,
    start_apyguard_login,
)
from apyguard_apiscout_mcp.errors import (
    AuthenticationPendingError,
    AuthenticationRequiredError,
    AuthenticationServiceError,
)

DEVICE_PATH = "/api/api_discovery/integrations/device"
TOKEN_PATH = "/api/api_discovery/integrations/token"
REVOKE_PATH = "/api/api_discovery/integrations/revoke"
VERIFICATION_URI = "https://app.apyguard.test/auth/device?user_code=BCDF-GHJK"
CODEX_TOKEN = "apg_cdx_" + "t" * 43


class FakeApyGuard(ThreadingHTTPServer):
    """ApyGuard's device sign-in as the backend answers it, with the requests it received."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeApyGuardHandler)
        self.requests: list[dict[str, Any]] = []
        self.approved = False
        self.token_error: str | None = None
        self.access_token = CODEX_TOKEN
        self.expires_in = 600
        self.interval = 0

    def requests_to(self, path: str) -> list[dict[str, Any]]:
        return [request for request in self.requests if request["path"] == path]


class FakeApyGuardHandler(BaseHTTPRequestHandler):
    server: FakeApyGuard

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.server.requests.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "legacy_key": self.headers.get("X-APYGUARD-TOKEN"),
            "body": body,
        })
        if self.path == DEVICE_PATH:
            self._answer(200, {
                "device_code": "device-code-1",
                "user_code": "BCDF-GHJK",
                "verification_uri": VERIFICATION_URI.split("?")[0],
                "verification_uri_complete": VERIFICATION_URI,
                "expires_in": self.server.expires_in,
                "interval": self.server.interval,
            })
        elif self.path == TOKEN_PATH:
            if self.server.token_error is not None:
                self._answer(400, {"error": self.server.token_error})
            elif not self.server.approved:
                self._answer(400, {"error": "authorization_pending"})
            else:
                self._answer(200, {"access_token": self.server.access_token, "token_type": "Bearer"})
        elif self.path == REVOKE_PATH:
            self._answer(200, {"status": "success"})
        else:
            self._answer(404, {"status": "failure"})

    def _answer(self, status_code: int, payload: object) -> None:
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def apyguard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeApyGuard]:
    server = FakeApyGuard()
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    server_host, server_port = server.server_address[:2]
    monkeypatch.setenv(AUTH_FILE_ENVIRONMENT_VARIABLE, str(tmp_path / "credentials" / "auth.json"))
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, f"http://{server_host}:{server_port}/")
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)


@pytest.fixture
def auth_file_path(tmp_path: Path) -> Path:
    return tmp_path / "credentials" / "auth.json"


def _stored(auth_file_path: Path) -> dict[str, Any]:
    return json.loads(auth_file_path.read_text(encoding="utf-8"))


def _sign_in(apyguard: FakeApyGuard) -> None:
    start_apyguard_login()
    apyguard.approved = True
    complete_apyguard_login()
    apyguard.approved = False


def test_a_device_sign_in_unlocks_apyguard_tools(apyguard: FakeApyGuard, auth_file_path: Path) -> None:
    with pytest.raises(AuthenticationRequiredError):
        require_apyguard_authentication()

    login = start_apyguard_login()

    assert (login["login_url"], login["user_code"], login["expires_in"]) == (VERIFICATION_URI, "BCDF-GHJK", 600)
    [device_request] = apyguard.requests_to(DEVICE_PATH)
    assert device_request["body"]["client_type"] == "codex"
    assert len(device_request["body"]["installation_id"]) == 36
    assert stat.S_IMODE(auth_file_path.stat().st_mode) == 0o600
    assert get_apyguard_auth_status() == {"authenticated": False, "login_required": True, "login_pending": True}

    with pytest.raises(AuthenticationPendingError, match="BCDF-GHJK"):
        complete_apyguard_login()

    apyguard.approved = True
    assert complete_apyguard_login()["authenticated"] is True

    assert apyguard.requests_to(TOKEN_PATH)[-1]["body"] == {
        "grant_type": DEVICE_CODE_GRANT_TYPE,
        "device_code": "device-code-1",
        "client_type": "codex",
    }
    assert get_apyguard_auth_status() == {"authenticated": True, "login_required": False, "login_pending": False}
    assert get_authenticated_apyguard_token() == CODEX_TOKEN
    assert _stored(auth_file_path)["pending"] is None
    assert stat.S_IMODE(auth_file_path.stat().st_mode) == 0o600


def test_the_login_instructions_name_the_link_to_open(apyguard: FakeApyGuard) -> None:
    # A client that relays only the instructions still gives the user the page to approve the code on.
    login = start_apyguard_login()

    assert VERIFICATION_URI in login["instructions"]


def test_the_next_apiscout_request_finishes_an_approved_sign_in(apyguard: FakeApyGuard) -> None:
    start_apyguard_login()
    apyguard.approved = True

    require_apyguard_authentication()

    assert get_apyguard_auth_status()["authenticated"] is True


@pytest.mark.parametrize("error", ["access_denied", "expired_token", "invalid_grant"])
def test_a_denied_or_ended_sign_in_must_start_again(apyguard: FakeApyGuard, error: str) -> None:
    start_apyguard_login()
    apyguard.token_error = error

    with pytest.raises(AuthenticationRequiredError, match="start_apyguard_login"):
        complete_apyguard_login()

    assert get_apyguard_auth_status() == {"authenticated": False, "login_required": True, "login_pending": False}


def test_an_expired_code_is_not_sent(apyguard: FakeApyGuard) -> None:
    apyguard.expires_in = 0
    start_apyguard_login()

    with pytest.raises(AuthenticationRequiredError, match="expired"):
        complete_apyguard_login()

    assert apyguard.requests_to(TOKEN_PATH) == []


def test_polling_keeps_to_the_interval_and_slows_down_when_told(apyguard: FakeApyGuard, auth_file_path: Path) -> None:
    apyguard.interval = 5
    start_apyguard_login()

    for _ in range(2):
        with pytest.raises(AuthenticationPendingError):
            complete_apyguard_login()

    assert len(apyguard.requests_to(TOKEN_PATH)) == 1

    apyguard.interval = 0
    start_apyguard_login()
    apyguard.token_error = "slow_down"
    with pytest.raises(AuthenticationPendingError):
        complete_apyguard_login()

    assert _stored(auth_file_path)["pending"]["interval"] == 5


def test_a_token_for_another_client_is_refused(apyguard: FakeApyGuard) -> None:
    apyguard.access_token = "apg_cc_" + "t" * 43
    start_apyguard_login()
    apyguard.approved = True

    with pytest.raises(AuthenticationServiceError):
        complete_apyguard_login()

    assert get_apyguard_auth_status()["authenticated"] is False


def test_signing_out_revokes_the_token_and_keeps_the_installation(apyguard: FakeApyGuard, auth_file_path: Path) -> None:
    _sign_in(apyguard)
    installation_id = _stored(auth_file_path)["installation_id"]

    assert sign_out_apyguard() == {"authenticated": False, "credential_removed": True, "token_revoked": True}

    [revoke_request] = apyguard.requests_to(REVOKE_PATH)
    assert revoke_request["authorization"] == f"Bearer {CODEX_TOKEN}"
    assert _stored(auth_file_path)["access_token"] is None
    with pytest.raises(AuthenticationRequiredError):
        require_apyguard_authentication()

    start_apyguard_login()

    assert apyguard.requests_to(DEVICE_PATH)[-1]["body"]["installation_id"] == installation_id


def test_signing_in_to_another_account_revokes_the_earlier_token(apyguard: FakeApyGuard) -> None:
    _sign_in(apyguard)
    start_apyguard_login()
    apyguard.access_token = "apg_cdx_" + "n" * 43
    apyguard.approved = True

    complete_apyguard_login()

    assert get_authenticated_apyguard_token() == "apg_cdx_" + "n" * 43
    assert [request["authorization"] for request in apyguard.requests_to(REVOKE_PATH)] == [f"Bearer {CODEX_TOKEN}"]


def test_a_connection_keeps_working_while_a_new_sign_in_waits(apyguard: FakeApyGuard) -> None:
    _sign_in(apyguard)
    start_apyguard_login()

    assert get_authenticated_apyguard_token() == CODEX_TOKEN


def test_a_key_from_an_earlier_version_is_removed_and_never_sent(apyguard: FakeApyGuard, auth_file_path: Path) -> None:
    auth_file_path.parent.mkdir(mode=0o700, parents=True)
    auth_file_path.write_text(json.dumps({"api_key": "earlier-key", "confirmed": True, "client_type": "codex"}))
    auth_file_path.chmod(0o600)

    assert get_apyguard_auth_status()["login_required"] is True
    assert not auth_file_path.exists()

    start_apyguard_login()

    assert all(
        request["legacy_key"] is None and "earlier-key" not in json.dumps(request["body"])
        for request in apyguard.requests
    )


def test_a_rejected_token_is_dropped_only_if_it_is_still_the_current_one(apyguard: FakeApyGuard) -> None:
    _sign_in(apyguard)

    discard_rejected_token(access_token="apg_cdx_" + "o" * 43)
    assert get_authenticated_apyguard_token() == CODEX_TOKEN

    discard_rejected_token(access_token=CODEX_TOKEN)
    with pytest.raises(AuthenticationRequiredError):
        require_apyguard_authentication()


def test_the_api_url_must_be_http(apyguard: FakeApyGuard, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, "ftp://apyguard.test")

    with pytest.raises(AuthenticationServiceError):
        start_apyguard_login()

    assert apyguard.requests == []


def test_a_token_goes_only_to_the_api_that_issued_it(
    apyguard: FakeApyGuard, auth_file_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(apyguard)
    assert _stored(auth_file_path)["api_url"].startswith("http://127.0.0.1:")
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, "https://apyguard.elsewhere.test")

    assert get_apyguard_auth_status()["authenticated"] is False
    with pytest.raises(AuthenticationRequiredError):
        require_apyguard_authentication()

    # Revoked where it was issued.
    assert sign_out_apyguard()["token_revoked"] is True
    [revoke_request] = apyguard.requests_to(REVOKE_PATH)
    assert revoke_request["authorization"] == f"Bearer {CODEX_TOKEN}"


def test_a_sign_in_started_at_another_api_is_dropped(
    apyguard: FakeApyGuard, auth_file_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_apyguard_login()
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, "https://apyguard.elsewhere.test")

    with pytest.raises(AuthenticationRequiredError, match="changed"):
        complete_apyguard_login()

    assert _stored(auth_file_path)["pending"] is None
    assert apyguard.requests_to(TOKEN_PATH) == []


@pytest.mark.parametrize("hang_up", [False, True], ids=["never_answers", "hangs_up"])
def test_an_unanswered_sign_in_request_is_reported_as_a_service_error(
    hang_up: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "AUTH_REQUEST_TIMEOUT_SECONDS", 0.1)
    held: list[socket.socket] = []
    with socket.create_server(("127.0.0.1", 0)) as listener:

        def accept() -> None:
            connection, _ = listener.accept()
            if hang_up:
                connection.close()
            else:
                held.append(connection)

        accept_thread = threading.Thread(target=accept, daemon=True)
        accept_thread.start()
        host, port = listener.getsockname()[:2]
        try:
            with pytest.raises(AuthenticationServiceError, match="url="):
                auth._post_json(url=f"http://{host}:{port}{TOKEN_PATH}", request_body={})
        finally:
            accept_thread.join(timeout=5.0)
            for connection in held:
                connection.close()
