from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from .candidate_selection import load_manual_overrides, select_top_candidates
from .config import settings
from .fec_client import FECClient
from .normalize import normalize_party


MISSING_COLUMNS = ["fec_candidate_id", "error"]


@dataclass(frozen=True)
class PipelineFilters:
    states: Optional[Set[str]] = None
    party_normalized: Optional[Set[str]] = None
    candidate_statuses: Optional[Set[str]] = None
    limit_candidates: Optional[int] = None


@dataclass(frozen=True)
class PipelineOptions:
    source_name: str
    output_suffix: str = ""
    checkpoint_dir: Optional[Path] = None
    resume: bool = True
    checkpoint_every: int = 25
    progress_every: int = 25
    dry_run: bool = False
    filters: PipelineFilters = PipelineFilters()


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def is_principal_committee(committee: dict) -> bool:
    designation = committee.get("designation")
    designation_full = (committee.get("designation_full") or "").lower()
    return designation == "P" or "principal" in designation_full


def format_duration(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _suffix_filename(filename: str, suffix: str) -> str:
    if not suffix:
        return filename
    path = Path(filename)
    return f"{path.stem}{suffix}{path.suffix}"


def _processed_path(root: Path, filename: str) -> Path:
    return root / "data" / "processed" / filename


def _outputs_path(root: Path, filename: str) -> Path:
    return root / "outputs" / filename


def _candidate_universe_path(root: Path) -> Path:
    return _processed_path(root, "senate_candidates_2026_all.csv")


def _candidate_outputs_path(root: Path) -> Path:
    return _outputs_path(root, "senate_candidates_2026_all.csv")


def _checkpoint_dir(root: Path, source_name: str, checkpoint_dir: Optional[Path]) -> Path:
    if checkpoint_dir is not None:
        return checkpoint_dir
    safe_source = source_name.lower().replace(" ", "_")
    return root / "data" / "interim" / f"{safe_source}_checkpoint"


def _checkpoint_paths(checkpoint_dir: Path) -> Dict[str, Path]:
    return {
        "progress": checkpoint_dir / "progress.json",
        "committees": checkpoint_dir / "committee_rows.csv",
        "totals": checkpoint_dir / "totals_rows.csv",
        "missing_committees": checkpoint_dir / "missing_committees.csv",
        "missing_totals": checkpoint_dir / "missing_totals.csv",
    }


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _write_json(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_candidate_universe(root: Path, client: FECClient) -> pd.DataFrame:
    candidates_path = _candidate_universe_path(root)
    outputs_candidate_path = _candidate_outputs_path(root)

    if not candidates_path.exists():
        print("Fetching 2026 Senate candidates...", flush=True)
        query_params = {
            "cycle": settings.ELECTION_CYCLE,
            "office": settings.OFFICE,
            "per_page": 100,
            "sort": "name",
        }
        candidates = client.fetch_all("/candidates/search/", params=query_params)
        df_candidates = pd.DataFrame(candidates)
        candidate_fields = [
            "candidate_id",
            "name",
            "party",
            "party_full",
            "state",
            "office",
            "election_years",
            "cycles",
            "incumbent_challenge_status",
            "candidate_status",
            "load_date",
            "update_date",
        ]
        available_fields = [field for field in candidate_fields if field in df_candidates.columns]
        df_clean = df_candidates[available_fields].copy()
        df_clean["party_normalized"] = df_clean["party"].apply(normalize_party)
        df_clean["raw_party"] = df_clean["party"]
        df_clean = df_clean.rename(columns={"candidate_id": "fec_candidate_id"})
        save_csv(df_clean, candidates_path)
        save_csv(df_clean, outputs_candidate_path)
        print("Saved candidate universe to", candidates_path, flush=True)
        return df_clean

    df_clean = pd.read_csv(candidates_path, dtype=str)
    print("Loaded existing candidate universe, rows=", len(df_clean), flush=True)
    return df_clean


def _apply_filters(df_candidates: pd.DataFrame, filters: PipelineFilters) -> pd.DataFrame:
    df = df_candidates.copy()
    if filters.states:
        df = df[df["state"].isin(filters.states)].copy()
    if filters.party_normalized:
        df = df[df["party_normalized"].isin(filters.party_normalized)].copy()
    if filters.candidate_statuses:
        df = df[df["candidate_status"].isin(filters.candidate_statuses)].copy()
    if filters.limit_candidates is not None:
        df = df.head(filters.limit_candidates).copy()
    return df.reset_index(drop=True)


def _save_scope_file(root: Path, df_scope: pd.DataFrame, output_suffix: str) -> None:
    if not output_suffix:
        return
    filename = _suffix_filename("senate_candidates_2026_scope.csv", output_suffix)
    save_csv(df_scope, _processed_path(root, filename))
    save_csv(df_scope, _outputs_path(root, filename))


def _save_checkpoint(
    checkpoint_dir: Path,
    source_name: str,
    candidate_ids: Sequence[str],
    committee_rows: List[Dict[str, object]],
    totals_rows: List[Dict[str, object]],
    missing_committees: List[Tuple[str, str]],
    missing_totals: List[Tuple[str, str]],
    completed_candidate_ids: Set[str],
) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    paths = _checkpoint_paths(checkpoint_dir)

    pd.DataFrame(committee_rows).to_csv(paths["committees"], index=False)
    pd.DataFrame(totals_rows).to_csv(paths["totals"], index=False)
    pd.DataFrame(missing_committees, columns=MISSING_COLUMNS).to_csv(paths["missing_committees"], index=False)
    pd.DataFrame(missing_totals, columns=MISSING_COLUMNS).to_csv(paths["missing_totals"], index=False)

    progress_data = {
        "source_name": source_name,
        "candidate_ids": list(candidate_ids),
        "completed_candidate_ids": sorted(completed_candidate_ids),
        "updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    _write_json(paths["progress"], progress_data)


def _load_checkpoint(
    checkpoint_dir: Path,
    source_name: str,
    candidate_ids: Sequence[str],
) -> Optional[Dict[str, object]]:
    paths = _checkpoint_paths(checkpoint_dir)
    progress_path = paths["progress"]
    if not progress_path.exists():
        return None

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("source_name") != source_name:
        return None
    if progress.get("candidate_ids") != list(candidate_ids):
        return None

    committee_rows = _read_csv_or_empty(paths["committees"]).to_dict("records")
    totals_rows = _read_csv_or_empty(paths["totals"]).to_dict("records")
    missing_committees_df = _read_csv_or_empty(paths["missing_committees"])
    missing_totals_df = _read_csv_or_empty(paths["missing_totals"])

    missing_committees = list(
        zip(
            missing_committees_df.get("fec_candidate_id", pd.Series(dtype=str)).fillna("").tolist(),
            missing_committees_df.get("error", pd.Series(dtype=str)).fillna("").tolist(),
        )
    )
    missing_totals = list(
        zip(
            missing_totals_df.get("fec_candidate_id", pd.Series(dtype=str)).fillna("").tolist(),
            missing_totals_df.get("error", pd.Series(dtype=str)).fillna("").tolist(),
        )
    )

    return {
        "committee_rows": committee_rows,
        "totals_rows": totals_rows,
        "missing_committees": missing_committees,
        "missing_totals": missing_totals,
        "completed_candidate_ids": set(progress.get("completed_candidate_ids", [])),
    }


def _write_pipeline_outputs(
    root: Path,
    output_suffix: str,
    df_committees: pd.DataFrame,
    df_totals: pd.DataFrame,
    df_selected: pd.DataFrame,
    df_snapshot: pd.DataFrame,
    df_wide: pd.DataFrame,
    missing_committees: List[Tuple[str, str]],
    missing_totals: List[Tuple[str, str]],
) -> None:
    filename_map = {
        "committees": "senate_candidate_committees_2026.csv",
        "totals": "senate_candidate_finance_totals_2026.csv",
        "selected": "senate_top_dem_rep_candidates_2026.csv",
        "wide": "senate_two_party_race_universe_2026.csv",
        "snapshot": "senate_money_snapshot_2026.csv",
        "missing_committees": "senate_missing_committees_2026.csv",
        "missing_totals": "senate_missing_totals_2026.csv",
    }

    outputs = {
        "committees": df_committees,
        "totals": df_totals,
        "selected": df_selected,
        "wide": df_wide,
        "snapshot": df_snapshot,
        "missing_committees": pd.DataFrame(missing_committees, columns=MISSING_COLUMNS),
        "missing_totals": pd.DataFrame(missing_totals, columns=MISSING_COLUMNS),
    }

    for key, filename in filename_map.items():
        output_filename = _suffix_filename(filename, output_suffix)
        save_csv(outputs[key], _processed_path(root, output_filename))
        save_csv(outputs[key], _outputs_path(root, output_filename))


def _build_snapshot(df_selected: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    wide = df_selected[df_selected["selected_candidate"] == True].pivot(
        index="state",
        columns="party_normalized",
        values=[
            "selected_candidate_name",
            "fec_candidate_id",
            "committee_id",
            "total_receipts",
            "total_disbursements",
            "cash_on_hand",
            "debts_owed_by_committee",
            "coverage_end_date",
            "selection_method",
        ],
    )
    wide.columns = [f"{col[0].lower()}_{col[1].lower()}" for col in wide.columns]
    wide = wide.reset_index()

    snapshot_rows = []
    for _, row in wide.iterrows():
        snapshot_rows.append(
            {
                "state": row["state"],
                "dem_candidate_name": row.get("selected_candidate_name_dem"),
                "dem_fec_candidate_id": row.get("fec_candidate_id_dem"),
                "dem_committee_id": row.get("committee_id_dem"),
                "dem_total_receipts": row.get("total_receipts_dem"),
                "dem_total_disbursements": row.get("total_disbursements_dem"),
                "dem_cash_on_hand": row.get("cash_on_hand_dem"),
                "dem_debts_owed_by_committee": row.get("debts_owed_by_committee_dem"),
                "dem_coverage_end_date": row.get("coverage_end_date_dem"),
                "dem_selection_method": row.get("selection_method_dem"),
                "rep_candidate_name": row.get("selected_candidate_name_rep"),
                "rep_fec_candidate_id": row.get("fec_candidate_id_rep"),
                "rep_committee_id": row.get("committee_id_rep"),
                "rep_total_receipts": row.get("total_receipts_rep"),
                "rep_total_disbursements": row.get("total_disbursements_rep"),
                "rep_cash_on_hand": row.get("cash_on_hand_rep"),
                "rep_debts_owed_by_committee": row.get("debts_owed_by_committee_rep"),
                "rep_coverage_end_date": row.get("coverage_end_date_rep"),
                "rep_selection_method": row.get("selection_method_rep"),
                "direct_money_data_pulled_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "notes": "Snapshot built from principal committee totals for the selected Senate candidates.",
            }
        )

    df_snapshot = pd.DataFrame(snapshot_rows)
    if not df_snapshot.empty:
        df_snapshot["dem_minus_rep_total_receipts"] = (
            pd.to_numeric(df_snapshot["dem_total_receipts"], errors="coerce").fillna(0)
            - pd.to_numeric(df_snapshot["rep_total_receipts"], errors="coerce").fillna(0)
        )
        df_snapshot["dem_minus_rep_cash_on_hand"] = (
            pd.to_numeric(df_snapshot["dem_cash_on_hand"], errors="coerce").fillna(0)
            - pd.to_numeric(df_snapshot["rep_cash_on_hand"], errors="coerce").fillna(0)
        )
    else:
        df_snapshot["dem_minus_rep_total_receipts"] = pd.Series(dtype=float)
        df_snapshot["dem_minus_rep_cash_on_hand"] = pd.Series(dtype=float)

    return wide, df_snapshot


def run_senate_pipeline(root: Path, options: PipelineOptions) -> None:
    client = FECClient()
    df_candidates = _load_candidate_universe(root, client)
    df_scope = _apply_filters(df_candidates, options.filters)

    candidate_ids = df_scope["fec_candidate_id"].fillna("").tolist()
    checkpoint_dir = _checkpoint_dir(root, options.source_name, options.checkpoint_dir)

    print(
        f"Pipeline scope '{options.source_name}' includes {len(df_scope)} candidates"
        f" (resume={'yes' if options.resume else 'no'}, checkpoint_every={options.checkpoint_every})",
        flush=True,
    )
    if options.filters.states:
        print(f"State filter count: {len(options.filters.states)}", flush=True)
    if options.filters.party_normalized:
        print(f"Party filter: {sorted(options.filters.party_normalized)}", flush=True)
    if options.filters.candidate_statuses:
        print(f"Candidate status filter: {sorted(options.filters.candidate_statuses)}", flush=True)

    if options.dry_run:
        print("Dry run only. No FEC finance requests were made.", flush=True)
        return

    if df_scope.empty:
        print("No candidates matched this scope. Nothing to fetch.", flush=True)
        return

    _save_scope_file(root, df_scope, options.output_suffix)

    checkpoint_state = None
    if options.resume:
        checkpoint_state = _load_checkpoint(checkpoint_dir, options.source_name, candidate_ids)

    committee_rows: List[Dict[str, object]] = []
    totals_rows: List[Dict[str, object]] = []
    missing_committees: List[Tuple[str, str]] = []
    missing_totals: List[Tuple[str, str]] = []
    completed_candidate_ids: Set[str] = set()

    if checkpoint_state is not None:
        committee_rows = list(checkpoint_state["committee_rows"])
        totals_rows = list(checkpoint_state["totals_rows"])
        missing_committees = list(checkpoint_state["missing_committees"])
        missing_totals = list(checkpoint_state["missing_totals"])
        completed_candidate_ids = set(checkpoint_state["completed_candidate_ids"])
        print(
            f"Resuming from checkpoint {checkpoint_dir} with {len(completed_candidate_ids)}/{len(df_scope)} candidates already completed.",
            flush=True,
        )

    remaining_df = df_scope[~df_scope["fec_candidate_id"].isin(completed_candidate_ids)].copy().reset_index(drop=True)
    total_candidates = len(df_scope)
    processed_candidates = len(completed_candidate_ids)
    completed_before_this_run = processed_candidates

    loop_started_at = time.monotonic()
    print(
        "Fetching committees and totals for",
        total_candidates,
        "candidates...",
        f"(request interval={client.request_interval_seconds}s, rate-limit sleep={client.rate_limit_sleep_seconds}s)",
        flush=True,
    )

    try:
        for remaining_index, (_, row) in enumerate(remaining_df.iterrows(), start=1):
            candidate_id = row.get("fec_candidate_id")
            name = row.get("name")
            if not candidate_id:
                continue

            committee_data = None
            try:
                committee_data = client.get(f"/candidate/{candidate_id}/committees/", params={"per_page": 100})
            except Exception as exc:
                missing_committees.append((candidate_id, str(exc)))
                print(f"Committee lookup failed for {candidate_id} ({name}): {exc}", flush=True)

            committee_results = committee_data.get("results", []) if committee_data else []
            for committee in committee_results:
                committee_rows.append(
                    {
                        "fec_candidate_id": candidate_id,
                        "candidate_name": name,
                        "committee_id": committee.get("committee_id"),
                        "committee_name": committee.get("name"),
                        "designation": committee.get("designation"),
                        "designation_full": committee.get("designation_full"),
                        "committee_type": committee.get("committee_type"),
                        "committee_type_full": committee.get("committee_type_full"),
                        "is_principal": is_principal_committee(committee),
                    }
                )

            totals_targets = [committee for committee in committee_results if is_principal_committee(committee)]
            if not totals_targets:
                totals_targets = committee_results

            had_totals_targets = bool(totals_targets)
            committee_totals_found = False
            if not had_totals_targets:
                missing_totals.append((candidate_id, "No committees returned for candidate"))
            else:
                for committee in totals_targets:
                    committee_id = committee.get("committee_id")
                    if not committee_id:
                        continue

                    try:
                        totals_data = client.get(f"/committee/{committee_id}/totals/")
                    except Exception as exc:
                        missing_totals.append((candidate_id, f"{committee_id}: {exc}"))
                        print(f"Totals lookup failed for {candidate_id} ({name}) via {committee_id}: {exc}", flush=True)
                        continue

                    totals_results = totals_data.get("results", []) if totals_data else []
                    if not totals_results:
                        continue

                    committee_totals_found = True
                    for totals in totals_results:
                        totals_rows.append(
                            {
                                "fec_candidate_id": candidate_id,
                                "candidate_name": name,
                                "state": row.get("state"),
                                "party": row.get("party"),
                                "committee_id": totals.get("committee_id") or committee_id,
                                "committee_name": totals.get("committee_name") or committee.get("name"),
                                "total_receipts": totals.get("receipts"),
                                "total_disbursements": totals.get("disbursements"),
                                "cash_on_hand_end_period": totals.get("last_cash_on_hand_end_period"),
                                "cash_on_hand": totals.get("last_cash_on_hand_end_period"),
                                "debts_owed_by_committee": totals.get("last_debts_owed_by_committee"),
                                "coverage_start_date": totals.get("coverage_start_date"),
                                "coverage_end_date": totals.get("coverage_end_date") or totals.get("transaction_coverage_date"),
                                "cycle": totals.get("cycle"),
                                "individual_contributions": totals.get("individual_contributions"),
                                "individual_itemized_contributions": totals.get("individual_itemized_contributions"),
                                "individual_unitemized_contributions": totals.get("individual_unitemized_contributions"),
                                "candidate_contribution": totals.get("candidate_contribution"),
                                "other_receipts": totals.get("other_receipts"),
                                "last_report_type_full": totals.get("last_report_type_full"),
                                "last_report_year": totals.get("last_report_year"),
                                "is_principal_committee": is_principal_committee(committee),
                                "source_endpoint": "committee_totals",
                            }
                        )

            if had_totals_targets and not committee_totals_found:
                missing_totals.append((candidate_id, "No totals returned"))

            completed_candidate_ids.add(candidate_id)
            processed_candidates += 1

            if processed_candidates % options.progress_every == 0 or processed_candidates == total_candidates:
                elapsed = time.monotonic() - loop_started_at
                rate = (processed_candidates - completed_before_this_run) / elapsed if elapsed > 0 else 0
                remaining = total_candidates - processed_candidates
                eta_seconds = remaining / rate if rate > 0 else 0
                print(
                    f"Progress {processed_candidates}/{total_candidates} candidates | "
                    f"elapsed {format_duration(elapsed)} | "
                    f"eta {format_duration(eta_seconds)} | "
                    f"committee rows={len(committee_rows)} | totals rows={len(totals_rows)} | "
                    f"missing committees={len(missing_committees)} | missing totals={len(missing_totals)}",
                    flush=True,
                )

            if processed_candidates % options.checkpoint_every == 0 or processed_candidates == total_candidates:
                _save_checkpoint(
                    checkpoint_dir=checkpoint_dir,
                    source_name=options.source_name,
                    candidate_ids=candidate_ids,
                    committee_rows=committee_rows,
                    totals_rows=totals_rows,
                    missing_committees=missing_committees,
                    missing_totals=missing_totals,
                    completed_candidate_ids=completed_candidate_ids,
                )
                print(f"Checkpoint saved at {checkpoint_dir}", flush=True)
    except KeyboardInterrupt:
        _save_checkpoint(
            checkpoint_dir=checkpoint_dir,
            source_name=options.source_name,
            candidate_ids=candidate_ids,
            committee_rows=committee_rows,
            totals_rows=totals_rows,
            missing_committees=missing_committees,
            missing_totals=missing_totals,
            completed_candidate_ids=completed_candidate_ids,
        )
        print(f"Interrupted. Checkpoint saved at {checkpoint_dir}", flush=True)
        raise
    except Exception:
        _save_checkpoint(
            checkpoint_dir=checkpoint_dir,
            source_name=options.source_name,
            candidate_ids=candidate_ids,
            committee_rows=committee_rows,
            totals_rows=totals_rows,
            missing_committees=missing_committees,
            missing_totals=missing_totals,
            completed_candidate_ids=completed_candidate_ids,
        )
        print(f"Error encountered. Checkpoint saved at {checkpoint_dir}", flush=True)
        raise

    df_committees = pd.DataFrame(committee_rows)
    df_totals = pd.DataFrame(totals_rows)

    print("Saved committee and totals tables.", flush=True)
    print("Missing committees count:", len(missing_committees), flush=True)
    print("Missing totals count:", len(missing_totals), flush=True)

    print("Selecting top Democratic and Republican candidates by state...", flush=True)
    if df_totals.empty:
        df_selected = pd.DataFrame(
            columns=[
                "state",
                "selected_candidate_name",
                "fec_candidate_id",
                "committee_id",
                "total_receipts",
                "total_disbursements",
                "cash_on_hand_end_period",
                "cash_on_hand",
                "debts_owed_by_committee",
                "coverage_end_date",
                "party",
                "party_normalized",
                "selected_candidate",
                "selection_method",
                "selection_rank_within_state_party",
                "manual_override_reason",
            ]
        )
    else:
        df_selected = select_top_candidates(df_totals, overrides=load_manual_overrides(root / "config" / "manual_overrides.yml"))
        selected_cols = [
            "state",
            "fec_candidate_id",
            "candidate_name",
            "committee_id",
            "total_receipts",
            "total_disbursements",
            "cash_on_hand_end_period",
            "cash_on_hand",
            "debts_owed_by_committee",
            "coverage_end_date",
            "party",
            "party_normalized",
            "selected_candidate",
            "selection_method",
            "selection_rank_within_state_party",
            "manual_override_reason",
        ]
        df_selected = df_selected[selected_cols].rename(columns={"candidate_name": "selected_candidate_name"})
    print("Saved top candidate selection files.", flush=True)

    df_wide, df_snapshot = _build_snapshot(df_selected)
    print("Saved two-party race universe file.", flush=True)
    print("Saved final snapshot file.", flush=True)

    _write_pipeline_outputs(
        root=root,
        output_suffix=options.output_suffix,
        df_committees=df_committees,
        df_totals=df_totals,
        df_selected=df_selected,
        df_snapshot=df_snapshot,
        df_wide=df_wide,
        missing_committees=missing_committees,
        missing_totals=missing_totals,
    )

    _save_checkpoint(
        checkpoint_dir=checkpoint_dir,
        source_name=options.source_name,
        candidate_ids=candidate_ids,
        committee_rows=committee_rows,
        totals_rows=totals_rows,
        missing_committees=missing_committees,
        missing_totals=missing_totals,
        completed_candidate_ids=completed_candidate_ids,
    )
    print(f"Final checkpoint saved at {checkpoint_dir}", flush=True)
