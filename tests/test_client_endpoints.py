from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from apyguard_apiscout_mcp.auth import (
    API_URL_ENVIRONMENT_VARIABLE,
    AUTH_FILE_ENVIRONMENT_VARIABLE,
    CLIENT_ENVIRONMENT_VARIABLE,
    StoredAuthState,
    load_auth_state,
    save_auth_state,
)
from apyguard_apiscout_mcp.errors import AuthenticationRequiredError, AuthenticationStorageError
from apyguard_apiscout_mcp.server import render_html_resource
from apyguard_apiscout_mcp.services import (
    _get_assets_url,
    _get_discovery_results_url,
    _get_json,
)


@pytest.fixture(autouse=True)
def _clear_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_URL_ENVIRONMENT_VARIABLE, raising=False)


@pytest.mark.parametrize("client_type", ["codex", "claude_code"])
def test_discovery_results_url_uses_client_endpoint(
    monkeypatch: pytest.MonkeyPatch, client_type: str
) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, client_type)

    assert _get_discovery_results_url() == (
        f"https://api.apyguard.com/api/api_discovery/integrations/{client_type}/discovery_results"
    )


@pytest.mark.parametrize("client_type", ["codex", "claude_code"])
def test_assets_url_uses_client_endpoint(
    monkeypatch: pytest.MonkeyPatch, client_type: str
) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, client_type)

    assert _get_assets_url() == (
        f"https://api.apyguard.com/api/api_discovery/integrations/{client_type}/assets"
    )


def test_the_token_cannot_be_sent_past_the_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "claude_code")
    monkeypatch.setenv("APYGUARD_APISCOUT_DISCOVERY_RESULTS_URL", "https://elsewhere.test/results")
    monkeypatch.setenv("APYGUARD_APISCOUT_ASSETS_URL", "https://elsewhere.test/assets")

    assert _get_discovery_results_url().startswith("https://api.apyguard.com/")
    assert _get_assets_url().startswith("https://api.apyguard.com/")


def test_client_endpoints_reject_unknown_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "vscode")

    with pytest.raises(AuthenticationStorageError):
        _get_assets_url()


def test_client_endpoints_follow_the_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    monkeypatch.setenv(API_URL_ENVIRONMENT_VARIABLE, " https://apyguard.internal.test/ ")

    assert _get_assets_url() == "https://apyguard.internal.test/api/api_discovery/integrations/codex/assets"
    assert _get_discovery_results_url() == (
        "https://apyguard.internal.test/api/api_discovery/integrations/codex/discovery_results"
    )


class RefusingHandler(BaseHTTPRequestHandler):
    authorizations: list[str | None] = []

    def do_GET(self) -> None:
        RefusingHandler.authorizations.append(self.headers.get("Authorization"))
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def test_a_refused_token_asks_for_a_new_sign_in_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "apg_cdx_" + "t" * 43
    auth_file_path = tmp_path / "auth.json"
    monkeypatch.setenv(AUTH_FILE_ENVIRONMENT_VARIABLE, str(auth_file_path))
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")
    save_auth_state(
        auth_file_path=auth_file_path,
        state=StoredAuthState(client_type="codex", installation_id="installation-1", access_token=token),
    )
    RefusingHandler.authorizations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RefusingHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    server_host, server_port = server.server_address[:2]
    try:
        with pytest.raises(AuthenticationRequiredError, match="start_apyguard_login"):
            _get_json(url=f"http://{server_host}:{server_port}/assets/project", access_token=token)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)

    assert RefusingHandler.authorizations == [f"Bearer {token}"]
    stored_state = load_auth_state(auth_file_path=auth_file_path)
    assert stored_state is not None and stored_state.access_token is None


def test_workbench_prompts_use_codex_code_panel_for_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "codex")

    html = render_html_resource("workbench.html")

    assert "__APYGUARD_SOURCE_LINK_INSTRUCTION__" not in html
    assert html.count("in the Codex code panel") == 2


def test_workbench_prompts_omit_codex_panel_for_claude_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "claude_code")

    html = render_html_resource("workbench.html")

    assert "__APYGUARD_SOURCE_LINK_INSTRUCTION__" not in html
    assert "Codex" not in html
    assert html.count("as a clickable file_path:line reference") == 2
