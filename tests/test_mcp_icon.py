from __future__ import annotations

import allure
import pytest

from greedy_token.mcp import mcp_icons
from tests.allure_reporting import attach_json

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("Server icon"),
    allure.suite("Server icon"),
]


@allure.story("SEP-973 icon")
@allure.title("MCP server icon advertises PNG data URI for Cursor")
def test_mcp_icons_advertises_png_data_uri() -> None:
    with allure.step("Load MCP server icons"):
        icons = mcp_icons()
        attach_json("icon metadata", [{"mimeType": i.mimeType, "sizes": i.sizes, "src_length": len(i.src)} for i in icons])
    with allure.step("Verify PNG data URI icon for Cursor"):
        assert len(icons) == 1
        icon = icons[0]
        assert icon.src.startswith("data:image/png;base64,")
        assert icon.mimeType == "image/png"
        assert icon.sizes == ["any"]
        assert len(icon.src) > 1000
