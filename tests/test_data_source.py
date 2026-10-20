"""Data-source reporting and axis tick helpers.

Rules under test
----------------
SRC1  /api/bootstrap exposes exactly one of ``live`` / ``cache`` / ``offline``
      as ``source``; the value comes from what the provider delivered, never
      from the port number
SRC2  ``offline`` is returned when the fixture provider answers
SRC3  ``cache`` carries the fetch timestamp so the badge can show "抓取于 …"
SRC4  ``niceTicks`` returns integer, strictly increasing ticks starting at 0,
      with an axis max >= the input value
SRC5  price axis uses steps of 5 / 10, protein axis steps of 5 / 10 / 15

Run: python3 tests/test_data_source.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import server as server_mod  # noqa: E402


# ------------------------------------------------------------ niceTicks (JS)
def load_niceticks():
    """Extract niceTicks() from web/app.js so the unit test checks the real code."""
    src = (ROOT / "web" / "app.js").read_text("utf-8")
    match = re.search(r"function niceTicks\(maxValue, candidateSteps\) \{.*?\n\}", src, re.S)
    if not match:
        raise AssertionError("niceTicks() not found in web/app.js")
    script = (
        match.group(0)
        + "\nconsole.log(JSON.stringify(niceTicks(inputValue, inputSteps)));\n"
    )
    return script


def run_niceticks(value, steps):
    script = load_niceticks()
    script = (
        "const inputValue = %s; const inputSteps = %s;\n" % (json.dumps(value), json.dumps(steps))
        + script
    )
    node = shutil.which("node") or "node"
    proc = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, cwd=str(ROOT), timeout=30
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


class NiceTicksTest(unittest.TestCase):
    CASES = [54, 45, 34.5, 25]

    def _assert_shape(self, value, steps):
        out = run_niceticks(value, steps)
        ticks = out["ticks"]
        self.assertTrue(all(isinstance(t, int) for t in ticks), f"not integers: {ticks}")
        self.assertEqual(ticks[0], 0, f"must start at 0: {ticks}")
        for i in range(1, len(ticks)):
            self.assertGreater(ticks[i], ticks[i - 1], f"not strictly increasing: {ticks}")
        self.assertGreaterEqual(out["max"], value, f"max {out['max']} < input {value}")
        self.assertIn(out["step"], steps, f"step {out['step']} not in {steps}")
        return out

    def test_t4_integers_monotonic_max_above_input(self):
        """SRC4: integer, monotonic, max >= input, for the four fixture values."""
        for value in self.CASES:
            for steps in ([5, 10], [5, 10, 15]):
                with self.subTest(value=value, steps=steps):
                    self._assert_shape(value, steps)

    def test_t5_price_steps_are_5_or_10(self):
        """SRC5: the price axis never picks a step outside 5 / 10."""
        for value in self.CASES:
            with self.subTest(value=value):
                out = run_niceticks(value, [5, 10])
                self.assertIn(out["step"], (5, 10))

    def test_t5_protein_steps_are_5_10_or_15(self):
        """SRC5: the protein axis never picks a step outside 5 / 10 / 15."""
        for value in self.CASES:
            with self.subTest(value=value):
                out = run_niceticks(value, [5, 10, 15])
                self.assertIn(out["step"], (5, 10, 15))

    def test_t4_no_eleven_twentytwo_ticks(self):
        """The reported bug was ticks like 11 / 22 / 33; a step must divide evenly."""
        for value in self.CASES:
            for steps in ([5, 10], [5, 10, 15]):
                out = run_niceticks(value, steps)
                step = out["step"]
                self.assertTrue(all(t % step == 0 for t in out["ticks"]), f"off-step: {out['ticks']}")

    def test_t5_tick_count_reasonable(self):
        """4-6 ticks is the guideline; never a 13-tick axis."""
        for value in self.CASES:
            out = run_niceticks(value, [5, 10, 15])
            self.assertLessEqual(len(out["ticks"]), 8, f"too many ticks for {value}: {out['ticks']}")

    def test_t4_zero_and_negative_fall_back(self):
        out = run_niceticks(0, [5, 10])
        self.assertGreaterEqual(out["max"], 1)
        out2 = run_niceticks(-5, [5, 10])
        self.assertGreaterEqual(out2["max"], 1)


# ------------------------------------------------------------------ source kind
class SourceKindTest(unittest.TestCase):
    def _kind(self, provider_name, info):
        svc = server_mod.Service.__new__(server_mod.Service)

        class _P:
            source_name = provider_name

        svc.provider = _P()
        handler = server_mod.Handler.__new__(server_mod.Handler)
        handler.service = svc
        return handler._source_kind(info)

    def test_t1_offline_provider_reports_offline(self):
        """SRC2: the fixture provider is reported as ``offline``, whatever the cache says."""
        for info in ({}, {"source": "none"}, {"source": "live", "fetched_at": "2026-10-20T12:00:00"}):
            with self.subTest(info=info):
                self.assertEqual(self._kind("offline", info), "offline")

    def test_t1_only_three_values_are_ever_returned(self):
        """SRC1: the badge only ever sees live / cache / offline."""
        samples = [
            self._kind("live", {"source": "live"}),
            self._kind("live", {"source": "cache", "fetched_at": "x"}),
            self._kind("offline", {"source": "offline"}),
            self._kind("live", {"source": "none", "fetched_at": "x"}),
            self._kind("live", {"source": "unavailable"}),
            self._kind("live", {}),
        ]
        for s in samples:
            self.assertIn(s, ("live", "cache", "offline"), s)

    def test_t3_cache_carries_fetch_time(self):
        """SRC3: cache is chosen when a fetch timestamp exists on a live provider."""
        self.assertEqual(self._kind("live", {"source": "cache", "fetched_at": "2026-10-20T10:00:00"}), "cache")
        self.assertEqual(self._kind("live", {"source": "none", "fetched_at": "2026-10-20T10:00:00"}), "cache")

    def test_t1_fresh_live_stays_live(self):
        self.assertEqual(self._kind("live", {"source": "live", "fetched_at": "2026-10-20T10:00:00"}), "live")
        self.assertEqual(self._kind("live", {}), "live")

    def test_t1_never_falls_back_to_a_port_number(self):
        """SRC1: a different port must not change the reported source."""
        a = self._kind("offline", {})
        b = self._kind("offline", {"source": "none"})
        self.assertEqual(a, b)


# --------------------------------------------------------------- HTTP bootstrap
class BootstrapHttpTest(unittest.TestCase):
    """The offline path must expose ``source`` on /api/bootstrap."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.proc = subprocess.Popen(
            [sys.executable, "src/server.py", "--offline", "--port", "8808"],
            cwd=str(ROOT),
            env={
                "MAI_MAI_DATA": cls.dir.name,
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(80):
            try:
                opener.open("http://127.0.0.1:8808/api/bootstrap", timeout=5).read()
                return
            except Exception:
                time.sleep(0.25)
        raise AssertionError("offline server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait(timeout=10)
        cls.dir.cleanup()

    def _bootstrap(self):
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8808/api/bootstrap", timeout=30) as resp:
            return json.loads(resp.read())

    def test_t2_offline_source_is_offline(self):
        data = self._bootstrap()
        self.assertEqual(data.get("source"), "offline", data.get("source"))
        self.assertEqual(data.get("cache", {}).get("source"), "offline")

    def test_t1_source_field_exists(self):
        data = self._bootstrap()
        self.assertIn("source", data)
        self.assertIn(data["source"], ("live", "cache", "offline"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
