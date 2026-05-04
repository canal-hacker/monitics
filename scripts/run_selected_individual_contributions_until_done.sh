#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

timestamp() {
  date +"%Y-%m-%dT%H:%M:%S%z"
}

cd "$ROOT_DIR" || exit 1

while true; do
  remaining=$(
    .venv/bin/python -c 'from pathlib import Path; from scripts import fetch_contributions as fc; targets = fc.load_targets("selected"); selected = set(targets["committee_id"].tolist()); completed = fc.load_completed_committee_ids(Path("data/interim/senate_individual_contributions_2026_selected/manifest.csv")); print(len(selected - completed))'
  )

  echo "$(timestamp) remaining_committees=$remaining"

  if [ "$remaining" -eq 0 ]; then
    echo "$(timestamp) complete"
    exit 0
  fi

  FEC_REQUEST_INTERVAL_SECONDS="${FEC_REQUEST_INTERVAL_SECONDS:-1.0}" \
    .venv/bin/python -u scripts/fetch_contributions.py --source selected --individual-only
  rc=$?
  echo "$(timestamp) fetch_exit_code=$rc"
  sleep 60
done
