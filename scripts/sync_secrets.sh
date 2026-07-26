#!/usr/bin/env bash
# Push local .env values up to GitHub Actions secrets, so a remote run sees the
# same config a local run does.
#
#   ./scripts/sync_secrets.sh          # sync
#   ./scripts/sync_secrets.sh --dry-run
#
# Only the names in SYNCED_KEYS are sent. Paths stay local — a runner's paths are
# not yours — and are set in the workflow instead.

set -euo pipefail

SYNCED_KEYS=(HEVY_API_KEY LOCAL_TZ ASSUMED_BODYWEIGHT_KG)

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env at $ENV_FILE — copy .env.example and fill it in first." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI not found. Install with: brew install gh && gh auth login" >&2
  exit 1
fi

for key in "${SYNCED_KEYS[@]}"; do
  # Read without sourcing, so .env can't execute anything.
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"

  if [[ -z "$value" ]]; then
    echo "  skip  $key (not set in .env)"
    continue
  fi

  if $DRY_RUN; then
    echo "  would set  $key (${#value} chars)"
  else
    printf '%s' "$value" | gh secret set "$key"
    echo "  set   $key (${#value} chars)"
  fi
done

echo
echo "Done. Verify with: gh secret list"
