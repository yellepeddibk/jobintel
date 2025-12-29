db-up:
	docker compose up -d db

db-down:
	docker compose down

db-logs:
	docker compose logs -f db
init-db:
	python scripts/init_db.py

etl-sample:
	python scripts/run_sample_etl.py
