"""Prepare traceable Olist tables for the BrasilMart training demo.

Python 3.11+, standard library only. Raw Olist CSV files are never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


SOURCE_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "products": "olist_products_dataset.csv",
    "categories": "product_category_name_translation.csv",
}
VALID_STATUSES = {
    "created", "approved", "invoiced", "processing", "shipped",
    "delivered", "canceled", "unavailable",
}
OPEN_STATUSES = {"created", "approved", "invoiced", "processing", "shipped"}
PRIMARY_PERSONAS = {
    "USER_A": "8d50f5eadf50201ccdcedfb9e2ac8455",
    "USER_B": "dc813062e0fc23409cd255f7f53c7074",
}
TABLE_FIELDS = {
    "customers": ["customer_unique_id", "customer_city", "customer_state"],
    "orders": [
        "order_id", "customer_id", "customer_unique_id", "order_status",
        "order_purchase_timestamp", "order_approved_at",
        "order_delivered_carrier_date", "order_delivered_customer_date",
        "order_estimated_delivery_date", "recorded_payment_total_brl",
        "payment_row_count", "quality_flags", "import_batch_id",
    ],
    "order_items": [
        "order_id", "order_item_id", "product_id", "product_category_pt",
        "product_category_en", "shipping_limit_date", "price_brl",
        "freight_value_brl", "import_batch_id",
    ],
    "order_payments": [
        "order_id", "payment_sequential", "payment_type",
        "payment_installments", "payment_value_brl", "import_batch_id",
    ],
    "import_batches": ["import_batch_id", "business_date", "mode", "source_manifest_sha256"],
    "demo_config": ["setting_name", "setting_value"],
    "customer_identity_template": [
        "persona", "customer_unique_id", "tenant_id_FILL_LOCALLY",
        "authenticated_user_id_FILL_LOCALLY", "enabled",
    ],
    "return_requests_template": [
        "request_id", "tenant_id", "authenticated_user_id", "order_id", "order_item_id",
        "reason", "requested_date", "policy_version", "decision",
        "refusal_reason", "state", "idempotency_key", "created_at",
    ],
    "complaints_template": [
        "complaint_id", "tenant_id", "authenticated_user_id", "order_id", "category",
        "description", "priority", "status", "created_at",
    ],
    "support_cases_template": [
        "support_case_id", "tenant_id", "authenticated_user_id", "order_id",
        "return_request_id", "complaint_id", "conversation_id", "issue_summary",
        "handoff_reason", "recorded_order_status", "estimated_delivery_date",
        "actual_delivery_date", "queue_state", "notification_state", "created_at",
    ],
    "exceptions": ["source_file", "source_key", "issue_code", "disposition"],
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_date(value: str, field: str) -> date | None:
    if not value:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date()
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def money(value: str, field: str) -> str:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
        return f"{amount:.2f}"
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def delivery_lateness(order: dict, business_date: date) -> tuple[str, int | None]:
    """Return a kind and calendar-day count, never inventing a missing date."""
    status = order["order_status"]
    estimated = parse_date(order["order_estimated_delivery_date"], "estimate")
    actual = parse_date(order["order_delivered_customer_date"], "delivery")
    if status in {"canceled", "unavailable"}:
        return "not_applicable", None
    if estimated is None:
        return "missing_estimate", None
    if status == "delivered":
        if actual is None:
            return "missing_delivery_date", None
        return "delivered_late" if actual > estimated else "delivered_on_time", max(0, (actual - estimated).days)
    if status in OPEN_STATUSES:
        return "open_overdue" if business_date > estimated else "open_not_overdue", max(0, (business_date - estimated).days)
    return "unknown_status", None


def return_eligibility(order: dict, business_date: date, window_days: int) -> tuple[bool, str]:
    if order["order_status"] != "delivered":
        return False, "not_delivered"
    actual = parse_date(order["order_delivered_customer_date"], "delivery")
    if actual is None:
        return False, "missing_delivery_date"
    age = (business_date - actual).days
    if age < 0:
        return False, "future_delivery"
    if age > window_days:
        return False, "outside_return_window"
    return True, "within_return_window"


def no_future_milestones(order: dict, business_date: date) -> bool:
    return all(
        not order[field] or parse_date(order[field], field) <= business_date
        for field in (
            "order_purchase_timestamp", "order_approved_at",
            "order_delivered_carrier_date", "order_delivered_customer_date",
        )
    )


def select_demo_fixtures(
    orders: dict[str, dict], items_by_order: dict[str, list[dict]],
    payments_by_order: dict[str, list[dict]], business_date: date, window_days: int,
) -> tuple[set[str], list[dict], dict[str, str]]:
    """Pick stable source-backed scenarios and all orders of two repeat buyers."""
    by_customer: dict[str, list[dict]] = defaultdict(list)
    for order in orders.values():
        if no_future_milestones(order, business_date):
            by_customer[order["customer_unique_id"]].append(order)

    selected: set[str] = set()
    fixtures: list[dict] = []
    personas = dict(PRIMARY_PERSONAS)
    for persona, customer_id in PRIMARY_PERSONAS.items():
        owned = by_customer.get(customer_id, [])
        if len(owned) < 2:
            raise RuntimeError(f"{persona} has fewer than two usable orders")
        selected.update(order["order_id"] for order in owned)
        fixtures.append({
            "scenario": persona.lower() + "_all_orders",
            "persona": persona,
            "customer_unique_id": customer_id,
            "order_ids": sorted(order["order_id"] for order in owned),
            "expected_order_count": len(owned),
        })
        eligible_orders = sorted(
            (o for o in owned if items_by_order.get(o["order_id"])
             and return_eligibility(o, business_date, window_days)[0]),
            key=lambda o: (o["order_delivered_customer_date"], o["order_id"]),
            reverse=True,
        )
        if not eligible_orders:
            raise RuntimeError(f"{persona} has no usable return fixture")
        example = eligible_orders[0]
        fixtures.append({
            "scenario": persona.lower() + "_return_eligible",
            "persona": persona,
            "customer_unique_id": customer_id,
            "order_id": example["order_id"],
            "order_item_id": items_by_order[example["order_id"]][0]["order_item_id"],
            "recorded_status": example["order_status"],
            "actual_delivery_date": example["order_delivered_customer_date"],
            "expected_return_eligible": True,
        })

    # Prefer a single-item, paid order so each policy boundary is easy to demonstrate.
    candidates = sorted(
        (o for o in orders.values() if no_future_milestones(o, business_date)
         and len(items_by_order.get(o["order_id"], [])) == 1
         and payments_by_order.get(o["order_id"])),
        key=lambda o: o["order_id"],
    )
    def age(o: dict) -> int | None:
        actual = parse_date(o["order_delivered_customer_date"], "delivery")
        return (business_date - actual).days if actual else None

    conditions = {
        "return_day_14": lambda o: o["order_status"] == "delivered" and age(o) == window_days,
        "return_day_15": lambda o: o["order_status"] == "delivered" and age(o) == window_days + 1,
        "open_overdue": lambda o: delivery_lateness(o, business_date)[0] == "open_overdue",
        "delivered_late": lambda o: delivery_lateness(o, business_date)[0] == "delivered_late",
        "delivered_on_time": lambda o: delivery_lateness(o, business_date)[0] == "delivered_on_time",
        "canceled_or_unavailable": lambda o: o["order_status"] in {"canceled", "unavailable"},
        "missing_delivery_date": lambda o: o["order_status"] == "delivered" and not o["order_delivered_customer_date"],
    }
    for scenario, condition in conditions.items():
        match = next((o for o in candidates if condition(o) and o["order_id"] not in selected), None)
        if match is None:
            raise RuntimeError(f"No source-backed fixture for {scenario}")
        selected.add(match["order_id"])
        persona = scenario.upper()
        personas[persona] = match["customer_unique_id"]
        late_kind, late_days = delivery_lateness(match, business_date)
        eligible, return_reason = return_eligibility(match, business_date, window_days)
        fixtures.append({
            "scenario": scenario,
            "persona": persona,
            "customer_unique_id": match["customer_unique_id"],
            "order_id": match["order_id"],
            "order_item_id": match["_fixture_item_id"],
            "recorded_status": match["order_status"],
            "estimated_delivery_date": match["order_estimated_delivery_date"],
            "actual_delivery_date": match["order_delivered_customer_date"],
            "expected_late_kind": late_kind,
            "expected_late_days": late_days,
            "expected_return_eligible": eligible,
            "expected_return_reason": return_reason,
        })

    return selected, fixtures, personas


def prepare(source_dir: Path, output_dir: Path, mode: str, business_date: date, window_days: int) -> dict:
    if output_dir.resolve() == source_dir.resolve():
        raise ValueError("Output directory must differ from source directory")
    missing = [name for name in SOURCE_FILES.values() if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing source CSVs: " + ", ".join(missing))
    source_hashes = {name: sha256_file(source_dir / filename) for name, filename in SOURCE_FILES.items()}
    raw = {name: read_csv(source_dir / filename) for name, filename in SOURCE_FILES.items()}
    manifest_digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    batch_id = hashlib.sha256(
        f"{manifest_digest}|{mode}|{business_date.isoformat()}|{window_days}".encode()
    ).hexdigest()[:16]
    exceptions: list[dict] = []
    def flag(source: str, key: str, code: str, disposition: str) -> None:
        exceptions.append({"source_file": SOURCE_FILES[source], "source_key": key,
                           "issue_code": code, "disposition": disposition})

    customer_rows: dict[str, dict] = {}
    for row in raw["customers"]:
        key = row["customer_id"]
        if not key or not row["customer_unique_id"] or key in customer_rows:
            flag("customers", key, "missing_or_duplicate_customer_key", "excluded")
            continue
        customer_rows[key] = row

    orders: dict[str, dict] = {}
    for row in raw["orders"]:
        key = row["order_id"]
        if not key or key in orders:
            flag("orders", key, "missing_or_duplicate_order_id", "excluded")
            continue
        customer = customer_rows.get(row["customer_id"])
        if not customer:
            flag("orders", key, "missing_customer_join", "excluded")
            continue
        if row["order_status"] not in VALID_STATUSES:
            flag("orders", key, "unknown_status", "excluded")
            continue
        try:
            if not row["order_purchase_timestamp"] or not row["order_estimated_delivery_date"]:
                raise ValueError("Missing required order date")
            for field in (
                "order_purchase_timestamp", "order_approved_at",
                "order_delivered_carrier_date", "order_delivered_customer_date",
                "order_estimated_delivery_date",
            ):
                parse_date(row[field], field)
        except ValueError:
            flag("orders", key, "invalid_required_or_present_date", "excluded")
            continue
        row = dict(row, customer_unique_id=customer["customer_unique_id"])
        quality = []
        if row["order_status"] == "delivered" and not row["order_delivered_customer_date"]:
            quality.append("missing_delivery_date")
            flag("orders", key, "delivered_without_actual_date", "loaded_flagged")
        row["quality_flags"] = ";".join(quality)
        orders[key] = row

    products = {r["product_id"]: r for r in raw["products"] if r["product_id"]}
    categories = {r["product_category_name"]: r["product_category_name_english"]
                  for r in raw["categories"] if r["product_category_name"]}
    items: dict[tuple[str, str], dict] = {}
    for row in raw["items"]:
        key = (row["order_id"], row["order_item_id"])
        if not all(key) or key in items:
            flag("items", ":".join(key), "missing_or_duplicate_item_key", "excluded")
            continue
        if row["order_id"] not in orders:
            flag("items", ":".join(key), "missing_order_join", "excluded")
            continue
        try:
            row["price_brl"] = money(row["price"], "item price")
            row["freight_value_brl"] = money(row["freight_value"], "freight")
            parse_date(row["shipping_limit_date"], "shipping limit")
        except ValueError:
            flag("items", ":".join(key), "invalid_money_or_date", "excluded")
            continue
        product = products.get(row["product_id"], {})
        category_pt = product.get("product_category_name", "")
        row["product_category_pt"] = category_pt
        row["product_category_en"] = categories.get(category_pt, "")
        row["import_batch_id"] = batch_id
        items[key] = row

    payments: dict[tuple[str, str], dict] = {}
    for row in raw["payments"]:
        key = (row["order_id"], row["payment_sequential"])
        if not all(key) or key in payments:
            flag("payments", ":".join(key), "missing_or_duplicate_payment_key", "excluded")
            continue
        if row["order_id"] not in orders:
            flag("payments", ":".join(key), "missing_order_join", "excluded")
            continue
        try:
            row["payment_value_brl"] = money(row["payment_value"], "payment value")
            if not row["payment_installments"].isdigit():
                raise ValueError("Invalid installments")
        except ValueError:
            flag("payments", ":".join(key), "invalid_money_or_installments", "excluded")
            continue
        row["import_batch_id"] = batch_id
        payments[key] = row

    items_by_order: dict[str, list[dict]] = defaultdict(list)
    payments_by_order: dict[str, list[dict]] = defaultdict(list)
    for row in items.values():
        items_by_order[row["order_id"]].append(row)
    for row in payments.values():
        payments_by_order[row["order_id"]].append(row)
    for order in orders.values():
        order_id = order["order_id"]
        if not items_by_order[order_id]:
            flag("orders", order_id, "no_valid_items", "loaded_flagged")
            order["quality_flags"] += ";no_valid_items" if order["quality_flags"] else "no_valid_items"
        if not payments_by_order[order_id]:
            flag("orders", order_id, "no_valid_payments", "loaded_flagged")
            order["quality_flags"] += ";no_valid_payments" if order["quality_flags"] else "no_valid_payments"
        order["recorded_payment_total_brl"] = (
            f"{sum((Decimal(p['payment_value_brl']) for p in payments_by_order[order_id]), Decimal('0.00')):.2f}"
            if payments_by_order[order_id] else ""
        )
        order["payment_row_count"] = len(payments_by_order[order_id])
        order["import_batch_id"] = batch_id

    fixtures: list[dict] = []
    personas: dict[str, str] = {}
    if mode == "demo":
        # Set a traceable item ID used by the fixture manifest.
        for order in orders.values():
            order["_fixture_item_id"] = items_by_order[order["order_id"]][0]["order_item_id"] if items_by_order[order["order_id"]] else ""
        selected, fixtures, personas = select_demo_fixtures(
            orders, items_by_order, payments_by_order, business_date, window_days
        )
    else:
        selected = set(orders)

    customer_ids = {orders[order_id]["customer_unique_id"] for order_id in selected}
    customer_details: dict[str, dict] = {}
    for row in customer_rows.values():
        uid = row["customer_unique_id"]
        if uid in customer_ids and uid not in customer_details:
            customer_details[uid] = {
                "customer_unique_id": uid,
                "customer_city": row["customer_city"],
                "customer_state": row["customer_state"],
            }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_rows = {
        "customers": sorted(customer_details.values(), key=lambda r: r["customer_unique_id"]),
        "orders": [orders[k] for k in sorted(selected)],
        "order_items": [v for k, v in sorted(items.items()) if k[0] in selected],
        "order_payments": [v for k, v in sorted(payments.items()) if k[0] in selected],
        "import_batches": [{"import_batch_id": batch_id, "business_date": business_date.isoformat(),
                            "mode": mode, "source_manifest_sha256": manifest_digest}],
        "demo_config": [
            {"setting_name": "business_date", "setting_value": business_date.isoformat()},
            {"setting_name": "return_window_days", "setting_value": str(window_days)},
            {"setting_name": "policy_version", "setting_value": "demo-v1"},
            {"setting_name": "source_notice", "setting_value": "Historical Olist training data; not live order tracking"},
        ],
        "customer_identity_template": [
            {"persona": persona, "customer_unique_id": uid,
             "tenant_id_FILL_LOCALLY": "", "authenticated_user_id_FILL_LOCALLY": "", "enabled": "false"}
            for persona, uid in sorted(personas.items())
        ],
        "return_requests_template": [],
        "complaints_template": [],
        "support_cases_template": [],
        "exceptions": sorted(exceptions, key=lambda r: (r["source_file"], r["source_key"], r["issue_code"])),
    }
    for table, rows in output_rows.items():
        write_csv(output_dir / f"{table}.csv", TABLE_FIELDS[table], rows)

    # Reconciliation: this must hold for both demo and full output.
    out_order_ids = {r["order_id"] for r in output_rows["orders"]}
    assert len(out_order_ids) == len(output_rows["orders"])
    assert all(r["order_id"] in out_order_ids for r in output_rows["order_items"])
    assert all(r["order_id"] in out_order_ids for r in output_rows["order_payments"])
    assert all(r["customer_unique_id"] in customer_details for r in output_rows["orders"])
    report = {
        "mode": mode,
        "business_date": business_date.isoformat(),
        "return_window_days": window_days,
        "import_batch_id": batch_id,
        "source_files": {
            SOURCE_FILES[name]: {"rows": len(raw[name]), "sha256": digest}
            for name, digest in source_hashes.items()
        },
        "output_rows": {name: len(rows) for name, rows in output_rows.items()},
        "source_order_status_counts": dict(sorted(Counter(r["order_status"] for r in orders.values()).items())),
        "exception_counts": dict(sorted(Counter(r["issue_code"] for r in exceptions).items())),
        "notes": [
            "Source order status is the export's recorded value, not a reconstructed status at business_date.",
            "Money is BRL decimal. Empty payment total means no valid payment row; no substitute total was inferred.",
            "A flagged delivered order with no actual date is readable but cannot receive an automated lateness or return decision.",
            "Identity template has no real Teams identity and is not import-ready until provisioned in the training tenant.",
        ],
    }
    (output_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if mode == "demo":
        (output_dir / "fixture_manifest.json").write_text(
            json.dumps({"business_date": business_date.isoformat(), "fixtures": fixtures}, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--mode", choices=("demo", "full"), default="demo")
    parser.add_argument("--business-date", type=date.fromisoformat, default=date(2018, 9, 1))
    parser.add_argument("--return-window-days", type=int, default=14)
    args = parser.parse_args()
    if args.return_window_days < 0:
        parser.error("--return-window-days must be nonnegative")
    report = prepare(args.source_dir, args.output_dir, args.mode, args.business_date, args.return_window_days)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()),
                      "import_batch_id": report["import_batch_id"],
                      "output_rows": report["output_rows"],
                      "exception_counts": report["exception_counts"]}, indent=2))


if __name__ == "__main__":
    main()
