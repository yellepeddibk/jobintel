# JobIntel

Job market intelligence platform: ingests postings from public job board APIs, normalizes them
through an ETL pipeline, extracts skills, and serves analytics through a Streamlit dashboard.

This file holds durable, non-obvious requirements only. Read the sources below for detail.

## Where to look

| Question | Source of truth |
|---|---|
| Product overview, user-facing docs | `README.md` |
| Python version, Ruff, pytest config | `pyproject.toml` |
| What CI actually runs | `.github/workflows/ci.yml` |
| Scheduled ingestion behavior | `.github/workflows/ingest-jobs.yml` |
| Schema | `src/jobintel/models.py`, `alembic/versions/` |
| Source adapter contract and registry | `src/jobintel/etl/sources/base.py`, `src/jobintel/etl/sources/registry.py` |
| Hashing and idempotency | `src/jobintel/etl/raw.py`, `src/jobintel/etl/transform.py` |
| Environment filtering | `src/jobintel/analytics/queries.py` |

`README.md` can drift. Where it disagrees with an implementation or configuration file, the
code wins. `db/schema.sql` is stale reference material and is not the source of truth.

## Validation

Run before every pull request. Both are CI gates; `ruff format` is not.

```bash
ruff check .
pytest
```

## Layer boundaries

New reusable domain, ETL, retrieval, analytics, and shared application logic belongs under
`src/jobintel/`. `app/dashboard.py`, `scripts/`, and `alembic/` are entrypoints: keep them thin
and do not add new shared business logic to them. The dashboard already carries query code of
its own; that is existing state, not a pattern to extend. Behavior shared between the dashboard
and a CLI script belongs in `etl/pipeline.py`.

## Source adapter boundary

Job boards are integrated as registered adapters behind the `JobSource` protocol, with
`fetch_from_source()` as the registry-driven path that fetches and validates. Preserve that
boundary: new fetch paths go through the registry, not an adapter directly. Adapters own their
rate limiting and retry behavior; do not strip it to speed up a run. Known exception:
`scripts/fetch_remotive.py` calls its adapter directly, bypassing validation.

## Hashing and idempotency

The content hash is what makes raw ingestion idempotent. Its input key set and its
serialization are persistent data-format behavior, not implementation detail: changing either
invalidates every stored hash and causes mass re-insertion of rows already ingested. The same
care applies to the dedup seeding in `transform_jobs`, which makes re-runs idempotent across
runs and not only within one. Sources do not all hash identically today; do not assume so.

## Raw payloads

`raw_jobs.payload_json` holds the canonical ingested payload: the adapter's normalized form of
the upstream response, not the untouched API body. Application code does not update or delete
raw rows after ingestion, and no database constraint enforces that. Preserve the behavior:
reprocess by re-running transform, not by rewriting stored raw rows.

## Environment separation

**Environment separation happens at query time, not end to end.** This is the easiest
invariant here to misread:

- `raw_jobs` and `ingest_runs` carry an `environment` column.
- `jobs` and `job_skills` do **not**.
- `transform_jobs()` processes all raw rows regardless of tag, so `jobs` is mixed-environment
  by construction.
- Only the dashboard data-query functions in `analytics/queries.py` separate environments,
  through an inner join to `raw_jobs` filtered on `environment`.

Do not treat `jobs` as production-only or assume a `jobs` row came from a production ingest, do
not weaken or drop that join, and add environment awareness below the analytics layer rather
than assuming it.

## Database

- **SQLite and Postgres must both work.** Tests run on SQLite, production is Postgres.
  Anything dialect-specific needs both branches and test coverage for both.
- Schema changes go through an Alembic migration, never destructive DDL and never
  `create_all()`. Follow the style of the existing revisions.
- Never point local development or a test run at the production `DATABASE_URL`. Several scripts
  write to whatever `DATABASE_URL` is configured; confirm the target before running one.

## Secrets

`.env` holds real local credentials, is gitignored, and is never committed or pasted into code,
tests, logs, or pull request text. `.env.example` is tracked, carries placeholders only, and
stays in sync when a setting is added. Production credentials live only in GitHub Actions and
Streamlit Cloud secrets. Redact connection strings before printing or logging them.

## Scheduled ingestion

Production ingestion runs weekly. **The cadence is an intentional cost constraint**, set to
stay inside the Neon free-tier transfer limit. Increasing ingestion frequency or data volume
is a maintainer decision, not an implementation detail.

## Tests

- New tests must be deterministic and must not depend on live external APIs. Mock at the
  adapter's `requests.get`, or at `fetch_from_source` for pipeline tests.
- Known exception: one legacy test in `tests/test_sources_registry.py` calls source adapters
  unmocked and makes real HTTP requests on every CI run. Do not copy the pattern.

## Change discipline

Smallest complete change. No drive-by reformatting, renaming, or dependency bumps. Never
weaken a test, a lint rule, or a safety check to make something pass.
