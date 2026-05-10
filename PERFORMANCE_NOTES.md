# Pallet Packing Performance Notes

## Routing strategy

The backend now performs a fast preflight pass before placing boxes.  Each SKU is classified as one or more of `large_base`, `small_light_top_fill`, `small_heavy_base`, `one_per_layer`, `one_per_pallet`, `two_per_pallet`, `brick_group_candidate`, `slab_or_thin_item`, and `awkward_aspect_ratio`.  The selected route is additive diagnostic metadata:

- `large_carton_freight_mode` sorts large, heavy, one-per-layer, and low-capacity freight cartons first so they reserve stable pallet bases before light top-fill is considered.
- `same_size_brick_mode` is selected for high-quantity identical/similar groups and favors broad same-SKU base coverage with alternating length/depth orientations where feasible.
- `normal_mixed_small_box_mode` handles ordinary mixed shipments with the same bounded deterministic placer.
- `fallback_greedy_mode` is reported only when the time budget is reached; the algorithm then lowers candidate counts rather than starting an unbounded search.

## Geometry lower bounds

For every SKU, the preflight pass computes valid orientations, max boxes per pallet layer, max stack layers under the configured height limit, max boxes per pallet, and a minimum pallet count.  The packer starts with the maximum of this geometry lower bound and the volume-only estimate, preventing impossible volume-estimate layouts from causing long searches.  If mixed geometry still needs more pallets, the packer adds pallets quickly when `allow_extra_pallets` is enabled.

## Guardrails and caches

The engine uses deterministic candidate positions from pallet corners, occupied extents, box edges, and valid support-surface tops.  It avoids dense grid scans and random search by default.  Guardrails include:

- default 10 second run budget;
- maximum candidate positions per item;
- maximum orientations per item class;
- maximum layer patterns per SKU group;
- early fallback to a smaller greedy candidate set when the budget is reached;
- `functools.lru_cache` caches for dimension orientation feasibility and layer-capacity results keyed by dimensions, pallet size, and overhang.

## Known limitations

This is a pragmatic deterministic refactor rather than a full 3D bin-packing optimizer.  It prioritizes correctness, bounded runtime, and complete accounting over mathematically optimal pallet count.  The geometry lower bound is SKU-local, so mixed-SKU interference may require extra pallets beyond the estimate.  The brick-layer behavior is implemented through route selection, orientation ordering, and stable support scoring; it does not run an expensive exhaustive layer-pattern search.
