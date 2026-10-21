"""Parity: the static demo solver must return the same plan as the backend.

Both sides read the *same* snapshot: ``tests/fixtures/demo-cache.json`` is the
raw MCP cache from the export run, and ``docs/demo/snapshot.json`` is what the
browser gets. The backend runs from the cache (no MCP, no token), the JS solver
runs the public snapshot.

Run: python3 tests/test_demo_parity.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/demo/snapshot.json"
CACHE = ROOT / "tests/fixtures/demo-cache.json"

MEALS = ["breakfast", "lunch", "dinner"]
PROTEINS = [20, 25, 30, 35, 40, 44, 50, 55]
BUDGETS = [15, 20, 25, 30, 35, 45, 60]
TOLERANCES = [0, 3]
FATS = [None, 30]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def node_bin() -> str:
    return shutil.which("node") or "node"


def prepare_data(tmp: Path) -> None:
    cache = json.loads(CACHE.read_text("utf-8"))
    (tmp / "state.json").write_text(json.dumps({
        "store": {"code": cache["store_code"], "name": "麦当劳珠海屏湾一路餐厅",
                  "beType": cache["be_type"], "orderType": cache["order_type"]},
        "date": cache["date"],
        "meal_times": cache["meal_times"],
    }, ensure_ascii=False), "utf-8")
    shutil.copy2(ROOT / "data/aliases.json", tmp / "aliases.json")
    shutil.copy2(ROOT / "data/upgrade_ladder.json", tmp / "upgrade_ladder.json")

    sys.path.insert(0, str(ROOT / "src"))
    from service import Service  # noqa: E402

    class _Null:
        source_name = "live"
        def now(self):
            raise AssertionError("state.date is set; now() must not be called")

    service = Service(_Null(), base=tmp)
    ctx = service.context()
    payload = dict(cache)
    payload["date"] = ctx.day
    payload["meal_times"] = ctx.meal_times
    payload["cache_key"] = ctx.key()
    payload["store_code"] = ctx.store_code
    payload["be_type"] = ctx.be_type
    payload["order_type"] = ctx.order_type
    payload["source"] = "live"
    (tmp / "cache").mkdir(parents=True, exist_ok=True)
    (tmp / "cache" / f"{ctx.day}__{ctx.store_code}__{ctx.key()}.json").write_text(
        json.dumps(payload, ensure_ascii=False), "utf-8")


def wait_server(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/bootstrap", timeout=3) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise AssertionError("server did not start")


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


@unittest.skipUnless(os.environ.get("RUN_DEMO_PARITY") == "1",
                     "grid parity is slow (672 cases); run with RUN_DEMO_PARITY=1")
class DemoParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert SNAPSHOT.exists(), "run scripts/export_demo_snapshot.py first"
        assert CACHE.exists(), "run scripts/export_demo_snapshot.py first"
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-parity-"))
        prepare_data(cls.tmp)
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = dict(os.environ, MAI_MAI_DATA=str(cls.tmp),
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "src/server.py"), "--port", str(cls.port)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        wait_server(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_grid_matches_backend(self) -> None:
        grid = []
        for meal in MEALS:
            for protein in PROTEINS:
                for budget in BUDGETS:
                    for tol in TOLERANCES:
                        for fat in FATS:
                            grid.append({"meal": meal, "protein_min": protein, "budget": budget,
                                         "protein_tolerance": tol, "fat_max": fat, "max_mains": 2})

        script = """
const fs = require('fs');
const S = require('./web/solver.js');
const snap = JSON.parse(fs.readFileSync('docs/demo/snapshot.json', 'utf8'));
const grid = JSON.parse(fs.readFileSync(process.argv[2] || process.argv[1], 'utf8'));
const out = grid.map((q) => {
  const r = S.solve(snap, q);
  return {
    status: r.protein_status,
    items: r.items.map(i => [i.name, i.quantity]),
    cost: r.cost,
    protein: r.totals.protein,
    saver: r.saver ? { cost: r.saver.cost, protein: r.saver.protein,
      items: r.saver.items.map(i => [i.name, i.quantity]) } : null,
    reach_cost: r.frontier && r.frontier.reach_price ? r.frontier.reach_price.cost : null,
    cap_protein: r.frontier && r.frontier.max_protein ? r.frontier.max_protein.protein : null,
  };
});
process.stdout.write(JSON.stringify(out));
"""
        grid_file = self.tmp / "grid.json"
        grid_file.write_text(json.dumps(grid), "utf-8")
        proc = subprocess.run([node_bin(), "-e", script, str(grid_file)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[:2000])
        js = json.loads(proc.stdout)

        mismatches = []
        for q, j in zip(grid, js):
            body = dict(q)
            if body["fat_max"] is None:
                body.pop("fat_max")
            py = post(self.base, "/api/meal", body)
            py_items = [[i["name"], i["quantity"]] for i in py.get("items") or []]
            js_items = [[n, float(qty)] for n, qty in j.get("items") or []]
            checks = [
                ("status", py.get("protein_status"), j.get("status")),
                ("cost", round(float(py.get("cost") or 0), 2), round(float(j.get("cost") or 0), 2)),
                ("protein", round(float((py.get("totals") or {}).get("protein") or 0), 1),
                 round(float(j.get("protein") or 0), 1)),
                ("items", py_items, js_items),
            ]
            py_saver = py.get("saver")
            if py_saver or j.get("saver"):
                checks.append((
                    "saver_cost",
                    None if not py_saver else round(float(py_saver.get("cost") or 0), 2),
                    None if not j.get("saver") else round(float(j["saver"]["cost"]), 2),
                ))
                checks.append((
                    "saver_items",
                    None if not py_saver else list(py_saver.get("items") or []),
                    None if not j.get("saver") else [
                        n if float(q) == 1 else f"{n} ×{int(q)}" for n, q in j["saver"]["items"]
                    ],
                ))
            py_front = py.get("frontier") or {}
            py_reach = py_front.get("reach_price") or {}
            js_reach = j.get("reach_cost")
            if py_reach or js_reach is not None:
                checks.append(("reach_cost",
                               None if not py_reach else round(float(py_reach.get("cost") or 0), 2),
                               None if js_reach is None else round(float(js_reach), 2)))
            py_cap = py_front.get("max_protein") or {}
            js_cap = j.get("cap_protein")
            if py_cap or js_cap is not None:
                checks.append(("cap_protein",
                               None if not py_cap else round(float(py_cap.get("protein") or 0), 1),
                               None if js_cap is None else round(float(js_cap), 1)))
            for name, a, b in checks:
                if a != b:
                    mismatches.append((q, name, a, b))
                    break

        if mismatches:
            sample = "\n".join(str(m) for m in mismatches[:8])
            self.fail(f"{len(mismatches)}/{len(grid)} cases differ:\n{sample}")
        print(f"parity: {len(grid)} cases identical")


if __name__ == "__main__":
    unittest.main(verbosity=2)
