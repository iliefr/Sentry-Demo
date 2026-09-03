"""
scripts/inventory_sync.py

Reconciles inventory levels between two simulated sources:
a local warehouse DB and a third-party logistics (3PL) provider.
Detects discrepancies, flags critical low-stock situations,
and raises exceptions on sync conflicts.
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import random
import time
import logging
import sentry_sdk
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PRODUCTS = [
    "SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005",
    "SKU-006", "SKU-007", "SKU-008", "SKU-009", "SKU-010",
]

LOW_STOCK_THRESHOLD = 5
CRITICAL_STOCK_THRESHOLD = 2


class InventoryConflictError(Exception):
    pass

class SyncConnectionError(Exception):
    pass


def fetch_warehouse_stock() -> dict[str, int]:
    """Pull stock levels from the local warehouse system."""
    log.info("Fetching stock from warehouse system")
    time.sleep(random.uniform(0.1, 0.3))

    if random.random() < 0.05:
        raise SyncConnectionError("Warehouse DB connection timed out after 10s")

    return {sku: random.randint(0, 50) for sku in PRODUCTS}


def fetch_3pl_stock() -> dict[str, int]:
    """Pull stock levels from the 3PL provider API."""
    log.info("Fetching stock from 3PL provider")
    time.sleep(random.uniform(0.15, 0.4))

    if random.random() < 0.05:
        raise SyncConnectionError("3PL API returned 503 — provider maintenance window")

    stock = {}
    for sku in PRODUCTS:
        # 3PL counts drift ±5 units vs warehouse
        stock[sku] = random.randint(0, 50)
    return stock


def reconcile(warehouse: dict, pl3: dict) -> dict:
    discrepancies = {}
    for sku in PRODUCTS:
        w = warehouse.get(sku, 0)
        p = pl3.get(sku, 0)
        delta = abs(w - p)

        if delta > 10:
            discrepancies[sku] = {"warehouse": w, "3pl": p, "delta": delta}

    return discrepancies


def apply_corrections(discrepancies: dict) -> None:
    for sku, diff in discrepancies.items():
        sentry_sdk.add_breadcrumb(
            category="inventory",
            message=f"Correcting {sku}: warehouse={diff['warehouse']} 3pl={diff['3pl']}",
            level="warning",
        )
        log.warning("Discrepancy on %s: Δ%d units (warehouse=%d, 3pl=%d)",
                    sku, diff["delta"], diff["warehouse"], diff["3pl"])

        # Large delta = conflict that requires manual review
        if diff["delta"] > 20:
            raise InventoryConflictError(
                f"{sku} has an unresolvable discrepancy of {diff['delta']} units — manual review required"
            )

    log.info("Applied %d corrections", len(discrepancies))


def check_low_stock(stock: dict) -> None:
    for sku, qty in stock.items():
        if qty <= CRITICAL_STOCK_THRESHOLD:
            sentry_sdk.capture_message(f"CRITICAL low stock: {sku} has only {qty} units", level="error")
            log.error("CRITICAL: %s has %d units remaining", sku, qty)
        elif qty <= LOW_STOCK_THRESHOLD:
            sentry_sdk.capture_message(f"Low stock warning: {sku} has {qty} units", level="warning")
            log.warning("LOW STOCK: %s has %d units remaining", sku, qty)


def run_inventory_sync() -> None:
    with sentry_sdk.start_transaction(op="inventory.sync", name="Inventory Reconciliation"):
        sentry_sdk.set_tag("integration", "warehouse+3pl")

        warehouse_stock = None
        pl3_stock = None

        with sentry_sdk.start_span(op="inventory.fetch", description="Fetch warehouse stock"):
            try:
                warehouse_stock = fetch_warehouse_stock()
            except SyncConnectionError as exc:
                sentry_sdk.capture_exception(exc)
                log.error("Warehouse fetch failed: %s", exc)
                return

        with sentry_sdk.start_span(op="inventory.fetch", description="Fetch 3PL stock"):
            try:
                pl3_stock = fetch_3pl_stock()
            except SyncConnectionError as exc:
                sentry_sdk.capture_exception(exc)
                log.error("3PL fetch failed: %s", exc)
                return

        with sentry_sdk.start_span(op="inventory.reconcile", description="Reconcile sources"):
            discrepancies = reconcile(warehouse_stock, pl3_stock)
            log.info("Found %d discrepancies", len(discrepancies))

        with sentry_sdk.start_span(op="inventory.correct", description="Apply corrections"):
            try:
                apply_corrections(discrepancies)
            except InventoryConflictError as exc:
                sentry_sdk.capture_exception(exc)
                log.error("Conflict: %s", exc)

        with sentry_sdk.start_span(op="inventory.alerts", description="Low stock check"):
            check_low_stock(warehouse_stock)

        log.info("Inventory sync complete")


if __name__ == "__main__":
    init_sentry("inventory-sync")

    log.info("Running inventory sync — 3 cycles")
    for cycle in range(3):
        log.info("--- Sync cycle %d ---", cycle + 1)
        run_inventory_sync()
        time.sleep(random.uniform(0.3, 0.7))
