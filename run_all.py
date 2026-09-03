"""
run_all.py — fires all scripts in sequence.

Usage:
    export SENTRY_DSN="https://..."
    python3 run_all.py
"""

import os
import sys
import time
import logging
import sentry_sdk

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(__file__))

if __name__ == "__main__":
    from utils.sentry_config import init_sentry
    init_sentry("demo-runner")

    sentry_sdk.profiler.start_profiler()

    log.info("=" * 60)
    log.info("  SENTRY PLATFORM DEMO")
    log.info("  Errors · Performance · Logs · Metrics · Profiling")
    log.info("=" * 60)

    log.info("\n── Layer 1: Error Monitoring ──────────────────────────────")

    from scripts.order_processor import build_random_order, process_order
    log.info("\n▶ order_processor")
    for _ in range(8):
        process_order(build_random_order())
    time.sleep(0.3)

    from scripts.data_pipeline import run_pipeline
    log.info("\n▶ data_pipeline")
    run_pipeline(batch_size=60)
    time.sleep(0.3)

    from scripts.scheduler import scheduler_loop
    log.info("\n▶ scheduler")
    scheduler_loop(max_iterations=3)
    time.sleep(0.3)

    from scripts.api_client import run_api_sync
    log.info("\n▶ api_client")
    run_api_sync()
    time.sleep(0.3)

    from scripts.user_auth import run_auth_simulation
    log.info("\n▶ user_auth")
    run_auth_simulation()
    time.sleep(0.3)

    from scripts.inventory_sync import run_inventory_sync
    log.info("\n▶ inventory_sync")
    for _ in range(2):
        run_inventory_sync()
    time.sleep(0.3)

    from scripts.report_generator import run_report_generation
    log.info("\n▶ report_generator")
    run_report_generation()
    time.sleep(0.3)

    log.info("\n── Layer 2: Performance Monitoring ────────────────────────")

    from scripts.performance_monitor import run_performance_simulation
    log.info("\n▶ performance_monitor")
    run_performance_simulation(n_requests=25)
    time.sleep(0.3)

    log.info("\n── Layer 3: Releases & Feature Flags ──────────────────────")

    from scripts.release_tracking import run_release_simulation
    log.info("\n▶ release_tracking")
    run_release_simulation()
    time.sleep(0.3)

    from scripts.feature_flags import run_rollout_simulation
    log.info("\n▶ feature_flags")
    run_rollout_simulation()

    sentry_sdk.profiler.stop_profiler()

    log.info("\n" + "=" * 60)
    log.info("  All done. Check Sentry:")
    log.info("  → Issues           (errors across all services)")
    log.info("  → Performance      (transactions + N+1 queries)")
    log.info("  → Explore → Logs   (structured log stream)")
    log.info("  → Explore → Metrics (custom counters + distributions)")
    log.info("  → Profiling        (flamegraphs for slow code)")
    log.info("  → Releases         (v1.0.0 / v1.1.0 / v1.1.1)")
    log.info("=" * 60)
