from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.pipeline import run_postprocess


def main() -> None:
    init_db()
    with SessionLocal() as session:
        raw = load_raw_jobs(session, "data/sample_jobs.jsonl")
        jobs, skills = run_postprocess(session)

    print(f"loaded_raw={raw} inserted_jobs={jobs} inserted_skills={skills}")


if __name__ == "__main__":
    main()
