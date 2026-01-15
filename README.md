# JobIntel

A production-grade job market intelligence platform that aggregates job postings from multiple sources, normalizes data through a robust ETL pipeline, extracts skill requirements using NLP techniques, and provides real-time analytics through an interactive dashboard.

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
- Rate limiting and retry logic for API reliability

### Real-Time Dashboard
- Interactive job browser with filtering by source, skills, location, and date
- Top skills visualization with configurable limits
- Skill trend charts showing demand over time
- Ingest run monitoring for operational visibility

### Environment Isolation
- Separation of development, test, and production data
- Sample data blocked from production environment
- Dashboard displays only production-tagged records

### Data Quality
- Content-hash based deduplication prevents duplicate ingestion
- URL-based job deduplication ensures unique listings
- Validation warnings surfaced in ingest run logs

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| Database | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 with DeclarativeBase |
| Migrations | Alembic |
| Web Framework | Streamlit |
| HTTP Client | Requests |
| Configuration | Pydantic Settings |
| Testing | Pytest |
| Linting | Ruff |
| Containerization | Docker Compose |

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

1. Navigate to the dashboard
2. Select a source from the dropdown (Remotive, RemoteOK, Arbeitnow)
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
| `development` | Default for local development. Sample ETL allowed. |
| `test` | For running tests. Sample ETL allowed. |
| `production` | For deployment. Sample ETL blocked. Dashboard shows only production data. |

### Example `.env` File

```bash
DATABASE_URL=postgresql+psycopg://jobintel:password@localhost:5433/jobintel
ENV=production
```

---

## Project Structure

```
jobintel/
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
│   └── run_live_etl.py         # CLI ETL runner
├── src/jobintel/
│   ├── analytics/
│   │   ├── queries.py          # Dashboard query functions
│   │   └── top_skills.py       # Skill aggregation logic
│   ├── core/
│   │   └── config.py           # Application settings
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
├── tests/                      # Test suite
├── docker-compose.yml          # PostgreSQL container
├── pyproject.toml              # Project metadata
├── requirements.txt            # Production dependencies
└── requirements-dev.txt        # Development dependencies
```

---

## Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_etl_pipeline.py

# Run with coverage
pytest --cov=src/jobintel
```

### Test Categories

| Category | Description |
|----------|-------------|
| `test_analytics_*.py` | Query function tests |
| `test_etl_*.py` | ETL pipeline component tests |
| `test_sources_*.py` | Source adapter tests |
| `test_environment_filtering.py` | Environment isolation guardrail tests |
| `test_ingest_runs.py` | Pipeline observability tests |

---

## Deployment

### Streamlit Cloud

1. Push repository to GitHub
2. Connect repository to Streamlit Cloud
3. Set secrets in Streamlit Cloud dashboard:
   ```
   DATABASE_URL = "postgresql+psycopg://..."
   ENV = "production"
   ```
4. Set main file path: `app/dashboard.py`
5. Deploy

### Database Migrations

```bash
# Apply migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# View migration history
alembic history
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
