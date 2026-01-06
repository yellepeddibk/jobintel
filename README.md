# JobIntel

Job market intelligence pipeline that ingests job postings, normalizes them, extracts skills, stores in Postgres, and visualizes in a Streamlit dashboard.

## Status
✅ **Working** - Dashboard renders with error handling, Remotive ingestion, and filtering.

## Features
- ✅ Remotive API integration
- ✅ ETL pipeline: raw jobs → normalized → skill extraction
- ✅ Hash-based deduplication
- ✅ Dictionary-based skill extraction
- ✅ Streamlit dashboard with filters (keyword, source, skills)
- ✅ DB connection validation with clear error messages
- ✅ Clickable job links

## Tech Stack
Python, Postgres, SQLAlchemy, Docker Compose, Streamlit

## Architecture
ETL (Python) → Postgres → Streamlit

## Data Model
- `raw_jobs` - Raw job payloads from sources
- `jobs` - Normalized jobs (title, company, location, url, etc.)
- `job_skills` - Extracted skills linked to jobs

## Quickstart
```bash
# 1. Start Postgres
docker-compose up -d

# 2. Start dashboard
streamlit run app/dashboard.py

# 3. Open browser at http://localhost:8501
# 4. Click "Run ingest" to fetch jobs
# 5. Use filters to explore!
```

## Setup
1. **Prerequisites**: Docker, Python 3.11+
2. **Install**: `pip install -r requirements.txt`
3. **Database**: `docker-compose up -d`
4. **Dashboard**: `streamlit run app/dashboard.py`

## Repo Structure
```
jobintel/
  src/jobintel/          # Core package
    etl/                 # Extract/transform/load
    analytics/           # Analytics queries
    models.py            # SQLAlchemy models
    db.py                # Database setup
  app/
    dashboard.py         # Streamlit UI
  db/
    schema.sql           # Database schema
  docker-compose.yml     # Postgres container
```
