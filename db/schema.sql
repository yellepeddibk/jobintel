CREATE TABLE IF NOT EXISTS raw_jobs (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT,
  location TEXT,
  url TEXT UNIQUE,
  posted_at DATE,
  description TEXT,
  hash TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS job_skills (
  job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  skill TEXT NOT NULL,
  PRIMARY KEY (job_id, skill)
);

CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill);
