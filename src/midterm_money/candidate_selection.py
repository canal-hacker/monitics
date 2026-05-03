from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

from .normalize import normalize_party


def load_manual_overrides(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def select_top_candidates(
    candidate_totals: pd.DataFrame,
    overrides: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    if overrides is None:
        overrides = {}

    df = candidate_totals.copy()
    df["party_normalized"] = df["party"].apply(normalize_party)
    df["total_receipts"] = pd.to_numeric(df["total_receipts"], errors="coerce").fillna(0)
    df["selection_rank_within_state_party"] = (
        df.sort_values(["state", "party_normalized", "total_receipts"], ascending=[True, True, False])
        .groupby(["state", "party_normalized"])['total_receipts']
        .rank(method='first', ascending=False)
    )
    df["selected_candidate"] = False
    df["selection_method"] = None
    df["manual_override_reason"] = None

    df.loc[df["party_normalized"].isin(["DEM", "REP"]), "selected_candidate"] = df["selection_rank_within_state_party"] == 1
    df.loc[df["selected_candidate"], "selection_method"] = "highest_total_receipts"

    if overrides:
        senate_overrides = overrides.get("senate_2026") or {}
        for state, party_overrides in senate_overrides.items():
            if not party_overrides:
                continue
            for party_code, override_data in party_overrides.items():
                if not override_data:
                    continue
                candidate_id = override_data.get("fec_candidate_id")
                reason = override_data.get("reason")
                if not candidate_id:
                    continue
                mask = (
                    (df["state"] == state)
                    & (df["party_normalized"] == party_code)
                )
                df.loc[mask, "selected_candidate"] = False
                override_mask = mask & (df["fec_candidate_id"] == candidate_id)
                df.loc[override_mask, "selected_candidate"] = True
                df.loc[override_mask, "selection_method"] = "manual_override"
                df.loc[override_mask, "manual_override_reason"] = reason

    return df
