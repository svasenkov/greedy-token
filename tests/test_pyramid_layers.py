from __future__ import annotations

from pathlib import Path

import allure
import pytest

from tests.allure_reporting import attach_json, attach_text
from tests.pyramid_layers import LAYER_BY_MODULE

pytestmark = [
    allure.epic("Test infrastructure"),
    allure.parent_suite("Test infrastructure"),
    allure.feature("Pyramid layers"),
    allure.suite("Pyramid layers"),
]


@allure.story("Module mapping")
@allure.title("Every test module has a pyramid layer mapping")
def test_every_test_module_has_pyramid_layer() -> None:
    with allure.step("Collect test modules and check layer mappings"):
        tests_dir = Path(__file__).resolve().parent
        modules = sorted(path.stem for path in tests_dir.glob("test_*.py"))
        missing = [name for name in modules if name not in LAYER_BY_MODULE]
        attach_text("test modules", "\n".join(modules))
        attach_text("missing mappings", "\n".join(missing) if missing else "(none)")
    with allure.step("Verify all modules have pyramid layer"):
        assert not missing, f"Add layer mapping in pyramid_layers.py: {missing}"


@allure.story("Layer distribution")
@allure.title("Layer mapping covers unit, component, and integration modules")
def test_layer_mapping_counts() -> None:
    with allure.step("Group modules by pyramid layer"):
        by_layer: dict[str, list[str]] = {}
        for module, layer in LAYER_BY_MODULE.items():
            by_layer.setdefault(layer, []).append(module)
        attach_json("layer counts", {layer: len(mods) for layer, mods in by_layer.items()})
    with allure.step("Verify minimum coverage per layer"):
        assert len(by_layer["unit"]) >= 5
        assert len(by_layer["component"]) >= 5
        assert len(by_layer["integration"]) >= 2
