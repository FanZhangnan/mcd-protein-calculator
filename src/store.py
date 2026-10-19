"""Local persistence: the shop context only.

After the day mode was removed there is exactly one JSON file under ``data/``:

  state.json - store, date, per-meal reservation times, avoid list

Eaten records (``intake.json``) are archived in ``data/backup-fullday/`` and are
never read again.
"""

from __future__ import annotations

import json
import os
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
    "store": {"code": "1440054", "name": "麦当劳珠海屏湾一路餐厅", "beType": 1, "orderType": 1},
    "date": None,
    "meal_times": {"breakfast": "08:15", "lunch": "12:15", "dinner": "18:30"},
    "prefs": {"avoid": []},
}


class Store:
    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else data_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.state_path = self.base / "state.json"
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
            if key in ("store", "meal_times", "prefs") and isinstance(value, dict):
                state.setdefault(key, {})
                state[key].update(value)
            else:
                state[key] = value
        _write(self.state_path, state)
        return state
