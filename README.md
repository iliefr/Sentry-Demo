# Sentry Platform Demo

A Python monorepo that instruments a simulated backend ecosystem across the **full Sentry product surface** — from error tracking through performance monitoring, release health, and feature flag change tracking.

Built to demonstrate how Sentry fits into a real engineering workflow, not just as a crash reporter but as an **end-to-end application observability platform**.

---

## What this covers

Sentry's value proposition has three layers. This repo deliberately exercises all three:

### Layer 1 — Error Monitoring (the foundation)
Every service captures exceptions with full context: breadcrumbs, user identity, custom tags, and severity levels. Errors aren't just stack traces — they carry the *story* of what the user was doing when things broke.

| Script | Simulated service | Error patterns |
|--------|------------------|----------------|
| `order_processor.py` | E-commerce pipeline | Payment declines, stock errors, fulfillment timeouts |
| `data_pipeline.py` | ETL / sensor ingestion | Schema violations, type casting failures, DB write timeouts |
| `scheduler.py` | Cron job runner | Service degradation, job timeouts, cache failures |
| `api_client.py` | External REST integration | HTTP timeouts with retry logic, unexpected payloads |
| `user_auth.py` | Auth & session management | Brute-force lockouts, expired tokens, invalid credentials |
| `inventory_sync.py` | Warehouse ↔ 3PL reconciliation | Stock discrepancies, low-stock alerts, conflict errors |
| `report_generator.py` | Metrics & anomaly detection | Statistical spike detection, template failures |

### Layer 2 — Performance Monitoring (where Sentry earns enterprise deals)
`performance_monitor.py` instruments a checkout service as a **distributed trace** — every DB query, cache lookup, and downstream API call becomes a span in a waterfall. Demonstrates:

- **Transaction monitoring** across three endpoints (`/checkout`, `/search`, `/profile`)
- **Slow query detection** with p95 latency alerting
- **N+1 query pattern** — the specific anti-pattern Sentry's Queries tab flags automatically
- **Distributed trace waterfall**: frontend → API → DB → cache → payment service → fulfillment

### Layer 3 — Release Health & Feature Flags (Sentry's SDLC narrative)
The two scripts that connect Sentry to the deployment pipeline:

**`release_tracking.py`** — simulates a three-release train (`v1.0.0` stable → `v1.1.0` regression → `v1.1.1` hotfix). Each deploy tracks crash-free session rate in real time and fires a rollback alert when health drops below threshold. Errors are automatically attributed to the release that introduced them.

**`feature_flags.py`** — simulates progressive flag rollouts (0% → 5% → 10% → 25% → 50% → 100%) for five flags, two of which contain intentional regressions. Every error carries the flag state at the time of the event, so you can see exactly which rollout stage caused the spike — the workflow Sentry calls "Flag Change Tracking."

---

## Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/your-username/sentry-demo.git
cd sentry-demo

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your DSN (from Settings → Projects → Client Keys in Sentry)
export SENTRY_DSN="https://your-key@oXXXXXX.ingest.sentry.io/XXXXXXX"

# 4. Run everything
python3 run_all.py
```

Or run scripts individually:

```bash
python3 scripts/performance_monitor.py   # performance + N+1 detection
python3 scripts/release_tracking.py      # release health + regression blame
python3 scripts/feature_flags.py         # flag rollout + regression correlation
python3 scripts/order_processor.py       # error tracking + breadcrumbs
```

---

## What to look at in Sentry after running

| Sentry tab | What you'll see |
|-----------|-----------------|
| **Issues → Feed** | Unresolved errors across all services, tagged by service and release |
| **Performance → Transactions** | Checkout, search, and profile endpoints with full trace waterfalls |
| **Performance → Queries** | N+1 pattern flagged on the checkout flow |
| **Releases** | Health scores per version — v1.1.0 will show degraded crash-free rate |
| **Issues → filter by release** | Errors attributed to the exact release that introduced them |
| **Issues → filter by flag tags** | Regression correlation with `new_checkout_flow` and `experimental_search` rollouts |

---

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SENTRY_DSN` | Project DSN from Sentry settings | required |
| `SENTRY_ENV` | Environment name | `development` |
| `SENTRY_TRACES_RATE` | Performance trace sample rate (0.0–1.0) | `1.0` |

---

## Tech stack

- **[sentry-sdk](https://github.com/getsentry/sentry-python)** — official Sentry Python SDK
- **[requests](https://requests.readthedocs.io)** — HTTP client (api_client.py)
- **[faker](https://faker.readthedocs.io)** — realistic synthetic data
- **[schedule](https://schedule.readthedocs.io)** — job scheduling
- **Python 3.10+**
