from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import desc, select

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.models import RawJob


def _coerce_payload(x: Any) -> dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            return {}
    return {}


st.set_page_config(page_title="JobIntel Dashboard", layout="wide")
st.title("JobIntel Dashboard")
st.caption("Live ingestion → normalize → extract skills → analytics")


with st.sidebar:
    st.header("Controls")
    top_n = st.slider("Top N skills", min_value=5, max_value=50, value=20, step=5)
    latest_n = st.slider("Latest ingested jobs", min_value=5, max_value=50, value=20, step=5)
    st.markdown("Populate data first:")
    st.code('python scripts/run_live_etl.py --search "data engineer" --limit 50 --top 20', language="bash")

init_db()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Top Skills")
    with SessionLocal() as session:
        rows = top_skills(session, limit=int(top_n))
    df = pd.DataFrame(rows, columns=["skill", "count"])
    st.dataframe(df, use_container_width=True, hide_index=True)
    if not df.empty:
        st.bar_chart(df.set_index("skill")["count"])

with col2:
    st.subheader("Latest Ingested Jobs")
    with SessionLocal() as session:
        raw_rows = (
            session.execute(select(RawJob).order_by(desc(RawJob.id)).limit(int(latest_n)))
            .scalars()
            .all()
        )

    items: list[dict[str, Any]] = []
    for r in raw_rows:
        p = _coerce_payload(getattr(r, "payload_json", None))
        items.append(
            {
                "posted_at": p.get("posted_at"),
                "title": p.get("title"),
                "company": p.get("company"),
                "location": p.get("location"),
                "source": p.get("source"),
                "url": p.get("url"),
            }
        )

    st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
