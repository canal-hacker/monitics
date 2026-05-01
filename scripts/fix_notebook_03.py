from pathlib import Path
import json

path = Path("notebooks/03_select_top_dem_rep_candidates.ipynb")
nb = json.loads(path.read_text(encoding="utf-8"))
for cell in nb["cells"]:
    if cell["cell_type"] == "code" and any("select_top_candidates" in line for line in cell["source"]):
        cell["source"] = [
            "df_selected = select_top_candidates(df_finance, overrides=overrides)\n",
            "df_selected = df_selected[\n",
            "    [\"state\", \"fec_candidate_id\", \"candidate_name\", \"committee_id\", \"total_receipts\", \"total_disbursements\", \"cash_on_hand_end_period\", \"cash_on_hand\", \"debts_owed_by_committee\", \"coverage_end_date\", \"party\", \"party_normalized\", \"selected_candidate\", \"selection_method\", \"selection_rank_within_state_party\", \"manual_override_reason\"]\n",
            "]\n",
            "df_selected = df_selected.rename(columns={\"candidate_name\": \"selected_candidate_name\"})\n",
            "print(\"Selected candidates shape:\", df_selected.shape)\n",
            "df_selected.head()\n",
        ]
path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
