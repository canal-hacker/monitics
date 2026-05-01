from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / 'src'))

from midterm_money.config import settings
from midterm_money.candidate_selection import load_manual_overrides, select_top_candidates
from midterm_money.fec_client import FECClient
from midterm_money.normalize import normalize_party


def save_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main() -> None:
    client = FECClient()

    candidates_path = ROOT / 'data' / 'processed' / 'senate_candidates_2026_all.csv'
    outputs_candidate_path = ROOT / 'outputs' / 'senate_candidates_2026_all.csv'

    if not candidates_path.exists():
        print('Fetching 2026 Senate candidates...')
        query_params = {
            'cycle': settings.ELECTION_CYCLE,
            'office': settings.OFFICE,
            'per_page': 100,
            'sort': 'name',
        }
        candidates = client.fetch_all('/candidates/search/', params=query_params)
        df_candidates = pd.DataFrame(candidates)
        candidate_fields = [
            'candidate_id', 'name', 'party', 'party_full', 'state', 'office',
            'election_years', 'cycles', 'incumbent_challenge_status', 'candidate_status',
            'load_date', 'update_date'
        ]
        available_fields = [field for field in candidate_fields if field in df_candidates.columns]
        df_clean = df_candidates[available_fields].copy()
        df_clean['party_normalized'] = df_clean['party'].apply(normalize_party)
        df_clean['raw_party'] = df_clean['party']
        df_clean = df_clean.rename(columns={'candidate_id': 'fec_candidate_id'})
        save_csv(df_clean, candidates_path)
        save_csv(df_clean, outputs_candidate_path)
        print('Saved candidate universe to', candidates_path)
    else:
        df_clean = pd.read_csv(candidates_path, dtype=str)
        print('Loaded existing candidate universe, rows=', len(df_clean))

    committee_rows = []
    totals_rows = []
    missing_committees = []
    missing_totals = []

    print('Fetching committees and totals for', len(df_clean), 'candidates...')
    for idx, row in df_clean.iterrows():
        candidate_id = row.get('fec_candidate_id')
        name = row.get('name')
        if not candidate_id:
            continue

        committee_data = None
        totals_data = None
        try:
            committee_data = client.get(f'/candidate/{candidate_id}/committees/', params={'per_page': 100})
        except Exception as exc:
            missing_committees.append((candidate_id, str(exc)))

        try:
            totals_data = client.get(f'/candidate/{candidate_id}/totals/')
        except Exception as exc:
            try:
                totals_data = client.get(f'/candidates/{candidate_id}/totals/')
            except Exception as exc2:
                missing_totals.append((candidate_id, f'{exc} | {exc2}'))

        committee_results = committee_data.get('results', []) if committee_data else []
        for committee in committee_results:
            committee_rows.append({
                'fec_candidate_id': candidate_id,
                'candidate_name': name,
                'committee_id': committee.get('committee_id'),
                'committee_name': committee.get('name'),
                'designation': committee.get('designation'),
                'designation_full': committee.get('designation_full'),
                'committee_type': committee.get('committee_type'),
                'committee_type_full': committee.get('committee_type_full'),
                'is_principal': (committee.get('designation') == 'P' or (committee.get('designation_full') or '').lower().find('principal') >= 0),
            })

        totals_results = totals_data.get('results', []) if totals_data else []
        if totals_results:
            for totals in totals_results:
                totals_rows.append({
                    'fec_candidate_id': candidate_id,
                    'candidate_name': name,
                    'state': row.get('state'),
                    'party': row.get('party'),
                    'committee_id': totals.get('committee_id'),
                    'total_receipts': totals.get('total_receipts'),
                    'total_disbursements': totals.get('total_disbursements'),
                    'cash_on_hand_end_period': totals.get('cash_on_hand_end_period'),
                    'cash_on_hand': totals.get('cash_on_hand'),
                    'debts_owed_by_committee': totals.get('debts_owed_by_committee'),
                    'coverage_end_date': totals.get('coverage_end_date'),
                    'cycle': totals.get('cycle'),
                    'source_endpoint': 'candidate_totals',
                })
        else:
            missing_totals.append((candidate_id, 'No totals returned'))

    df_committees = pd.DataFrame(committee_rows)
    df_totals = pd.DataFrame(totals_rows)

    for out_path in [
        ROOT / 'data' / 'processed' / 'senate_candidate_committees_2026.csv',
        ROOT / 'outputs' / 'senate_candidate_committees_2026.csv',
        ROOT / 'data' / 'processed' / 'senate_candidate_finance_totals_2026.csv',
        ROOT / 'outputs' / 'senate_candidate_finance_totals_2026.csv',
    ]:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    save_csv(df_committees, ROOT / 'data' / 'processed' / 'senate_candidate_committees_2026.csv')
    save_csv(df_committees, ROOT / 'outputs' / 'senate_candidate_committees_2026.csv')
    save_csv(df_totals, ROOT / 'data' / 'processed' / 'senate_candidate_finance_totals_2026.csv')
    save_csv(df_totals, ROOT / 'outputs' / 'senate_candidate_finance_totals_2026.csv')

    print('Saved committee and totals tables.')
    print('Missing committees count:', len(missing_committees))
    print('Missing totals count:', len(missing_totals))

    df_selected = select_top_candidates(df_totals, overrides=load_manual_overrides(ROOT / 'config' / 'manual_overrides.yml'))
    selected_cols = [
        'state', 'fec_candidate_id', 'candidate_name', 'committee_id', 'total_receipts',
        'total_disbursements', 'cash_on_hand_end_period', 'cash_on_hand', 'debts_owed_by_committee',
        'coverage_end_date', 'party', 'party_normalized', 'selected_candidate', 'selection_method',
        'selection_rank_within_state_party', 'manual_override_reason'
    ]
    df_selected = df_selected[selected_cols].rename(columns={'candidate_name': 'selected_candidate_name'})
    save_csv(df_selected, ROOT / 'data' / 'processed' / 'senate_top_dem_rep_candidates_2026.csv')
    save_csv(df_selected, ROOT / 'outputs' / 'senate_top_dem_rep_candidates_2026.csv')
    print('Saved top candidate selection files.')

    wide = df_selected[df_selected['selected_candidate'] == True].pivot(
        index='state',
        columns='party_normalized',
        values=['selected_candidate_name', 'fec_candidate_id', 'committee_id', 'total_receipts']
    )
    wide.columns = [f'{col[0].lower()}_{col[1].lower()}' for col in wide.columns]
    wide = wide.reset_index()
    save_csv(wide, ROOT / 'outputs' / 'senate_two_party_race_universe_2026.csv')
    print('Saved two-party race universe file.')

    snapshot_rows = []
    for _, row in wide.iterrows():
        snapshot_rows.append({
            'state': row['state'],
            'dem_candidate_name': row.get('selected_candidate_name_dem'),
            'dem_fec_candidate_id': row.get('fec_candidate_id_dem'),
            'dem_committee_id': row.get('committee_id_dem'),
            'dem_total_receipts': row.get('total_receipts_dem'),
            'dem_total_disbursements': None,
            'dem_cash_on_hand': None,
            'dem_debts_owed_by_committee': None,
            'dem_coverage_end_date': None,
            'dem_selection_method': None,
            'rep_candidate_name': row.get('selected_candidate_name_rep'),
            'rep_fec_candidate_id': row.get('fec_candidate_id_rep'),
            'rep_committee_id': row.get('committee_id_rep'),
            'rep_total_receipts': row.get('total_receipts_rep'),
            'rep_total_disbursements': None,
            'rep_cash_on_hand': None,
            'rep_debts_owed_by_committee': None,
            'rep_coverage_end_date': None,
            'rep_selection_method': None,
            'direct_money_data_pulled_at': pd.Timestamp.now().isoformat(),
            'notes': 'Snapshot built from top candidate selection. Some fields are placeholders pending upstream totals enrichment.',
        })

    df_snapshot = pd.DataFrame(snapshot_rows)
    df_snapshot['dem_minus_rep_total_receipts'] = pd.to_numeric(df_snapshot['dem_total_receipts'], errors='coerce').fillna(0) - pd.to_numeric(df_snapshot['rep_total_receipts'], errors='coerce').fillna(0)
    df_snapshot['dem_minus_rep_cash_on_hand'] = None
    save_csv(df_snapshot, ROOT / 'outputs' / 'senate_money_snapshot_2026.csv')
    print('Saved final snapshot file.')


if __name__ == '__main__':
    main()
