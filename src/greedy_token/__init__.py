"""Greedy-token: route dev tasks before expensive LLM calls."""

try:
    from importlib.metadata import version

    __version__ = version("greedy-token")
except Exception:
    __version__ = "0.5.2"
