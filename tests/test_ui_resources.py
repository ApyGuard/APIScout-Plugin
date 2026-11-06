from __future__ import annotations

import pytest

from apyguard_apiscout_mcp.auth import CLIENT_ENVIRONMENT_VARIABLE
from apyguard_apiscout_mcp.server import render_html_resource


@pytest.fixture(autouse=True)
def _claude_code_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_ENVIRONMENT_VARIABLE, "claude_code")


@pytest.mark.parametrize("resource_name", ["workbench.html", "asset_form.html"])
def test_ui_resource_reports_its_content_size_to_the_host(resource_name: str) -> None:
    html = render_html_resource(resource_name)

    assert "ui/notifications/size-changed" in html
    assert "new ResizeObserver(scheduleSizeChanged)" in html
    # Size reporting starts only after the host answered ui/initialize.
    assert html.index('await request("ui/initialize"') < html.index("reportSizeChanges();\n")


@pytest.mark.parametrize("resource_name", ["workbench.html", "asset_form.html"])
def test_ui_resource_skips_zero_size_reports(resource_name: str) -> None:
    html = render_html_resource(resource_name)

    assert "if (!width || !height) return;" in html


def test_workbench_offers_full_screen_only_when_the_host_supports_it() -> None:
    html = render_html_resource("workbench.html")

    assert 'state.displayModes.includes("fullscreen")' in html
    assert 'request("ui/request-display-mode", { mode })' in html
    assert "ui/notifications/host-context-changed" in html


@pytest.mark.parametrize("resource_name", ["workbench.html", "asset_form.html"])
def test_ui_resource_does_not_report_an_unchanged_import_as_a_new_asset(resource_name: str) -> None:
    html = render_html_resource(resource_name)

    assert 'already_imported: "ApyGuard already holds this documentation; nothing new was imported."' in html


@pytest.mark.parametrize("resource_name", ["workbench.html", "asset_form.html"])
def test_ui_resource_offers_chat_when_the_form_cannot_reach_the_tool(resource_name: str) -> None:
    html = render_html_resource(resource_name)

    assert "share these details in chat" in html


def test_asset_form_has_no_display_mode_toggle() -> None:
    html = render_html_resource("asset_form.html")

    assert "ui/request-display-mode" not in html
