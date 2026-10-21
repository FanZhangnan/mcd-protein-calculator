"""Local web entry point + JSON API (stdlib only)."""

from __future__ import annotations

import json
import math
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
import quantity as quantity_mod  # noqa: E402
from service import (LiveProvider, OfflineProvider, Service,  # noqa: E402
                     UnsupportedCustomization)
from store import Store  # noqa: E402

WEB_DIR = ROOT / "web"
PORT = 8765
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class InputError(ValueError):
    pass


def _num(value: Any, label: str, *, allow_none: bool = True,
         minimum: float | None = 0.0) -> float | None:
    if value in ("", None):
        if allow_none:
            return None
        raise InputError(f"{label}不能为空")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{label}必须是数字")
    if minimum is not None and number < minimum:
        raise InputError(f"{label}不能小于 {minimum}")
    return number


def _int_in_range(value: Any, label: str, low: int, high: int, *, default: int) -> int:
    """Integer-only input; floats like 3.5 are refused instead of rounded."""
    if value in ("", None):
        return default
    if isinstance(value, bool):
        raise InputError(f"{label}必须是 {low}–{high} 的整数")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{label}必须是 {low}–{high} 的整数")
    if not math.isfinite(number) or not number.is_integer():
        raise InputError(f"{label}必须是 {low}–{high} 的整数")
    number = int(number)
    if not low <= number <= high:
        raise InputError(f"{label}只能是 {low}–{high} 的整数")
    return number


def validate_meal_request(body: dict[str, Any]) -> tuple[str, planner_mod.Targets, bool, int]:
    """Validate ``POST /api/meal``.

    Required: meal, protein_min, budget. Everything else is optional and only
    becomes a hard constraint when the user actually fills it in.
    """
    meal = str(body.get("meal") or "")
    if meal not in planner_mod.MEALS:
        raise InputError("meal 必须是 breakfast / lunch / dinner")
    protein_min = _num(body.get("protein_min"), "蛋白质下限", allow_none=False, minimum=0)
    if protein_min <= 0:
        # 0 would silently mean "no goal" and return the cheapest item, which is
        # not what a muscle-building calculator is being asked for.
        raise InputError("蛋白质下限必须大于 0")
    budget = _num(body.get("budget"), "价格上限", allow_none=False, minimum=0)
    if budget <= 0:
        raise InputError("价格上限必须大于 0")
    fat_max = _num(body.get("fat_max"), "脂肪上限", minimum=0)
    kcal_min = _num(body.get("kcal_min"), "热量下限", minimum=0)
    kcal_max = _num(body.get("kcal_max"), "热量上限", minimum=0)
    if kcal_min is not None and kcal_max is not None and kcal_max <= kcal_min:
        raise InputError("热量上限必须大于下限")
    max_mains = body.get("max_mains", 2)
    if isinstance(max_mains, bool) or max_mains in ("", None):
        raise InputError("主食数量上限必须是 1–3 的整数")
    try:
        max_mains = int(str(max_mains).strip())
    except (TypeError, ValueError):
        raise InputError("主食数量上限必须是 1–3 的整数")
    if not 1 <= max_mains <= 3:
        raise InputError("主食数量上限只能是 1、2 或 3")
    protein_tolerance = _int_in_range(
        body.get("protein_tolerance"), "可接受少几克蛋白质", 0, 10, default=3)
    targets = planner_mod.Targets(
        protein_min=protein_min, budget=budget, fat_max=fat_max,
        kcal_min=kcal_min, kcal_max=kcal_max,
        protein_tolerance=float(protein_tolerance),
    )
    return meal, targets, bool(body.get("allow_side", False)), max_mains


def validate_select(value: Any) -> str:
    select = str(value or "main")
    if select not in ("main", "saver"):
        raise InputError("select 只能是 main 或 saver")
    return select


def validate_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Only the shop context is persisted now — no day targets."""
    patch: dict[str, Any] = {}
    if "store" in body and body["store"]:
        code = str(body["store"].get("code") or "").strip()
        if not code:
            raise InputError("门店代码不能为空")
        patch["store"] = {"code": code}
    if "meal_times" in body and body["meal_times"]:
        times = {}
        for meal in planner_mod.MEALS:
            value = body["meal_times"].get(meal)
            if value:
                if not TIME_RE.match(value):
                    raise InputError(f"{meal} 时间格式应为 HH:MM")
                hour, minute = (int(part) for part in value.split(":"))
                if hour > 23 or minute > 59:
                    raise InputError(f"{meal} 时间必须是 00:00–23:59")
                times[meal] = value
        if times:
            patch["meal_times"] = {**body["meal_times"], **times}
    if "date" in body:
        patch["date"] = body["date"] or None
    return patch


def build_service(offline: bool = False) -> Service:
    provider = OfflineProvider(ROOT / "data" / "fixtures") if offline else LiveProvider()
    return Service(provider)


class Handler(BaseHTTPRequestHandler):
    service: Service
    server_version = "MaiMaiPlanner/2.0"

    # ------------------------------------------------------------------ utils
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, rel: str) -> None:
        path = (WEB_DIR / rel).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.exists():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # --------------------------------------------------------------- handlers
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self._static("index.html")
        if path in ("/app.js", "/style.css"):
            return self._static(path.lstrip("/"))
        if path == "/api/bootstrap":
            return self._guarded(self._bootstrap)
        if path == "/api/meal/landscape":
            qs = parse_qs(parsed.query)
            meal = (qs.get("meal") or ["lunch"])[0]
            raw = (qs.get("max_mains") or ["2"])[0]
            return self._guarded(lambda: self._landscape(meal, raw))
        if path == "/api/removable":
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [""])[0]
            meal = (qs.get("meal") or [None])[0]
            return self._guarded(lambda: self._json(self.service.removable_options(code, meal)))
        if path == "/api/ladder":
            return self._guarded(lambda: self._json({
                "pairs": self.service.upgrade_ladder()}))
        if path == "/api/coupons":
            qs = parse_qs(parsed.query)
            meal = (qs.get("meal") or ["lunch"])[0]
            return self._guarded(lambda: self._json(self.service.coupons(meal)))
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        routes = {
            "/api/refresh": lambda: self._json(self.service.refresh()),
            "/api/settings": lambda: self._settings(body),
            "/api/meal": lambda: self._meal(body),
            "/api/quote": lambda: self._quote(body),
            "/api/quote_with_coupon": lambda: self._quote_with_coupon(body),
            "/api/recompute": lambda: self._recompute(body),
        }
        handler = routes.get(path)
        if not handler:
            return self._json({"error": "unknown endpoint", "kind": "not_found"}, 404)
        return self._guarded(handler)

    def do_PATCH(self) -> None:  # noqa: N802
        return self._json({"error": "unknown endpoint", "kind": "not_found"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        return self._json({"error": "unknown endpoint", "kind": "not_found"}, 404)

    def _guarded(self, fn) -> None:
        try:
            fn()
        except quantity_mod.InvalidQuantity as exc:
            self._json({"error": str(exc), "kind": "invalid_quantity"}, 400)
        except UnsupportedCustomization as exc:
            self._json({"error": str(exc), "kind": "unsupported_customization",
                        "supported": exc.supported, "action": exc.action}, 400)
        except InputError as exc:
            self._json({"error": str(exc), "kind": "input"}, 400)
        except Exception as exc:  # keep UI alive, surface the error
            self._json({"error": f"{type(exc).__name__}: {exc}", "kind": "server"}, 500)

    # ------------------------------------------------------------------- impl
    def _bootstrap(self) -> None:
        state = self.service.store.get_state()
        info: dict[str, Any] = {}
        catalog_info: dict[str, Any] = {}
        unknown: dict[str, list] = {}
        combos: dict[str, dict] = {}
        desserts: dict[str, list] = {}
        try:
            self.service.ensure_cache()
            info = self.service.cache_info()
            catalog_info = self.service.catalog().describe()
            for meal in planner_mod.MEALS:
                unknown[meal] = self.service.unknown_items(meal)
                combos[meal] = self.service.combo_report(meal)
                desserts[meal] = self.service.dessert_items(meal)
        except Exception as exc:
            info = {"source": "unavailable", "error": str(exc)}
        source = self._source_kind(info)
        self._json({
            "state": state,
            "cache": info,
            "catalog": catalog_info,
            "unknown": unknown,
            "combos": combos,
            "desserts": desserts,
            "baseline": self.service.baseline_status(),
            "working_signature": self.service.working_signature(),
            "provider_source": getattr(self.service.provider, "source_name", "live"),
            "source": source,
            "fetched_at": info.get("fetched_at"),
        })

    def _source_kind(self, info: dict[str, Any]) -> str:
        """Normalize the data source to exactly ``live`` / ``cache`` / ``offline``.

        Decided from what the provider actually delivered, never from the port
        number: ``offline`` when the fixture provider answered, ``live`` for a
        fresh fetch from the real POS, ``cache`` when we are serving a previously
        fetched snapshot.
        """
        provider = getattr(self.service.provider, "source_name", "live")
        if provider == "offline":
            return "offline"
        raw = info.get("source")
        if raw == "offline":
            return "offline"
        if raw == "cache" or raw == "cached":
            return "cache"
        if raw in ("live", "real"):
            return "live"
        # ``none`` / ``unavailable`` / anything unknown: the provider is a live
        # one but the snapshot did not say it was freshly fetched.
        return "cache" if info.get("fetched_at") else "live"

    def _settings(self, body: dict[str, Any]) -> None:
        patch = validate_settings(body)
        if not patch:
            return self._json({"error": "没有可保存的字段"}, 400)
        state = self.service.store.save_state(patch)
        self.service.invalidate()
        self._json({"state": state, "cache": self.service.cache_info()})

    def _quote(self, body: dict[str, Any]) -> None:
        """Quote the plan currently on screen. Never re-runs the solver."""
        payload, status = self.service.quote_working(str(body.get("signature") or ""))
        self._json(payload, status)

    def _quote_with_coupon(self, body: dict[str, Any]) -> None:
        """Trial-quote the current plan with store coupons (never redeems one)."""
        meal = str(body.get("meal") or "lunch")
        if meal not in planner_mod.MEALS:
            raise InputError("meal 必须是 breakfast / lunch / dinner")
        payload, status = self.service.quote_with_coupon(
            meal, str(body.get("signature") or ""))
        self._json(payload, status)

    def _meal(self, body: dict[str, Any]) -> None:
        """One meal. Does not read any history."""
        meal, targets, allow_side, max_mains = validate_meal_request(body)
        select = validate_select(body.get("select"))
        payload = self.service.plan_meal(meal, targets, allow_side,
                                         quote=bool(body.get("quote")),
                                         max_mains=max_mains,
                                         select=select)
        self._json(payload)

    def _landscape(self, meal: str, raw_max_mains: str) -> None:
        if meal not in planner_mod.MEALS:
            raise InputError("meal 必须是 breakfast / lunch / dinner")
        try:
            max_mains = int(str(raw_max_mains).strip())
        except (TypeError, ValueError):
            raise InputError("max_mains 必须是 1–3 的整数")
        if not 1 <= max_mains <= 3:
            raise InputError("max_mains 只能是 1、2 或 3")
        self._json(self.service.landscape(meal, max_mains))

    def _recompute(self, body: dict[str, Any]) -> None:
        payload = self.service.recompute(
            body.get("quantities") or {}, body.get("customizations") or {})
        if payload.get("error"):
            code = 409 if payload.get("kind") in ("stale_baseline", "no_baseline") else 400
            return self._json(payload, code)
        self._json(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)


def main() -> int:
    offline = "--offline" in sys.argv
    port = PORT
    for idx, arg in enumerate(sys.argv):
        if arg == "--port" and idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    service = build_service(offline)
    Handler.service = service
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"麦当劳增肌计算器 running on http://127.0.0.1:{port}  "
          f"(mode={'offline' if offline else 'live'}, source={service.provider.source_name})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
