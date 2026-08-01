.PHONY: help setup venv lock check ingest backfill dbt dbt-test dbt-docs pipeline query snapshot sync-secrets secrets-status clean-venv

PYTHON := python3.12
VENV   := .venv
BIN    := $(VENV)/bin

export DBT_PROFILES_DIR := $(CURDIR)/dbt
export DUCKDB_PATH      := $(CURDIR)/data/warehouse.duckdb

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: venv ## Create the venv and .env from the template
	@test -f .env || (cp .env.example .env && echo "Created .env — fill in HEVY_API_KEY")
	@test -f .env && echo ".env exists"

venv: $(BIN)/dbt ## Build the virtualenv from the lockfile

$(BIN)/dbt: requirements.lock.txt
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install -r requirements.lock.txt
	@touch $(BIN)/dbt

lock: ## Re-resolve requirements.txt and refresh the lockfile
	$(BIN)/pip install -r requirements.txt --upgrade
	$(BIN)/pip freeze > requirements.lock.txt
	@echo "Lockfile refreshed. Commit requirements.lock.txt."

check: venv ## Verify the Hevy API key resolves and works
	$(BIN)/python scripts/check_connection.py

ingest: venv ## Run the bronze ingest (incremental)
	$(BIN)/python ingest/run_ingest.py

backfill: venv ## Re-pull every workout from scratch
	$(BIN)/python ingest/run_ingest.py --full-refresh

dbt: venv ## Build all dbt models and run their tests
	$(BIN)/dbt build --project-dir dbt

dbt-test: venv ## Run dbt tests only
	$(BIN)/dbt test --project-dir dbt

dbt-docs: venv ## Generate and serve the dbt docs site
	$(BIN)/dbt docs generate --project-dir dbt && $(BIN)/dbt docs serve --project-dir dbt

pipeline: ingest dbt ## Ingest then transform - the full local run

query: venv ## Run SQL against the warehouse: make query Q="select ..."
	@$(BIN)/python scripts/query.py $(if $(Q),"$(Q)")

snapshot: ## Copy the warehouse so a DB tool can browse it without locking the pipeline
	@cp data/warehouse.duckdb data/warehouse_snapshot.duckdb
	@echo "Snapshot refreshed: data/warehouse_snapshot.duckdb"

clean-venv: ## Delete the virtualenv
	rm -rf $(VENV)

sync-secrets: ## Push .env values to GitHub Actions secrets
	./scripts/sync_secrets.sh

secrets-status: ## Show which secrets GitHub currently has
	gh secret list
