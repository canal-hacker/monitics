from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from midterm_money.pipeline_runner import PipelineFilters, PipelineOptions, run_senate_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full 2026 Senate finance pipeline with checkpoint/resume support.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore any compatible checkpoint and start fresh.")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Save checkpoint files every N processed candidates.")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N processed candidates.")
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=None,
        help="Optional custom checkpoint directory. Defaults to data/interim/full_senate_pipeline_checkpoint.",
    )
    parser.add_argument("--limit-candidates", type=int, default=None, help="Limit the candidate scope for testing.")
    parser.add_argument("--dry-run", action="store_true", help="Show the filtered scope without making finance requests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    options = PipelineOptions(
        source_name="full_senate_pipeline",
        output_suffix="",
        checkpoint_dir=args.checkpoint_dir,
        resume=not args.no_resume,
        checkpoint_every=args.checkpoint_every,
        progress_every=args.progress_every,
        dry_run=args.dry_run,
        filters=PipelineFilters(limit_candidates=args.limit_candidates),
    )
    run_senate_pipeline(ROOT, options)


if __name__ == "__main__":
    main()
