"""
scripts/order_processor.py

Simulates an e-commerce order pipeline: validation → payment → fulfillment.
Sentry captures per-order transactions, breadcrumbs at each stage, exceptions,
and custom metrics (order values, failure counts, fulfillment latency).
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import sentry_sdk
from sentry_sdk import metrics
from faker import Faker
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
fake = Faker()

PRODUCT_CATALOG = {
    "SKU-001": {"name": "Wireless Headphones", "price": 89.99,  "stock": 12},
    "SKU-002": {"name": "Mechanical Keyboard",  "price": 149.99, "stock": 5},
    "SKU-003": {"name": "USB-C Hub",            "price": 39.99,  "stock": 0},
    "SKU-004": {"name": "Monitor Stand",        "price": 59.99,  "stock": 20},
    "SKU-005": {"name": "Webcam Pro",           "price": 119.99, "stock": 8},
}


class OrderValidationError(Exception):
    pass

class PaymentDeclinedError(Exception):
    pass

class FulfillmentError(Exception):
    pass


def validate_order(order: dict) -> None:
    sentry_sdk.add_breadcrumb(category="order", message=f"Validating order {order['id']}", level="info")
    log.info("Validating order %s", order["id"])
    time.sleep(random.uniform(0.05, 0.15))

    for item in order["items"]:
        sku = item["sku"]
        if sku not in PRODUCT_CATALOG:
            raise OrderValidationError(f"Unknown SKU: {sku}")
        product = PRODUCT_CATALOG[sku]
        if product["stock"] < item["qty"]:
            raise OrderValidationError(
                f"Insufficient stock for {product['name']}: requested {item['qty']}, available {product['stock']}"
            )


def charge_payment(order: dict) -> str:
    sentry_sdk.add_breadcrumb(category="payment", message=f"Charging {order['total']:.2f} EUR", level="info")
    log.info("Charging payment for order %s (%.2f EUR)", order["id"], order["total"])
    time.sleep(random.uniform(0.1, 0.3))

    if random.random() < 0.15:
        metrics.count("order.payment.declined", 1, attributes={"currency": "EUR"})
        raise PaymentDeclinedError(f"Card ending {random.randint(1000,9999)} declined by issuer")

    transaction_id = fake.uuid4()
    metrics.count("order.payment.success", 1, attributes={"currency": "EUR"})
    metrics.distribution("order.value.eur", order["total"], attributes={"currency": "EUR"})
    log.info("Payment authorised — txn %s", transaction_id)
    return transaction_id


def fulfill_order(order: dict, txn_id: str) -> None:
    sentry_sdk.add_breadcrumb(category="fulfillment", message="Creating shipment", level="info")
    log.info("Creating shipment for order %s", order["id"])
    start = time.monotonic()
    time.sleep(random.uniform(0.1, 0.25))

    if random.random() < 0.1:
        metrics.count("order.fulfillment.failed", 1)
        raise FulfillmentError("Warehouse API timeout — could not create shipping label")

    duration_ms = (time.monotonic() - start) * 1000
    metrics.distribution("order.fulfillment.duration_ms", duration_ms)
    metrics.count("order.fulfillment.success", 1)

    tracking = f"NL{random.randint(10000000, 99999999)}TK"
    log.info("Shipment created — tracking %s", tracking)


def build_random_order() -> dict:
    skus = random.sample(list(PRODUCT_CATALOG.keys()), k=random.randint(1, 3))
    items = [{"sku": s, "qty": random.randint(1, 2)} for s in skus]
    total = sum(PRODUCT_CATALOG[i["sku"]]["price"] * i["qty"] for i in items)
    return {
        "id": f"ORD-{random.randint(10000, 99999)}",
        "customer": fake.name(),
        "email": fake.email(),
        "items": items,
        "total": total,
    }


def process_order(order: dict) -> None:
    with sentry_sdk.start_transaction(op="order.process", name=f"Process order {order['id']}") as tx:
        sentry_sdk.set_user({"email": order["email"], "username": order["customer"]})
        sentry_sdk.set_tag("order_id", order["id"])
        tx.set_data("order_total", order["total"])
        metrics.gauge("order.cart.items", len(order["items"]))

        try:
            with sentry_sdk.start_span(op="order.validate"):
                validate_order(order)
            with sentry_sdk.start_span(op="payment.charge"):
                txn_id = charge_payment(order)
            with sentry_sdk.start_span(op="fulfillment.ship"):
                fulfill_order(order, txn_id)
            metrics.count("order.completed", 1)
            log.info("Order %s completed successfully", order["id"])

        except (OrderValidationError, PaymentDeclinedError, FulfillmentError) as exc:
            sentry_sdk.capture_exception(exc)
            metrics.count("order.failed", 1, attributes={"reason": type(exc).__name__})
            log.error("Order %s failed: %s", order["id"], exc)

        except Exception as exc:
            log.critical("Unexpected failure on order %s: %s", order["id"], exc)
            raise


if __name__ == "__main__":
    init_sentry("order-processor")
    sentry_sdk.profiler.start_profiler()

    log.info("Starting order processor — running 10 orders")
    for _ in range(10):
        order = build_random_order()
        log.info("Processing %s for %s (%.2f EUR)", order["id"], order["customer"], order["total"])
        process_order(order)
        time.sleep(random.uniform(0.2, 0.5))

    sentry_sdk.profiler.stop_profiler()
    log.info("Order processor run complete")
