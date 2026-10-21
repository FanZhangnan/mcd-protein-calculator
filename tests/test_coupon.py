"""Post-coupon quote: only POS-confirmed discounts, never guessed.

Rules under test
----------------
CP1  an applicable coupon lowers the price; the result is the POS amount
CP2  coupon ids/codes never appear in the API payload
CP3  a coupon that does not apply, or a failed coupon quote, yields status=none
CP4  a stale signature is rejected with 409
CP5  the service never calls claim/order/mall tools

Run: python3 tests/test_coupon.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import planner as planner_mod  # noqa: E402
from service import OfflineProvider, Service  # noqa: E402

FIXTURES = ROOT / "data/fixtures"


def make_service(coupon_payload=None) -> Service:
    tmp = Path(tempfile.mkdtemp(prefix="mai-coupon-"))
    provider = OfflineProvider(FIXTURES)
    if coupon_payload is not None:
        provider.coupons = lambda *a, **k: coupon_payload  # type: ignore[assignment]
    service = Service(provider, base=tmp)
    service.refresh()
    return service


def plan_for(service: Service, code: str = "1406") -> planner_mod.MealPlan:
    pools = service.pools("lunch")
    item = next(i for i in pools["main"] if str(i.code) == code)
    items = [item.copy()]
    totals, unknown = planner_mod.sum_nutrition(items)
    return planner_mod.MealPlan(meal="lunch", items=items, nutrition=totals,
                                unknown=unknown,
                                price_ref=planner_mod.sum_reference_cost(items) or 0.0)


class CouponQuoteTest(unittest.TestCase):
    def test_discount_uses_pos_amount_and_hides_ids(self) -> None:
        service = make_service()
        plan = plan_for(service)
        targets = planner_mod.Targets(protein_min=20, budget=30)
        service.set_working([plan], targets)
        payload, code = service.quote_with_coupon("lunch", service.working_signature())
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["baseline_amount"], 23.5)
        self.assertEqual(payload["coupon"]["amount"], 11.9)
        self.assertEqual(payload["coupon"]["discount"], 11.6)
        self.assertIn("测试券", payload["coupon"]["title"])
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("OFFLINE-TEST", blob)
        self.assertNotIn("couponId", blob)

    def test_no_matching_coupon_is_none(self) -> None:
        other = {"success": True, "code": 200, "data": [{
            "title": "不适用券", "couponId": "X", "couponCode": "Y",
            "tradeDateTime": "", "products": [{"productCode": "999999"}],
            "priceFen": 100}]}
        service = make_service(other)
        plan = plan_for(service)
        service.set_working([plan], planner_mod.Targets(protein_min=20, budget=30))
        payload, code = service.quote_with_coupon("lunch", service.working_signature())
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "none")
        self.assertIsNone(payload["coupon"])

    def test_failed_coupon_quote_is_none(self) -> None:
        service = make_service()
        plan = plan_for(service)
        service.set_working([plan], planner_mod.Targets(protein_min=20, budget=30))
        os.environ["MAI_MAI_QUOTE_FAIL"] = "1"
        try:
            payload, code = service.quote_with_coupon("lunch", service.working_signature())
        finally:
            os.environ.pop("MAI_MAI_QUOTE_FAIL", None)
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "none")
        self.assertEqual(payload["quote_status"], "failed")

    def test_stale_signature_rejected(self) -> None:
        service = make_service()
        plan = plan_for(service)
        service.set_working([plan], planner_mod.Targets(protein_min=20, budget=30))
        payload, code = service.quote_with_coupon("lunch", "deadbeef")
        self.assertEqual(code, 409)
        self.assertEqual(payload["kind"], "stale_signature")

    def test_no_claim_or_order_tools_in_source(self) -> None:
        forbidden = ["auto-bind-coupons", "create-order", "cancel-order", "draw-lottery",
                     "mall-create-order", "party-order-create", "delivery-create-address",
                     "query-my-account", "mall-points-products"]
        for path in (ROOT / "src").glob("*.py"):
            text = path.read_text("utf-8")
            for name in forbidden:
                self.assertNotIn(name, text, f"{path.name} references {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
