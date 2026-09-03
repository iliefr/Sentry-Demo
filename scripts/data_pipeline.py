"""
scripts/data_pipeline.py

ETL pipeline: ingests raw sensor readings, transforms and validates them,
then loads them into a downstream store. Sentry tracks the full pipeline
transaction, captures errors, and emits metrics on throughput and quality.
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

SENSOR_TYPES = ["temperature", "humidity", "pressure", "vibration", "co2"]


class SchemaValidationError(Exception):
    pass

class TransformError(Exception):
    pass


def ingest_raw_records(n: int = 50) -> list[dict]:
    log.info("Ingesting %d raw records from queue", n)
    time.sleep(random.uniform(0.1, 0.2))
    records = []
    for i in range(n):
        if random.random() < 0.08:
            records.append({"id": i, "sensor": None, "value": "NaN", "ts": "bad-timestamp"})
        else:
            records.append({
                "id": i,
                "sensor": random.choice(SENSOR_TYPES),
                "value": round(random.uniform(-10, 150), 3),
                "ts": fake.iso8601(),
                "unit": random.choice(["C", "%", "hPa", "mm/s", "ppm"]),
                "device_id": f"DEV-{random.randint(100, 999)}",
            })
    metrics.gauge("pipeline.queue.depth", len(records))
    return records


def validate_record(record: dict) -> None:
    required_fields = {"id", "sensor", "value", "ts"}
    missing = required_fields - set(record.keys())
    if missing:
        raise SchemaValidationError(f"Record {record.get('id')} missing fields: {missing}")
    if record["sensor"] is None:
        raise SchemaValidationError(f"Record {record['id']} has null sensor type")


def transform_record(record: dict) -> dict:
    try:
        value = float(record["value"])
    except (ValueError, TypeError) as exc:
        raise TransformError(f"Cannot cast value '{record['value']}' to float for record {record['id']}") from exc
    return {
        "record_id": record["id"],
        "sensor_type": record["sensor"],
        "value_normalised": round(value / 100, 6),
        "raw_value": value,
        "device_id": record.get("device_id", "UNKNOWN"),
        "ingested_at": record["ts"],
    }


def load_records(transformed: list[dict]) -> int:
    log.info("Loading %d transformed records", len(transformed))
    time.sleep(random.uniform(0.15, 0.35))
    if random.random() < 0.07:
        raise ConnectionError("Database write timeout after 5000ms — retries exhausted")
    return len(transformed)


def run_pipeline(batch_size: int = 50) -> dict:
    stats = {"ingested": 0, "valid": 0, "transformed": 0, "loaded": 0, "errors": 0}

    with sentry_sdk.start_transaction(op="etl.pipeline", name="Sensor Data Pipeline") as tx:
        sentry_sdk.set_tag("pipeline", "sensor-etl")
        tx.set_data("batch_size", batch_size)

        with sentry_sdk.start_span(op="etl.extract"):
            raw = ingest_raw_records(batch_size)
            stats["ingested"] = len(raw)

        transformed = []
        with sentry_sdk.start_span(op="etl.transform"):
            for record in raw:
                try:
                    validate_record(record)
                    t = transform_record(record)
                    transformed.append(t)
                    stats["valid"] += 1
                except (SchemaValidationError, TransformError) as exc:
                    stats["errors"] += 1
                    metrics.count("pipeline.record.invalid", 1, attributes={"stage": "transform"})
                    sentry_sdk.capture_exception(exc)
                    log.warning("Skipping record %s: %s", record.get("id"), exc)

        stats["transformed"] = len(transformed)
        metrics.distribution("pipeline.batch.valid_pct",
                             (stats["valid"] / stats["ingested"] * 100) if stats["ingested"] else 0)

        with sentry_sdk.start_span(op="etl.load"):
            try:
                loaded = load_records(transformed)
                stats["loaded"] = loaded
                metrics.count("pipeline.records.loaded", loaded)
            except ConnectionError as exc:
                stats["errors"] += 1
                metrics.count("pipeline.load.failed", 1)
                sentry_sdk.capture_exception(exc)
                log.error("Load failed: %s", exc)

        tx.set_data("stats", stats)
        log.info("Pipeline complete: %s", stats)
        return stats


if __name__ == "__main__":
    init_sentry("data-pipeline")
    sentry_sdk.profiler.start_profiler()

    log.info("Running ETL pipeline — 3 batches")
    for run in range(3):
        log.info("--- Batch %d ---", run + 1)
        run_pipeline(batch_size=random.randint(40, 80))
        time.sleep(0.5)

    sentry_sdk.profiler.stop_profiler()
