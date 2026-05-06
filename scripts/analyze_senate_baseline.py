from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd

sys.path.append(str(ROOT / "src"))

from midterm_money.config import settings


ANALYSIS_DIR = ROOT / "analysis"
PROCESSED_DIR = ROOT / "data" / "processed"
INTERIM_DIR = ROOT / "data" / "interim"

SELECTED_PATH = PROCESSED_DIR / f"senate_top_dem_rep_candidates_{settings.ELECTION_CYCLE}.csv"
IE_PATH = PROCESSED_DIR / f"senate_independent_expenditures_{settings.ELECTION_CYCLE}_selected.csv"
INDIVIDUAL_PATH = PROCESSED_DIR / f"senate_individual_contributions_{settings.ELECTION_CYCLE}_selected.csv"
INDIVIDUAL_MANIFEST_PATH = (
    INTERIM_DIR / f"senate_individual_contributions_{settings.ELECTION_CYCLE}_selected" / "manifest.csv"
)


def clean_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def load_manifest_latest_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}

    latest_rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            committee_id = row.get("committee_id")
            if committee_id:
                latest_rows[committee_id] = row
    return latest_rows


def build_candidate_summary(
    selected: pd.DataFrame,
    ie: pd.DataFrame,
    contributions: pd.DataFrame,
    manifest_rows: dict[str, dict[str, str]],
) -> pd.DataFrame:
    ie = ie.copy()
    ie["expenditure_amount"] = pd.to_numeric(ie["expenditure_amount"], errors="coerce").fillna(0.0)
    ie["support_oppose_indicator"] = ie["support_oppose_indicator"].fillna("")
    ie_summary = (
        ie.groupby(
            [
                "source_fec_candidate_id",
                "source_candidate_name",
                "source_state",
                "source_party_normalized",
                "support_oppose_indicator",
            ],
            dropna=False,
        )["expenditure_amount"]
        .sum()
        .reset_index()
        .pivot_table(
            index=[
                "source_fec_candidate_id",
                "source_candidate_name",
                "source_state",
                "source_party_normalized",
            ],
            columns="support_oppose_indicator",
            values="expenditure_amount",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )
    ie_summary.columns.name = None
    if "S" not in ie_summary.columns:
        ie_summary["S"] = 0.0
    if "O" not in ie_summary.columns:
        ie_summary["O"] = 0.0
    ie_summary = ie_summary.rename(
        columns={
            "source_fec_candidate_id": "fec_candidate_id",
            "source_candidate_name": "selected_candidate_name",
            "source_state": "state",
            "source_party_normalized": "party_normalized",
            "S": "ie_support_amount",
            "O": "ie_oppose_amount",
        }
    )
    ie_summary["ie_total_amount"] = ie_summary["ie_support_amount"] + ie_summary["ie_oppose_amount"]
    ie_summary["ie_net_support_minus_oppose"] = (
        ie_summary["ie_support_amount"] - ie_summary["ie_oppose_amount"]
    )

    contributions = contributions.copy()
    contributions["contribution_receipt_amount"] = pd.to_numeric(
        contributions["contribution_receipt_amount"], errors="coerce"
    ).fillna(0.0)
    contributions["sub_id"] = contributions["sub_id"].astype(str)
    contribution_summary = (
        contributions.groupby(
            [
                "source_fec_candidate_id",
                "source_candidate_name",
                "source_state",
                "source_party_normalized",
                "source_committee_id",
            ],
            dropna=False,
        )
        .agg(
            schedule_a_rows_raw=("sub_id", "size"),
            schedule_a_unique_sub_ids=("sub_id", "nunique"),
            schedule_a_net_amount_raw=("contribution_receipt_amount", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "source_fec_candidate_id": "fec_candidate_id",
                "source_candidate_name": "selected_candidate_name",
                "source_state": "state",
                "source_party_normalized": "party_normalized",
                "source_committee_id": "committee_id",
            }
        )
    )
    deduped = contributions.drop_duplicates(subset=["sub_id"]).copy()
    deduped_summary = (
        deduped.groupby(
            [
                "source_fec_candidate_id",
                "source_candidate_name",
                "source_state",
                "source_party_normalized",
                "source_committee_id",
            ],
            dropna=False,
        )["contribution_receipt_amount"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "source_fec_candidate_id": "fec_candidate_id",
                "source_candidate_name": "selected_candidate_name",
                "source_state": "state",
                "source_party_normalized": "party_normalized",
                "source_committee_id": "committee_id",
                "contribution_receipt_amount": "schedule_a_net_amount_deduped_by_sub_id",
            }
        )
    )
    contribution_summary = contribution_summary.merge(
        deduped_summary,
        on=["fec_candidate_id", "selected_candidate_name", "state", "party_normalized", "committee_id"],
        how="left",
    )
    contribution_summary["schedule_a_duplication_factor"] = (
        contribution_summary["schedule_a_rows_raw"]
        / contribution_summary["schedule_a_unique_sub_ids"].where(
            contribution_summary["schedule_a_unique_sub_ids"] > 0
        )
    )
    contribution_summary["schedule_a_suspect_repeated_page_fetch"] = (
        contribution_summary["schedule_a_rows_raw"] > contribution_summary["schedule_a_unique_sub_ids"] * 2
    )

    manifest_frame = pd.DataFrame(
        [
            {
                "committee_id": committee_id,
                "schedule_a_manifest_status": row.get("status", ""),
                "schedule_a_manifest_pages_fetched": pd.to_numeric(
                    row.get("pages_fetched", 0), errors="coerce"
                ),
                "schedule_a_manifest_total_pages_reported": pd.to_numeric(
                    row.get("total_pages_reported", 0), errors="coerce"
                ),
                "schedule_a_manifest_row_count": pd.to_numeric(row.get("row_count", 0), errors="coerce"),
                "schedule_a_manifest_fetched_at": row.get("fetched_at", ""),
                "schedule_a_manifest_error": row.get("error", ""),
            }
            for committee_id, row in manifest_rows.items()
        ]
    )

    candidate_summary = selected.copy()
    numeric_cols = [
        "total_receipts",
        "total_disbursements",
        "cash_on_hand_end_period",
        "cash_on_hand",
        "debts_owed_by_committee",
    ]
    for column in numeric_cols:
        candidate_summary[column] = pd.to_numeric(candidate_summary[column], errors="coerce").fillna(0.0)

    candidate_summary = candidate_summary.merge(
        ie_summary,
        on=["fec_candidate_id", "selected_candidate_name", "state", "party_normalized"],
        how="left",
    )
    candidate_summary = candidate_summary.merge(
        contribution_summary,
        on=["fec_candidate_id", "selected_candidate_name", "state", "party_normalized", "committee_id"],
        how="left",
    )
    candidate_summary = candidate_summary.merge(manifest_frame, on="committee_id", how="left")

    fill_zero_cols = [
        "ie_support_amount",
        "ie_oppose_amount",
        "ie_total_amount",
        "ie_net_support_minus_oppose",
        "schedule_a_rows_raw",
        "schedule_a_unique_sub_ids",
        "schedule_a_net_amount_raw",
        "schedule_a_net_amount_deduped_by_sub_id",
        "schedule_a_duplication_factor",
        "schedule_a_manifest_pages_fetched",
        "schedule_a_manifest_total_pages_reported",
        "schedule_a_manifest_row_count",
    ]
    for column in fill_zero_cols:
        if column in candidate_summary.columns:
            candidate_summary[column] = candidate_summary[column].fillna(0.0)
    if "schedule_a_suspect_repeated_page_fetch" in candidate_summary.columns:
        candidate_summary["schedule_a_suspect_repeated_page_fetch"] = candidate_summary[
            "schedule_a_suspect_repeated_page_fetch"
        ].fillna(False)
    else:
        candidate_summary["schedule_a_suspect_repeated_page_fetch"] = False

    candidate_summary = candidate_summary.sort_values(
        ["total_receipts", "cash_on_hand", "selected_candidate_name"], ascending=[False, False, True]
    ).reset_index(drop=True)
    return candidate_summary


def build_state_summary(candidate_summary: pd.DataFrame) -> pd.DataFrame:
    state_totals = (
        candidate_summary.groupby("state", dropna=False)
        .agg(
            selected_candidate_count=("fec_candidate_id", "size"),
            combined_total_receipts=("total_receipts", "sum"),
            combined_total_disbursements=("total_disbursements", "sum"),
            combined_cash_on_hand=("cash_on_hand", "sum"),
            combined_ie_total_amount=("ie_total_amount", "sum"),
            suspect_schedule_a_candidate_count=(
                "schedule_a_suspect_repeated_page_fetch",
                lambda s: int(s.fillna(False).sum()),
            ),
        )
        .reset_index()
    )

    top_two = (
        candidate_summary.sort_values(
            ["state", "total_receipts", "cash_on_hand", "selected_candidate_name"],
            ascending=[True, False, False, True],
        )
        .groupby("state", dropna=False)
        .head(2)
        .copy()
    )
    top_two["rank_within_state"] = top_two.groupby("state").cumcount() + 1

    candidate_1 = (
        top_two[top_two["rank_within_state"] == 1]
        .rename(
            columns={
                "selected_candidate_name": "leading_candidate_name",
                "party_normalized": "leading_candidate_party",
                "fec_candidate_id": "leading_fec_candidate_id",
                "committee_id": "leading_committee_id",
                "total_receipts": "leading_total_receipts",
                "cash_on_hand": "leading_cash_on_hand",
                "ie_total_amount": "leading_ie_total_amount",
            }
        )[
            [
                "state",
                "leading_candidate_name",
                "leading_candidate_party",
                "leading_fec_candidate_id",
                "leading_committee_id",
                "leading_total_receipts",
                "leading_cash_on_hand",
                "leading_ie_total_amount",
            ]
        ]
    )
    candidate_2 = (
        top_two[top_two["rank_within_state"] == 2]
        .rename(
            columns={
                "selected_candidate_name": "runner_up_candidate_name",
                "party_normalized": "runner_up_candidate_party",
                "fec_candidate_id": "runner_up_fec_candidate_id",
                "committee_id": "runner_up_committee_id",
                "total_receipts": "runner_up_total_receipts",
                "cash_on_hand": "runner_up_cash_on_hand",
                "ie_total_amount": "runner_up_ie_total_amount",
            }
        )[
            [
                "state",
                "runner_up_candidate_name",
                "runner_up_candidate_party",
                "runner_up_fec_candidate_id",
                "runner_up_committee_id",
                "runner_up_total_receipts",
                "runner_up_cash_on_hand",
                "runner_up_ie_total_amount",
            ]
        ]
    )

    state_summary = state_totals.merge(candidate_1, on="state", how="left").merge(candidate_2, on="state", how="left")
    state_summary["leading_receipts_share"] = (
        state_summary["leading_total_receipts"]
        / state_summary["combined_total_receipts"].where(state_summary["combined_total_receipts"] > 0)
    )
    state_summary["leading_cash_share"] = (
        state_summary["leading_cash_on_hand"]
        / state_summary["combined_cash_on_hand"].where(state_summary["combined_cash_on_hand"] > 0)
    )
    state_summary["receipts_gap_between_top_two"] = (
        state_summary["leading_total_receipts"] - state_summary["runner_up_total_receipts"].fillna(0.0)
    )
    state_summary["cash_gap_between_top_two"] = (
        state_summary["leading_cash_on_hand"] - state_summary["runner_up_cash_on_hand"].fillna(0.0)
    )
    state_summary = state_summary.sort_values(
        ["combined_total_receipts", "state"], ascending=[False, True]
    ).reset_index(drop=True)
    return state_summary


def build_party_summary(candidate_summary: pd.DataFrame) -> pd.DataFrame:
    party_summary = (
        candidate_summary.groupby("party_normalized", dropna=False)
        .agg(
            selected_candidate_count=("fec_candidate_id", "size"),
            states_represented=("state", "nunique"),
            total_receipts=("total_receipts", "sum"),
            total_disbursements=("total_disbursements", "sum"),
            cash_on_hand=("cash_on_hand", "sum"),
            debts_owed_by_committee=("debts_owed_by_committee", "sum"),
            ie_support_amount=("ie_support_amount", "sum"),
            ie_oppose_amount=("ie_oppose_amount", "sum"),
            ie_total_amount=("ie_total_amount", "sum"),
            ie_net_support_minus_oppose=("ie_net_support_minus_oppose", "sum"),
            candidates_with_nonzero_ie=("ie_total_amount", lambda s: int((s > 0).sum())),
            suspect_schedule_a_candidate_count=(
                "schedule_a_suspect_repeated_page_fetch",
                lambda s: int(s.fillna(False).sum()),
            ),
        )
        .reset_index()
    )

    for column in ["total_receipts", "cash_on_hand", "ie_total_amount"]:
        total = pd.to_numeric(party_summary[column], errors="coerce").fillna(0.0).sum()
        share_column = f"{column}_share_of_selected_total"
        party_summary[share_column] = party_summary[column] / total if total else 0.0

    party_summary["outside_to_receipts_ratio"] = (
        party_summary["ie_total_amount"]
        / party_summary["total_receipts"].where(party_summary["total_receipts"] > 0)
    )

    party_order = {"DEM": 0, "REP": 1}
    party_summary["_sort_order"] = party_summary["party_normalized"].map(party_order).fillna(99)
    party_summary = party_summary.sort_values(
        ["_sort_order", "total_receipts", "party_normalized"], ascending=[True, False, True]
    ).drop(columns="_sort_order")
    return party_summary.reset_index(drop=True)


def build_headline_metrics(party_summary: pd.DataFrame) -> pd.DataFrame:
    metric_specs = [
        (
            "total_receipts",
            "direct_money",
            "Total receipts",
            "Trusted current-cycle campaign receipts from selected candidate committees.",
        ),
        (
            "cash_on_hand",
            "direct_money",
            "Cash on hand",
            "Trusted end-of-period cash for selected candidate committees.",
        ),
        (
            "ie_total_amount",
            "outside_money",
            "Outside spending total",
            "Trusted default-view independent expenditures targeting selected candidates.",
        ),
        (
            "ie_support_amount",
            "outside_money",
            "Outside support",
            "Supportive independent expenditures for selected candidates.",
        ),
        (
            "ie_oppose_amount",
            "outside_money",
            "Outside opposition",
            "Opposing independent expenditures for selected candidates.",
        ),
    ]

    rows: list[dict[str, object]] = []
    for metric_key, metric_group, metric_label, note in metric_specs:
        total = pd.to_numeric(party_summary[metric_key], errors="coerce").fillna(0.0).sum()
        for row in party_summary.itertuples():
            amount = float(getattr(row, metric_key))
            rows.append(
                {
                    "party_normalized": row.party_normalized,
                    "metric_group": metric_group,
                    "metric_key": metric_key,
                    "metric_label": metric_label,
                    "amount": amount,
                    "share_of_metric_total": (amount / total) if total else 0.0,
                    "trusted_for_mvp": True,
                    "note": note,
                }
            )

    rows.append(
        {
            "party_normalized": "ALL",
            "metric_group": "outside_money",
            "metric_key": "dark_money_total",
            "metric_label": "Dark money total",
            "amount": float("nan"),
            "share_of_metric_total": float("nan"),
            "trusted_for_mvp": False,
            "note": (
                "Not available yet. This requires classifying outside spenders and disclosure status; "
                "candidate donor receipts do not answer it."
            ),
        }
    )

    return pd.DataFrame(rows)


def build_contribution_quality_audit(
    candidate_summary: pd.DataFrame,
) -> pd.DataFrame:
    audit_cols = [
        "state",
        "selected_candidate_name",
        "party_normalized",
        "committee_id",
        "schedule_a_manifest_status",
        "schedule_a_manifest_pages_fetched",
        "schedule_a_manifest_total_pages_reported",
        "schedule_a_manifest_row_count",
        "schedule_a_rows_raw",
        "schedule_a_unique_sub_ids",
        "schedule_a_duplication_factor",
        "schedule_a_net_amount_raw",
        "schedule_a_net_amount_deduped_by_sub_id",
        "schedule_a_suspect_repeated_page_fetch",
        "schedule_a_manifest_fetched_at",
        "schedule_a_manifest_error",
    ]
    audit = candidate_summary[audit_cols].copy()
    audit["schedule_a_duplication_factor"] = audit["schedule_a_duplication_factor"].round(2)
    audit = audit.sort_values(
        ["schedule_a_suspect_repeated_page_fetch", "schedule_a_rows_raw", "selected_candidate_name"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return audit


def build_overview_markdown(
    candidate_summary: pd.DataFrame,
    state_summary: pd.DataFrame,
    party_summary: pd.DataFrame,
    audit: pd.DataFrame,
) -> str:
    total_selected_receipts = candidate_summary["total_receipts"].sum()
    total_selected_cash = candidate_summary["cash_on_hand"].sum()
    total_ie = candidate_summary["ie_total_amount"].sum()
    suspect_count = int(audit["schedule_a_suspect_repeated_page_fetch"].fillna(False).sum())
    nonzero_ie_count = int((candidate_summary["ie_total_amount"] > 0).sum())

    top_receipt_lines = [
        f"- {row.selected_candidate_name} ({row.party_normalized}-{row.state}): "
        f"${row.total_receipts:,.0f} receipts, ${row.cash_on_hand:,.0f} cash"
        for row in candidate_summary.head(10).itertuples()
    ]
    top_state_lines = [
        f"- {row.state}: ${row.combined_total_receipts:,.0f} combined receipts; "
        f"leader {row.leading_candidate_name} at {row.leading_receipts_share:.1%} share"
        for row in state_summary.head(10).itertuples()
    ]
    party_lines = [
        f"- {row.party_normalized}: ${row.total_receipts:,.0f} receipts, "
        f"${row.cash_on_hand:,.0f} cash, ${row.ie_total_amount:,.0f} outside money"
        for row in party_summary.itertuples()
    ]
    closest_state_lines = [
        f"- {row.state}: {row.leading_candidate_name} leads by "
        f"${row.receipts_gap_between_top_two:,.0f} in receipts"
        for row in state_summary.sort_values("receipts_gap_between_top_two").head(5).itertuples()
    ]
    top_ie_lines = [
        f"- {row.selected_candidate_name} ({row.party_normalized}-{row.state}): "
        f"${row.ie_total_amount:,.0f} total IE, net ${row.ie_net_support_minus_oppose:,.0f}"
        for row in candidate_summary.sort_values("ie_total_amount", ascending=False).head(10).itertuples()
    ]

    lines = [
        f"# 2026 Senate Baseline Overview ({settings.ELECTION_CYCLE})",
        "",
        "## Trusted Baseline",
        f"- Selected candidates analyzed: {len(candidate_summary)}",
        f"- States represented: {state_summary['state'].nunique()}",
        f"- Combined selected-candidate receipts: ${total_selected_receipts:,.0f}",
        f"- Combined selected-candidate cash on hand: ${total_selected_cash:,.0f}",
        f"- Combined independent expenditures in default view: ${total_ie:,.0f}",
        f"- Candidates with nonzero independent expenditures: {nonzero_ie_count}",
        "",
        "## Party Snapshot",
        *party_lines,
        "",
        "## Top Candidates By Receipts",
        *top_receipt_lines,
        "",
        "## Biggest Money States",
        *top_state_lines,
        "",
        "## Closest States By Receipt Gap",
        *closest_state_lines,
        "",
        "## Largest Independent Expenditure Targets",
        *top_ie_lines,
        "",
        "## Schedule A Quality Warning",
        "- The current `senate_individual_contributions_*` file is not trustworthy for donor-level analysis yet.",
        f"- Suspect repeated-page fetch pattern detected for {suspect_count} selected committees.",
        "- Multi-page committees appear to contain the same first-page rows repeated many times.",
        "- Use aggregate totals and independent expenditures for baseline product decisions until the Schedule A fetch is repaired.",
        "- `Dark money` is not available from the donor file; it requires classifying outside spenders and disclosure status.",
        "",
    ]
    return "\n".join(lines)


def _format_millions(value: float, _: int) -> str:
    return f"${value / 1_000_000:.0f}M"


def render_party_headline_chart(party_summary: pd.DataFrame, output_path: Path) -> None:
    if party_summary.empty:
        return

    plot_data = party_summary.copy()
    parties = plot_data["party_normalized"].astype(str).tolist()
    x = list(range(len(parties)))
    width = 0.24

    colors = {"DEM": "#1f77b4", "REP": "#d62728"}
    fallback_color = "#6b7280"
    bar_colors = [colors.get(party, fallback_color) for party in parties]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), constrained_layout=True)
    fig.suptitle("2026 Selected Senate Baseline: Direct vs Outside Money", fontsize=15, fontweight="bold")

    axes[0].bar(
        [value - width for value in x],
        plot_data["total_receipts"],
        width=width,
        color=bar_colors,
        alpha=0.95,
        label="Total receipts",
    )
    axes[0].bar(
        x,
        plot_data["cash_on_hand"],
        width=width,
        color=bar_colors,
        alpha=0.55,
        label="Cash on hand",
    )
    axes[0].bar(
        [value + width for value in x],
        plot_data["ie_total_amount"],
        width=width,
        color="#111827",
        alpha=0.9,
        label="Outside spending total",
    )
    axes[0].set_xticks(x, parties)
    axes[0].set_title("Headline totals by party")
    axes[0].yaxis.set_major_formatter(FuncFormatter(_format_millions))
    axes[0].grid(axis="y", alpha=0.2)
    axes[0].legend(frameon=False, fontsize=9)

    axes[1].bar(x, plot_data["ie_support_amount"], color="#16a34a", label="Outside support")
    axes[1].bar(
        x,
        plot_data["ie_oppose_amount"],
        bottom=plot_data["ie_support_amount"],
        color="#f59e0b",
        label="Outside opposition",
    )
    axes[1].set_xticks(x, parties)
    axes[1].set_title("Outside spending mix")
    axes[1].yaxis.set_major_formatter(FuncFormatter(_format_millions))
    axes[1].grid(axis="y", alpha=0.2)
    axes[1].legend(frameon=False, fontsize=9)

    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    selected = pd.read_csv(SELECTED_PATH, low_memory=False)
    if "selected_candidate" in selected.columns:
        selected = selected[selected["selected_candidate"].apply(clean_bool)].copy()
    ie = pd.read_csv(IE_PATH, low_memory=False)
    contributions = pd.read_csv(INDIVIDUAL_PATH, low_memory=False)
    manifest_rows = load_manifest_latest_rows(INDIVIDUAL_MANIFEST_PATH)

    candidate_summary = build_candidate_summary(selected, ie, contributions, manifest_rows)
    state_summary = build_state_summary(candidate_summary)
    party_summary = build_party_summary(candidate_summary)
    headline_metrics = build_headline_metrics(party_summary)
    audit = build_contribution_quality_audit(candidate_summary)
    overview = build_overview_markdown(candidate_summary, state_summary, party_summary, audit)

    candidate_summary_path = ANALYSIS_DIR / f"senate_baseline_candidate_summary_{settings.ELECTION_CYCLE}_selected.csv"
    state_summary_path = ANALYSIS_DIR / f"senate_baseline_state_summary_{settings.ELECTION_CYCLE}_selected.csv"
    party_summary_path = ANALYSIS_DIR / f"senate_baseline_party_summary_{settings.ELECTION_CYCLE}_selected.csv"
    headline_metrics_path = ANALYSIS_DIR / f"senate_baseline_headline_metrics_{settings.ELECTION_CYCLE}_selected.csv"
    audit_path = ANALYSIS_DIR / f"senate_individual_contributions_quality_audit_{settings.ELECTION_CYCLE}_selected.csv"
    overview_path = ANALYSIS_DIR / f"senate_baseline_overview_{settings.ELECTION_CYCLE}_selected.md"
    chart_path = ANALYSIS_DIR / f"senate_baseline_party_headline_chart_{settings.ELECTION_CYCLE}_selected.png"

    candidate_summary.to_csv(candidate_summary_path, index=False)
    state_summary.to_csv(state_summary_path, index=False)
    party_summary.to_csv(party_summary_path, index=False)
    headline_metrics.to_csv(headline_metrics_path, index=False)
    audit.to_csv(audit_path, index=False)
    overview_path.write_text(overview, encoding="utf-8")
    render_party_headline_chart(party_summary, chart_path)

    suspect_count = int(audit["schedule_a_suspect_repeated_page_fetch"].fillna(False).sum())
    print(f"Saved candidate summary to {candidate_summary_path}")
    print(f"Saved state summary to {state_summary_path}")
    print(f"Saved party summary to {party_summary_path}")
    print(f"Saved headline metrics to {headline_metrics_path}")
    print(f"Saved contribution quality audit to {audit_path}")
    print(f"Saved overview markdown to {overview_path}")
    print(f"Saved party headline chart to {chart_path}")
    print(f"Selected candidates analyzed: {len(candidate_summary)}")
    print(f"States analyzed: {state_summary['state'].nunique()}")
    print(f"Parties summarized: {len(party_summary)}")
    print(f"Suspect Schedule A committees: {suspect_count}")


if __name__ == "__main__":
    main()
