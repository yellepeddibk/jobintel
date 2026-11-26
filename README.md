# JobIntel

[![CI](https://github.com/yellepeddibk/jobintel/actions/workflows/ci.yml/badge.svg)](https://github.com/yellepeddibk/jobintel/actions/workflows/ci.yml)
[![Scheduled Ingestion](https://github.com/yellepeddibk/jobintel/actions/workflows/ingest-jobs.yml/badge.svg)](https://github.com/yellepeddibk/jobintel/actions/workflows/ingest-jobs.yml)

A job market analytics project that ingests postings from three public job board APIs, normalizes them through an ETL pipeline, tags skills with a keyword dictionary, and serves analytics through a Streamlit dashboard. Production ingestion runs on a weekly schedule.

**[🚀 Live Dashboard](https://jobintel-main.streamlit.app/)**

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [ETL Pipeline](#etl-pipeline)
- [Data Model](#data-model)
- [Features](#features)
- [Technology Stack](#technology-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

JobIntel provides a unified view of job postings collected from several job boards. The platform:

- Collects postings from Remotive and RemoteOK (remote-job boards) and Arbeitnow (a general job board with mostly German listings; the adapter keeps on-site and remote roles alike)
- Normalizes heterogeneous payloads into a consistent schema
- Tags postings with skills from a fixed keyword dictionary
- Provides analytics and trend charts over the ingested data
- Skips duplicate postings and drops payloads missing required fields

---

## Architecture

```
                                    JobIntel Architecture
    
    +------------------+     +------------------+     +------------------+
    |   Job Sources    |     |   ETL Pipeline   |     |   Data Layer     |
    +------------------+     +------------------+     +------------------+
    |                  |     |                  |     |                  |
    |  - Remotive API  |---->|  1. Fetch        |---->|  PostgreSQL DB   |
    |  - RemoteOK API  |     |  2. Normalize    |     |                  |
    |  - Arbeitnow API |     |  3. Deduplicate  |     |  - raw_jobs      |
    |                  |     |  4. Transform    |     |  - jobs          |
    +------------------+     |  5. Extract      |     |  - job_skills    |
                             |     Skills       |     |  - ingest_runs   |
                             |  6. Load         |     |                  |
                             +------------------+     +------------------+
                                                              |
                                                              v
                             +------------------+     +------------------+
                             |   Presentation   |<----|   Analytics      |
                             +------------------+     +------------------+
                             |                  |     |                  |
                             |  Streamlit       |     |  - KPI Queries   |
                             |  Dashboard       |     |  - Skill Trends  |
                             |                  |     |  - Aggregations  |
                             |  - Analytics Tab |     |                  |
                             |  - Jobs Tab      |     +------------------+
                             |  - Ingest Runs   |
                             +------------------+
```

---

## ETL Pipeline

The ETL pipeline is the core of JobIntel, designed for reliability, idempotency, and observability.

### Pipeline Flow

```
    +-------------+     +---------------+     +----------------+     +---------------+
    |   EXTRACT   |---->|   TRANSFORM   |---->|   LOAD         |---->|   ENRICH      |
    +-------------+     +---------------+     +----------------+     +---------------+
    |             |     |               |     |                |     |               |
    | Fetch from  |     | Normalize     |     | Insert new     |     | Extract       |
    | source APIs |     | payload to    |     | raw_jobs with  |     | skills from   |
    |             |     | canonical     |     | content hash   |     | descriptions  |
    | Validate    |     | schema        |     | deduplication  |     |               |
    | responses   |     |               |     |                |     | Link skills   |
    |             |     | Parse dates,  |     | Transform to   |     | to jobs       |
    | Handle      |     | clean HTML,   |     | normalized     |     |               |
    | pagination  |     | extract       |     | jobs table     |     | Commit to     |
    |             |     | metadata      |     |                |     | job_skills    |
    +-------------+     +---------------+     +----------------+     +---------------+
```

### Pipeline Components

| Component | Description | Location |
|-----------|-------------|----------|
| Source Registry | Pluggable source adapters with common interface | `src/jobintel/etl/sources/` |
| Raw Ingestion | Insert-if-new raw rows, keyed on content hash and URL within one environment | `src/jobintel/etl/raw.py` |
| Transformation | Insert-only normalization, deduplicated by URL and normalized hash within one environment | `src/jobintel/etl/transform.py` |
| Skill Extraction | Regex matching against a fixed dictionary of 10 skills | `src/jobintel/etl/skills.py` |
| Pipeline Orchestration | Shared ingest runner for the CLI, the scheduled workflow, and the dashboard; records each run | `src/jobintel/etl/pipeline.py` |
| Retrieval | Deterministic, explainable job ranking with per-component score breakdowns. Library code with tests; not yet called by the dashboard or any API | `src/jobintel/retrieval/` |

### Idempotency and Deduplication

Deduplication prevents duplicate inserts. It does not update listings that already exist.

1. **Raw ingestion** (`upsert_raw_job`): a payload is skipped when a `raw_jobs` row in the same environment already has the same content hash (and the same URL, when the payload has one). Remotive and RemoteOK payloads are hashed with SHA-256 over a fixed set of payload fields; the Arbeitnow adapter supplies its own MD5 hash of title, company, and description. A payload whose content has changed gets a new hash and is stored as an additional raw row.

2. **Normalized jobs** (`transform_jobs`): a raw row becomes a `jobs` row only if no job in the same environment already has its URL or its normalized hash (SHA-256 of the trimmed, lowercased title, company, and location plus the posted date). The database enforces the same identity with `UNIQUE(environment, url)` and `UNIQUE(environment, hash)`.

3. **Re-runs**: running the pipeline again does not create duplicate rows. Because transformation is insert-only, an existing job keeps the content of the first raw row it was normalized from; later changes to a listing are kept in `raw_jobs` but not applied to `jobs`.

### Observability

Each pipeline run is tracked in the `ingest_runs` table with:

- Source, search query, and limit parameters
- Start and finish timestamps
- Status (running, success, failed)
- Counts: fetched, inserted_raw, inserted_jobs, inserted_skills
- Warnings and error messages

A run is marked `failed` when the source adapter raises. The Arbeitnow adapter currently logs request errors, exhausted rate-limit retries, and invalid JSON and stops paginating instead of raising, so a failed or partial Arbeitnow fetch is recorded as `success` with a lower `fetched` count.

---

## Data Model

```
    +------------------+          +------------------------------+     +------------------+
    |    raw_jobs      |          |            jobs              |     |   job_skills     |
    +------------------+          +------------------------------+     +------------------+
    | id (PK)          |          | id (PK)                      |     | job_id (PK, FK)  |
    | source           |   N:1    | environment                  | 1:N | skill (PK)       |
    | payload_json     |--------->| title                        |<----|                  |
    | ingested_at      | (env +   | company                      |     +------------------+
    | environment      |   url)   | location                     |
    +------------------+          | url                          |     job_skills has no
                                  | posted_at                    |     environment column:
    +------------------+          | description                  |     it derives one
    |   ingest_runs    |          | hash                         |     through job_id.
    +------------------+          +------------------------------+
    | id (PK)          |          | UNIQUE (environment, url)    |
    | source           |          | UNIQUE (environment, hash)   |
    | search           |          +------------------------------+
    | limit            |
    | environment      |
    | status           |
    | started_at       |
    | finished_at      |
    | fetched          |
    | inserted_raw     |
    | inserted_jobs    |
    | inserted_skills  |
    | warnings         |
    | error            |
    +------------------+
```

### Table Descriptions

| Table | Purpose |
|-------|---------|
| `raw_jobs` | Append-only store of each adapter's normalized payload (not the untouched API response). Application code never updates or deletes these rows; no database constraint enforces that |
| `jobs` | Normalized job postings, deduplicated **within an environment** and carrying the environment they were ingested under |
| `job_skills` | Many-to-many relationship between jobs and extracted skills |
| `ingest_runs` | One row per ingest run, from the CLI, the scheduled workflow, or the dashboard |

---

## Features

### Multi-Source Ingestion
- Pluggable source adapters behind a common interface and registry
- Currently integrated: Remotive, RemoteOK, Arbeitnow
- Scheduled ingestion via GitHub Actions once a week (Sundays at 00:00 UTC), plus manual runs from the Actions tab
- Arbeitnow requests are paced and retried on HTTP 429; Remotive and RemoteOK make a single request with a timeout

### Dashboard
- Job browser with filtering by keyword, source, skills, location, and date
- Top skills chart and table with configurable limits
- Skill trend chart grouped into 6-hour, daily, or weekly buckets by ingestion time. These are chart groupings, not refresh intervals; data changes only when an ingest runs
- Skills by source comparison chart
- Ingest run history with failure and warning details

### Hosted Deployment
- Neon PostgreSQL as the production database
- Streamlit Community Cloud for dashboard hosting
- GitHub Actions for scheduled ingestion
- Environment tagging (development, test, production) on every raw row, job, and ingest run

### Data Quality
- Content-hash and URL checks skip payloads already ingested in the same environment
- URL and normalized-hash job identity is scoped per environment: unique listings within an environment, and development data can never merge into production
- Payloads missing a source, URL, or title are dropped and reported as warnings on the ingest run
- Re-runs do not create duplicate rows; existing jobs are not updated when a listing changes

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| Database | PostgreSQL (Neon in production, Docker Compose locally); SQLite by default for local runs and tests |
| ORM | SQLAlchemy 2.0 with DeclarativeBase |
| Migrations | Alembic |
| Dashboard | Streamlit (Streamlit Cloud in production) |
| Visualization | Plotly, Pandas |
| HTTP Client | Requests |
| Configuration | Pydantic Settings |
| CI/CD | GitHub Actions |
| Testing | Pytest |
| Linting | Ruff |
| Containerization | Docker Compose (local development) |

---

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Docker and Docker Compose (optional, for local PostgreSQL)
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/yellepeddibk/jobintel.git
cd jobintel

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies (requirements.txt also installs jobintel in editable mode)
pip install -r requirements.txt

# Test and lint tools
pip install -r requirements-dev.txt
```

### Database Setup

SQLite (`sqlite:///./jobintel.db`) is the default and needs no setup. To use PostgreSQL locally instead:

```bash
# Start PostgreSQL container
docker-compose up -d

# Verify database is running
docker-compose ps

# Point DATABASE_URL at the container (port 5433)
cp .env.example .env
```

The dashboard and the ingestion scripts write to whatever `DATABASE_URL` is configured. Check it before running them.

### Running the Application

```bash
# Start the dashboard
streamlit run app/dashboard.py

# Open browser at http://localhost:8501
```

### Ingesting Jobs

**Scheduled (Production):**
- GitHub Actions runs once a week (Sundays at 00:00 UTC) and can also be triggered manually
- Each run applies migrations, then ingests Remotive, Arbeitnow, and RemoteOK in turn (up to 200 postings per source). If a source raises, the remaining sources are skipped for that run

**Manual (CLI):**
```bash
# Ingest from all sources (default)
python scripts/run_live_etl.py

# Ingest from specific source
python scripts/run_live_etl.py --source remotive

# With search filter
python scripts/run_live_etl.py --search "python" --source arbeitnow
```

**Manual (Dashboard, outside production):**

The ingest form, cache control, and debug panel appear only when `ENV` is not `production`, so the hosted dashboard does not offer them.

1. Open the "Ingest jobs" panel in the sidebar
2. Enter a search query and limit, and select a source
3. Click "Run ingest" to fetch and process jobs
4. View results in the Jobs and Ingest Runs tabs

---

## Configuration

Configuration is managed through environment variables, with support for `.env` files.

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `sqlite:///./jobintel.db` |
| `ENV` | Environment mode: `development`, `test`, `production` | `development` |

### Environment Modes

| Mode | Behavior |
|------|----------|
| `development` | Default for local development. Uses local SQLite or Docker Postgres. |
| `test` | Set by the test suite, which runs against in-memory or temporary SQLite databases. |
| `production` | Used by the scheduled workflow and the hosted dashboard. |

In every mode, the pipeline writes rows tagged with the current `ENV`, and the dashboard shows only rows tagged with it.

### Example `.env` File

```bash
DATABASE_URL=postgresql+psycopg://jobintel:password@localhost:5433/jobintel
ENV=development
```

---

## Project Structure

```
jobintel/
├── .github/
│   └── workflows/
│       ├── ci.yml              # CI pipeline (lint + test)
│       └── ingest-jobs.yml     # Scheduled ingestion (weekly, plus manual runs)
├── alembic/                    # Database migrations
│   ├── versions/               # Migration scripts
│   └── env.py                  # Alembic configuration
├── app/
│   └── dashboard.py            # Streamlit dashboard application
├── db/
│   └── schema.sql              # Reference SQL schema
├── scripts/
│   ├── check_prod_data.py      # Production data validation
│   ├── fetch_remotive.py       # Standalone fetch script
│   ├── init_db.py              # Database initialization
│   ├── migrate_db.py           # Run Alembic migrations
│   ├── repair_posted_at.py     # One-environment posted_at and job hash repair
│   ├── report_top_skills.py    # CLI skill report
│   └── run_live_etl.py         # CLI ETL runner (ingests all sources by default)
├── src/jobintel/
│   ├── analytics/
│   │   ├── queries.py          # Dashboard query functions (KPIs, trends, bucketing)
│   │   └── top_skills.py       # Skill aggregation logic
│   ├── core/
│   │   └── config.py           # Application settings (Streamlit secrets support)
│   ├── etl/
│   │   ├── sources/            # Source adapters
│   │   │   ├── arbeitnow.py
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   ├── remoteok.py
│   │   │   └── remotive.py
│   │   ├── load_raw.py         # JSONL file loader
│   │   ├── pipeline.py         # Orchestration layer
│   │   ├── raw.py              # Raw job ingestion
│   │   ├── repair.py           # posted_at and job hash data repair
│   │   ├── skills.py           # Skill extraction
│   │   └── transform.py        # Job normalization
│   ├── retrieval/              # Deterministic job ranking (not yet used by the dashboard)
│   ├── db.py                   # Database setup
│   └── models.py               # SQLAlchemy models
├── tests/
│   ├── conftest.py             # Pytest fixtures and configuration
│   ├── fixtures.py             # Test data fixtures
│   ├── test_analytics_*.py     # Analytics query tests
│   ├── test_bucket_expr.py     # Time bucket SQL tests
│   ├── test_etl_*.py           # ETL pipeline tests
│   ├── test_sources_*.py       # Source adapter tests
│   └── ...                     # Additional test modules
├── docker-compose.yml          # PostgreSQL container (local dev)
├── pyproject.toml              # Project metadata
├── requirements.txt            # Production dependencies
└── requirements-dev.txt        # Development dependencies
```

---

## Testing

Tests run against in-memory or temporary SQLite databases, and source adapter tests mock HTTP, with one exception: `tests/test_sources_registry.py::test_source_fetch_signature` calls the live job board APIs.

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_etl_transform.py

# Run without network access
pytest --deselect tests/test_sources_registry.py::test_source_fetch_signature
```

### Test Categories

| Category | Description |
|----------|-------------|
| `test_analytics_*.py` | Query functions, KPIs, skill aggregation |
| `test_bucket_expr.py` | Time bucket SQL for Postgres and SQLite |
| `test_etl_*.py` | ETL pipeline component tests |
| `test_sources_*.py` | Source adapter tests |
| `test_environment_filtering.py`, `test_environment_isolation.py` | Environment isolation guardrail tests |
| `test_ingest_runs.py` | Pipeline observability tests |
| `test_db_init.py` | Database initialization tests |
| `test_migrations.py` | Alembic upgrade, downgrade, and round-trip tests |
| `test_repair_posted_at.py` | posted_at and job hash repair tests |
| `test_retrieval_*.py` | Deterministic ranking and retrieval tests |

---

## Deployment

### Production Architecture

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   Streamlit Cloud   │────▶│   Neon PostgreSQL    │◀────│   GitHub Actions    │
│   (Dashboard)       │     │   (Database)         │     │   (Ingestion)       │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
        ▲                                                         │
        │                                                         │
    Users view                                              Weekly/manual:
    analytics                                               - Fetch from APIs
                                                            - Transform & load
                                                            - Extract skills
```

### 1. Database Setup (Neon)

1. Create a free account at [neon.tech](https://neon.tech)
2. Create a new project (e.g., `jobintel`)
3. Copy the connection string
4. Initialize tables:
   ```bash
   export DATABASE_URL="postgresql+psycopg://user:pass@host/db?sslmode=require"
   export ENV="production"
   python -c "from jobintel.db import init_db; init_db()"
   alembic stamp head
   ```
   `alembic upgrade head` cannot build the schema on an empty database, because the first revision expects the tables to exist already. `init_db()` falls back to creating the tables directly, and `alembic stamp head` then records them as current.

### 2. Dashboard Hosting (Streamlit Cloud)

1. Push repository to GitHub
2. Connect repository to [Streamlit Cloud](https://share.streamlit.io)
3. Set secrets in Streamlit Cloud dashboard:
   ```toml
   DATABASE_URL = "postgresql+psycopg://user:pass@host/db?sslmode=require"
   ENV = "production"
   ```
4. Set main file path: `app/dashboard.py`
5. Deploy

### 3. Automated Ingestion (GitHub Actions)

1. Add repository secret `DATABASE_URL` with your Neon connection string
2. The workflow runs automatically once a week (Sundays at 00:00 UTC)
3. Manual trigger available in Actions tab → "Ingest Jobs" → "Run workflow"

### Database Migrations

```bash
# Apply migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# View migration history
alembic history
```

### Local Development

```bash
# Start local PostgreSQL
docker-compose up -d

# Or use SQLite (default)
streamlit run app/dashboard.py
```

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/description`)
3. Make changes and add tests
4. Run linting (`ruff check .`)
5. Run tests (`pytest`)
6. Commit changes (`git commit -m "Add feature"`)
7. Push to branch (`git push origin feature/description`)
8. Open a Pull Request

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
