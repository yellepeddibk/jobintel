# JobIntel

Ingests job postings from public job board APIs, normalizes them, extracts skills, and serves
analytics in a Streamlit dashboard. This file lists durable rules only. Where the README
disagrees with code, the code wins. `db/schema.sql` is stale; ignore it.

## Where to look

| Topic | Source of truth |
|---|---|
| Python, Ruff, pytest config | `pyproject.toml` |
| CI and scheduled ingestion | `.github/workflows/ci.yml`, `ingest-jobs.yml` |
| Schema | `src/jobintel/models.py`, `alembic/versions/` |
| Adapters and registry | `src/jobintel/etl/sources/` |
| Hashing and idempotency | `src/jobintel/etl/raw.py`, `transform.py` |
| Analytics queries | `src/jobintel/analytics/queries.py` |

## Validation

`ruff check .` and `pytest` must pass before every PR (both are CI gates; `ruff format` is not).

## Writing

- Comments explain why, in one or two lines. No history, no restating the code.
- Docstrings: a one-line summary, plus detail only when behavior is not obvious.
- Docs stay short. README is for users; this file is for durable rules.

## Layers

- Reusable logic goes under `src/jobintel/`. `app/dashboard.py`, `scripts/`, and `alembic/`
  are thin entrypoints.
- Behavior shared by the dashboard and a script goes in `etl/pipeline.py`.
- The dashboard's own query code is legacy; do not extend it.

## Source adapters

- New fetch paths go through the registry (`fetch_from_source()`), not an adapter directly.
  Known exception: `scripts/fetch_remotive.py`.
- Adapters own their rate limiting and retries; do not strip them.
- A failed or partial fetch must raise so the run is recorded as `failed`.

## Hashing and idempotency

- The raw content hash's key set and serialization are a persistent data format. Changing
  either re-inserts every stored row. Sources do not all hash the same way (Arbeitnow uses MD5).
- Raw dedup and `transform_jobs` dedup seeding are scoped to one environment. Keep them so.
- `_safe_date()` accepts what adapters emit (dates, datetimes, epochs, ISO strings with or
  without offset or `Z`, numeric epoch strings) and rejects `bool`.
- Parser fixes do not rewrite stored hashes. Old rows are fixed only by the explicit repair,
  `scripts/repair_posted_at.py`: one environment, one transaction, refuses to break a unique
  constraint, never runs `transform_jobs`.
- Transform is insert-only: the first version of a listing wins; edits stay in `raw_jobs`.

## Raw payloads

`raw_jobs.payload_json` is the adapter's normalized payload. Code never updates or deletes raw
rows (no constraint enforces this). Reprocess by re-running transform.

## Environment separation

- `raw_jobs`, `ingest_runs`, and `jobs` carry `environment`. `job_skills` must not; it
  inherits through `job_id`.
- `environment` is always set explicitly on all three tables: no Python, ORM, or database
  default (removed in `c4e81f6a2b57` and `2e3a197ea25f`). Fix the write path; never add one.
- `transform_jobs()` handles exactly one environment per call. Loop at the call site.
- Analytics filter `Job.environment` directly.
- Identity is `UNIQUE(environment, url)` and `UNIQUE(environment, hash)`. That a job's
  environment matches its raw row is held by `tests/test_environment_isolation.py`; do not
  weaken it.
- Joins to raw metadata must match environment and URL. Use `authoritative_raw_jobs()` and
  `job_raw_onclause()`. A job's raw metadata is its lowest-id raw row for that environment
  and URL (no `raw_job_id` exists).
- One database can hold several environments; they must stay distinguishable.

## Dashboard

- The production dashboard is public: no operator controls, no exception details, and it
  never changes the database structure.
- Every Analytics widget filters and charts by `job_date()` (posting date, else ingestion date).

## Database

- SQLite and Postgres must both work. Tests default to SQLite; `TEST_POSTGRES_URL` runs the
  shared fixtures and migration tests on Postgres (CI job `test-postgres`). Use the fixtures
  or the session's dialect; never hard-code one.
- Schema changes go through Alembic migrations, never `create_all()` or destructive DDL.
- Never point local runs or tests at the production `DATABASE_URL`.

## Secrets

`.env` is gitignored and never committed or pasted anywhere. `.env.example` holds placeholders
and stays in sync with settings. Production credentials live only in GitHub Actions and
Streamlit Cloud secrets. Redact connection strings before printing them.

## Scheduled ingestion

Weekly cadence is a deliberate cost limit (Neon free tier). Changing frequency or volume is a
maintainer decision.

## Tests

- Deterministic and offline. Mock the adapter's `requests.get`, or `fetch_from_source`.
- `tests/conftest.py` blocks non-local network access. Mock instead of disabling it.
- SQLAlchemy warnings fail the run (`pyproject.toml`).

## Change discipline

Smallest complete change. No drive-by reformatting, renames, or dependency bumps. Never weaken
a test, lint rule, or safety check to make something pass.
