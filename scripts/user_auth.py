"""
scripts/user_auth.py

Authentication and session management simulation.
Covers: login attempts, token generation, token validation,
session expiry, and brute-force lockout detection.
Sentry captures auth failures, suspicious activity, and expired sessions.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import random
import time
import hashlib
import logging
import datetime
import sentry_sdk
from faker import Faker
from utils.sentry_config import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
fake = Faker()

# In-memory "database"
USER_DB: dict[str, dict] = {}
SESSION_STORE: dict[str, dict] = {}
FAILED_ATTEMPTS: dict[str, int] = {}
LOCKOUT_THRESHOLD = 5


class AuthError(Exception):
    pass

class AccountLockedError(Exception):
    pass

class SessionExpiredError(Exception):
    pass


def seed_users(n: int = 10) -> None:
    for _ in range(n):
        email = fake.email()
        USER_DB[email] = {
            "name": fake.name(),
            "password_hash": hashlib.sha256(fake.password().encode()).hexdigest(),
            "role": random.choice(["user", "user", "user", "admin"]),
            "created_at": fake.date_time_this_year().isoformat(),
        }
    log.info("Seeded %d users", n)


def attempt_login(email: str, password: str) -> str:
    sentry_sdk.set_user({"email": email})
    sentry_sdk.add_breadcrumb(category="auth", message=f"Login attempt for {email}", level="info")

    if FAILED_ATTEMPTS.get(email, 0) >= LOCKOUT_THRESHOLD:
        sentry_sdk.capture_message(
            f"Account locked after {LOCKOUT_THRESHOLD} failed attempts: {email}",
            level="warning",
        )
        raise AccountLockedError(f"Account {email} is locked due to too many failed attempts")

    time.sleep(random.uniform(0.05, 0.15))

    user = USER_DB.get(email)
    if not user:
        FAILED_ATTEMPTS[email] = FAILED_ATTEMPTS.get(email, 0) + 1
        raise AuthError(f"No account found for {email}")

    # Simulate 30% wrong-password rate
    provided_hash = hashlib.sha256(password.encode()).hexdigest()
    if random.random() < 0.30:
        FAILED_ATTEMPTS[email] = FAILED_ATTEMPTS.get(email, 0) + 1
        count = FAILED_ATTEMPTS[email]
        log.warning("Failed login for %s (%d attempts)", email, count)
        if count >= LOCKOUT_THRESHOLD:
            sentry_sdk.capture_message(f"Brute-force threshold hit for {email}", level="error")
        raise AuthError(f"Invalid password for {email}")

    FAILED_ATTEMPTS[email] = 0
    token = hashlib.sha256(f"{email}{fake.uuid4()}".encode()).hexdigest()
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=random.choice([1, 8, 24, -1]))  # -1 = already expired
    SESSION_STORE[token] = {"email": email, "role": user["role"], "expires_at": expires_at}

    log.info("Login successful for %s (role=%s)", email, user["role"])
    return token


def validate_token(token: str) -> dict:
    sentry_sdk.add_breadcrumb(category="auth", message="Validating token", level="info")

    session = SESSION_STORE.get(token)
    if not session:
        raise AuthError("Token not found or already invalidated")

    if session["expires_at"] < datetime.datetime.utcnow():
        del SESSION_STORE[token]
        raise SessionExpiredError(f"Session expired for {session['email']}")

    log.info("Token valid — user=%s role=%s", session["email"], session["role"])
    return session


def run_auth_simulation() -> None:
    seed_users(20)
    emails = list(USER_DB.keys())

    with sentry_sdk.start_transaction(op="auth.simulation", name="Auth Flow Simulation"):
        for _ in range(15):
            email = random.choice(emails)
            password = fake.password()  # usually wrong, which is fine

            with sentry_sdk.start_span(op="auth.login", description="Login attempt"):
                try:
                    token = attempt_login(email, password)
                except AccountLockedError as exc:
                    sentry_sdk.capture_exception(exc)
                    log.error("Locked: %s", exc)
                    continue
                except AuthError as exc:
                    log.warning("Auth failed: %s", exc)
                    continue

            with sentry_sdk.start_span(op="auth.validate", description="Token validation"):
                try:
                    session = validate_token(token)
                except SessionExpiredError as exc:
                    sentry_sdk.capture_exception(exc)
                    log.warning("Expired session: %s", exc)
                except AuthError as exc:
                    sentry_sdk.capture_exception(exc)
                    log.error("Token error: %s", exc)

            time.sleep(random.uniform(0.05, 0.2))


if __name__ == "__main__":
    init_sentry("user-auth")
    log.info("Starting auth simulation")
    run_auth_simulation()
    log.info("Auth simulation complete — %d sessions stored", len(SESSION_STORE))
