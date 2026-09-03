"""
scripts/release_tracking.py

Demonstrates Sentry Release Health — the product narrative Sentry pitches
as "know the health of every deploy before your users feel it."

Simulates the full release lifecycle:
  1. Create a release with commit metadata
  2. Track session health (crash-free sessions %)
  3. Detect a regression introduced in a specific release
  4. Trigger an alert when crash-free rate drops below threshold
  5. Associate errors with the exact release that introduced them

This maps to Sentry's SDLC pitch to engineering managers:
  "Stop finding out about regressions from your users. Know at deploy time."
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import hashlib
import sentry_sdk
from faker import Faker
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
fake = Faker()

CRASH_FREE_THRESHOLD_PCT = 99.0

RELEASES = [
    {
        "version": "frontend@1.0.0",
        "ref": "main",
        "commits": 12,
        "stable": True,
        "crash_free_baseline": 99.7,
    },
    {
        "version": "frontend@1.1.0",
        "ref": "feature/new-auth-flow",
        "commits": 8,
        "stable": False,
        "crash_free_baseline": 96.2,
    },
    {
        "version": "frontend@1.1.1",
        "ref": "hotfix/auth-null-check",
        "commits": 2,
        "stable": True,
        "crash_free_baseline": 99.5,
    },
]


class SessionCrashError(Exception):
    pass

class ReleaseRegressionError(Exception):
    pass


def reinit_for_release(version: str) -> None:
    """Re-initialise the Sentry SDK with the correct release version.
    This is what makes each deploy appear as a distinct entry in
    Sentry Releases with its own crash-free session health score."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    environment = os.getenv("SENTRY_ENV", "development")
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=1.0,
        release=version,
        auto_session_tracking=True,
    )
    sentry_sdk.set_tag("service", "release-tracking")
    log.info("[sentry] Re-initialised for release=%s", version)


def generate_commits(n: int, branch: str) -> list[dict]:
    verbs = ["fix", "feat", "refactor", "chore", "test", "docs", "perf"]
    scopes = ["auth", "checkout", "api", "ui", "db", "cache", "payments", "search"]
    return [
        {
            "id": hashlib.sha1(fake.uuid4().encode()).hexdigest()[:10],
            "message": f"{random.choice(verbs)}({random.choice(scopes)}): {fake.sentence(nb_words=6).rstrip('.')}",
            "author": fake.name(),
            "timestamp": fake.date_time_between(start_date="-7d", end_date="now").isoformat(),
        }
        for _ in range(n)
    ]


def simulate_user_session(release_version: str, crash_free_rate: float) -> dict:
    user_id = fake.uuid4()[:8]
    session_id = fake.uuid4()

    sentry_sdk.set_user({"id": user_id})
    sentry_sdk.set_tag("release", release_version)

    crashed = random.random() > (crash_free_rate / 100)
    duration_s = random.uniform(30, 600)

    if crashed:
        exc = SessionCrashError(
            f"Unhandled exception in AuthController.handleCallback() — "
            f"NullReferenceError: token is undefined (session {session_id})"
        )
        sentry_sdk.capture_exception(exc)
        log.warning("  Session crashed: user=%s release=%s", user_id, release_version)
        return {"session_id": session_id, "status": "crashed", "duration_s": duration_s}

    return {"session_id": session_id, "status": "ok", "duration_s": duration_s}


def deploy_release(release: dict) -> None:
    version = release["version"]
    log.info("\n" + "=" * 55)
    log.info("Deploying %s", version)
    log.info("=" * 55)

    # Each release gets its own SDK init so Sentry tracks it separately
    reinit_for_release(version)

    with sentry_sdk.start_transaction(
        op="release.deploy",
        name=f"Deploy {version}",
    ) as tx:
        sentry_sdk.set_tag("release.version", version)
        sentry_sdk.set_tag("release.ref", release["ref"])
        sentry_sdk.set_tag("release.stable", str(release["stable"]).lower())

        with sentry_sdk.start_span(op="release.create", description="Register release"):
            commits = generate_commits(release["commits"], release["ref"])
            log.info("Release %s — %d commits on %s", version, len(commits), release["ref"])
            for c in commits[:3]:
                log.info("  %s  %s  (%s)", c["id"], c["message"][:60], c["author"])
            if len(commits) > 3:
                log.info("  ... and %d more commits", len(commits) - 3)
            time.sleep(0.1)

        stages = ["build", "staging", "canary", "production"]
        for stage in stages:
            with sentry_sdk.start_span(op=f"deploy.{stage}", description=f"Deploy to {stage}"):
                duration = random.uniform(0.1, 0.4)
                time.sleep(duration)
                log.info("  Deployed to %s (%.1fs)", stage, duration)
                sentry_sdk.add_breadcrumb(
                    category="deploy",
                    message=f"Deployed {version} to {stage}",
                    level="info",
                    data={"stage": stage, "duration_s": round(duration, 2)},
                )

        with sentry_sdk.start_span(op="release.health", description="Track session health"):
            n_sessions = random.randint(30, 60)
            log.info("Monitoring %d sessions for release %s", n_sessions, version)

            results = []
            for _ in range(n_sessions):
                session = simulate_user_session(version, release["crash_free_baseline"])
                results.append(session)
                time.sleep(0.02)

            crashed = sum(1 for s in results if s["status"] == "crashed")
            crash_free_pct = ((n_sessions - crashed) / n_sessions) * 100

            log.info(
                "Release health: %.1f%% crash-free (%d/%d sessions ok)",
                crash_free_pct, n_sessions - crashed, n_sessions,
            )

            tx.set_data("crash_free_pct", round(crash_free_pct, 2))
            tx.set_data("total_sessions", n_sessions)
            tx.set_data("crashed_sessions", crashed)

        if crash_free_pct < CRASH_FREE_THRESHOLD_PCT:
            regression_msg = (
                f"Release health alert: {version} crash-free rate is {crash_free_pct:.1f}% "
                f"— below {CRASH_FREE_THRESHOLD_PCT}% threshold. "
                f"Consider rollback to {RELEASES[0]['version']}."
            )
            sentry_sdk.capture_message(regression_msg, level="error")
            log.error("ALERT: %s", regression_msg)

            if not release["stable"]:
                regression_exc = ReleaseRegressionError(
                    f"Regression detected in {version}: AuthController.handleCallback() "
                    f"crashes when OAuth token response is missing 'scope' field. "
                    f"Introduced in commit {commits[-1]['id']} by {commits[-1]['author']}."
                )
                sentry_sdk.capture_exception(regression_exc)
                log.error("Root cause captured: %s", regression_exc)
        else:
            log.info("Release %s is healthy (%.1f%% crash-free)", version, crash_free_pct)
            sentry_sdk.capture_message(
                f"Release {version} passed health check: {crash_free_pct:.1f}% crash-free",
                level="info",
            )


def run_release_simulation() -> None:
    log.info("Starting release simulation — %d releases", len(RELEASES))
    log.info("Threshold: %.1f%% crash-free required to pass", CRASH_FREE_THRESHOLD_PCT)

    for release in RELEASES:
        deploy_release(release)
        time.sleep(0.3)

    log.info("\nRelease simulation complete")
    log.info("Check Sentry Releases — each version should have its own health score")


if __name__ == "__main__":
    init_sentry("release-tracking")
    run_release_simulation()
