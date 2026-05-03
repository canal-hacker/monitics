from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from midterm_money.config import settings
from midterm_money.fec_client import FECClient
from midterm_money.normalize import normalize_party


PROCESSED_DIR = ROOT / "data" / "processed"
INTERIM_DIR = ROOT / "data" / "interim"
OUTPUTS_DIR = ROOT / "outputs"

DEFAULT_SELECTED_PATH = PROCESSED_DIR / "senate_top_dem_rep_candidates_2026.csv"
DEFAULT_TOTALS_PATH = PROCESSED_DIR / "senate_candidate_finance_totals_2026.csv"
DEFAULT_ALL_CYCLES_TOTALS_PATH = PROCESSED_DIR / "senate_candidate_finance_totals_2026_all_cycles.csv"

MANIFEST_FIELDS = [
    "fec_candidate_id",
    "candidate_name",
    "state",
    "party",
    "party_normalized",
    "source_scope",
    "include_notices",
    "most_recent_only",
    "support_oppose_indicator",
    "status",
    "row_count",
    "requests_fetched",
    "total_pages_reported",
    "fetched_at",
    "error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch line-item Schedule E independent expenditures for Senate candidates."
    )
    parser.add_argument(
        "--source",
        choices=["selected", "totals", "all_cycles"],
        default="selected",
        help="Use top selected Senate candidates, the default current-cycle totals universe, or the preserved all-cycle totals universe.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=100,
        help="OpenFEC page size for Schedule E calls.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Limit the number of candidates fetched in this run for testing.",
    )
    parser.add_argument(
        "--max-pages-per-candidate",
        type=int,
        default=None,
        help="Limit keyset-paginated requests per candidate for smoke tests.",
    )
    parser.add_argument(
        "--include-notices",
        action="store_true",
        help="Include 24- and 48-hour notice filings. Excluded by default to reduce obvious double counting.",
    )
    parser.add_argument(
        "--include-non-most-recent",
        action="store_true",
        help="Include superseded amendments. By default most_recent=True is requested.",
    )
    parser.add_argument(
        "--support-oppose",
        choices=["S", "O"],
        default=None,
        help="Optional filter for support-only (S) or oppose-only (O) expenditures.",
    )
    return parser.parse_args()


def load_dataframe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")
    return pd.read_csv(path, dtype=str)


def _clean_bool_string(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def load_targets(source: str) -> pd.DataFrame:
    if source == "selected":
        df = load_dataframe(DEFAULT_SELECTED_PATH).copy()
        if "selected_candidate" in df.columns:
            df = df[df["selected_candidate"].apply(_clean_bool_string)].copy()
        if "selected_candidate_name" in df.columns and "candidate_name" not in df.columns:
            df = df.rename(columns={"selected_candidate_name": "candidate_name"})
    elif source == "all_cycles":
        totals_path = DEFAULT_ALL_CYCLES_TOTALS_PATH if DEFAULT_ALL_CYCLES_TOTALS_PATH.exists() else DEFAULT_TOTALS_PATH
        df = load_dataframe(totals_path).copy()
    else:
        df = load_dataframe(DEFAULT_TOTALS_PATH).copy()

    if "fec_candidate_id" not in df.columns:
        raise ValueError(f"Input data for source={source!r} does not contain a fec_candidate_id column")

    if "party_normalized" not in df.columns and "party" in df.columns:
        df["party_normalized"] = df["party"].apply(normalize_party)

    df["fec_candidate_id"] = df["fec_candidate_id"].fillna("").astype(str).str.strip()
    df = df[df["fec_candidate_id"] != ""].copy()

    if "candidate_name" not in df.columns:
        if "selected_candidate_name" in df.columns:
            df["candidate_name"] = df["selected_candidate_name"]
        else:
            df["candidate_name"] = None

    df["source_scope"] = source

    keep_cols = [
        "fec_candidate_id",
        "candidate_name",
        "state",
        "party",
        "party_normalized",
        "source_scope",
        "selected_candidate",
        "selection_method",
        "manual_override_reason",
    ]
    available_cols = [col for col in keep_cols if col in df.columns]
    df = df[available_cols].drop_duplicates(subset=["fec_candidate_id"]).reset_index(drop=True)
    return df


def build_dataset_paths(
    source: str,
    include_notices: bool,
    include_non_most_recent: bool,
    support_oppose: Optional[str],
) -> tuple[Path, Path, Path, Path]:
    dataset_name = f"senate_independent_expenditures_{settings.ELECTION_CYCLE}_{source}"
    if include_notices:
        dataset_name += "_with_notices"
    if include_non_most_recent:
        dataset_name += "_all_versions"
    if support_oppose:
        dataset_name += f"_{support_oppose.lower()}"

    output_path = PROCESSED_DIR / f"{dataset_name}.csv"
    outputs_copy_path = OUTPUTS_DIR / f"{dataset_name}.csv"
    shards_dir = INTERIM_DIR / dataset_name / "shards"
    manifest_path = INTERIM_DIR / dataset_name / "manifest.csv"
    return output_path, outputs_copy_path, shards_dir, manifest_path


def load_completed_candidate_ids(manifest_path: Path) -> Set[str]:
    if not manifest_path.exists():
        return set()

    completed: Set[str] = set()
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("status") == "ok" and row.get("fec_candidate_id"):
                completed.add(row["fec_candidate_id"])
    return completed


def append_manifest_row(manifest_path: Path, row: Dict[str, object]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: row.get(field) for field in MANIFEST_FIELDS})


def build_expenditure_row(expenditure: Dict[str, object], candidate_row: pd.Series) -> Dict[str, object]:
    row = dict(expenditure)
    row["source_fec_candidate_id"] = candidate_row.get("fec_candidate_id")
    row["source_candidate_name"] = candidate_row.get("candidate_name")
    row["source_state"] = candidate_row.get("state")
    row["source_party"] = candidate_row.get("party")
    row["source_party_normalized"] = candidate_row.get("party_normalized")
    row["source_scope"] = candidate_row.get("source_scope")
    row["source_selected_candidate"] = candidate_row.get("selected_candidate")
    row["source_selection_method"] = candidate_row.get("selection_method")
    row["source_manual_override_reason"] = candidate_row.get("manual_override_reason")
    row["fetched_for_cycle"] = settings.ELECTION_CYCLE
    row["fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    return row


def fetch_candidate_independent_expenditures(
    client: FECClient,
    candidate_row: pd.Series,
    per_page: int,
    include_notices: bool,
    include_non_most_recent: bool,
    max_pages_per_candidate: Optional[int],
    support_oppose: Optional[str],
) -> tuple[list[Dict[str, object]], int, int]:
    candidate_id = candidate_row["fec_candidate_id"]
    params = {
        "candidate_id": candidate_id,
        "candidate_office": settings.OFFICE,
        "cycle": settings.ELECTION_CYCLE,
        "per_page": per_page,
        "sort": "-expenditure_date",
    }
    if not include_notices:
        params["is_notice"] = False
    if not include_non_most_recent:
        params["most_recent"] = True
    if support_oppose:
        params["support_oppose_indicator"] = support_oppose

    expenditures: list[Dict[str, object]] = []
    requests_fetched = 0
    total_pages_reported = 1
    last_index: Optional[str] = None
    last_expenditure_date: Optional[str] = None

    while True:
        page_params = params.copy()
        if last_index:
            page_params["last_index"] = last_index
        if last_expenditure_date:
            page_params["last_expenditure_date"] = last_expenditure_date

        data = client.get("/schedules/schedule_e/", params=page_params)
        requests_fetched += 1

        results = data.get("results", [])
        if not isinstance(results, list):
            raise ValueError(f"Unexpected Schedule E response for candidate {candidate_id}")

        expenditures.extend(build_expenditure_row(result, candidate_row) for result in results)

        pagination = data.get("pagination", {}) or {}
        total_pages_reported = int(pagination.get("pages", total_pages_reported) or total_pages_reported)
        last_indexes = pagination.get("last_indexes", {}) or {}
        next_last_index = last_indexes.get("last_index")
        next_last_expenditure_date = last_indexes.get("last_expenditure_date")

        if max_pages_per_candidate is not None and requests_fetched >= max_pages_per_candidate:
            break
        if not results:
            break
        if requests_fetched >= total_pages_reported:
            break
        if not next_last_index or next_last_index == last_index:
            break

        last_index = str(next_last_index)
        last_expenditure_date = None if next_last_expenditure_date is None else str(next_last_expenditure_date)

    return expenditures, requests_fetched, total_pages_reported


def save_candidate_shard(path: Path, rows: Iterable[Dict[str, object]]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return len(rows)


def combine_shards(shards_dir: Path, allowed_candidate_ids: Optional[Set[str]] = None) -> pd.DataFrame:
    csv_paths = sorted(path for path in shards_dir.glob("*.csv") if path.name != "manifest.csv")
    if allowed_candidate_ids is not None:
        csv_paths = [path for path in csv_paths if path.stem in allowed_candidate_ids]
    if not csv_paths:
        return pd.DataFrame()

    frames = [pd.read_csv(path, dtype=str) for path in csv_paths]
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    args = parse_args()
    client = FECClient()
    output_path, outputs_copy_path, shards_dir, manifest_path = build_dataset_paths(
        source=args.source,
        include_notices=args.include_notices,
        include_non_most_recent=args.include_non_most_recent,
        support_oppose=args.support_oppose,
    )

    targets = load_targets(args.source)
    all_target_candidate_ids = set(targets["fec_candidate_id"].tolist())
    completed_candidate_ids = load_completed_candidate_ids(manifest_path)
    targets = targets[~targets["fec_candidate_id"].isin(completed_candidate_ids)].copy()

    if args.max_candidates is not None:
        targets = targets.head(args.max_candidates).copy()

    print(f"Candidates remaining for source={args.source}: {len(targets)}")
    print(f"Request interval seconds: {client.request_interval_seconds}")
    print(f"Include notices: {args.include_notices}")
    print(f"Most recent only: {not args.include_non_most_recent}")

    shards_dir.mkdir(parents=True, exist_ok=True)

    for index, (_, candidate_row) in enumerate(targets.iterrows(), start=1):
        candidate_id = candidate_row["fec_candidate_id"]
        shard_path = shards_dir / f"{candidate_id}.csv"
        candidate_name = candidate_row.get("candidate_name")
        print(f"[{index}/{len(targets)}] Fetching candidate {candidate_id} for {candidate_name}")

        try:
            expenditures, requests_fetched, total_pages_reported = fetch_candidate_independent_expenditures(
                client=client,
                candidate_row=candidate_row,
                per_page=args.per_page,
                include_notices=args.include_notices,
                include_non_most_recent=args.include_non_most_recent,
                max_pages_per_candidate=args.max_pages_per_candidate,
                support_oppose=args.support_oppose,
            )
            row_count = save_candidate_shard(shard_path, expenditures)
            append_manifest_row(
                manifest_path,
                {
                    "fec_candidate_id": candidate_id,
                    "candidate_name": candidate_row.get("candidate_name"),
                    "state": candidate_row.get("state"),
                    "party": candidate_row.get("party"),
                    "party_normalized": candidate_row.get("party_normalized"),
                    "source_scope": candidate_row.get("source_scope"),
                    "include_notices": args.include_notices,
                    "most_recent_only": not args.include_non_most_recent,
                    "support_oppose_indicator": args.support_oppose or "",
                    "status": "ok",
                    "row_count": row_count,
                    "requests_fetched": requests_fetched,
                    "total_pages_reported": total_pages_reported,
                    "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "error": "",
                },
            )
        except Exception as exc:
            append_manifest_row(
                manifest_path,
                {
                    "fec_candidate_id": candidate_id,
                    "candidate_name": candidate_row.get("candidate_name"),
                    "state": candidate_row.get("state"),
                    "party": candidate_row.get("party"),
                    "party_normalized": candidate_row.get("party_normalized"),
                    "source_scope": candidate_row.get("source_scope"),
                    "include_notices": args.include_notices,
                    "most_recent_only": not args.include_non_most_recent,
                    "support_oppose_indicator": args.support_oppose or "",
                    "status": "error",
                    "row_count": 0,
                    "requests_fetched": 0,
                    "total_pages_reported": 0,
                    "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "error": str(exc),
                },
            )
            print(f"  Failed: {exc}")

    combined = combine_shards(shards_dir, allowed_candidate_ids=all_target_candidate_ids)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    outputs_copy_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)
    combined.to_csv(outputs_copy_path, index=False)

    print(f"Saved combined independent expenditure dataset to {output_path}")
    print(f"Saved outputs copy to {outputs_copy_path}")
    print(f"Manifest path: {manifest_path}")
    print(f"Combined rows: {len(combined)}")


if __name__ == "__main__":
    main()
