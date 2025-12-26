# JobIntel

JobIntel is a job market intelligence pipeline that ingests job postings, cleans + deduplicates them, extracts skills, stores results in Postgres, exposes insights via FastAPI, and visualizes trends in a Streamlit dashboard.

## Status
In progress. Initial scaffolding + local Postgres + first ETL vertical slice coming next.

## Features (MVP)
- Ingest raw job postings into `raw_jobs`
- Clean/normalize/dedupe into `jobs`
- Skill extraction into `job_skills` (dictionary matching in v1)
- FastAPI endpoints:
  - `GET /jobs?skill=python&location=...&limit=50&offset=0`
  - `GET /skills/top?days=30&limit=50`
  - `GET /skills/trend?skill=python&days=90`
- Streamlit dashboard:
  - Top skills chart
  - Filters (role/location/date)
  - Jobs table

## Tech stack
Python, Postgres, FastAPI, Streamlit, Docker Compose, GitHub Actions

## Architecture
ETL (Python) -> Postgres -> FastAPI -> Streamlit

## Data model (draft)
- `raw_jobs(id, source, payload_json, ingested_at)`
- `jobs(id, title, company, location, url, posted_at, description, hash)`
- `job_skills(job_id, skill)`

## Quickstart (coming soon)
```bash
docker compose up -d
make etl
make api
make dashboard
```

## Local setup (coming next)
This repo will be runnable locally with Docker Compose:
- API docs at `http://localhost:8000/docs`
- Dashboard at `http://localhost:8501`

## Repo structure (planned)
```text
jobintel/
  src/jobintel/       # core package (etl + api + shared code)
    etl/              # extract/transform/load
    api/              # FastAPI app
  dashboard/          # Streamlit app
  db/                 # schema + migrations (optional)
  tests/              # unit/integration tests
  data/               # raw/processed (gitignored)
  scripts/            # helper scripts
  docs/               # screenshots, notes
  docker-compose.yml
  Makefile
  pyproject.toml
```
