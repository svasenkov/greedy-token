from __future__ import annotations

import allure
import pytest

from greedy_token.mcp import mcp_icons

pytestmark = [
    allure.epic("MCP"),
    allure.parent_suite("MCP"),
    allure.feature("Server icon"),
    allure.suite("Server icon"),
]


@allure.story("SEP-973 icon")
@allure.title("mcp_icons advertises PNG data URI for Cursor")
def test_mcp_icons_advertises_png_data_uri() -> None:
    icons = mcp_icons()
    assert len(icons) == 1
    icon = icons[0]
    assert icon.src.startswith("data:image/png;base64,")
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["any"]
    assert len(icon.src) > 1000
