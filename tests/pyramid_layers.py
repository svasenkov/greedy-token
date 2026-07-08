"""Allure pyramid layer keys per test module (label layer → TestOps Test Layer)."""

from __future__ import annotations

# Keys match automator PYRAMID_LAYER_MAPPINGS / Java @Layer values.
LAYER_BY_MODULE: dict[str, str] = {
    # unit — pure logic, no subprocess / external IO
    "test_budget": "unit",
    "test_context_audit": "unit",
    "test_mcp_icon": "unit",
    "test_rag_tokens": "unit",
    "test_router": "unit",
    "test_settings": "unit",
    "test_tokens": "unit",
    "test_tool_output": "unit",
    "test_pyramid_layers": "unit",
    # component — module wiring, mocks, minimal_workspace fixtures
    "test_executors": "component",
    "test_pipeline": "component",
    "test_prompt_compress": "component",
    "test_rag_index": "component",
    "test_rag_search": "component",
    "test_search": "component",
    "test_security": "component",
    "test_usage": "component",
    "test_wrappers": "component",
    # integration — subprocess CLI, real rg, monorepo checkout
    "test_cli": "integration",
    "test_code_search": "integration",
    "test_tool_paths": "integration",
}

PYRAMID_LAYERS = ("unit", "component", "integration", "api", "e2e", "manual")


def layer_for_module(module_name: str) -> str | None:
    return LAYER_BY_MODULE.get(module_name)
