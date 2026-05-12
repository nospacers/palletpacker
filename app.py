"""Flask entrypoint for the pallet packing trainer backend."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request, send_from_directory

from packing_engine import pack_shipment

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.setdefault("DATABASE", "palletpacker.db")

REQUIRED_CSV_COLUMNS = ("sku", "quantity", "height", "length", "depth", "weight")
OPTIONAL_BOOL_COLUMNS = ("allow_roll", "roll", "allow_rotate", "rotate")


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _number(row: Dict[str, str], column: str, row_number: int, errors: List[str]) -> float:
    value = (row.get(column) or "").strip()
    try:
        parsed = float(value)
    except ValueError:
        errors.append(f"Row {row_number}: {column} must be a number")
        return 0.0
    if parsed <= 0:
        errors.append(f"Row {row_number}: {column} must be greater than zero")
    return parsed


def parse_csv_items(csv_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse shipment rows from CSV text for the upload endpoint and tests."""
    errors: List[str] = []
    stream = io.StringIO(csv_text.strip())
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        return [], ["CSV file is empty or missing a header row"]

    normalized_headers = {header.strip().lower(): header for header in reader.fieldnames if header}
    missing = [column for column in REQUIRED_CSV_COLUMNS if column not in normalized_headers]
    if missing:
        return [], [f"CSV is missing required columns: {', '.join(missing)}"]

    items: List[Dict[str, Any]] = []
    for row_number, raw_row in enumerate(reader, start=2):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw_row.items()}
        if not any(row.values()):
            continue
        sku = row.get("sku", "").strip()
        if not sku:
            errors.append(f"Row {row_number}: sku is required")
            continue
        item = {
            "sku": sku,
            "quantity": int(_number(row, "quantity", row_number, errors)),
            "height": _number(row, "height", row_number, errors),
            "length": _number(row, "length", row_number, errors),
            "depth": _number(row, "depth", row_number, errors),
            "weight": _number(row, "weight", row_number, errors),
        }
        for column in OPTIONAL_BOOL_COLUMNS:
            if column in row:
                item[column] = _parse_bool(row[column], default=(column in {"allow_rotate", "rotate"}))
        items.append(item)
    if not items and not errors:
        errors.append("CSV did not contain any item rows")
    return items, errors


def pallet_from_request() -> Dict[str, Any]:
    source = request.form if request.form else (request.get_json(silent=True) or {})
    return {
        "length": source.get("pallet_length", source.get("length", 48)),
        "depth": source.get("pallet_depth", source.get("depth", 40)),
        "max_height": source.get("max_height", source.get("height", 72)),
        "max_overhang": source.get("max_overhang", source.get("overhang", 0)),
        "allow_extra_pallets": _parse_bool(source.get("allow_extra_pallets", True), default=True),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                items_json TEXT NOT NULL,
                pallet_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def shipment_row_to_dict(row: sqlite3.Row, include_payload: bool = True) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_payload:
        data["items"] = json.loads(row["items_json"])
        data["pallet"] = json.loads(row["pallet_json"])
        data["result"] = json.loads(row["result_json"])
    return data


def save_shipment_record(name: str, items: List[Dict[str, Any]], pallet: Dict[str, Any], result: Dict[str, Any], shipment_id: int | None = None) -> Dict[str, Any]:
    init_db()
    timestamp = now_iso()
    payload = (
        name.strip() or f"Shipment {timestamp}",
        json.dumps(items),
        json.dumps(pallet),
        json.dumps(result),
        timestamp,
    )
    with get_db() as db:
        if shipment_id is None:
            cursor = db.execute(
                "INSERT INTO shipments (name, items_json, pallet_json, result_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (*payload, timestamp),
            )
            shipment_id = int(cursor.lastrowid)
        else:
            db.execute(
                "UPDATE shipments SET name = ?, items_json = ?, pallet_json = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (*payload, shipment_id),
            )
        row = db.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    return shipment_row_to_dict(row)


@app.before_request
def ensure_database() -> None:
    if request.endpoint != "static":
        init_db()


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
    result = pack_shipment(items, pallet, time_budget_seconds=time_budget)
    if payload.get("save"):
        shipment = save_shipment_record(payload.get("name", "Untitled shipment"), items, pallet, result, payload.get("id"))
        result["shipment"] = shipment
    return jsonify(result)


@app.post("/api/pack-csv")
@app.post("/api/pack_csv")
def api_pack_csv():
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"success": False, "errors": ["Upload a CSV file using form field 'file'"]}), 400
    csv_text = upload.read().decode("utf-8-sig")
    items, errors = parse_csv_items(csv_text)
    if errors:
        return jsonify({"success": False, "errors": errors, "items": items}), 400
    time_budget = float(request.form.get("time_budget_seconds", 10.0))
    pallet = pallet_from_request()
    result = pack_shipment(items, pallet, time_budget_seconds=time_budget)
    result["uploaded_items"] = items
    if _parse_bool(request.form.get("save_shipment", False)):
        name = request.form.get("shipment_name") or upload.filename or "Uploaded shipment"
        result["shipment"] = save_shipment_record(name, items, pallet, result)
    return jsonify(result)


@app.get("/api/shipments")
def list_shipments():
    with get_db() as db:
        rows = db.execute("SELECT * FROM shipments ORDER BY updated_at DESC, id DESC").fetchall()
    return jsonify({"shipments": [shipment_row_to_dict(row, include_payload=False) for row in rows]})


@app.post("/api/shipments")
def create_shipment():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    pallet = payload.get("pallet") or {}
    result = payload.get("result") or pack_shipment(items, pallet, time_budget_seconds=float(payload.get("time_budget_seconds", 10.0)))
    shipment = save_shipment_record(payload.get("name", "Untitled shipment"), items, pallet, result)
    return jsonify({"shipment": shipment}), 201


@app.get("/api/shipments/<int:shipment_id>")
def get_shipment(shipment_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    if row is None:
        return jsonify({"success": False, "errors": ["Shipment not found"]}), 404
    return jsonify({"shipment": shipment_row_to_dict(row)})


@app.put("/api/shipments/<int:shipment_id>")
def update_shipment(shipment_id: int):
    payload = request.get_json(silent=True) or {}
    with get_db() as db:
        row = db.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    if row is None:
        return jsonify({"success": False, "errors": ["Shipment not found"]}), 404
    existing = shipment_row_to_dict(row)
    shipment = save_shipment_record(
        payload.get("name", existing["name"]),
        payload.get("items", existing["items"]),
        payload.get("pallet", existing["pallet"]),
        payload.get("result", existing["result"]),
        shipment_id,
    )
    return jsonify({"shipment": shipment})


@app.delete("/api/shipments/<int:shipment_id>")
def delete_shipment(shipment_id: int):
    with get_db() as db:
        cursor = db.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
    if cursor.rowcount == 0:
        return jsonify({"success": False, "errors": ["Shipment not found"]}), 404
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True)
