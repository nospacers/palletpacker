import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark_packer import FIXTURES, PALLET, TARGET_SECONDS
from packing_engine import pack_shipment

EPS = 1e-6


def overlap(a, b):
    return (
        a["x"] < b["x"] + b["length"] - EPS
        and a["x"] + a["length"] > b["x"] + EPS
        and a["y"] < b["y"] + b["depth"] - EPS
        and a["y"] + a["depth"] > b["y"] + EPS
        and a["z"] < b["z"] + b["height"] - EPS
        and a["z"] + a["height"] > b["z"] + EPS
    )


def same_size(a, b):
    av = sorted([a["length"], a["depth"], a["height"]])
    bv = sorted([b["length"], b["depth"], b["height"]])
    return all(abs(x - y) <= max(0.5, 0.04 * max(x, y)) for x, y in zip(av, bv))


def assert_core_rules(result, expected_count):
    placed = 0
    for pallet in result["pallets"]:
        boxes = pallet["boxes"]
        placed += len(boxes)
        for i, a in enumerate(boxes):
            assert a["z"] + a["height"] <= PALLET["max_height"] + EPS
            assert a["x"] >= -PALLET["max_overhang"] - EPS
            assert a["y"] >= -PALLET["max_overhang"] - EPS
            assert a["x"] + a["length"] <= PALLET["length"] + PALLET["max_overhang"] + EPS
            assert a["y"] + a["depth"] <= PALLET["depth"] + PALLET["max_overhang"] + EPS
            for b in boxes[i + 1 :]:
                assert not overlap(a, b)
            if a["z"] > EPS:
                for s in boxes:
                    support_area = max(0, min(a["x"] + a["length"], s["x"] + s["length"]) - max(a["x"], s["x"])) * max(0, min(a["y"] + a["depth"], s["y"] + s["depth"]) - max(a["y"], s["y"]))
                    if abs(s["z"] + s["height"] - a["z"]) <= EPS and support_area > EPS:
                        assert a["weight"] <= s["weight"] + EPS or same_size(a, s)
    assert placed + len(result["overflow"]) == expected_count
    assert result["diagnostics"]["violations"]["total"] == 0


def test_benchmark_fixtures_obey_core_rules_and_time_budget():
    for name, items in FIXTURES:
        result = pack_shipment(items, PALLET, time_budget_seconds=TARGET_SECONDS)
        assert_core_rules(result, sum(int(i["quantity"]) for i in items))
        assert result["diagnostics"]["runtime_seconds"] < TARGET_SECONDS, name
        assert len(result["overflow"]) == 0, name
        assert result["diagnostics"]["route"] in {
            "normal_mixed_small_box_mode",
            "large_carton_freight_mode",
            "same_size_brick_mode",
            "fallback_greedy_mode",
        }
