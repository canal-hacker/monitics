from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from midterm_money.normalize import normalize_party
from midterm_money.polymarket_client import PolymarketClient


PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUTS_DIR = ROOT / "outputs"

CANDIDATES_PATH = PROCESSED_DIR / "senate_candidates_2026_all.csv"
SELECTED_PATH = PROCESSED_DIR / "senate_top_dem_rep_candidates_2026.csv"

ALL_MARKETS_PATH = PROCESSED_DIR / "polymarket_senate_markets_2026.csv"
TOP_TWO_PATH = PROCESSED_DIR / "polymarket_senate_top_two_2026.csv"
OUTPUTS_ALL_MARKETS_PATH = OUTPUTS_DIR / "polymarket_senate_markets_2026.csv"
OUTPUTS_TOP_TWO_PATH = OUTPUTS_DIR / "polymarket_senate_top_two_2026.csv"

SENATE_SEARCH_QUERY = "senate election winner"
PLACEHOLDER_PREFIX = "candidate "

STATE_NAME_TO_CODE = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

PARTY_LABEL_TO_CODE = {
    "DEMOCRAT": "DEM",
    "REPUBLICAN": "REP",
    "INDEPENDENT": "OTHER",
    "OTHER": "OTHER",
}

NAME_STOPWORDS = {
    "DR",
    "JR",
    "SR",
    "II",
    "III",
    "IV",
    "MD",
    "PHD",
    "SEN",
    "SENATOR",
    "MR",
    "MRS",
    "MS",
    "MISS",
    "GOV",
}


def current_utc_iso() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def load_dataframe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    return pd.read_csv(path, dtype=str)


def normalize_person_name(name: Optional[str]) -> str:
    if not name or not isinstance(name, str):
        return ""

    text = name.strip().upper()
    if "," in text:
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            text = " ".join(parts[1:] + [parts[0]])

    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    tokens = [token for token in text.split() if token and token not in NAME_STOPWORDS]
    return " ".join(tokens)


def parse_json_list(value: object) -> List[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def extract_price_for_outcome(market: Dict[str, object], outcome_name: str) -> Optional[float]:
    outcomes = [str(item) for item in parse_json_list(market.get("outcomes"))]
    prices = parse_json_list(market.get("outcomePrices"))
    outcome_index = next((idx for idx, value in enumerate(outcomes) if value.lower() == outcome_name.lower()), None)
    if outcome_index is None or outcome_index >= len(prices):
        return None

    try:
        return float(prices[outcome_index])
    except (TypeError, ValueError):
        return None


def infer_state_name_from_title(title: str) -> Optional[str]:
    for state_name in STATE_NAME_TO_CODE:
        if title.startswith(f"{state_name} "):
            return state_name
    return None


def load_selected_candidates() -> pd.DataFrame:
    if not SELECTED_PATH.exists():
        return pd.DataFrame()

    df_selected = load_dataframe(SELECTED_PATH).copy()
    if "selected_candidate" in df_selected.columns:
        selected_mask = df_selected["selected_candidate"].astype(str).str.lower().isin({"true", "1", "yes", "y", "t"})
        df_selected = df_selected[selected_mask].copy()

    if "selected_candidate_name" in df_selected.columns:
        df_selected["matched_candidate_name"] = df_selected["selected_candidate_name"]
    else:
        df_selected["matched_candidate_name"] = df_selected.get("candidate_name")

    df_selected["normalized_candidate_name"] = df_selected["matched_candidate_name"].apply(normalize_person_name)
    return df_selected


def prepare_candidate_universe() -> pd.DataFrame:
    df_candidates = load_dataframe(CANDIDATES_PATH).copy()
    df_candidates["normalized_candidate_name"] = df_candidates["name"].apply(normalize_person_name)
    if "party_normalized" not in df_candidates.columns:
        df_candidates["party_normalized"] = df_candidates["party"].apply(normalize_party)
    return df_candidates


def match_candidate_market(
    state_code: str,
    entity_name: str,
    df_candidates: pd.DataFrame,
    df_selected: pd.DataFrame,
) -> Dict[str, object]:
    normalized_name = normalize_person_name(entity_name)
    exact_candidates = df_candidates[
        (df_candidates["state"] == state_code)
        & (df_candidates["normalized_candidate_name"] == normalized_name)
    ].copy()

    if len(exact_candidates) == 1:
        row = exact_candidates.iloc[0]
        return {
            "matched_fec_candidate_id": row.get("fec_candidate_id"),
            "matched_candidate_name": row.get("name"),
            "matched_party": row.get("party"),
            "matched_party_normalized": row.get("party_normalized"),
            "candidate_match_status": "exact_candidate_universe",
            "candidate_match_count": 1,
            "candidate_match_candidates": row.get("fec_candidate_id"),
        }

    if len(exact_candidates) > 1 and not df_selected.empty:
        selected_matches = df_selected[
            (df_selected["state"] == state_code)
            & (df_selected["normalized_candidate_name"] == normalized_name)
        ].copy()
        if len(selected_matches) == 1:
            row = selected_matches.iloc[0]
            return {
                "matched_fec_candidate_id": row.get("fec_candidate_id"),
                "matched_candidate_name": row.get("matched_candidate_name"),
                "matched_party": row.get("party"),
                "matched_party_normalized": row.get("party_normalized"),
                "candidate_match_status": "exact_selected_candidate",
                "candidate_match_count": len(exact_candidates),
                "candidate_match_candidates": "|".join(exact_candidates["fec_candidate_id"].fillna("").tolist()),
            }

    return {
        "matched_fec_candidate_id": None,
        "matched_candidate_name": None,
        "matched_party": None,
        "matched_party_normalized": None,
        "candidate_match_status": "ambiguous_candidate_universe" if len(exact_candidates) > 1 else "no_candidate_match",
        "candidate_match_count": len(exact_candidates),
        "candidate_match_candidates": "|".join(exact_candidates["fec_candidate_id"].fillna("").tolist()),
    }


def match_party_market(
    state_code: str,
    entity_name: str,
    df_selected: pd.DataFrame,
) -> Dict[str, object]:
    party_code = PARTY_LABEL_TO_CODE.get(entity_name.strip().upper())
    if party_code is None:
        return {
            "matched_fec_candidate_id": None,
            "matched_candidate_name": None,
            "matched_party": None,
            "matched_party_normalized": None,
            "candidate_match_status": "unknown_party_market",
            "candidate_match_count": 0,
            "candidate_match_candidates": "",
        }

    if df_selected.empty:
        return {
            "matched_fec_candidate_id": None,
            "matched_candidate_name": None,
            "matched_party": entity_name,
            "matched_party_normalized": party_code,
            "candidate_match_status": "party_market_waiting_for_selected_file",
            "candidate_match_count": 0,
            "candidate_match_candidates": "",
        }

    selected_matches = df_selected[
        (df_selected["state"] == state_code)
        & (df_selected["party_normalized"] == party_code)
    ].copy()
    if len(selected_matches) == 1:
        row = selected_matches.iloc[0]
        return {
            "matched_fec_candidate_id": row.get("fec_candidate_id"),
            "matched_candidate_name": row.get("matched_candidate_name"),
            "matched_party": row.get("party"),
            "matched_party_normalized": row.get("party_normalized"),
            "candidate_match_status": "party_market_selected_candidate",
            "candidate_match_count": 1,
            "candidate_match_candidates": row.get("fec_candidate_id"),
        }

    return {
        "matched_fec_candidate_id": None,
        "matched_candidate_name": None,
        "matched_party": entity_name,
        "matched_party_normalized": party_code,
        "candidate_match_status": "party_market_no_selected_candidate",
        "candidate_match_count": len(selected_matches),
        "candidate_match_candidates": "|".join(selected_matches["fec_candidate_id"].fillna("").tolist()) if not selected_matches.empty else "",
    }


def build_market_row(
    event: Dict[str, object],
    market: Dict[str, object],
    df_candidates: pd.DataFrame,
    df_selected: pd.DataFrame,
) -> Optional[Dict[str, object]]:
    event_title = str(event.get("title") or "").strip()
    state_name = infer_state_name_from_title(event_title)
    if not state_name:
        return None

    entity_name = str(market.get("groupItemTitle") or "").strip()
    if not entity_name:
        return None

    entity_type = "party" if entity_name.strip().upper() in PARTY_LABEL_TO_CODE else "candidate"
    state_code = STATE_NAME_TO_CODE[state_name]
    yes_price = extract_price_for_outcome(market, "Yes")
    no_price = extract_price_for_outcome(market, "No")

    match_info = (
        match_candidate_market(state_code, entity_name, df_candidates, df_selected)
        if entity_type == "candidate"
        else match_party_market(state_code, entity_name, df_selected)
    )

    row = {
        "state": state_code,
        "state_name": state_name,
        "polymarket_event_id": event.get("id"),
        "polymarket_event_slug": event.get("slug"),
        "polymarket_event_title": event_title,
        "polymarket_event_url": f"https://polymarket.com/event/{event.get('slug')}",
        "polymarket_market_id": market.get("id"),
        "polymarket_market_slug": market.get("slug"),
        "polymarket_market_question": market.get("question"),
        "polymarket_market_url": f"https://polymarket.com/event/{event.get('slug')}#{market.get('id')}",
        "polymarket_embed_url": f"https://embed.polymarket.com/market?market={market.get('slug')}&height=300",
        "market_entity_name": entity_name,
        "market_entity_type": entity_type,
        "market_party_normalized": PARTY_LABEL_TO_CODE.get(entity_name.strip().upper()),
        "yes_probability": yes_price,
        "no_probability": no_price,
        "last_trade_price": market.get("lastTradePrice"),
        "best_bid": market.get("bestBid"),
        "best_ask": market.get("bestAsk"),
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "active": market.get("active"),
        "closed": market.get("closed"),
        "event_updated_at": event.get("updatedAt"),
        "data_pulled_at": current_utc_iso(),
    }
    row.update(match_info)
    return row


def fetch_active_senate_events(client: PolymarketClient) -> List[Dict[str, object]]:
    data = client.public_search(SENATE_SEARCH_QUERY, limit_per_type=200, events_status="active")
    events = data.get("events", [])
    if not isinstance(events, list):
        raise ValueError("Unexpected Polymarket events search format")

    filtered: List[Dict[str, object]] = []
    seen_slugs = set()
    for event in events:
        title = str(event.get("title") or "").strip()
        slug = str(event.get("slug") or "").strip()
        if not title.endswith("Senate Election Winner"):
            continue
        state_name = infer_state_name_from_title(title)
        if state_name is None:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        filtered.append(event)

    return sorted(filtered, key=lambda item: str(item.get("title") or ""))


def build_top_two_summary(df_markets: pd.DataFrame) -> pd.DataFrame:
    if df_markets.empty:
        return pd.DataFrame()

    summary_rows = []
    for state_code, group in df_markets.groupby("state", sort=True):
        state_rows = group.sort_values(["yes_probability", "volume"], ascending=[False, False], na_position="last").reset_index(drop=True)
        top_one = state_rows.iloc[0] if len(state_rows) >= 1 else None
        top_two = state_rows.iloc[1] if len(state_rows) >= 2 else None

        summary_rows.append(
            {
                "state": state_code,
                "state_name": state_rows.iloc[0]["state_name"],
                "polymarket_event_slug": state_rows.iloc[0]["polymarket_event_slug"],
                "polymarket_event_url": state_rows.iloc[0]["polymarket_event_url"],
                "top_1_entity_name": top_one.get("market_entity_name") if top_one is not None else None,
                "top_1_entity_type": top_one.get("market_entity_type") if top_one is not None else None,
                "top_1_yes_probability": top_one.get("yes_probability") if top_one is not None else None,
                "top_1_market_slug": top_one.get("polymarket_market_slug") if top_one is not None else None,
                "top_1_embed_url": top_one.get("polymarket_embed_url") if top_one is not None else None,
                "top_1_fec_candidate_id": top_one.get("matched_fec_candidate_id") if top_one is not None else None,
                "top_1_matched_candidate_name": top_one.get("matched_candidate_name") if top_one is not None else None,
                "top_1_match_status": top_one.get("candidate_match_status") if top_one is not None else None,
                "top_2_entity_name": top_two.get("market_entity_name") if top_two is not None else None,
                "top_2_entity_type": top_two.get("market_entity_type") if top_two is not None else None,
                "top_2_yes_probability": top_two.get("yes_probability") if top_two is not None else None,
                "top_2_market_slug": top_two.get("polymarket_market_slug") if top_two is not None else None,
                "top_2_embed_url": top_two.get("polymarket_embed_url") if top_two is not None else None,
                "top_2_fec_candidate_id": top_two.get("matched_fec_candidate_id") if top_two is not None else None,
                "top_2_matched_candidate_name": top_two.get("matched_candidate_name") if top_two is not None else None,
                "top_2_match_status": top_two.get("candidate_match_status") if top_two is not None else None,
                "data_pulled_at": current_utc_iso(),
            }
        )

    return pd.DataFrame(summary_rows)


def main() -> None:
    client = PolymarketClient()
    df_candidates = prepare_candidate_universe()
    df_selected = load_selected_candidates()
    events = fetch_active_senate_events(client)

    print(f"Active US Senate winner events found: {len(events)}", flush=True)
    print(f"Selected candidate file available: {'yes' if not df_selected.empty else 'no'}", flush=True)

    market_rows = []
    for event in events:
        active_markets = [
            market
            for market in event.get("markets", [])
            if market.get("active") and not str(market.get("groupItemTitle") or "").strip().lower().startswith(PLACEHOLDER_PREFIX)
        ]
        print(f"Processing {event.get('title')} | active markets={len(active_markets)}", flush=True)
        for market in active_markets:
            row = build_market_row(event, market, df_candidates, df_selected)
            if row is not None:
                market_rows.append(row)

    df_markets = pd.DataFrame(market_rows)
    if not df_markets.empty:
        df_markets["yes_probability"] = pd.to_numeric(df_markets["yes_probability"], errors="coerce")
        df_markets["no_probability"] = pd.to_numeric(df_markets["no_probability"], errors="coerce")
        df_markets["volume"] = pd.to_numeric(df_markets["volume"], errors="coerce")
        df_markets["liquidity"] = pd.to_numeric(df_markets["liquidity"], errors="coerce")
        df_markets = df_markets.sort_values(
            ["state", "yes_probability", "volume"],
            ascending=[True, False, False],
            na_position="last",
        ).reset_index(drop=True)

    df_top_two = build_top_two_summary(df_markets)

    for path in [ALL_MARKETS_PATH, TOP_TWO_PATH, OUTPUTS_ALL_MARKETS_PATH, OUTPUTS_TOP_TWO_PATH]:
        path.parent.mkdir(parents=True, exist_ok=True)

    df_markets.to_csv(ALL_MARKETS_PATH, index=False)
    df_markets.to_csv(OUTPUTS_ALL_MARKETS_PATH, index=False)
    df_top_two.to_csv(TOP_TWO_PATH, index=False)
    df_top_two.to_csv(OUTPUTS_TOP_TWO_PATH, index=False)

    print(f"Saved all-market Polymarket rows to {ALL_MARKETS_PATH}", flush=True)
    print(f"Saved top-two Polymarket summary to {TOP_TWO_PATH}", flush=True)
    print(f"Total active market rows: {len(df_markets)}", flush=True)


if __name__ == "__main__":
    main()
