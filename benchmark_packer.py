"""Benchmark fixtures for the deterministic pallet packing engine.

Run from the project root:
    python benchmark_packer.py
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from packing_engine import pack_shipment

PALLET = {"length": 48, "depth": 40, "max_height": 72, "max_overhang": 0, "allow_extra_pallets": True}
TARGET_SECONDS = 10.0

FIXTURE_A = [
    {"sku": "SKU-00005", "quantity": 30, "height": 25.27, "length": 24.18, "depth": 39.84, "weight": 100},
    {"sku": "SKU-00006", "quantity": 26, "height": 38.38, "length": 30.17, "depth": 36.14, "weight": 100},
    {"sku": "SKU-00009", "quantity": 16, "height": 28.91, "length": 29.03, "depth": 27.78, "weight": 100},
    {"sku": "SKU-00001", "quantity": 35, "height": 14.5, "length": 17.5, "depth": 19.58, "weight": 67.32},
    {"sku": "SKU-00003", "quantity": 2, "height": 18.63, "length": 19.7, "depth": 29.13, "weight": 55.64},
    {"sku": "SKU-00007", "quantity": 2, "height": 13.46, "length": 26.89, "depth": 18.99, "weight": 52.78},
    {"sku": "SKU-00004", "quantity": 3, "height": 24.32, "length": 14.08, "depth": 12.98, "weight": 36.31},
    {"sku": "SKU-00008", "quantity": 5, "height": 6.82, "length": 16.77, "depth": 15.42, "weight": 20.95},
    {"sku": "SKU-00010", "quantity": 44, "height": 11.39, "length": 11.27, "depth": 4.31, "weight": 7.84},
    {"sku": "SKU-00002", "quantity": 12, "height": 4.49, "length": 7.6, "depth": 14.09, "weight": 2.57},
]

FIXTURE_B = [
    {"sku": "SKU-00004", "quantity": 10, "height": 25.17, "length": 21.94, "depth": 28.34, "weight": 88},
    {"sku": "SKU-00009", "quantity": 35, "height": 6.37, "length": 10.92, "depth": 5, "weight": 87.61},
    {"sku": "SKU-00005", "quantity": 30, "height": 24.12, "length": 40.73, "depth": 21.61, "weight": 87.57},
    {"sku": "SKU-00008", "quantity": 8, "height": 10.39, "length": 15.72, "depth": 36.99, "weight": 64.72},
    {"sku": "SKU-00007", "quantity": 49, "height": 3.32, "length": 25.15, "depth": 31.62, "weight": 60.03},
    {"sku": "SKU-00006", "quantity": 7, "height": 26.01, "length": 1.82, "depth": 24.28, "weight": 57.99},
    {"sku": "SKU-00010", "quantity": 47, "height": 5.89, "length": 20.68, "depth": 21.72, "weight": 35.56},
    {"sku": "SKU-00002", "quantity": 3, "height": 32.6, "length": 4.32, "depth": 38.96, "weight": 23.74},
    {"sku": "SKU-00003", "quantity": 17, "height": 30.7, "length": 7.81, "depth": 26.82, "weight": 1.56},
    {"sku": "SKU-00001", "quantity": 25, "height": 26.89, "length": 40.38, "depth": 5.98, "weight": 1.1},
]

# Existing-style small/mixed successful fixture retained for regression coverage.
SMALL_MIXED = [
    {"sku": "SMALL-A", "quantity": 36, "height": 8, "length": 12, "depth": 10, "weight": 8},
    {"sku": "SMALL-B", "quantity": 24, "height": 6, "length": 10, "depth": 8, "weight": 5},
    {"sku": "MED-C", "quantity": 12, "height": 12, "length": 18, "depth": 14, "weight": 24},
    {"sku": "FILL-D", "quantity": 20, "height": 5, "length": 8, "depth": 6, "weight": 2},
]

FIXTURES: List[Tuple[str, List[Dict[str, float]]]] = [
    ("Fixture A large carton freight stress", FIXTURE_A),
    ("Fixture B awkward generated shipment", FIXTURE_B),
    ("Small mixed regression", SMALL_MIXED),
]


def count_boxes(items):
    return sum(int(i["quantity"]) for i in items)


def run_fixture(name, items):
    result = pack_shipment(items, PALLET, time_budget_seconds=TARGET_SECONDS)
    diag = result["diagnostics"]
    return {
        "name": name,
        "boxes": count_boxes(items),
        "route": diag["route"],
        "pallets": len(result["pallets"]),
        "overflow": len(result["overflow"]),
        "runtime": diag["runtime_seconds"],
        "violations": diag["violations"]["total"],
        "fallback": diag.get("fallback_reason"),
    }


def main() -> int:
    rows = [run_fixture(name, items) for name, items in FIXTURES]
    print("Shipment name | boxes | route | pallets used | overflow | runtime seconds | violations")
    print("--- | ---: | --- | ---: | ---: | ---: | ---:")
    failed = False
    for row in rows:
        route = row["route"] + (f" ({row['fallback']})" if row["fallback"] else "")
        print(f"{row['name']} | {row['boxes']} | {route} | {row['pallets']} | {row['overflow']} | {row['runtime']:.3f} | {row['violations']}")
        if row["runtime"] > TARGET_SECONDS or row["violations"] or row["overflow"]:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
