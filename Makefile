.PHONY: help setup check sync-secrets secrets-status

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create .env from the template
	@test -f .env || (cp .env.example .env && echo "Created .env — fill in HEVY_API_KEY")
	@test -f .env && echo ".env exists"

check: ## Verify the Hevy API key resolves and works
	python scripts/check_connection.py

sync-secrets: ## Push .env values to GitHub Actions secrets
	./scripts/sync_secrets.sh

secrets-status: ## Show which secrets GitHub currently has
	gh secret list
