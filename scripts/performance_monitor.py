"""
scripts/performance_monitor.py

Demonstrates Sentry Performance Monitoring with custom metrics.
Instruments a checkout service with transactions, spans, N+1 detection,
and emits latency distributions and throughput counters to Sentry Metrics.
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import statistics
import sentry_sdk
from sentry_sdk import metrics
from faker import Faker
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
fake = Faker()

SLOW_QUERY_THRESHOLD_MS = 200
VERY_SLOW_QUERY_THRESHOLD_MS = 500
P95_ALERT_THRESHOLD_MS = 1000


class SlowQueryError(Exception):
    pass


def simulate_db_query(query: str, base_ms: float, n_plus_one: bool = False) -> float:
    multiplier = random.choice([1, 1, 1, 1, 1, 1, 2, 3, 8])
    duration_ms = base_ms * multiplier * random.uniform(0.8, 1.2)

    if n_plus_one:
        duration_ms *= random.randint(8, 15)
        sentry_sdk.add_breadcrumb(category="db", message=f"N+1 detected: {query}", level="warning")

    time.sleep(duration_ms / 1000)
    metrics.distribution("db.query.duration_ms", duration_ms, attributes={"slow": str(duration_ms > SLOW_QUERY_THRESHOLD_MS)})

    if duration_ms > VERY_SLOW_QUERY_THRESHOLD_MS:
        metrics.count("db.query.very_slow", 1)
        sentry_sdk.capture_message(f"Very slow query: {query} took {duration_ms:.0f}ms", level="warning")
    return duration_ms


def simulate_cache_lookup(key: str) -> tuple[bool, float]:
    latency_ms = random.uniform(1, 8)
    hit = random.random() < 0.70
    time.sleep(latency_ms / 1000)
    metrics.count("cache.lookup", 1, attributes={"result": "hit" if hit else "miss"})
    return hit, latency_ms


def simulate_external_call(service: str, base_ms: float) -> float:
    duration_ms = base_ms * random.uniform(0.6, 2.5)
    time.sleep(duration_ms / 1000)
    metrics.distribution("http.client.duration_ms", duration_ms, attributes={"service": service})
    if random.random() < 0.05:
        metrics.count("http.client.timeout", 1, attributes={"service": service})
        raise ConnectionError(f"Downstream service '{service}' timed out after {duration_ms:.0f}ms")
    return duration_ms


def endpoint_checkout(user_id: str) -> dict:
    with sentry_sdk.start_transaction(op="http.server", name="POST /api/checkout") as tx:
        sentry_sdk.set_user({"id": user_id})
        durations = []

        with sentry_sdk.start_span(op="cache.get", description="GET cart:{user_id}"):
            hit, ms = simulate_cache_lookup(f"cart:{user_id}")
            durations.append(ms)

        if not hit:
            with sentry_sdk.start_span(op="db.query", description="SELECT * FROM carts WHERE user_id = ?"):
                ms = simulate_db_query("SELECT cart items", base_ms=45)
                durations.append(ms)

        cart_size = random.randint(1, 6)
        with sentry_sdk.start_span(op="db.query", description=f"SELECT product (×{cart_size}) — N+1"):
            for i in range(cart_size):
                ms = simulate_db_query(
                    f"SELECT * FROM products WHERE id = {random.randint(1,500)}",
                    base_ms=18, n_plus_one=(cart_size > 3)
                )
                durations.append(ms)

        with sentry_sdk.start_span(op="http.client", description="POST payment-service/charge"):
            ms = simulate_external_call("payment-service", base_ms=180)
            durations.append(ms)

        with sentry_sdk.start_span(op="db.query", description="INSERT INTO orders"):
            ms = simulate_db_query("INSERT order", base_ms=30)
            durations.append(ms)

        with sentry_sdk.start_span(op="http.client", description="POST fulfillment-service/dispatch"):
            ms = simulate_external_call("fulfillment-service", base_ms=90)
            durations.append(ms)

        total_ms = sum(durations)
        metrics.distribution("endpoint.checkout.duration_ms", total_ms)
        metrics.gauge("endpoint.checkout.cart_size", cart_size)
        tx.set_data("total_ms", round(total_ms))
        return {"user_id": user_id, "total_ms": total_ms, "cart_size": cart_size}


def endpoint_search(query: str) -> dict:
    with sentry_sdk.start_transaction(op="http.server", name="GET /api/search"):
        durations = []
        with sentry_sdk.start_span(op="cache.get"):
            hit, ms = simulate_cache_lookup(f"search:{query}")
            durations.append(ms)
        if not hit:
            with sentry_sdk.start_span(op="db.query", description="SELECT products LIKE ?"):
                ms = simulate_db_query("Full-text search query", base_ms=120)
                durations.append(ms)
            with sentry_sdk.start_span(op="db.query", description="SELECT category filters"):
                ms = simulate_db_query("Facet aggregation", base_ms=60)
                durations.append(ms)

        total_ms = sum(durations)
        metrics.distribution("endpoint.search.duration_ms", total_ms)
        return {"query": query, "total_ms": total_ms}


def endpoint_user_profile(user_id: str) -> dict:
    with sentry_sdk.start_transaction(op="http.server", name="GET /api/users/{id}"):
        with sentry_sdk.start_span(op="cache.get"):
            hit, ms = simulate_cache_lookup(f"user:{user_id}")
        if not hit:
            with sentry_sdk.start_span(op="db.query", description="SELECT * FROM users WHERE id = ?"):
                simulate_db_query("User profile lookup", base_ms=25)
            with sentry_sdk.start_span(op="db.query", description="SELECT * FROM orders WHERE user_id = ?"):
                simulate_db_query("Order history", base_ms=80)
        return {"user_id": user_id}


def run_performance_simulation(n_requests: int = 20) -> None:
    endpoints = [
        (endpoint_checkout, lambda: fake.uuid4()[:8]),
        (endpoint_search,   lambda: fake.word()),
        (endpoint_user_profile, lambda: fake.uuid4()[:8]),
    ]

    all_durations: list[float] = []
    metrics.gauge("perf.simulation.requests", n_requests)

    for i in range(n_requests):
        fn, arg_fn = random.choice(endpoints)
        try:
            result = fn(arg_fn())
            if "total_ms" in result:
                all_durations.append(result["total_ms"])
            metrics.count("endpoint.request.success", 1)
        except ConnectionError as exc:
            sentry_sdk.capture_exception(exc)
            metrics.count("endpoint.request.failed", 1)
            log.error("Request failed: %s", exc)
        time.sleep(random.uniform(0.05, 0.15))

    if all_durations:
        p95 = sorted(all_durations)[int(len(all_durations) * 0.95)]
        mean = statistics.mean(all_durations)
        metrics.distribution("endpoint.p95_ms", p95)
        log.info("Performance summary — mean: %.0fms | p95: %.0fms", mean, p95)
        if p95 > P95_ALERT_THRESHOLD_MS:
            sentry_sdk.capture_message(f"p95 latency alert: {p95:.0f}ms exceeds threshold", level="error")


if __name__ == "__main__":
    init_sentry("performance-monitor")
    sentry_sdk.profiler.start_profiler()
    run_performance_simulation(n_requests=25)
    sentry_sdk.profiler.stop_profiler()
    log.info("Done — check Performance → Transactions, Explore → Metrics")
