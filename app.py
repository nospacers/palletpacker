"""Minimal Flask entrypoint for the pallet packing trainer backend."""
from __future__ import annotations

from flask import Flask, jsonify, request, send_from_directory

from packing_engine import pack_shipment

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.post("/api/pack")
@app.post("/pack")
def api_pack():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("shipment") or payload.get("boxes") or []
    pallet = payload.get("pallet") or payload.get("pallet_config") or {}
    time_budget = float(payload.get("time_budget_seconds", 10.0))
    return jsonify(pack_shipment(items, pallet, time_budget_seconds=time_budget))


if __name__ == "__main__":
    app.run(debug=True)
