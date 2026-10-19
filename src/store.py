"""Local persistence: user targets + immutable-until-edited intake records.

Two JSON files under ``data/``:
  state.json   - targets, store, meal times, preferences, chosen estimate scenario
  intake.json  - eaten records, each carrying a full nutrition snapshot

A record stores what was true *at the time it was written* (nutrition values,
data status, estimate source, price status). Later menu or nutrition refreshes
never rewrite history.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def data_dir() -> Path:
    path = Path(os.environ.get("MAI_MAI_DATA", DEFAULT_DATA_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


DEFAULT_STATE: dict[str, Any] = {
    "targets": {
        "kcal_min": 1700,
        "kcal_max": 2000,
        "protein_min": 85,
        "fat_min": None,
        "fat_max": 85,
        "carb_min": None,
        "carb_max": 250,
        "budget": 100,
    },
    "store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅", "beType": 1, "orderType": 1},
    "date": None,
    "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    "prefs": {"avoid": []},
    "estimate_scenario": "official_only",
    "takeaway": "eat-in",
}


class Store:
    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else data_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.state_path = self.base / "state.json"
        self.intake_path = self.base / "intake.json"
        if not self.state_path.exists():
            _write(self.state_path, DEFAULT_STATE)

    # ------------------------------------------------------------------ state
    def get_state(self) -> dict[str, Any]:
        state = _read(self.state_path, dict(DEFAULT_STATE))
        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged.update(state or {})
        return merged

    def save_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.get_state()
        for key, value in patch.items():
            if key in ("targets", "store", "meal_times", "prefs") and isinstance(value, dict):
                state.setdefault(key, {})
                state[key].update(value)
            else:
                state[key] = value
        _write(self.state_path, state)
        return state

    # ----------------------------------------------------------------- intake
    def list_intake(self, date: str | None = None) -> list[dict[str, Any]]:
        payload = _read(self.intake_path, {"records": []})
        records = payload.get("records", [])
        if date:
            records = [r for r in records if r.get("date") == date]
        return records

    def add_intake(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = _read(self.intake_path, {"records": []})
        record = dict(record)
        record["id"] = record.get("id") or uuid.uuid4().hex[:12]
        record["created_at"] = record.get("created_at") or datetime.now().isoformat(timespec="seconds")
        record["locked"] = True
        payload.setdefault("records", []).append(record)
        _write(self.intake_path, payload)
        return record

    def delete_intake(self, record_id: str) -> bool:
        payload = _read(self.intake_path, {"records": []})
        before = len(payload.get("records", []))
        payload["records"] = [r for r in payload["records"] if r.get("id") != record_id]
        _write(self.intake_path, payload)
        return len(payload["records"]) != before

    def replace_intake(self, record_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
        payload = _read(self.intake_path, {"records": []})
        for idx, existing in enumerate(payload.get("records", [])):
            if existing.get("id") == record_id:
                record = dict(record)
                record["id"] = record_id
                record["created_at"] = existing.get("created_at")
                record["updated_at"] = datetime.now().isoformat(timespec="seconds")
                record["locked"] = True
                payload["records"][idx] = record
                _write(self.intake_path, payload)
                return record
        return None
