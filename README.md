# JobIntel

JobIntel is a job market intelligence pipeline that ingests job postings, cleans + deduplicates them, extracts skills, stores results in Postgres, exposes insights via FastAPI, and visualizes trends in a Streamlit dashboard.

## Features (MVP)
- Ingest raw job postings into `raw_jobs`
- Clean/normalize/dedupe into `jobs`
- Skill extraction into `job_skills` (dictionary matching in v1)
- FastAPI endpoints:
  - `GET /jobs?skill=python&location=...`
  - `GET /skills/top?days=30`
  - `GET /skills/trend?skill=python&days=90`
- Streamlit dashboard:
  - Top skills chart
  - Filters (role/location/date)
  - Jobs table

## Tech stack
Python, Postgres, FastAPI, Streamlit, Docker Compose, GitHub Actions

## Architecture
ETL (Python) -> Postgres -> FastAPI -> Streamlit

## Local setup (coming next)
This repo will be runnable locally with Docker Compose:
- API docs at `http://localhost:8000/docs`
- Dashboard at `http://localhost:8501`

## Repo structure (planned)
```text
jobintel/
  etl/         # extract/transform/load + tests
  api/         # FastAPI + tests
  dashboard/   # Streamlit app
  db/          # schema + migrations (optional)
  data/        # raw/processed (gitignored)
  scripts/     # helper scripts
  docs/        # screenshots, notes
  docker-compose.yml
  Makefile
```
