from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from midterm_money.pipeline_runner import PipelineFilters, PipelineOptions, run_senate_pipeline


DEFAULT_STATES_FILE = ROOT / "data" / "processed" / "polymarket_senate_top_two_2026.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a faster Senate finance pipeline scoped to active Polymarket Senate race states."
    )
    parser.add_argument(
        "--states-file",
        type=Path,
        default=DEFAULT_STATES_FILE,
        help="CSV with a state column used to scope the run. Defaults to the Polymarket top-two file.",
    )
    parser.add_argument(
        "--party",
        action="append",
        default=["DEM", "REP"],
        help="Repeatable normalized party filter. Defaults to DEM and REP.",
    )
    parser.add_argument(
        "--candidate-status",
        action="append",
        default=["C"],
        help="Repeatable candidate_status filter. Defaults to C for a faster first-pass run.",
    )
    parser.add_argument("--no-resume", action="store_true", help="Ignore any compatible checkpoint and start fresh.")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Save checkpoint files every N processed candidates.")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N processed candidates.")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=None,
        help="Optional custom checkpoint directory. Defaults to data/interim/top_races_pipeline_checkpoint.",
    )
    parser.add_argument("--limit-candidates", type=int, default=None, help="Limit the candidate scope for testing.")
    parser.add_argument("--dry-run", action="store_true", help="Show the filtered scope without making finance requests.")
    return parser.parse_args()


def load_states(states_file: Path) -> set[str]:
    if not states_file.exists():
        raise FileNotFoundError(
            f"States file not found: {states_file}. Run scripts/fetch_polymarket_senate.py first or pass --states-file."
        )
    df = pd.read_csv(states_file, dtype=str)
    if "state" not in df.columns:
        raise ValueError(f"States file does not include a 'state' column: {states_file}")
    return set(df["state"].dropna().astype(str).str.strip())


def main() -> None:
    args = parse_args()
    states = load_states(args.states_file)
    party_filter = {item.strip().upper() for item in args.party if item and item.strip()}
    status_filter = {item.strip().upper() for item in args.candidate_status if item and item.strip()}

    options = PipelineOptions(
        source_name="top_races_pipeline",
        output_suffix="_race_subset",
        checkpoint_dir=args.checkpoint_dir,
        resume=not args.no_resume,
        checkpoint_every=args.checkpoint_every,
        progress_every=args.progress_every,
        dry_run=args.dry_run,
        filters=PipelineFilters(
            states=states,
            party_normalized=party_filter or None,
            candidate_statuses=status_filter or None,
            limit_candidates=args.limit_candidates,
        ),
    )
    run_senate_pipeline(ROOT, options)


if __name__ == "__main__":
    main()
