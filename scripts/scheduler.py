"""
scripts/scheduler.py

Cron-style job runner. Emits metrics per job: duration, success/failure counts,
and a gauge of active jobs. Profiler runs across the full scheduler loop.
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import datetime
import sentry_sdk
from sentry_sdk import metrics
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

JOB_REGISTRY: dict[str, dict] = {}

def job(name: str, interval_s: int):
    def decorator(fn):
        JOB_REGISTRY[name] = {"fn": fn, "interval": interval_s, "last_run": None}
        return fn
    return decorator


@job("cache.warmup", interval_s=30)
def warm_cache() -> None:
    log.info("[cache.warmup] Warming application cache")
    time.sleep(random.uniform(0.1, 0.4))
    if random.random() < 0.1:
        raise RuntimeError("Cache server refused connection on port 6379")
    keys = random.randint(200, 800)
    metrics.gauge("cache.warmed_keys", keys)
    log.info("[cache.warmup] %d keys refreshed", keys)


@job("health.check", interval_s=15)
def health_check() -> None:
    services = ["api-gateway", "auth-service", "database", "queue-broker"]
    for svc in services:
        latency = random.uniform(5, 250)
        metrics.distribution("service.health.latency_ms", latency, attributes={"service": svc})
        if latency > 200:
            metrics.count("service.health.degraded", 1, attributes={"service": svc})
            sentry_sdk.capture_message(f"Service degraded: {svc} responded in {latency:.0f}ms", level="warning")
        else:
            metrics.count("service.health.ok", 1, attributes={"service": svc})
    time.sleep(0.1)


@job("report.snapshot", interval_s=60)
def snapshot_report() -> None:
    log.info("[report.snapshot] Taking metrics snapshot")
    time.sleep(random.uniform(0.2, 0.6))
    if random.random() < 0.08:
        raise TimeoutError("Metrics aggregation query exceeded 30s timeout")
    metrics.gauge("report.active_users", random.randint(1200, 4500))
    metrics.gauge("report.orders_today", random.randint(80, 350))
    metrics.gauge("report.revenue_eur", round(random.uniform(5000, 25000), 2))
    metrics.distribution("report.error_rate_pct", round(random.uniform(0.1, 2.5), 2))


@job("cleanup.temp_files", interval_s=120)
def cleanup_temp() -> None:
    time.sleep(random.uniform(0.05, 0.2))
    deleted = random.randint(0, 40)
    freed_mb = round(deleted * random.uniform(0.5, 5.0), 1)
    metrics.count("cleanup.files_deleted", deleted)
    metrics.gauge("cleanup.freed_mb", freed_mb)
    log.info("[cleanup] Deleted %d files, freed %.1fMB", deleted, freed_mb)


def run_job(name: str, meta: dict) -> None:
    with sentry_sdk.start_transaction(op="job.run", name=f"Job: {name}") as tx:
        sentry_sdk.set_tag("job_name", name)
        start = time.monotonic()
        try:
            meta["fn"]()
            duration_ms = (time.monotonic() - start) * 1000
            metrics.distribution("job.duration_ms", duration_ms, attributes={"job": name})
            metrics.count("job.success", 1, attributes={"job": name})
            tx.set_data("duration_ms", round(duration_ms))
        except Exception as exc:
            metrics.count("job.failed", 1, attributes={"job": name})
            sentry_sdk.capture_exception(exc)
            log.error("Job '%s' failed: %s", name, exc)


def scheduler_loop(max_iterations: int = 5) -> None:
    log.info("Scheduler starting — %d iterations", max_iterations)
    metrics.gauge("scheduler.registered_jobs", len(JOB_REGISTRY))
    for i in range(max_iterations):
        now = time.monotonic()
        for name, meta in JOB_REGISTRY.items():
            last = meta["last_run"]
            if last is None or (now - last) >= meta["interval"]:
                run_job(name, meta)
                meta["last_run"] = time.monotonic()
        time.sleep(1)
    log.info("Scheduler loop finished")


if __name__ == "__main__":
    init_sentry("job-scheduler")
    sentry_sdk.profiler.start_profiler()
    scheduler_loop(max_iterations=5)
    sentry_sdk.profiler.stop_profiler()
