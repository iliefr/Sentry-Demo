"""
utils/sentry_config.py
Centralised Sentry initialisation shared by all scripts.
Covers: error tracking, performance, logs, metrics, profiling.
"""

import os
import logging
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration


def init_sentry(service_name: str) -> None:
    dsn = os.getenv("SENTRY_DSN")
    environment = os.getenv("SENTRY_ENV", "development")
    traces_rate = float(os.getenv("SENTRY_TRACES_RATE", "1.0"))

    if not dsn:
        print("[sentry] SENTRY_DSN not set — running without Sentry")
        return

    logging_integration = LoggingIntegration(
        level=logging.INFO,         # INFO and above → Sentry Logs
        event_level=logging.ERROR,  # ERROR and above → also creates an Issue
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # Tracing
        traces_sample_rate=traces_rate,
        # Profiling — captures slow code flamegraphs
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
        # Logs — routes Python logging to Explore → Logs
        enable_logs=True,
        # Integrations
        integrations=[logging_integration],
        release=f"{service_name}@1.0.0",
        auto_session_tracking=True,
    )

    sentry_sdk.set_tag("service", service_name)
    print(f"[sentry] Initialised — service={service_name} env={environment} | tracing + logs + metrics + profiling enabled")
