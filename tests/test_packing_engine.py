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


def test_csv_upload_endpoint_runs_packer():
    from io import BytesIO

    import pytest

    pytest.importorskip("flask")
    from app import app

    csv_text = "sku,quantity,height,length,depth,weight\nA,4,10,12,10,20\nB,6,6,8,6,3\n"
    client = app.test_client()
    response = client.post(
        "/api/pack-csv",
        data={
            "file": (BytesIO(csv_text.encode("utf-8")), "shipment.csv"),
            "pallet_length": "48",
            "pallet_depth": "40",
            "max_height": "72",
            "max_overhang": "0",
            "time_budget_seconds": "10",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["diagnostics"]["boxes_placed"] == 10
    assert payload["diagnostics"]["overflow_boxes"] == 0
    assert payload["uploaded_items"][0]["sku"] == "A"


def test_csv_upload_reports_missing_columns():
    from io import BytesIO

    import pytest

    pytest.importorskip("flask")
    from app import app

    client = app.test_client()
    response = client.post(
        "/api/pack-csv",
        data={"file": (BytesIO(b"sku,quantity\nA,1\n"), "bad.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert "missing required columns" in payload["errors"][0]


def test_pallet_start_estimate_uses_total_volume_not_summed_per_sku_minimums():
    from benchmark_packer import FIXTURE_A

    result = pack_shipment(FIXTURE_A, PALLET, time_budget_seconds=TARGET_SECONDS)
    diagnostics = result["diagnostics"]
    details = diagnostics["geometry_estimate_details"]

    pallet_volume = PALLET["length"] * PALLET["depth"] * PALLET["max_height"]
    shipment_volume = sum(
        item["quantity"] * item["height"] * item["length"] * item["depth"]
        for item in FIXTURE_A
    )
    expected_volume_estimate = int(-(-shipment_volume // pallet_volume))

    assert diagnostics["volume_only_pallet_estimate"] == expected_volume_estimate
    assert details["starting_pallet_estimate"] == max(
        expected_volume_estimate,
        details["max_sku_geometry_minimum"],
    )
    assert details["per_sku_geometry_pallet_sum"] > details["starting_pallet_estimate"]
