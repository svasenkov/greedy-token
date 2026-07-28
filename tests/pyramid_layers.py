"""Allure pyramid layer keys per test module (label layer → TestOps Test Layer)."""

from __future__ import annotations

import allure

# Keys match automator PYRAMID_LAYER_MAPPINGS / Java @Layer values.
LAYER_BY_MODULE: dict[str, str] = {
    # unit — pure logic, no subprocess / external IO
    "test_baseline": "unit",
    "test_budget": "unit",
    "test_budget_policy": "unit",
    "test_calibration": "unit",
    "test_resource_probe": "unit",
    "test_branch_coverage": "unit",
    "test_cli_handlers": "unit",
    "test_context_audit": "unit",
    "test_estimator": "unit",
    "test_init": "unit",
    "test_mcp_icon": "unit",
    "test_paths": "unit",
    "test_pyramid_layers": "unit",
    "test_pyramid_layer_colors": "unit",
    "test_rag_tokens": "unit",
    "test_router": "unit",
    "test_settings": "unit",
    "test_config_presets": "unit",
    "test_cheap_llm": "unit",
    "test_model_select": "unit",
    "test_model_select_gaps": "unit",
    "test_llm_invoke": "unit",
    "test_llm_invoke_gaps": "unit",
    "test_advisory": "unit",
    "test_expensive_llm": "unit",
    "test_spend_guard_gaps": "unit",
    "test_budget_gaps": "unit",
    "test_router_gaps": "unit",
    "test_scripts_lint_gaps": "unit",
    "test_code_search_gaps": "unit",
    "test_executors_gaps": "unit",
    "test_settings_gaps": "unit",
    "test_tokens": "unit",
    "test_tool_output": "unit",
    "test_doc_sync": "unit",
    "test_time_baseline": "unit",
    "test_mutation_equivalents": "unit",
    # component — module wiring, mocks, minimal_workspace fixtures
    "test_coverage_gaps": "component",
    "test_executors": "component",
    "test_mcp_handlers": "component",
    "test_mcp_tools": "component",
    "test_pipeline": "component",
    "test_prompt_compress": "component",
    "test_rag_index": "component",
    "test_rag_search": "component",
    "test_search": "component",
    "test_search_enrich": "component",
    "test_security": "component",
    "test_tool_paths": "component",
    "test_usage": "component",
    "test_wrappers": "component",
    "test_hub": "component",
    "test_hub_gaps": "component",
    "test_crystallize_l3": "component",
    "test_resource_probe_gaps": "component",
    "test_cli_gaps": "component",
    "test_pipeline_gaps": "component",
    "test_mcp_gaps": "component",
    "test_usage_gaps": "component",
    "test_coverage_gaps_v2": "component",
    "test_metered_bulk": "component",
    "test_agent_host": "component",
    # integration — subprocess CLI, real rg, workspace checkout
    "test_cli": "integration",
    "test_cli_commands": "integration",
    "test_code_search": "integration",
    "test_mcp_stdio": "e2e",
}

PYRAMID_LAYERS = ("unit", "component", "integration", "api", "e2e", "manual")

# Collage testResultSeverities panel — stable spread by pyramid layer (Allure label severity).
SEVERITY_BY_LAYER: dict[str, allure.severity_level] = {
    "unit": allure.severity_level.NORMAL,
    "component": allure.severity_level.MINOR,
    "integration": allure.severity_level.CRITICAL,
    "e2e": allure.severity_level.BLOCKER,
    "api": allure.severity_level.NORMAL,
    "manual": allure.severity_level.TRIVIAL,
}


def layer_for_module(module_name: str) -> str | None:
    return LAYER_BY_MODULE.get(module_name)
