import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import func, or_, select

from jobintel.analytics.queries import (
    get_kpis,
    get_skill_trends,
    get_top_skills,
    get_top_skills_by_source,
)
from jobintel.core.config import settings
from jobintel.db import SessionLocal, init_db
from jobintel.etl.pipeline import run_ingest
from jobintel.etl.sources.registry import list_sources
from jobintel.models import IngestRun, Job, JobSkill, RawJob

# Use production environment filter in prod, else use current ENV setting
DATA_ENV = "production" if settings.is_production else settings.ENV

st.set_page_config(page_title="JobIntel Dashboard", layout="wide")
st.title("JobIntel Dashboard")
st.caption("Live ingestion → normalize → extract skills → analytics")

# Debug: show exactly what config the dashboard is using
with st.sidebar.expander("🔧 Debug Config", expanded=False):
    st.write("**settings.ENV:**", settings.ENV)
    st.write("**DATA_ENV:**", DATA_ENV)
    db_url = settings.DATABASE_URL
    # Mask password in display
    safe_db = db_url
    if "@" in db_url:
        # Mask everything between : and @ after //
        import re as _re
        safe_db = _re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", db_url)
    st.write("**DATABASE_URL:**", safe_db)

# Ensure tables exist (and surface errors in the UI if DB is unreachable)
try:
    init_db()
    # Test DB connection immediately
    with SessionLocal() as test_session:
        test_session.execute(select(1))
    st.success("✓ Database connected")
except Exception as e:
    st.error(
        "❌ Database connection failed. Check DATABASE_URL and ensure "
        "Postgres is running on port 5433."
    )
    st.exception(e)
    st.info(
        "Expected format: postgresql+psycopg://jobintel:jobintel_dev_password@127.0.0.1:5433/jobintel"
    )
    st.stop()


def strip_html_tags(text: str) -> str:
    """Remove HTML tags from text for better readability."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", text)
    # Replace multiple whitespace with single space
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


@st.cache_data(ttl=30)
def get_sources(environment: str) -> list[str]:
    """Get list of sources from specified environment."""
    try:
        with SessionLocal() as s:
            rows = s.execute(
                select(RawJob.source)
                .where(RawJob.environment == environment)
                .distinct()
                .order_by(RawJob.source)
            ).all()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        st.error(f"Failed to fetch sources: {e}")
        return []


@st.cache_data(ttl=30)
def get_skill_choices(environment: str, limit: int = 200) -> list[str]:
    """Get list of skills from specified environment."""
    try:
        with SessionLocal() as s:
            url_expr = RawJob.payload_json["url"].as_string()
            # Only include skills from jobs in specified environment
            rows = s.execute(
                select(JobSkill.skill)
                .join(Job, Job.id == JobSkill.job_id)
                .join(RawJob, url_expr == Job.url)
                .where(RawJob.environment == environment)
                .distinct()
                .order_by(JobSkill.skill)
                .limit(limit)
            ).all()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        st.error(f"Failed to fetch skills: {e}")
        return []


@st.cache_data(ttl=30)
def get_latest_jobs(
    environment: str,
    latest_n: int,
    keyword: str | None,
    sources: list[str],
    skills: list[str],
    days_back: int | None = None,
    location_filter: str | None = None,
) -> pd.DataFrame:
    """Get latest jobs from specified environment."""
    try:
        with SessionLocal() as s:
            url_expr = RawJob.payload_json["url"].as_string()

            # Inner join RawJob - required for environment/source filtering
            q = s.query(Job, RawJob.source.label("source")).join(RawJob, url_expr == Job.url)

            # Filter by specified environment
            q = q.filter(RawJob.environment == environment)

            # Filter by date (posted_at or ingested_at as fallback)
            if days_back:
                cutoff_date = date.today() - timedelta(days=days_back)
                date_expr = func.coalesce(Job.posted_at, RawJob.ingested_at)
                q = q.filter(date_expr >= cutoff_date)

            # Filter by location with smart US detection
            if location_filter:
                filter_lower = location_filter.strip().lower()

                # Smart US detection
                if filter_lower in ["us", "usa", "united states"]:
                    # Match common US location patterns
                    us_patterns = [
                        Job.location.ilike("%United States%"),
                        Job.location.ilike("%USA%"),
                        Job.location.ilike("%US%"),
                        Job.location.ilike("%, __"),  # State abbreviations: ", CA", ", NY", ", WA"
                        # Major US cities (tech hubs + common job markets)
                        Job.location.ilike("%New York%"),
                        Job.location.ilike("%Los Angeles%"),
                        Job.location.ilike("%San Francisco%"),
                        Job.location.ilike("%Bay Area%"),
                        Job.location.ilike("%Chicago%"),
                        Job.location.ilike("%Boston%"),
                        Job.location.ilike("%Seattle%"),
                        Job.location.ilike("%Austin%"),
                        Job.location.ilike("%Denver%"),
                        Job.location.ilike("%Portland%"),
                        Job.location.ilike("%Miami%"),
                        Job.location.ilike("%Atlanta%"),
                        Job.location.ilike("%Dallas%"),
                        Job.location.ilike("%Houston%"),
                        Job.location.ilike("%Phoenix%"),
                        Job.location.ilike("%Philadelphia%"),
                        Job.location.ilike("%San Diego%"),
                        Job.location.ilike("%Washington%"),
                    ]
                    q = q.filter(or_(*us_patterns))
                else:
                    # Regular substring match for other locations
                    q = q.filter(Job.location.ilike(f"%{location_filter}%"))

            if keyword:
                like = f"%{keyword}%"
                q = q.filter(
                    or_(
                        Job.title.ilike(like),
                        Job.company.ilike(like),
                        Job.location.ilike(like),
                        Job.description.ilike(like),
                    )
                )

            if sources:
                q = q.filter(RawJob.source.in_(sources))

            if skills:
                q = (
                    q.join(JobSkill, JobSkill.job_id == Job.id)
                    .filter(JobSkill.skill.in_(skills))
                    .distinct()
                )

            q = q.order_by(Job.id.desc()).limit(latest_n)
            rows = q.all()

            job_ids = [job.id for job, _source in rows]
            skill_map: dict[int, list[str]] = {}
            if job_ids:
                for job_id, skill in s.execute(
                    select(JobSkill.job_id, JobSkill.skill).where(JobSkill.job_id.in_(job_ids))
                ).all():
                    skill_map.setdefault(job_id, []).append(skill)

            data = []
            for job, source in rows:
                # Get full description and strip HTML
                full_desc = strip_html_tags(job.description or "")

                data.append(
                    {
                        "id": job.id,
                        "title": job.title,
                        "company": job.company,
                        "location": job.location,
                        "posted_at": job.posted_at,
                        "source": source,
                        "skills": ", ".join(sorted(set(skill_map.get(job.id, [])))),
                        "url": job.url,
                        "description_full": full_desc,
                    }
                )

            return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Failed to fetch jobs: {e}")
        st.exception(e)
        return pd.DataFrame()


with st.sidebar:
    st.header("Controls")

    # Cache control with visual feedback
    if st.button("🗑️ Clear cache", help="Clear cached data to refresh all views"):
        st.cache_data.clear()
        st.cache_resource.clear()

        # Clear UI state for a complete reset feel
        for key in list(st.session_state.keys()):
            if key.startswith(("jobs_sources", "jobs_skills", "jobs_keyword")):
                del st.session_state[key]

        st.success("✓ Cache cleared. Rerunning...")
        st.rerun()

    with st.expander("Ingest jobs (no scripts needed)", expanded=True):
        ingest_query = st.text_input("Search", value="engineer")
        ingest_limit = st.number_input("Limit", min_value=1, max_value=500, value=50, step=10)

        # Get available sources from registry
        try:
            available_sources = list_sources()
        except Exception as e:
            st.error(f"Failed to load sources: {e}")
            available_sources = []

        if not available_sources:
            st.warning("No sources available. Check source registration.")
            ingest_source = None
        else:
            ingest_source = st.selectbox("Source", options=available_sources, index=0)

        if ingest_source and st.button("Run ingest", type="primary"):
            try:
                with st.spinner("Fetching and loading jobs..."):
                    with SessionLocal() as session:
                        result = run_ingest(session, ingest_source, ingest_query, int(ingest_limit))

                        # Show warnings if any payloads were invalid
                        for warning in result.warnings:
                            st.warning(warning)

                    st.success(
                        f"Fetched={result.fetched} inserted_raw={result.inserted_raw} "
                        f"inserted_jobs={result.inserted_jobs} "
                        f"inserted_skills={result.inserted_skills}"
                    )

                # Refresh cached queries after ingest
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error("Ingestion failed:")
                st.exception(e)

    st.divider()
    st.subheader("Jobs Tab Filters")

    top_n = st.slider("Top N skills (table)", 5, 50, 25, key="jobs_top_n")
    latest_n = st.slider("Latest jobs to show", 5, 100, 25, key="jobs_latest_n")

    keyword = st.text_input("Keyword filter", value="", key="jobs_keyword")

    sources_all = get_sources(DATA_ENV)
    sources_sel = st.multiselect(
        "Source filter",
        options=sources_all,
        default=[],
        key="jobs_sources_v2",
        help="Leave empty to include all sources.",
    )

    skills_all = get_skill_choices(DATA_ENV)
    skills_sel = st.multiselect(
        "Skill filter",
        options=skills_all,
        default=[],
        key="jobs_skills",
        help="Leave empty to include all skills.",
    )

    st.divider()
    st.subheader("Date & Location")
    days_back = st.slider("Posted within last N days", 7, 180, 90, key="jobs_days_back")
    location_filter = st.text_input(
        "Location contains",
        value="",
        key="jobs_location",
        help="Examples: 'US' (all USA jobs), 'CA' (California), 'Remote', 'New York'",
    )


# --- Main Content with Tabs ---
tab_analytics, tab_jobs, tab_runs = st.tabs(["📊 Analytics", "📋 Jobs", "🔄 Ingest Runs"])

# ==================== ANALYTICS TAB ====================
with tab_analytics:
    st.subheader("Market Analytics")

    # Analytics filters
    with st.expander("🔧 Analytics Filters", expanded=False):
        acol1, acol2, acol3, acol4 = st.columns(4)
        with acol1:
            analytics_source = st.selectbox(
                "Source",
                options=["All"] + get_sources(DATA_ENV),
                key="analytics_source",
            )
        with acol2:
            date_range = st.selectbox(
                "Date Range",
                options=["All Time", "Last 7 Days", "Last 30 Days", "Last 90 Days"],
                key="analytics_date_range",
            )
        with acol3:
            analytics_search = st.text_input(
                "Search (title/company/location)",
                key="analytics_search",
            )
        with acol4:
            analytics_limit = st.slider("Top N Skills", 5, 30, 15, key="analytics_limit")

    # Compute date filters
    date_from = None
    date_to = None
    if date_range == "Last 7 Days":
        date_from = date.today() - timedelta(days=7)
    elif date_range == "Last 30 Days":
        date_from = date.today() - timedelta(days=30)
    elif date_range == "Last 90 Days":
        date_from = date.today() - timedelta(days=90)

    source_filter = None if analytics_source == "All" else analytics_source
    search_filter = analytics_search.strip() or None

    # KPI Cards
    st.markdown("### Key Metrics")
    try:
        with SessionLocal() as s:
            kpis = get_kpis(
                s,
                source=source_filter,
                date_from=date_from,
                date_to=date_to,
                search=search_filter,
                environment=DATA_ENV,
            )

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Total Jobs", kpis["total_jobs"])
        with kpi2:
            st.metric("Jobs (Last 7 Days)", kpis["jobs_last_7d"])
        with kpi3:
            st.metric("Unique Companies", kpis["unique_companies"])
        with kpi4:
            st.metric("Data Sources", kpis["sources_count"])
    except Exception as e:
        st.error(f"Failed to load KPIs: {e}")

    # Charts row
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("### Top Skills")
        try:
            with SessionLocal() as s:
                top_skills_data = get_top_skills(
                    s,
                    source=source_filter,
                    date_from=date_from,
                    date_to=date_to,
                    search=search_filter,
                    limit=analytics_limit,
                    environment=DATA_ENV,
                )

            if top_skills_data:
                skills_chart_df = pd.DataFrame(top_skills_data, columns=["Skill", "Jobs"])
                st.bar_chart(skills_chart_df.set_index("Skill"), horizontal=True)
            else:
                st.info("No skill data available. Try ingesting some jobs first.")
        except Exception as e:
            st.error(f"Failed to load top skills: {e}")

    with chart_col2:
        st.markdown("### Skills by Source")
        try:
            with SessionLocal() as s:
                skills_by_source = get_top_skills_by_source(
                    s,
                    date_from=date_from,
                    date_to=date_to,
                    search=search_filter,
                    limit=10,
                    environment=DATA_ENV,
                )

            if skills_by_source:
                # Create a comparison dataframe
                all_skills = set()
                for source_skills in skills_by_source.values():
                    all_skills.update(skill for skill, _ in source_skills)

                comparison_data = []
                for skill in sorted(all_skills):
                    row = {"Skill": skill}
                    for source_name, source_skills in skills_by_source.items():
                        skill_dict = dict(source_skills)
                        row[source_name] = skill_dict.get(skill, 0)
                    comparison_data.append(row)

                if comparison_data:
                    comparison_df = pd.DataFrame(comparison_data).set_index("Skill")
                    st.bar_chart(comparison_df)
                else:
                    st.info("No source comparison data available.")
            else:
                st.info("No source data available. Try ingesting from multiple sources.")
        except Exception as e:
            st.error(f"Failed to load skills by source: {e}")

    # Skill Trends
    st.markdown("### Skill Trends Over Time")

    # Granularity selector
    granularity_options = {"Auto": None, "6 hours": "6h", "Daily": "day", "Weekly": "week"}
    granularity_label = st.selectbox(
        "Time granularity",
        options=list(granularity_options.keys()),
        index=0,
        help="Auto: 6h for ≤7 days, daily for ≤60 days, weekly for longer periods",
    )
    granularity = granularity_options[granularity_label]

    try:
        with SessionLocal() as s:
            # Get top 5 skills to show trends for
            top_5_skills = get_top_skills(
                s,
                source=source_filter,
                date_from=date_from,
                date_to=date_to,
                limit=5,
                environment=DATA_ENV,
            )
            skill_names = [skill for skill, _ in top_5_skills]

            if skill_names:
                trends_data = get_skill_trends(
                    s,
                    skills=skill_names,
                    source=source_filter,
                    date_from=date_from,
                    date_to=date_to,
                    granularity=granularity,
                    environment=DATA_ENV,
                )

                if trends_data:
                    # Convert to DataFrame and prepare for charting
                    trends_df = pd.DataFrame(trends_data)
                    trends_df = trends_df.sort_values("bucket")

                    # Convert bucket to datetime when possible for proper x-axis ordering
                    trends_df["bucket"] = pd.to_datetime(trends_df["bucket"], errors="coerce")

                    if not trends_df.empty:
                        # Check if we have only one bucket (single time point)
                        unique_buckets = trends_df["bucket"].nunique()
                        if unique_buckets == 1:
                            st.warning(
                                "⚠️ Only one time bucket found. The chart will show markers only. "
                                "Run more ingestions (every 6 hours) to see trend lines."
                            )

                        trends_pivot = trends_df.pivot(
                            index="bucket", columns="skill", values="count"
                        ).fillna(0)
                        st.line_chart(trends_pivot)
                    else:
                        st.info("No trend data available for the selected period.")
                else:
                    st.info("No trend data available. Try adjusting the date range or granularity.")
            else:
                st.info("No skills found to show trends. Try ingesting some jobs first.")
    except Exception as e:
        st.error(f"Failed to load skill trends: {e}")


# ==================== JOBS TAB ====================
with tab_jobs:
    col1, col2 = st.columns([1, 2], gap="large")

    with col1:
        st.subheader("Top Skills")
        try:
            with SessionLocal() as s:
                skills = get_top_skills(s, limit=top_n, environment=DATA_ENV)
            skills_df = pd.DataFrame(skills, columns=["skill", "count"])
            st.dataframe(skills_df, width="stretch", hide_index=True)
        except Exception as e:
            st.error("Failed to load top skills:")
            st.exception(e)

    with col2:
        st.subheader("Latest Ingested Jobs")
        jobs_df = get_latest_jobs(
            environment=DATA_ENV,
            latest_n=latest_n,
            keyword=keyword.strip() or None,
            sources=sources_sel,
            skills=skills_sel,
            days_back=days_back,
            location_filter=location_filter.strip() or None,
        )

        if not jobs_df.empty:
            # Display table without description_full and id
            display_df = jobs_df.drop(columns=["id", "description_full"], errors="ignore")
            st.dataframe(
                display_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "url": st.column_config.LinkColumn("url", display_text="open"),
                    "posted_at": st.column_config.DateColumn("Posted", format="YYYY-MM-DD"),
                },
            )

            st.caption(
                "Tip: use the clickable 'open' link. "
                "The raw URL may look truncated but the link works."
            )
        else:
            st.info("No jobs found. Click 'Run ingest' in the sidebar to fetch jobs.")

    st.divider()
    st.subheader("Job Details")

    if jobs_df.empty:
        st.info("No jobs match your filters yet. Try ingesting more jobs or relaxing filters.")
    else:
        for _, row in jobs_df.iterrows():
            title = row.get("title") or "(untitled)"
            company = row.get("company") or ""
            source = row.get("source") or ""
            url = row.get("url") or ""
            job_id = int(row["id"])

            label = f"{title}"
            if company:
                label += f" | {company}"
            if source:
                label += f" [{source}]"

            with st.expander(label, expanded=False):
                if url:
                    st.markdown(f"[Open posting]({url})")
                meta = {
                    "company": row.get("company"),
                    "location": row.get("location"),
                    "posted_at": row.get("posted_at"),
                    "skills": row.get("skills"),
                    "source": row.get("source"),
                }
                st.json(meta)

                # Show full description with Show More/Less toggle
                description_full = row.get("description_full") or ""
                if description_full:
                    # Use stable job ID for session state keys
                    show_key = f"show_full_{job_id}"
                    more_key = f"more_{job_id}"
                    less_key = f"less_{job_id}"

                    if show_key not in st.session_state:
                        st.session_state[show_key] = False

                    # Show preview or full based on state
                    preview_length = 500
                    if len(description_full) > preview_length:
                        if st.session_state[show_key]:
                            st.write(description_full)
                            if st.button("Show Less", key=less_key):
                                st.session_state[show_key] = False
                        else:
                            st.write(description_full[:preview_length] + "...")
                            if st.button("Show More", key=more_key):
                                st.session_state[show_key] = True
                    else:
                        st.write(description_full)


# ==================== INGEST RUNS TAB ====================
with tab_runs:
    st.subheader("Recent Ingest Runs")
    st.caption(
        "Track ingest operations for observability and debugging. "
        f"Showing {DATA_ENV} environment runs."
    )

    if st.button("🔄 Refresh", key="refresh_runs"):
        st.rerun()

    try:
        with SessionLocal() as s:
            runs = (
                s.query(IngestRun)
                .filter(IngestRun.environment == DATA_ENV)
                .order_by(IngestRun.started_at.desc())
                .limit(20)
                .all()
            )

        if runs:
            runs_data = []
            for run in runs:
                # Calculate duration
                duration = ""
                if run.finished_at and run.started_at:
                    delta = run.finished_at - run.started_at
                    duration = f"{delta.total_seconds():.1f}s"

                # Count warnings
                warnings_count = len(run.warnings) if run.warnings else 0

                # Status emoji
                status_emoji = {
                    "success": "✅",
                    "failed": "❌",
                    "running": "🔄",
                }.get(run.status, "❓")

                runs_data.append(
                    {
                        "Status": f"{status_emoji} {run.status}",
                        "Source": run.source,
                        "Search": run.search or "",
                        "Started": run.started_at.strftime("%Y-%m-%d %H:%M:%S")
                        if run.started_at
                        else "",
                        "Duration": duration,
                        "Fetched": run.fetched,
                        "New Jobs": run.inserted_jobs,
                        "Skills": run.inserted_skills,
                        "Warnings": warnings_count,
                    }
                )

            runs_df = pd.DataFrame(runs_data)
            st.dataframe(runs_df, hide_index=True, width="stretch")

            # Show details for failed runs
            failed_runs = [r for r in runs if r.status == "failed"]
            if failed_runs:
                st.markdown("### Failed Runs Details")
                for run in failed_runs:
                    with st.expander(
                        f"❌ {run.source} - {run.started_at.strftime('%Y-%m-%d %H:%M:%S')}"
                    ):
                        st.error(run.error or "No error message recorded")

            # Show runs with warnings
            warning_runs = [r for r in runs if r.warnings]
            if warning_runs:
                st.markdown("### Runs with Warnings")
                for run in warning_runs:
                    with st.expander(
                        f"⚠️ {run.source} - {run.started_at.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"({len(run.warnings)} warnings)"
                    ):
                        for warning in run.warnings:
                            st.warning(warning)
        else:
            st.info("No ingest runs recorded yet. Use the sidebar to run an ingest operation.")
    except Exception as e:
        st.error(f"Failed to load ingest runs: {e}")
        st.exception(e)
