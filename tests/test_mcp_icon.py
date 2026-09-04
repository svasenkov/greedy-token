from __future__ import annotations

from pathlib import Path

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
@allure.title("MCP server icon advertises a small SVG data URI for Cursor")
def test_mcp_icons_advertises_png_data_uri() -> None:
    with allure.step("Load MCP server icons"):
        icons = mcp_icons()
        attach_json("icon metadata", [{"mimeType": i.mimeType, "sizes": i.sizes, "src_length": len(i.src)} for i in icons])
    with allure.step("Verify small SVG data URI (PNG 270KB times out Cursor offerings)"):
        assert len(icons) == 1
        icon = icons[0]
        assert icon.src.startswith("data:image/svg+xml;base64,")
        assert icon.mimeType == "image/svg+xml"
        assert icon.sizes in ("any", ["any"])
        assert 100 < len(icon.src) < 8_192 * 2


@allure.story("SEP-973 icon")
@allure.title("MCP server icon skips payloads over MAX_ICON_BYTES")
def test_mcp_icons_skips_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import mcp as mcp_mod

    monkeypatch.setattr(mcp_mod, "MAX_ICON_BYTES", 1)
    with pytest.raises(FileNotFoundError, match="static icon not found"):
        mcp_mod.mcp_icons()


@allure.story("SEP-973 icon")
@allure.title("MCP server icon falls back to a small PNG when SVG is absent")
def test_mcp_icons_png_when_svg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from greedy_token import mcp as mcp_mod

    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "icon.png")
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"\x89PNG\r\n" + b"x" * 32)

    class Missing:
        def joinpath(self, name: str) -> None:
            raise FileNotFoundError(name)

    monkeypatch.setattr(mcp_mod.resources, "files", lambda *_a, **_k: Missing())
    icons = mcp_mod.mcp_icons()
    assert icons[0].mimeType == "image/png"
    assert icons[0].src.startswith("data:image/png;base64,")
