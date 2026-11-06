from __future__ import annotations

import asyncio

from apyguard_apiscout_mcp.server import mcp


TOOLS_THAT_REACH_APYGUARD = {
    "complete_apyguard_login",
    "create_apyguard_asset",
    "get_apyguard_progress",
    "list_apyguard_assets",
    "sign_out_apyguard",
    "start_apyguard_login",
}


def test_only_the_tools_that_reach_apyguard_declare_an_open_world() -> None:
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools if tool.annotations.openWorldHint} == TOOLS_THAT_REACH_APYGUARD
