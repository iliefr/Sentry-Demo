"""
scripts/feature_flags.py

Demonstrates Sentry's Feature Flag change tracking — one of Sentry's
newer product investments, positioned as "know instantly when a flag
rollout causes a regression."

Simulates a progressive flag rollout (0% → 10% → 50% → 100%) and ties
errors back to the flag state at the time of the event. This is exactly
the workflow Sentry shows in enterprise demos:
  "You rolled out dark_mode_v2 to 50% — error rate spiked 3x. Here's the
   first event that fired after the rollout."
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import sentry_sdk
from faker import Faker
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
fake = Faker()


# ---------------------------------------------------------------------------
# Simulated feature flag store
# Each flag has a rollout percentage and a "buggy" flag for realism
# ---------------------------------------------------------------------------
FLAG_STORE: dict[str, dict] = {
    "dark_mode_v2":          {"rollout_pct": 0,   "buggy": False},
    "new_checkout_flow":     {"rollout_pct": 0,   "buggy": True},   # will cause errors at >25%
    "ai_recommendations":    {"rollout_pct": 0,   "buggy": False},
    "redesigned_dashboard":  {"rollout_pct": 0,   "buggy": False},
    "experimental_search":   {"rollout_pct": 0,   "buggy": True},   # will cause errors at >50%
}

ROLLOUT_STAGES = [0, 5, 10, 25, 50, 75, 100]


class FeatureFlagRegressionError(Exception):
    pass


def is_flag_enabled(flag_name: str, user_id: str) -> bool:
    """Deterministic per-user rollout using a simple hash bucket."""
    flag = FLAG_STORE.get(flag_name)
    if not flag:
        return False
    bucket = hash(f"{flag_name}:{user_id}") % 100
    return bucket < flag["rollout_pct"]


def set_rollout(flag_name: str, pct: int) -> None:
    """Update a flag's rollout percentage and record in Sentry context."""
    old_pct = FLAG_STORE[flag_name]["rollout_pct"]
    FLAG_STORE[flag_name]["rollout_pct"] = pct
    log.info("Flag '%s': %d%% → %d%%", flag_name, old_pct, pct)

    # Tag the Sentry scope so all subsequent events carry flag state
    sentry_sdk.set_tag(f"flag.{flag_name}", f"{pct}pct")
    sentry_sdk.add_breadcrumb(
        category="feature_flag",
        message=f"Rollout updated: {flag_name} {old_pct}% → {pct}%",
        level="info",
        data={"flag": flag_name, "old_pct": old_pct, "new_pct": pct},
    )


def simulate_user_request(user_id: str, endpoint: str) -> None:
    """
    Simulate a user hitting an endpoint. If they're in the rollout cohort for
    a buggy flag, there's a chance of a regression error.
    """
    active_flags = {
        name: is_flag_enabled(name, user_id)
        for name in FLAG_STORE
    }

    # Attach current flag state to Sentry event context — this is what makes
    # Sentry's flag tracking powerful: every error knows which flags were active
    with sentry_sdk.configure_scope() as scope:
        for flag_name, enabled in active_flags.items():
            scope.set_tag(f"user.flag.{flag_name}", str(enabled).lower())

    sentry_sdk.set_user({"id": user_id})
    sentry_sdk.add_breadcrumb(
        category="navigation",
        message=f"User {user_id} → {endpoint}",
        level="info",
        data={"flags": active_flags},
    )

    time.sleep(random.uniform(0.01, 0.05))

    # new_checkout_flow is buggy above 25%
    if active_flags.get("new_checkout_flow") and FLAG_STORE["new_checkout_flow"]["rollout_pct"] > 25:
        if random.random() < 0.35:
            exc = FeatureFlagRegressionError(
                f"new_checkout_flow caused NullPointerException in PaymentForm.validate() "
                f"for user {user_id}"
            )
            sentry_sdk.capture_exception(exc)
            log.error("Regression: new_checkout_flow @ %d%% → %s",
                      FLAG_STORE["new_checkout_flow"]["rollout_pct"], exc)
            return

    # experimental_search is buggy above 50%
    if active_flags.get("experimental_search") and FLAG_STORE["experimental_search"]["rollout_pct"] > 50:
        if random.random() < 0.20:
            exc = FeatureFlagRegressionError(
                f"experimental_search raised IndexError in RankingEngine.score() "
                f"for query from user {user_id}"
            )
            sentry_sdk.capture_exception(exc)
            log.error("Regression: experimental_search @ %d%% → %s",
                      FLAG_STORE["experimental_search"]["rollout_pct"], exc)
            return


def run_rollout_simulation() -> None:
    """
    Simulate a realistic flag rollout lifecycle:
    - Start at 0%
    - Progressively widen cohort
    - Observe error rate increase as buggy flags hit more users
    - This generates the "error spike after rollout" pattern Sentry detects
    """
    users = [fake.uuid4()[:8] for _ in range(40)]
    endpoints = ["/checkout", "/search", "/dashboard", "/profile", "/recommendations"]

    for flag_name in FLAG_STORE:
        log.info("\n=== Rolling out: %s ===", flag_name)

        with sentry_sdk.start_transaction(
            op="feature_flag.rollout",
            name=f"Rollout: {flag_name}",
        ) as tx:
            sentry_sdk.set_tag("flag_name", flag_name)

            for stage_pct in ROLLOUT_STAGES:
                with sentry_sdk.start_span(
                    op="flag.stage",
                    description=f"{flag_name} @ {stage_pct}%",
                ):
                    set_rollout(flag_name, stage_pct)

                    # Simulate a wave of user traffic at this rollout stage
                    n_requests = random.randint(8, 15)
                    log.info("  Simulating %d requests at %d%% rollout", n_requests, stage_pct)
                    for _ in range(n_requests):
                        user = random.choice(users)
                        endpoint = random.choice(endpoints)
                        simulate_user_request(user, endpoint)

                time.sleep(0.1)

            # Capture final flag state summary
            summary = {k: v["rollout_pct"] for k, v in FLAG_STORE.items()}
            tx.set_data("final_rollout_state", summary)
            log.info("Rollout complete: %s", summary)


if __name__ == "__main__":
    init_sentry("feature-flags")
    log.info("Starting feature flag rollout simulation")
    run_rollout_simulation()
    log.info("Done — check Sentry Issues and filter by flag tags to see regression correlation")
