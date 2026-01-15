# JobIntel

[![CI](https://github.com/yellepeddibk/jobintel/actions/workflows/ci.yml/badge.svg)](https://github.com/yellepeddibk/jobintel/actions/workflows/ci.yml)
[![Scheduled Ingestion](https://github.com/yellepeddibk/jobintel/actions/workflows/ingest-jobs.yml/badge.svg)](https://github.com/yellepeddibk/jobintel/actions/workflows/ingest-jobs.yml)

A production-grade job market intelligence platform that aggregates job postings from multiple sources, normalizes data through a robust ETL pipeline, extracts skill requirements using NLP techniques, and provides real-time analytics through an interactive dashboard.

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

JobIntel solves the challenge of understanding job market trends by providing a unified view of job postings across multiple job boards. The platform:

- Aggregates job postings from multiple sources (Remotive, RemoteOK, Arbeitnow)
- Normalizes heterogeneous data into a consistent schema
- Extracts skills and technologies mentioned in job descriptions
- Provides real-time analytics and trend visualization
- Maintains data quality through deduplication and validation

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
                             |  - Jobs Tab      |     |                  |
                             |  - Analytics Tab |     +------------------+
                             |  - Ingest Tab    |
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
    | Fetch from  |     | Normalize     |     | Upsert to      |     | Extract       |
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
| Raw Ingestion | Content-hash based idempotent upserts | `src/jobintel/etl/raw.py` |
| Transformation | URL-based deduplication, schema normalization | `src/jobintel/etl/transform.py` |
| Skill Extraction | Dictionary-based NLP skill extraction | `src/jobintel/etl/skills.py` |
| Pipeline Orchestration | Centralized ETL runner with observability | `src/jobintel/etl/pipeline.py` |

### Idempotency and Deduplication

The pipeline ensures data quality through multiple deduplication strategies:

1. **Content Hash Deduplication**: Each raw job payload is hashed using SHA-256. Duplicate payloads are rejected at ingestion time.

2. **URL-Based Deduplication**: Normalized jobs are deduplicated by URL to prevent the same job from appearing multiple times.

3. **Idempotent Operations**: All pipeline operations can be safely re-run without creating duplicate data.

### Observability

Each pipeline run is tracked in the `ingest_runs` table with:

- Source, search query, and limit parameters
- Start and finish timestamps
- Status (running, success, failed)
- Counts: fetched, inserted_raw, inserted_jobs, inserted_skills
- Warnings and error messages

---

## Data Model

```
    +------------------+          +------------------+          +------------------+
    |    raw_jobs      |          |      jobs        |          |   job_skills     |
    +------------------+          +------------------+          +------------------+
    | id (PK)          |          | id (PK)          |          | job_id (PK, FK)  |
    | source           |    1:1   | title            |    1:N   | skill (PK)       |
    | payload_json     |--------->| company          |<---------|                  |
    | ingested_at      |  (url)   | location         |          +------------------+
    | environment      |          | url (unique)     |
    +------------------+          | posted_at        |
                                  | description      |
    +------------------+          | hash (unique)    |
    |   ingest_runs    |          +------------------+
    +------------------+
    | id (PK)          |
    | source           |
    | search           |
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
| `raw_jobs` | Immutable store of original job payloads from sources |
| `jobs` | Normalized, deduplicated job postings |
| `job_skills` | Many-to-many relationship between jobs and extracted skills |
| `ingest_runs` | Audit log of all pipeline executions |

---

## Features

### Multi-Source Ingestion
- Pluggable source architecture supporting multiple job boards
- Currently integrated: Remotive, RemoteOK, Arbeitnow
- Automated ingestion via GitHub Actions (every 6 hours)
- Rate limiting and retry logic for API reliability

### Real-Time Dashboard
- Interactive job browser with filtering by source, skills, location, and date
- Top skills visualization with configurable limits
- Time-bucketed skill trends with granularity selector (6-hour, daily, weekly)
- Auto-detection of optimal granularity based on date range
- Skills by source comparison charts
- Ingest run monitoring for operational visibility

### Production Infrastructure
- Neon PostgreSQL for serverless production database
- Streamlit Cloud for zero-ops dashboard hosting
- GitHub Actions for scheduled data collection
- Environment isolation (development, test, production)

### Data Quality
- Content-hash based deduplication prevents duplicate ingestion
- URL-based job deduplication ensures unique listings
- Validation warnings surfaced in ingest run logs
- Idempotent pipeline operations safe to re-run

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| Database | PostgreSQL 15+ (Neon serverless in production) |
| ORM | SQLAlchemy 2.0 with DeclarativeBase |
| Migrations | Alembic |
| Dashboard | Streamlit (Streamlit Cloud in production) |
| Visualization | Plotly, Pandas |
| HTTP Client | Requests |
| Configuration | Pydantic Settings |
| CI/CD | GitHub Actions |
| Testing | Pytest (48 tests) |
| Linting | Ruff |
| Containerization | Docker Compose (local development) |

---

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Docker and Docker Compose
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/yellepeddibk/jobintel.git
cd jobintel

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -e .
```

### Database Setup

```bash
# Start PostgreSQL container
docker-compose up -d

# Verify database is running
docker-compose ps
```

### Running the Application

```bash
# Start the dashboard
streamlit run app/dashboard.py

# Open browser at http://localhost:8501
```

### Ingesting Jobs

**Automated (Production):**
- GitHub Actions runs every 6 hours
- Ingests from all 3 sources automatically (Remotive, RemoteOK, Arbeitnow)

**Manual (CLI):**
```bash
# Ingest from all sources (default)
python scripts/run_live_etl.py

# Ingest from specific source
python scripts/run_live_etl.py --source remotive

# With search filter
python scripts/run_live_etl.py --search "python" --source arbeitnow
```

**Manual (Dashboard):**
1. Navigate to the "Ingest" tab
2. Select a source from the dropdown
3. Optionally enter a search query
4. Click "Run Ingest" to fetch and process jobs
5. View results in the Jobs tab

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
| `test` | For running tests. Isolated test database with fixtures. |
| `production` | For deployment. Dashboard shows only production-tagged records. |

### Example `.env` File

```bash
DATABASE_URL=postgresql+psycopg://jobintel:password@localhost:5433/jobintel
ENV=production
```

---

## Project Structure

```
jobintel/
├── .github/
│   └── workflows/
│       ├── ci.yml              # CI pipeline (lint + test)
│       └── ingest-jobs.yml     # Scheduled ingestion (every 6h)
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
│   │   ├── skills.py           # Skill extraction
│   │   └── transform.py        # Job normalization
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

The project maintains **48 tests** covering all core functionality.

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_etl_transform.py

# Run with coverage
pytest --cov=src/jobintel
```

### Test Categories

| Category | Description |
|----------|-------------|
| `test_analytics_*.py` | Query functions, KPIs, skill aggregation |
| `test_bucket_expr.py` | Time bucket SQL for Postgres and SQLite |
| `test_etl_*.py` | ETL pipeline component tests |
| `test_sources_*.py` | Source adapter tests |
| `test_environment_filtering.py` | Environment isolation guardrail tests |
| `test_ingest_runs.py` | Pipeline observability tests |
| `test_db_init.py` | Database initialization tests |

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
    Users view                                              Every 6 hours:
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
2. The workflow runs automatically every 6 hours
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
4. Run linting (`ruff check . && ruff format .`)
5. Run tests (`pytest`)
6. Commit changes (`git commit -m "Add feature"`)
7. Push to branch (`git push origin feature/description`)
8. Open a Pull Request

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
