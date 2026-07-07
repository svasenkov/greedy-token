from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from greedy_token.wrappers import WRAPPERS, ollama_available, resolve_wrapper_command


def test_wrappers_registry_has_check_meta_sync() -> None:
    assert "check-meta-sync" in WRAPPERS
    assert WRAPPERS["check-meta-sync"].read_only is True


def test_resolve_wrapper_command_python(minimal_workspace) -> None:
    script = minimal_workspace / "scripts" / "check-meta-sync.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    cmd = resolve_wrapper_command("check-meta-sync", minimal_workspace)
    assert "check-meta-sync.sh" in cmd
    assert "python" not in cmd


def test_resolve_wrapper_unknown_raises(minimal_workspace: Path) -> None:
    with pytest.raises(KeyError):
        resolve_wrapper_command("no-such-wrapper", minimal_workspace)


@patch("greedy_token.wrappers.json.load", return_value={"models": []})
@patch("urllib.request.urlopen")
def test_ollama_available_true(mock_urlopen, mock_json_load) -> None:
    from greedy_token.wrappers import _ollama_probe_cache

    _ollama_probe_cache.clear()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp
    assert ollama_available("http://localhost:11434") is True


@patch("urllib.request.urlopen", side_effect=OSError("connection refused"))
def test_ollama_available_false(mock_urlopen) -> None:
    from greedy_token.wrappers import _ollama_probe_cache

    _ollama_probe_cache.clear()
    assert ollama_available("http://localhost:11434") is False
