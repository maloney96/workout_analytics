.PHONY: help setup venv lock check sync-secrets secrets-status clean-venv

PYTHON := python3.12
VENV   := .venv
BIN    := $(VENV)/bin

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

clean-venv: ## Delete the virtualenv
	rm -rf $(VENV)

sync-secrets: ## Push .env values to GitHub Actions secrets
	./scripts/sync_secrets.sh

secrets-status: ## Show which secrets GitHub currently has
	gh secret list
