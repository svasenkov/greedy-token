from __future__ import annotations

from greedy_token.mcp import mcp_icons


def test_mcp_icons_advertises_png_data_uri() -> None:
    icons = mcp_icons()
    assert len(icons) == 1
    icon = icons[0]
    assert icon.src.startswith("data:image/png;base64,")
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["any"]
    assert len(icon.src) > 1000
