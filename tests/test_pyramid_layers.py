from __future__ import annotations

from pathlib import Path

from tests.pyramid_layers import LAYER_BY_MODULE


def test_every_test_module_has_pyramid_layer() -> None:
    tests_dir = Path(__file__).resolve().parent
    modules = sorted(path.stem for path in tests_dir.glob("test_*.py"))
    missing = [name for name in modules if name not in LAYER_BY_MODULE]
    assert not missing, f"Add layer mapping in pyramid_layers.py: {missing}"


def test_layer_mapping_counts() -> None:
    by_layer: dict[str, list[str]] = {}
    for module, layer in LAYER_BY_MODULE.items():
        by_layer.setdefault(layer, []).append(module)
    assert len(by_layer["unit"]) >= 5
    assert len(by_layer["component"]) >= 5
    assert len(by_layer["integration"]) >= 2
