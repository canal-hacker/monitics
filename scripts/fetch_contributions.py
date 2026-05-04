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
DEFAULT_COMMITTEES_PATH = PROCESSED_DIR / "senate_candidate_committees_2026.csv"

MANIFEST_FIELDS = [
    "committee_id",
    "candidate_name",
    "fec_candidate_id",
    "state",
    "party",
    "party_normalized",
    "source_scope",
    "status",
    "row_count",
    "pages_fetched",
    "total_pages_reported",
    "fetched_at",
    "error",
]


def _parse_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _committee_fetch_complete(row: Dict[str, object]) -> bool:
    if row.get("status") != "ok":
        return False
    pages_fetched = _parse_int(row.get("pages_fetched"))
    total_pages_reported = _parse_int(row.get("total_pages_reported"))
    if total_pages_reported <= 0:
        return pages_fetched > 0
    return pages_fetched >= total_pages_reported


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch line-item Schedule A contribution receipts for Senate campaign committees."
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
        help="OpenFEC page size for Schedule A calls.",
    )
    parser.add_argument(
        "--max-committees",
        type=int,
        default=None,
        help="Limit the number of committees fetched in this run for testing.",
    )
    parser.add_argument(
        "--max-pages-per-committee",
        type=int,
        default=None,
        help="Limit pages per committee for smoke tests.",
    )
    parser.add_argument(
        "--individual-only",
        action="store_true",
        help="Only fetch receipts flagged by OpenFEC as individual contributions.",
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

    if "committee_id" not in df.columns:
        raise ValueError(f"Input data for source={source!r} does not contain a committee_id column")

    if "party_normalized" not in df.columns and "party" in df.columns:
        df["party_normalized"] = df["party"].apply(normalize_party)

    df["committee_id"] = df["committee_id"].fillna("").astype(str).str.strip()
    df = df[df["committee_id"] != ""].copy()

    if "candidate_name" not in df.columns:
        if "selected_candidate_name" in df.columns:
            df["candidate_name"] = df["selected_candidate_name"]
        else:
            df["candidate_name"] = None

    df["source_scope"] = source

    keep_cols = [
        "committee_id",
        "candidate_name",
        "fec_candidate_id",
        "state",
        "party",
        "party_normalized",
        "source_scope",
        "selected_candidate",
        "selection_method",
        "manual_override_reason",
    ]
    available_cols = [col for col in keep_cols if col in df.columns]
    df = df[available_cols].drop_duplicates(subset=["committee_id"]).reset_index(drop=True)

    if DEFAULT_COMMITTEES_PATH.exists():
        committees = load_dataframe(DEFAULT_COMMITTEES_PATH)
        if "committee_id" in committees.columns:
            committee_cols = [
                "committee_id",
                "committee_name",
                "designation",
                "designation_full",
                "committee_type",
                "committee_type_full",
                "is_principal",
            ]
            committees = committees[[col for col in committee_cols if col in committees.columns]]
            committees = committees.drop_duplicates(subset=["committee_id"])
            df = df.merge(committees, on="committee_id", how="left")

    return df


def build_dataset_paths(source: str, individual_only: bool) -> tuple[Path, Path, Path, Path]:
    dataset_prefix = "senate_individual_contributions" if individual_only else "senate_campaign_contributions"
    dataset_name = f"{dataset_prefix}_{settings.ELECTION_CYCLE}_{source}"
    output_path = PROCESSED_DIR / f"{dataset_name}.csv"
    outputs_copy_path = OUTPUTS_DIR / f"{dataset_name}.csv"
    shards_dir = INTERIM_DIR / dataset_name / "shards"
    manifest_path = INTERIM_DIR / dataset_name / "manifest.csv"
    return output_path, outputs_copy_path, shards_dir, manifest_path


def load_completed_committee_ids(manifest_path: Path) -> Set[str]:
    if not manifest_path.exists():
        return set()

    completed: Set[str] = set()
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("committee_id") and _committee_fetch_complete(row):
                completed.add(row["committee_id"])
    return completed


def append_manifest_row(manifest_path: Path, row: Dict[str, object]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: row.get(field) for field in MANIFEST_FIELDS})


def build_receipt_row(receipt: Dict[str, object], committee_row: pd.Series) -> Dict[str, object]:
    row = dict(receipt)
    row["source_committee_id"] = committee_row.get("committee_id")
    row["source_committee_name"] = committee_row.get("committee_name")
    row["source_candidate_name"] = committee_row.get("candidate_name")
    row["source_fec_candidate_id"] = committee_row.get("fec_candidate_id")
    row["source_state"] = committee_row.get("state")
    row["source_party"] = committee_row.get("party")
    row["source_party_normalized"] = committee_row.get("party_normalized")
    row["source_scope"] = committee_row.get("source_scope")
    row["source_selected_candidate"] = committee_row.get("selected_candidate")
    row["source_selection_method"] = committee_row.get("selection_method")
    row["source_manual_override_reason"] = committee_row.get("manual_override_reason")
    row["source_is_principal"] = committee_row.get("is_principal")
    row["fetched_for_cycle"] = settings.ELECTION_CYCLE
    row["fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    return row


def fetch_committee_receipts(
    client: FECClient,
    committee_row: pd.Series,
    per_page: int,
    individual_only: bool,
    max_pages_per_committee: Optional[int],
) -> tuple[list[Dict[str, object]], int, int]:
    committee_id = committee_row["committee_id"]
    params = {
        "committee_id": committee_id,
        "two_year_transaction_period": settings.ELECTION_CYCLE,
        "per_page": per_page,
        "sort": "-contribution_receipt_date",
        "sort_hide_null": True,
    }
    if individual_only:
        params["is_individual"] = True

    page = 1
    total_pages_reported = 1
    receipts: list[Dict[str, object]] = []

    while True:
        page_params = params.copy()
        page_params["page"] = page
        data = client.get("/schedules/schedule_a/", params=page_params)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ValueError(f"Unexpected Schedule A response for committee {committee_id}")

        receipts.extend(build_receipt_row(result, committee_row) for result in results)

        pagination = data.get("pagination", {})
        current_page = pagination.get("page", page)
        total_pages_reported = pagination.get("pages", current_page)

        if max_pages_per_committee is not None and current_page >= max_pages_per_committee:
            break
        if current_page >= total_pages_reported:
            break

        page = current_page + 1

    return receipts, current_page, total_pages_reported


def save_committee_shard(path: Path, rows: Iterable[Dict[str, object]]) -> int:
    rows = list(rows)
    if not rows:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return len(rows)


def combine_shards(shards_dir: Path, allowed_committee_ids: Optional[Set[str]] = None) -> pd.DataFrame:
    csv_paths = sorted(path for path in shards_dir.glob("*.csv") if path.name != "manifest.csv")
    if allowed_committee_ids is not None:
        csv_paths = [path for path in csv_paths if path.stem in allowed_committee_ids]
    if not csv_paths:
        return pd.DataFrame()

    frames = [pd.read_csv(path, dtype=str) for path in csv_paths]
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    args = parse_args()
    client = FECClient()
    output_path, outputs_copy_path, shards_dir, manifest_path = build_dataset_paths(
        source=args.source,
        individual_only=args.individual_only,
    )

    targets = load_targets(args.source)
    all_target_committee_ids = set(targets["committee_id"].tolist())
    completed_committee_ids = load_completed_committee_ids(manifest_path)
    targets = targets[~targets["committee_id"].isin(completed_committee_ids)].copy()

    if args.max_committees is not None:
        targets = targets.head(args.max_committees).copy()

    print(f"Committees remaining for source={args.source}: {len(targets)}")
    print(f"Request interval seconds: {client.request_interval_seconds}")

    shards_dir.mkdir(parents=True, exist_ok=True)

    for index, (_, committee_row) in enumerate(targets.iterrows(), start=1):
        committee_id = committee_row["committee_id"]
        shard_path = shards_dir / f"{committee_id}.csv"
        candidate_name = committee_row.get("candidate_name")
        print(f"[{index}/{len(targets)}] Fetching committee {committee_id} for {candidate_name}")

        try:
            receipts, pages_fetched, total_pages_reported = fetch_committee_receipts(
                client=client,
                committee_row=committee_row,
                per_page=args.per_page,
                individual_only=args.individual_only,
                max_pages_per_committee=args.max_pages_per_committee,
            )
            row_count = save_committee_shard(shard_path, receipts)
            status = "ok"
            if args.max_pages_per_committee is not None and pages_fetched < total_pages_reported:
                status = "partial"
            append_manifest_row(
                manifest_path,
                {
                    "committee_id": committee_id,
                    "candidate_name": committee_row.get("candidate_name"),
                    "fec_candidate_id": committee_row.get("fec_candidate_id"),
                    "state": committee_row.get("state"),
                    "party": committee_row.get("party"),
                    "party_normalized": committee_row.get("party_normalized"),
                    "source_scope": committee_row.get("source_scope"),
                    "status": status,
                    "row_count": row_count,
                    "pages_fetched": pages_fetched,
                    "total_pages_reported": total_pages_reported,
                    "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "error": "",
                },
            )
        except Exception as exc:
            append_manifest_row(
                manifest_path,
                {
                    "committee_id": committee_id,
                    "candidate_name": committee_row.get("candidate_name"),
                    "fec_candidate_id": committee_row.get("fec_candidate_id"),
                    "state": committee_row.get("state"),
                    "party": committee_row.get("party"),
                    "party_normalized": committee_row.get("party_normalized"),
                    "source_scope": committee_row.get("source_scope"),
                    "status": "error",
                    "row_count": 0,
                    "pages_fetched": 0,
                    "total_pages_reported": 0,
                    "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "error": str(exc),
                },
            )
            print(f"  Failed: {exc}")

    combined = combine_shards(shards_dir, allowed_committee_ids=all_target_committee_ids)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    outputs_copy_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)
    combined.to_csv(outputs_copy_path, index=False)

    print(f"Saved combined contribution dataset to {output_path}")
    print(f"Saved outputs copy to {outputs_copy_path}")
    print(f"Manifest path: {manifest_path}")
    print(f"Combined rows: {len(combined)}")


if __name__ == "__main__":
    main()
