"""
scripts/api_client.py

External API integration layer with retry logic, timeout handling, and
rate-limit awareness. Calls a set of mock endpoints and reports errors,
slow responses, and unexpected payloads to Sentry.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import logging
import requests
import sentry_sdk
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Using JSONPlaceholder as a real free public API for demo purposes
BASE_URL = "https://jsonplaceholder.typicode.com"

MAX_RETRIES = 3
TIMEOUT_S = 5

##Custom error class signaling which endpoint failed and what HTTP status code came back. When Sentry captures this, those fields appear in the issue detail alongside the stack trace.##
class APIError(Exception):
    def __init__(self, endpoint: str, status: int, message: str):
        super().__init__(message)
        self.endpoint = endpoint
        self.status = status

##Before every attempt, drops a breadcrumb into Sentry. So if it fails on attempt 3, you can see attempts 1 and 2 in the trail leading up to the error.##
def fetch_with_retry(endpoint: str, params: dict | None = None) -> dict | list:
    url = f"{BASE_URL}{endpoint}"
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        sentry_sdk.add_breadcrumb(
            category="http",
            message=f"GET {url} (attempt {attempt})",
            level="info",
            data={"params": params},
        )
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT_S)
             ##actual HTTP GET request, when it takes longer than 5s, raises a Timeout exception##
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                log.warning("Rate limited on %s — waiting %ds", endpoint, retry_after)
                time.sleep(retry_after)
                continue
             ##reads the Retry-After header to know how long to wait, then tries again##
            if resp.status_code >= 500:
                raise APIError(endpoint, resp.status_code, f"Server error: {resp.status_code}")

            if resp.status_code >= 400:
                raise APIError(endpoint, resp.status_code, f"Client error: {resp.status_code}")
             ##500s server's fault, 400s are our fault. Both raise our custom APIError with the status code attached##
            data = resp.json()
            log.info("✓ GET %s → %d items", endpoint, len(data) if isinstance(data, list) else 1)
            return data

        except requests.Timeout as exc:
            last_exc = exc
            log.warning("Timeout on %s (attempt %d/%d)", endpoint, attempt, MAX_RETRIES)
            sentry_sdk.add_breadcrumb(category="http", message=f"Timeout attempt {attempt}", level="warning")
            time.sleep(0.5 * attempt)
            ##timeout caught, logs it, adds another breadcrumb, waits a bit longer each attempt (0.5s, 1s, 1.5s).##
        except requests.ConnectionError as exc:
            last_exc = exc
            log.error("Connection error on %s: %s", endpoint, exc)
            break
            ##connection errors (no internet, DNS failure) are pointless to retry - breaks out of the loop immediately.##
    if last_exc:
        sentry_sdk.capture_exception(last_exc)
        raise last_exc
    raise APIError(endpoint, 0, "All retries exhausted")
            ##after all retries are spent - captures the exception in Sentry and re-raises it so the caller knows it failed.##

def sync_users() -> None:
    with sentry_sdk.start_span(op="api.sync", description="Sync users"):
        users = fetch_with_retry("/users")
        for user in users[:3]:  # process first 3
            log.info("  User: %s <%s>", user["name"], user["email"])
            ##fetches all users, logs the first 3, the sentry sdk wraps the whole thing in a span, so in the Sentry trace waterfall it shows up asa atimed block called "Sync user""
            
def sync_posts(user_id: int) -> None:
    with sentry_sdk.start_span(op="api.sync", description=f"Sync posts for user {user_id}"):
        posts = fetch_with_retry("/posts", params={"userId": user_id})
        log.info("  Fetched %d posts for user %d", len(posts), user_id)

        # Simulate a post-processing anomaly
        if random.random() < 0.2:
            bad_post = random.choice(posts)
            if "body" not in bad_post:
                raise ValueError(f"Post {bad_post['id']} missing 'body' field")
            ##fetches posts for a specific user. The random block is a 20% chance of simulating a post-processing bug, checking for a missing field and raising a ValueError if found. This is what generates errors in Sentry form the script##   

def fetch_comments(post_id: int) -> None:
    with sentry_sdk.start_span(op="api.fetch", description=f"Comments for post {post_id}"):
        comments = fetch_with_retry(f"/posts/{post_id}/comments")
        log.info("  Post %d has %d comments", post_id, len(comments))
            ##fetches comments for a post, logs the count. Another span in the trace.##

def run_api_sync() -> None:
    with sentry_sdk.start_transaction(op="api.integration", name="External API Sync"):
        sentry_sdk.set_tag("integration", "jsonplaceholder")
            ##opens the top-level transaction, this is the parent that all the spans sit inside. Set a tag so every event from this run is labelled in sentry"
        try:
            sync_users()
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            log.error("User sync failed: %s", exc)
            ##calls sync_users() if it fails, captures the exception in Sentry but doesn't crach the whole sync.
        for user_id in random.sample(range(1, 11), 3):
            ##picks 3 random user IDs from 1-10 without repeats
            try:
                sync_posts(user_id)
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                log.error("Post sync failed for user %d: %s", user_id, exc)
            
        for post_id in random.sample(range(1, 101), 5):
            ##picks 5 random post IDs form 1-100. Fetches comments for each.
            try:
                fetch_comments(post_id)
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                log.error("Comment fetch failed for post %d: %s", post_id, exc)
            

if __name__ == "__main__":
    init_sentry("api-client")

    log.info("Running API sync — 2 rounds")
    for r in range(2):
        log.info("--- Round %d ---", r + 1)
        run_api_sync()
        time.sleep(1)

##run logic, if you cal the file directly python3 scripts/api_client.py, if ran by run_all.py __main__ is skipped and only run_api_sync() is called
