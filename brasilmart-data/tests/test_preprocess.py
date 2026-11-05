"""Checks for the decision boundaries and the committed demo export."""

import csv
import json
import sys
import unittest
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from preprocess import delivery_lateness, no_future_milestones, return_eligibility  # noqa: E402


def rows(name):
    with (ROOT / "demo" / name).open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


class BusinessRuleTests(unittest.TestCase):
    def test_return_day_14_inclusive_and_day_15_refused(self):
        order = {"order_status": "delivered", "order_delivered_customer_date": "2018-08-18 14:25:48"}
        self.assertEqual(return_eligibility(order, date(2018, 9, 1), 14), (True, "within_return_window"))
        order["order_delivered_customer_date"] = "2018-08-17 22:21:27"
        self.assertEqual(return_eligibility(order, date(2018, 9, 1), 14), (False, "outside_return_window"))

    def test_missing_delivery_date_does_not_invent_lateness(self):
        order = {"order_status": "delivered", "order_delivered_customer_date": "",
                 "order_estimated_delivery_date": "2018-08-01 00:00:00"}
        self.assertEqual(delivery_lateness(order, date(2018, 9, 1)), ("missing_delivery_date", None))
        self.assertEqual(return_eligibility(order, date(2018, 9, 1), 14), (False, "missing_delivery_date"))

    def test_canceled_order_has_no_overdue_days(self):
        order = {"order_status": "canceled", "order_delivered_customer_date": "",
                 "order_estimated_delivery_date": "2018-01-01 00:00:00"}
        self.assertEqual(delivery_lateness(order, date(2018, 9, 1)), ("not_applicable", None))


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.customers = rows("customers.csv")
        cls.orders = rows("orders.csv")
        cls.items = rows("order_items.csv")
        cls.payments = rows("order_payments.csv")
        cls.fixtures = json.loads((ROOT / "demo" / "fixture_manifest.json").read_text(encoding="utf-8"))["fixtures"]

    def test_foreign_keys_and_unique_keys(self):
        customers = {r["customer_unique_id"] for r in self.customers}
        orders = {r["order_id"] for r in self.orders}
        self.assertEqual(len(orders), len(self.orders))
        self.assertEqual(len(customers), len(self.customers))
        self.assertTrue(all(r["customer_unique_id"] in customers for r in self.orders))
        self.assertTrue(all(r["order_id"] in orders for r in self.items + self.payments))
        self.assertEqual(len({(r["order_id"], r["order_item_id"]) for r in self.items}), len(self.items))
        self.assertEqual(len({(r["order_id"], r["payment_sequential"]) for r in self.payments}), len(self.payments))

    def test_payment_totals_match_original_rows(self):
        totals = defaultdict(lambda: Decimal("0.00"))
        for payment in self.payments:
            totals[payment["order_id"]] += Decimal(payment["payment_value_brl"])
        for order in self.orders:
            expected = f"{totals[order['order_id']]:.2f}" if order["order_id"] in totals else ""
            self.assertEqual(order["recorded_payment_total_brl"], expected)

    def test_fixture_outcomes_and_customer_isolation(self):
        orders = {r["order_id"]: r for r in self.orders}
        primary = {f["persona"]: set(f["order_ids"]) for f in self.fixtures if f["scenario"].endswith("_all_orders")}
        self.assertFalse(primary["USER_A"] & primary["USER_B"])
        for persona in ("USER_A", "USER_B"):
            fixture = next(f for f in self.fixtures if f["scenario"] == persona.lower() + "_all_orders")
            self.assertEqual(len(primary[persona]), fixture["expected_order_count"])
            self.assertTrue(all(orders[oid]["customer_unique_id"] == fixture["customer_unique_id"] for oid in primary[persona]))
        for fixture in self.fixtures:
            if "order_id" not in fixture:
                continue
            order = orders[fixture["order_id"]]
            self.assertEqual(order["customer_unique_id"], fixture["customer_unique_id"])
            self.assertTrue(no_future_milestones(order, date(2018, 9, 1)))
            self.assertEqual(return_eligibility(order, date(2018, 9, 1), 14)[0], fixture["expected_return_eligible"])
            if "expected_late_kind" in fixture:
                self.assertEqual(delivery_lateness(order, date(2018, 9, 1)),
                                 (fixture["expected_late_kind"], fixture["expected_late_days"]))


if __name__ == "__main__":
    unittest.main()
