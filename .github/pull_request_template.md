## Summary

<!-- What changed, and why it matters. -->

## Motivation

<!-- The problem this solves. Link an issue if one exists. -->

## Invariants touched

Tick only what this pull request actually affects, and explain each ticked item below.

- [ ] Source adapter boundary (`JobSource` protocol, registry, `fetch_from_source`)
- [ ] Content hashing or deduplication (`compute_content_hash`, `upsert_raw_job`, `transform_jobs`)
- [ ] Raw payload semantics (`raw_jobs` is append-only)
- [ ] SQLAlchemy models or Alembic migrations
- [ ] Environment tagging or analytics environment filtering
- [ ] Secrets handling (`.env`, `.env.example`, Streamlit or Actions secrets, `redact_db_url`)
- [ ] Scheduled ingestion workflow or its cadence
- [ ] None of the above

<!-- For each ticked box, say what changed and why the invariant still holds. -->

## Validation

Tick only commands that were actually run, and paste the real result.

- [ ] `ruff check .`
- [ ] `pytest`

<!--
Both are CI gates on Python 3.11. Paste the pass/fail summary line.
Note any check that was skipped, and say so in one line.
-->

## Database and deployment impact

- [ ] Adds or changes an Alembic migration
- [ ] Requires a migration run against the production database
- [ ] Changes the ingestion schedule or data volume (Neon free-tier transfer applies)
- [ ] No database or deployment impact

<!-- If a migration is included, confirm the downgrade path was considered. -->

## Notes for reviewers

<!-- Anything worth knowing that is not obvious from the diff. -->
