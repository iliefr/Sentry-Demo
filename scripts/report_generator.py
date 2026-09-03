"""
scripts/report_generator.py

Generates usage and performance reports from simulated time-series metrics.
Detects anomalies (spikes, drops, threshold breaches) and sends them to Sentry
as captured messages with context. Great for showing Sentry's alerting features.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import datetime
import statistics
import sentry_sdk
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

METRICS = ["response_time_ms", "error_rate_pct", "active_sessions", "cpu_usage_pct", "queue_depth"]
REPORT_TYPES = ["daily_summary", "performance_digest", "error_spike_alert", "capacity_forecast"]


class AnomalyDetectedError(Exception):
    pass

class ReportGenerationError(Exception):
    pass


def generate_time_series(metric: str, n_points: int = 24) -> list[float]:
    """Simulate 24h of hourly metric data with occasional anomalies."""
    baselines = {
        "response_time_ms": 120,
        "error_rate_pct": 0.8,
        "active_sessions": 1500,
        "cpu_usage_pct": 45,
        "queue_depth": 80,
    }
    base = baselines.get(metric, 100)
    series = []
    for i in range(n_points):
        noise = random.gauss(0, base * 0.1)
        spike = base * random.uniform(1.5, 3.0) if random.random() < 0.05 else 0
        value = max(0, base + noise + spike)
        series.append(round(value, 2))
    return series


def detect_anomalies(metric: str, series: list[float]) -> list[dict]:
    mean = statistics.mean(series)
    stdev = statistics.stdev(series)
    threshold = mean + 2.5 * stdev

    anomalies = []
    for i, val in enumerate(series):
        if val > threshold:
            anomalies.append({"hour": i, "value": val, "mean": round(mean, 2), "threshold": round(threshold, 2)})
    return anomalies


def build_report(report_type: str) -> dict:
    log.info("Building report: %s", report_type)
    time.sleep(random.uniform(0.15, 0.5))

    if random.random() < 0.08:
        raise ReportGenerationError(f"Template renderer crashed for report type '{report_type}'")

    report_data = {
        "type": report_type,
        "generated_at": datetime.datetime.utcnow().isoformat(),
        "period": "last_24h",
        "metrics": {},
        "anomalies": [],
    }

    for metric in METRICS:
        series = generate_time_series(metric)
        report_data["metrics"][metric] = {
            "mean": round(statistics.mean(series), 2),
            "max": round(max(series), 2),
            "min": round(min(series), 2),
            "p95": round(sorted(series)[int(len(series) * 0.95)], 2),
        }
        anomalies = detect_anomalies(metric, series)
        if anomalies:
            report_data["anomalies"].extend([{"metric": metric, **a} for a in anomalies])

    return report_data


def publish_report(report: dict) -> None:
    """Simulate publishing — email dispatch, S3 upload, etc."""
    log.info("Publishing report '%s' (%d anomalies detected)", report["type"], len(report["anomalies"]))
    time.sleep(random.uniform(0.05, 0.15))

    # Raise Sentry alerts for each anomaly found
    for anomaly in report["anomalies"]:
        msg = (
            f"Anomaly in {anomaly['metric']}: value={anomaly['value']} "
            f"at hour {anomaly['hour']} (mean={anomaly['mean']}, threshold={anomaly['threshold']})"
        )
        sentry_sdk.capture_message(msg, level="warning")
        log.warning("⚠ %s", msg)

    # Surface a critical if error_rate spiked
    er_stats = report["metrics"].get("error_rate_pct", {})
    if er_stats.get("max", 0) > 3.0:
        sentry_sdk.capture_message(
            f"Error rate spike detected: max={er_stats['max']}% in last 24h",
            level="error",
        )
        log.error("Error rate max %.2f%% exceeded critical threshold", er_stats["max"])


def run_report_generation() -> None:
    with sentry_sdk.start_transaction(op="report.generate", name="Report Generation Run"):
        sentry_sdk.set_tag("report_runner", "automated")

        for report_type in REPORT_TYPES:
            with sentry_sdk.start_span(op="report.build", description=f"Build {report_type}"):
                try:
                    report = build_report(report_type)
                except ReportGenerationError as exc:
                    sentry_sdk.capture_exception(exc)
                    log.error("Report build failed: %s", exc)
                    continue

            with sentry_sdk.start_span(op="report.publish", description=f"Publish {report_type}"):
                try:
                    publish_report(report)
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    log.error("Report publish failed: %s", exc)

        log.info("Report generation run complete")


if __name__ == "__main__":
    init_sentry("report-generator")

    log.info("Starting report generation — 2 runs")
    for run in range(2):
        log.info("--- Run %d ---", run + 1)
        run_report_generation()
        time.sleep(0.5)
