import re

import pandas as pd
import streamlit as st
from sqlalchemy import or_, select

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.sources.remotive import fetch_remotive_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import Job, JobSkill, RawJob

st.set_page_config(page_title="JobIntel Dashboard", layout="wide")
st.title("JobIntel Dashboard")
st.caption("Live ingestion → normalize → extract skills → analytics")

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
def get_sources() -> list[str]:
    try:
        with SessionLocal() as s:
            rows = s.execute(select(RawJob.source).distinct().order_by(RawJob.source)).all()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        st.error(f"Failed to fetch sources: {e}")
        return []


@st.cache_data(ttl=30)
def get_skill_choices(limit: int = 200) -> list[str]:
    try:
        with SessionLocal() as s:
            rows = s.execute(
                select(JobSkill.skill).distinct().order_by(JobSkill.skill).limit(limit)
            ).all()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        st.error(f"Failed to fetch skills: {e}")
        return []


@st.cache_data(ttl=30)
def get_latest_jobs(
    latest_n: int,
    keyword: str | None,
    sources: list[str],
    skills: list[str],
) -> pd.DataFrame:
    try:
        with SessionLocal() as s:
            url_expr = RawJob.payload_json["url"].as_string()

            q = s.query(Job, RawJob.source.label("source")).outerjoin(RawJob, url_expr == Job.url)

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

    with st.expander("Ingest jobs (no scripts needed)", expanded=True):
        ingest_query = st.text_input("Search", value="engineer")
        ingest_limit = st.number_input("Limit", min_value=1, max_value=500, value=50, step=10)
        ingest_source = st.selectbox("Source", options=["remotive"], index=0)

        if st.button("Run ingest", type="primary"):
            try:
                with st.spinner("Fetching and loading jobs..."):
                    with SessionLocal() as session:
                        if ingest_source == "remotive":
                            # Use keyword args to match fetch_remotive_jobs signature
                            payloads = fetch_remotive_jobs(
                                search=ingest_query, limit=int(ingest_limit)
                            )
                        else:
                            payloads = []

                        inserted_raw = 0
                        for payload in payloads:
                            if upsert_raw_job(session, payload):
                                inserted_raw += 1

                        inserted_jobs = transform_jobs(session)
                        inserted_skills = extract_skills_for_all_jobs(session)

                    st.success(
                        f"✅ fetched={len(payloads)} inserted_raw={inserted_raw} "
                        f"inserted_jobs={inserted_jobs} inserted_skills={inserted_skills}"
                    )

                # Refresh cached queries after ingest
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error("Ingestion failed:")
                st.exception(e)

    top_n = st.slider("Top N skills", 5, 50, 25)
    latest_n = st.slider("Latest ingested jobs", 5, 100, 25)

    keyword = st.text_input("Keyword filter (title/company/location/description)", value="")

    sources_all = get_sources()
    sources_sel = st.multiselect("Source", options=sources_all, default=[])

    skills_all = get_skill_choices()
    skills_sel = st.multiselect("Skill contains", options=skills_all, default=[])


col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("Top Skills")
    try:
        with SessionLocal() as s:
            skills = top_skills(s, top_n)
        skills_df = pd.DataFrame(skills, columns=["skill", "count"])
        st.dataframe(skills_df, width="stretch", hide_index=True)
    except Exception as e:
        st.error("Failed to load top skills:")
        st.exception(e)

with col2:
    st.subheader("Latest Ingested Jobs")
    jobs_df = get_latest_jobs(
        latest_n=latest_n,
        keyword=keyword.strip() or None,
        sources=sources_sel,
        skills=skills_sel,
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
            },
        )

        st.caption(
            "Tip: use the clickable 'open' link. The raw URL may look truncated but the link works."
        )
    else:
        st.info("No jobs found. Try adjusting filters or ingesting more data.")


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
