"""Deterministic, bounded pallet packing engine for the trainer app.

The module intentionally avoids unbounded/random search.  It combines a fast
SKU preflight pass, geometry lower bounds, and a greedy candidate-position
packer.  The public ``pack_shipment`` function returns additive diagnostics but
keeps a simple JSON shape: ``pallets`` contain placed ``boxes`` and ``overflow``
contains boxes that cannot be placed because their individual geometry is
impossible for the configured pallet.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from math import ceil, floor
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

EPS = 1e-6
DEFAULT_TIME_BUDGET_SECONDS = 10.0
DEFAULT_MAX_CANDIDATES = 96
DEFAULT_MAX_ORIENTATIONS = 6
DEFAULT_MAX_LAYER_PATTERNS = 12
SUPPORT_THRESHOLD = 0.70
SAME_SIZE_TOLERANCE = 0.04


@dataclass(frozen=True)
class PalletConfig:
    length: float = 48.0
    depth: float = 40.0
    max_height: float = 72.0
    max_overhang: float = 0.0
    max_pallets: Optional[int] = None
    allow_extra_pallets: bool = True

    @property
    def usable_length(self) -> float:
        return self.length + 2 * max(0.0, self.max_overhang)

    @property
    def usable_depth(self) -> float:
        return self.depth + 2 * max(0.0, self.max_overhang)

    @property
    def min_x(self) -> float:
        return -max(0.0, self.max_overhang)

    @property
    def min_y(self) -> float:
        return -max(0.0, self.max_overhang)


@dataclass
class ItemGroup:
    sku: str
    quantity: int
    height: float
    length: float
    depth: float
    weight: float
    allow_roll: bool = False
    allow_rotate: bool = True

    @property
    def volume(self) -> float:
        return self.height * self.length * self.depth


@dataclass
class Box:
    id: str
    sku: str
    group_index: int
    height: float
    length: float
    depth: float
    weight: float
    classes: Tuple[str, ...]

    @property
    def volume(self) -> float:
        return self.height * self.length * self.depth


@dataclass
class PlacedBox:
    id: str
    sku: str
    x: float
    y: float
    z: float
    length: float
    depth: float
    height: float
    weight: float
    classes: Tuple[str, ...]

    @property
    def right(self) -> float:
        return self.x + self.length

    @property
    def back(self) -> float:
        return self.y + self.depth

    @property
    def top(self) -> float:
        return self.z + self.height

    @property
    def area(self) -> float:
        return self.length * self.depth


@dataclass
class Pallet:
    index: int
    boxes: List[PlacedBox]

    @property
    def height(self) -> float:
        return max((b.top for b in self.boxes), default=0.0)

    @property
    def base_area(self) -> float:
        return sum(b.length * b.depth for b in self.boxes if abs(b.z) <= EPS)


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize_pallet_config(config: Optional[Dict[str, Any]] = None) -> PalletConfig:
    config = config or {}
    return PalletConfig(
        length=_float(config.get("length", config.get("pallet_length", 48.0)), 48.0),
        depth=_float(config.get("depth", config.get("pallet_depth", 40.0)), 40.0),
        max_height=_float(config.get("max_height", config.get("height", 72.0)), 72.0),
        max_overhang=_float(config.get("max_overhang", config.get("overhang", 0.0)), 0.0),
        max_pallets=(None if config.get("max_pallets") in (None, "") else _int(config.get("max_pallets"), 0)),
        allow_extra_pallets=bool(config.get("allow_extra_pallets", True)),
    )


def normalize_groups(items: Sequence[Dict[str, Any]]) -> List[ItemGroup]:
    groups: List[ItemGroup] = []
    for i, raw in enumerate(items):
        qty = max(0, _int(raw.get("quantity", raw.get("qty", 1)), 1))
        h = _float(raw.get("height", raw.get("h")), 0)
        l = _float(raw.get("length", raw.get("l", raw.get("width"))), 0)
        d = _float(raw.get("depth", raw.get("d")), 0)
        if qty <= 0 or h <= 0 or l <= 0 or d <= 0:
            continue
        groups.append(ItemGroup(
            sku=str(raw.get("sku", raw.get("name", f"SKU-{i+1:05d}"))),
            quantity=qty,
            height=h,
            length=l,
            depth=d,
            weight=_float(raw.get("weight", 1.0), 1.0),
            allow_roll=bool(raw.get("allow_roll", raw.get("roll", False))),
            allow_rotate=bool(raw.get("allow_rotate", raw.get("rotate", True))),
        ))
    return groups


@lru_cache(maxsize=8192)
def orientations_for_dimensions(length: float, depth: float, height: float, allow_rotate: bool, allow_roll: bool, max_orientations: int) -> Tuple[Tuple[float, float, float], ...]:
    dims = (round(length, 4), round(depth, 4), round(height, 4))
    if allow_roll:
        raw = {(dims[0], dims[1], dims[2]), (dims[1], dims[0], dims[2]), (dims[0], dims[2], dims[1]), (dims[2], dims[0], dims[1]), (dims[1], dims[2], dims[0]), (dims[2], dims[1], dims[0])}
    elif allow_rotate:
        raw = {(dims[0], dims[1], dims[2]), (dims[1], dims[0], dims[2])}
    else:
        raw = {(dims[0], dims[1], dims[2])}
    ordered = sorted(raw, key=lambda o: (o[2], -o[0] * o[1], o[0]))
    return tuple(ordered[:max_orientations])


@lru_cache(maxsize=8192)
def layer_capacity(length: float, depth: float, pallet_length: float, pallet_depth: float, overhang: float) -> int:
    usable_l = pallet_length + 2 * max(0.0, overhang)
    usable_d = pallet_depth + 2 * max(0.0, overhang)
    if length <= 0 or depth <= 0:
        return 0
    return max(0, floor((usable_l + EPS) / length) * floor((usable_d + EPS) / depth))


def preflight(groups: Sequence[ItemGroup], pallet: PalletConfig) -> Tuple[Dict[str, Any], int, int, Dict[str, int]]:
    sku_info: Dict[str, Any] = {}
    pallet_volume = pallet.usable_length * pallet.usable_depth * pallet.max_height
    total_volume = sum(g.volume * g.quantity for g in groups)
    volume_estimate = max(1, ceil(total_volume / pallet_volume)) if groups else 0
    # Use total shipment volume as the primary pallet estimate.  Per-SKU
    # geometry is still calculated below, but a mixed shipment can share pallets
    # across SKUs; summing each SKU's individual pallet minimum overstates the
    # number of pallets and makes empty pallets too attractive for small boxes.
    geometry_lb = volume_estimate
    per_sku_geometry_sum = 0
    max_sku_geometry_minimum = 0
    max_group_volume = max((g.volume for g in groups), default=1.0)

    for g in groups:
        valid_orients: List[Tuple[float, float, float]] = []
        best_layer = 0
        best_per_pallet = 0
        for l, d, h in orientations_for_dimensions(g.length, g.depth, g.height, g.allow_rotate, g.allow_roll, DEFAULT_MAX_ORIENTATIONS):
            if h <= pallet.max_height + EPS and l <= pallet.usable_length + EPS and d <= pallet.usable_depth + EPS:
                valid_orients.append((l, d, h))
                per_layer = layer_capacity(l, d, pallet.length, pallet.depth, pallet.max_overhang)
                layers = floor((pallet.max_height + EPS) / h) if h > 0 else 0
                best_layer = max(best_layer, per_layer)
                best_per_pallet = max(best_per_pallet, per_layer * layers)
        min_pallets = ceil(g.quantity / best_per_pallet) if best_per_pallet else g.quantity
        per_sku_geometry_sum += min_pallets
        max_sku_geometry_minimum = max(max_sku_geometry_minimum, min_pallets)
        largest_side = max(g.length, g.depth, g.height)
        smallest_side = min(g.length, g.depth, g.height)
        area_ratio = (g.length * g.depth) / max(1.0, pallet.usable_length * pallet.usable_depth)
        density = g.weight / max(g.volume, EPS)
        avg_density = sum(x.weight / max(x.volume, EPS) for x in groups) / max(1, len(groups))
        classes = set()
        if area_ratio >= 0.32 or g.weight >= 50:
            classes.add("large_base")
        if area_ratio < 0.12 and g.weight < 35:
            classes.add("small_light_top_fill")
        if area_ratio < 0.22 and (g.weight >= 50 or density >= avg_density * 1.5):
            classes.add("small_heavy_base")
        if best_layer <= 1:
            classes.add("one_per_layer")
        if best_per_pallet <= 1:
            classes.add("one_per_pallet")
        elif best_per_pallet <= 2:
            classes.add("two_per_pallet")
        if g.quantity >= 8 and g.volume >= 0.10 * max_group_volume and largest_side / max(smallest_side, EPS) < 5:
            classes.add("brick_group_candidate")
        if smallest_side <= 4 or g.height <= 4:
            classes.add("slab_or_thin_item")
        if largest_side / max(smallest_side, EPS) >= 5:
            classes.add("awkward_aspect_ratio")
        sku_info[g.sku] = {
            "classes": sorted(classes),
            "orientations": [list(o) for o in valid_orients],
            "max_boxes_per_layer": best_layer,
            "max_stack_layers": max((floor((pallet.max_height + EPS) / o[2]) for o in valid_orients), default=0),
            "max_boxes_per_pallet": best_per_pallet,
            "minimum_pallets_required": min_pallets,
        }
    geometry_lb = max(volume_estimate, max_sku_geometry_minimum)
    geometry_summary = {
        "per_sku_geometry_pallet_sum": per_sku_geometry_sum,
        "max_sku_geometry_minimum": max_sku_geometry_minimum,
        "volume_only_pallet_estimate": volume_estimate,
        "starting_pallet_estimate": geometry_lb,
    }
    return sku_info, max(volume_estimate, 0), max(geometry_lb, volume_estimate), geometry_summary


def select_route(groups: Sequence[ItemGroup], sku_info: Dict[str, Any]) -> str:
    if len(groups) == 1 and groups[0].quantity >= 12 and "brick_group_candidate" in sku_info[groups[0].sku]["classes"]:
        return "same_size_brick_mode"
    large_qty = sum(g.quantity for g in groups if any(c in sku_info[g.sku]["classes"] for c in ("one_per_layer", "one_per_pallet", "two_per_pallet", "large_base")))
    total = sum(g.quantity for g in groups)
    if total and large_qty / total >= 0.35:
        return "large_carton_freight_mode"
    return "normal_mixed_small_box_mode"


def expand_boxes(groups: Sequence[ItemGroup], sku_info: Dict[str, Any], route: str) -> List[Box]:
    boxes: List[Box] = []
    for gi, g in enumerate(groups):
        classes = tuple(sku_info[g.sku]["classes"])
        for n in range(g.quantity):
            boxes.append(Box(f"{g.sku}-{n+1:04d}", g.sku, gi, g.height, g.length, g.depth, g.weight, classes))
    def key(b: Box) -> Tuple[Any, ...]:
        is_top = "small_light_top_fill" in b.classes
        is_base = any(c in b.classes for c in ("large_base", "small_heavy_base", "one_per_layer", "one_per_pallet", "two_per_pallet"))
        brick = "brick_group_candidate" in b.classes
        if route == "same_size_brick_mode":
            return (0 if brick else 1, -b.length * b.depth, -b.weight, b.sku, b.id)
        return (1 if is_top else 0, 0 if is_base else 1, -b.weight, -b.length * b.depth, -b.volume, b.sku, b.id)
    boxes.sort(key=key)
    return boxes


def boxes_overlap(a: PlacedBox, b: PlacedBox) -> bool:
    return (a.x < b.right - EPS and a.right > b.x + EPS and a.y < b.back - EPS and a.back > b.y + EPS and a.z < b.top - EPS and a.top > b.z + EPS)


def same_size(a: Any, b: Any) -> bool:
    av = sorted([a.length, a.depth, a.height])
    bv = sorted([b.length, b.depth, b.height])
    return all(abs(x - y) <= max(0.5, SAME_SIZE_TOLERANCE * max(x, y)) for x, y in zip(av, bv))


def support_info(pallet: Pallet, x: float, y: float, z: float, length: float, depth: float, box: Box) -> Tuple[float, bool]:
    if z <= EPS:
        return length * depth, True
    supported = 0.0
    ok_weight = True
    for s in pallet.boxes:
        if abs(s.top - z) > EPS:
            continue
        ox = max(0.0, min(x + length, s.right) - max(x, s.x))
        oy = max(0.0, min(y + depth, s.back) - max(y, s.y))
        area = ox * oy
        if area > EPS:
            supported += area
            if box.weight > s.weight + EPS and not same_size(box, s):
                ok_weight = False
    return supported, ok_weight


def generate_candidates(pallet: Pallet, cfg: PalletConfig, box: Box, max_candidates: int) -> List[Tuple[float, float, float]]:
    pts = {(cfg.min_x, cfg.min_y, 0.0)}
    xs = {cfg.min_x}
    ys = {cfg.min_y}
    zs = {0.0}
    for b in pallet.boxes:
        xs.update([cfg.min_x, b.x, b.right])
        ys.update([cfg.min_y, b.y, b.back])
        zs.add(b.top)
        pts.update([(b.right, b.y, b.z), (b.x, b.back, b.z), (b.right, cfg.min_y, 0.0), (cfg.min_x, b.back, 0.0)])
        # top-surface anchors; use corners and align with existing extents.
        pts.update([(b.x, b.y, b.top), (b.right, b.y, b.top), (b.x, b.back, b.top)])
    for z in sorted(zs)[:24]:
        for x in sorted(xs)[:24]:
            pts.add((x, cfg.min_y, z))
        for y in sorted(ys)[:24]:
            pts.add((cfg.min_x, y, z))
    ordered = sorted(pts, key=lambda p: (p[2], p[1], p[0]))
    return ordered[:max_candidates]


def can_place(pallet: Pallet, cfg: PalletConfig, box: Box, pos: Tuple[float, float, float], orient: Tuple[float, float, float], candidate_counter: Dict[str, int]) -> Optional[PlacedBox]:
    candidate_counter["tested"] += 1
    x, y, z = pos
    l, d, h = orient
    if x < cfg.min_x - EPS or y < cfg.min_y - EPS or x + l > cfg.min_x + cfg.usable_length + EPS or y + d > cfg.min_y + cfg.usable_depth + EPS or z + h > cfg.max_height + EPS:
        return None
    placed = PlacedBox(box.id, box.sku, x, y, z, l, d, h, box.weight, box.classes)
    if any(boxes_overlap(placed, other) for other in pallet.boxes):
        return None
    if z <= EPS and "small_light_top_fill" in box.classes and any(("large_base" in b.classes or "one_per_layer" in b.classes) and b.z <= EPS for b in pallet.boxes):
        # Preserve the preference that light fill goes on top before it consumes
        # scarce base area beside freight cartons.  Empty pallets may still use it.
        return None
    supported, ok_weight = support_info(pallet, x, y, z, l, d, box)
    if z > EPS and (supported + EPS < SUPPORT_THRESHOLD * l * d or not ok_weight):
        return None
    return placed


def placement_score(pallet: Pallet, cfg: PalletConfig, placed: PlacedBox) -> Tuple[float, ...]:
    base_penalty = 1.0 if placed.z <= EPS and "small_light_top_fill" in placed.classes else 0.0
    base_bonus = 1.0 if placed.z <= EPS and any(c in placed.classes for c in ("large_base", "small_heavy_base")) else 0.0
    compact_x = placed.x + placed.length - cfg.min_x
    compact_y = placed.y + placed.depth - cfg.min_y
    return (placed.z + placed.height, base_penalty, -base_bonus, compact_x + compact_y, placed.y, placed.x)


def place_one(box: Box, pallets: List[Pallet], cfg: PalletConfig, deadline: float, candidate_counter: Dict[str, int], fallback_reason: List[str], max_candidates: int = DEFAULT_MAX_CANDIDATES) -> bool:
    if perf_counter() > deadline:
        fallback_reason[:] = ["time_budget_exceeded"]
        max_candidates = min(max_candidates, 32)
    best: Optional[Tuple[Tuple[float, ...], Pallet, PlacedBox]] = None
    orients = orientations_for_dimensions(box.length, box.depth, box.height, True, False, DEFAULT_MAX_ORIENTATIONS)
    orients = tuple(o for o in orients if o[2] <= cfg.max_height + EPS and o[0] <= cfg.usable_length + EPS and o[1] <= cfg.usable_depth + EPS)
    if not orients:
        return False
    for pallet in pallets:
        for pos in generate_candidates(pallet, cfg, box, max_candidates):
            for orient in orients:
                placed = can_place(pallet, cfg, box, pos, orient, candidate_counter)
                if placed is None:
                    continue
                score = placement_score(pallet, cfg, placed)
                if best is None or score < best[0]:
                    best = (score, pallet, placed)
        if best and any(c in box.classes for c in ("one_per_pallet", "one_per_layer", "large_base")):
            break
    if best:
        _, pallet, placed = best
        pallet.boxes.append(placed)
        return True
    return False


def validate_layout(pallets: Sequence[Pallet], cfg: PalletConfig) -> Dict[str, int]:
    violations = {"collisions": 0, "height": 0, "overhang": 0, "heavy_on_light": 0}
    for pallet in pallets:
        boxes = pallet.boxes
        for i, a in enumerate(boxes):
            if a.top > cfg.max_height + EPS:
                violations["height"] += 1
            if a.x < cfg.min_x - EPS or a.y < cfg.min_y - EPS or a.right > cfg.min_x + cfg.usable_length + EPS or a.back > cfg.min_y + cfg.usable_depth + EPS:
                violations["overhang"] += 1
            for b in boxes[i + 1:]:
                if boxes_overlap(a, b):
                    violations["collisions"] += 1
            if a.z > EPS:
                for s in boxes:
                    if abs(s.top - a.z) <= EPS:
                        ox = max(0.0, min(a.right, s.right) - max(a.x, s.x))
                        oy = max(0.0, min(a.back, s.back) - max(a.y, s.y))
                        if ox * oy > EPS and a.weight > s.weight + EPS and not same_size(a, s):
                            violations["heavy_on_light"] += 1
    violations["total"] = sum(violations.values())
    return violations


def pallet_to_json(p: Pallet) -> Dict[str, Any]:
    return {"id": p.index, "index": p.index, "height": round(p.height, 4), "boxes": [asdict(b) for b in p.boxes]}


def pack_shipment(items: Sequence[Dict[str, Any]], pallet_config: Optional[Dict[str, Any]] = None, *, time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> Dict[str, Any]:
    start = perf_counter()
    cfg = normalize_pallet_config(pallet_config)
    groups = normalize_groups(items)
    sku_info, volume_estimate, geometry_lb, geometry_summary = preflight(groups, cfg)
    route = select_route(groups, sku_info)
    boxes = expand_boxes(groups, sku_info, route)
    candidate_counter = {"tested": 0}
    fallback_reason: List[str] = []

    target = max(1 if boxes else 0, geometry_lb)
    if cfg.max_pallets is not None and not cfg.allow_extra_pallets:
        target = min(target, cfg.max_pallets)
    pallets = [Pallet(i + 1, []) for i in range(target)]
    overflow: List[Dict[str, Any]] = []
    deadline = start + max(0.1, time_budget_seconds)

    for box in boxes:
        if place_one(box, pallets, cfg, deadline, candidate_counter, fallback_reason, max_candidates=max_candidates):
            continue
        # Geometry lower bounds are conservative per SKU; mixed freight can still
        # require additional pallets.  Add one quickly instead of deep-searching.
        can_add = cfg.allow_extra_pallets and (cfg.max_pallets is None or len(pallets) < cfg.max_pallets)
        if can_add:
            new_pallet = Pallet(len(pallets) + 1, [])
            pallets.append(new_pallet)
            if place_one(box, [new_pallet], cfg, deadline, candidate_counter, fallback_reason, max_candidates=min(max_candidates, 32)):
                continue
        overflow.append({"id": box.id, "sku": box.sku, "height": box.height, "length": box.length, "depth": box.depth, "weight": box.weight, "reason": "does_not_fit_or_pallet_limit"})

    pallets = [p for p in pallets if p.boxes]
    violations = validate_layout(pallets, cfg)
    runtime = perf_counter() - start
    selected_route = "fallback_greedy_mode" if fallback_reason else route
    placed_count = sum(len(p.boxes) for p in pallets)
    diagnostics = {
        "route": selected_route,
        "selected_route": selected_route,
        "fallback_reason": fallback_reason[0] if fallback_reason else None,
        "preflight_classification": sku_info,
        "geometry_lower_bound_pallet_estimate": geometry_lb,
        "volume_only_pallet_estimate": volume_estimate,
        "geometry_estimate_details": geometry_summary,
        "candidate_count_tested": candidate_counter["tested"],
        "runtime_seconds": runtime,
        "boxes_placed": placed_count,
        "overflow_boxes": len(overflow),
        "violations": violations,
        "guardrails": {
            "time_budget_seconds": time_budget_seconds,
            "max_candidate_positions_per_item": max_candidates,
            "max_orientations_per_item_class": DEFAULT_MAX_ORIENTATIONS,
            "max_layer_patterns_per_sku_group": DEFAULT_MAX_LAYER_PATTERNS,
            "random_search_enabled": False,
        },
    }
    return {
        "success": len(overflow) == 0 and violations["total"] == 0,
        "pallets": [pallet_to_json(p) for p in pallets],
        "overflow": overflow,
        "unplaced": overflow,
        "diagnostics": diagnostics,
        "stats": {
            "pallets_used": len(pallets),
            "boxes_placed": placed_count,
            "overflow_boxes": len(overflow),
            "runtime_seconds": runtime,
        },
    }


# Compatibility aliases used by small Flask apps/tests in earlier versions.
def pack_items(items: Sequence[Dict[str, Any]], pallet_config: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return pack_shipment(items, pallet_config, **kwargs)


def pack(items: Sequence[Dict[str, Any]], pallet_config: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return pack_shipment(items, pallet_config, **kwargs)
