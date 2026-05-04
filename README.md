# 2026 U.S. Senate Money Tracker

A Python data pipeline for tracking campaign finance money in the 2026 U.S. Senate elections using official FEC/OpenFEC data.

## What this project does

- Fetches 2026 U.S. Senate candidates from the OpenFEC API.
- Normalizes candidate metadata by state, party, and office.
- Fetches candidate committee mappings and finance totals.
- Writes default campaign-finance outputs using the latest current-cycle row per candidate when available, while preserving an all-cycle totals file for later analysis.
- Can fetch line-item Schedule A contribution receipts for campaign committees, including individual-only pulls.
- Can fetch line-item Schedule E independent expenditures for Senate candidates.
- Can fetch active Polymarket 2026 Senate winner markets and summarize the top two outcomes per state.
- Selects top Democratic and top Republican candidates in each Senate race using highest total receipts.
- Exports clean CSV snapshots for analysis.

## What this project does NOT do yet

- No House races.
- No advertising data (Google, Meta, etc.).
- No lobbying data.
- No website.

## Setup

1. Create a Python 3.11+ virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env`.
4. Add your `FEC_API_KEY` to `.env`.
5. Run the notebooks in order:
   - `notebooks/00_setup_and_api_smoke_test.ipynb`
   - `notebooks/01_fetch_2026_senate_candidates.ipynb`
   - `notebooks/02_fetch_candidate_committees_and_totals.ipynb`
   - `notebooks/03_select_top_dem_rep_candidates.ipynb`
   - `notebooks/04_build_senate_money_snapshot.ipynb`

## Scripted pipeline

Run these commands from the repo root:

```bash
cd /Users/benedictgordon/Documents/monitics/monitics
```

If you stay one level higher in `/Users/benedictgordon/Documents/monitics`, prepend `monitics/` to script paths.

You can also run the pipeline from scripts instead of notebooks:

```bash
python3 scripts/run_pipeline.py
```

The full runner now checkpoints progress and can resume after interruption. By default it writes:

- `senate_candidate_finance_totals_2026.csv`: latest current-cycle row per candidate when available
- `senate_candidate_finance_totals_2026_all_cycles.csv`: preserved raw all-cycle totals rows

You can inspect scope without calling the API:

```bash
python3 scripts/run_pipeline.py --dry-run
```

To build a resumable Schedule A contribution dataset after committee/totals data exists:

```bash
python3 scripts/fetch_contributions.py --source selected --individual-only --max-committees 5 --max-pages-per-committee 2
python3 scripts/fetch_contributions.py --source selected --individual-only
```

The first command is a safe smoke test. The second continues committee-by-committee and resumes from a dataset-specific manifest in `data/interim/`.
Files in `data/interim/` are local resume checkpoints and are intentionally gitignored.
Full non-individual Schedule A exports are also intended to stay local because they can exceed GitHub's file size limits.

To build a resumable Schedule E outside-spending dataset:

```bash
python3 scripts/fetch_independent_expenditures.py --source selected --max-candidates 5 --max-pages-per-candidate 2
python3 scripts/fetch_independent_expenditures.py --source selected
```

The default Schedule E pull requests `most_recent=true` and excludes notice filings to reduce obvious double counting. If you want notice filings too:

```bash
python3 scripts/fetch_independent_expenditures.py --source selected --include-notices
```

To fetch active Polymarket Senate winner markets and a per-state top-two summary:

```bash
python3 scripts/fetch_polymarket_senate.py
```

To run a faster first-pass finance pull for active Polymarket Senate race states only:

```bash
python3 scripts/run_top_races_pipeline.py
python3 scripts/run_top_races_pipeline.py --dry-run
```

## Key concepts

- **Candidate**: The person running for office as reported by the FEC.
- **Committee**: The legal campaign finance entity associated with a candidate.
- **Principal campaign committee**: The candidate's main official campaign committee.
- **Total receipts**: Money raised by the committee during the reporting period.
- **Total disbursements**: Money spent by the committee.
- **Cash on hand**: Available cash reported by the committee.
- **Coverage end date**: The latest reporting date for the financial snapshot.
- **Nominees may not be final**: Primary elections and candidate filing status can change. This project uses a mechanical financial selection rule.

## Data limitations

- FEC data is updated nightly, but reporting schedules are not real-time.
- Candidate totals may lag current campaign spending.
- The default totals output now focuses on current-cycle coverage dates; older totals remain available in the all-cycle file.
- Multiple candidates from the same party may appear in a state before nominations are finalized.
- Highest receipts is a simple selection heuristic, not a formal nomination decision.
- Manual overrides may be required for special cases.
- Detailed contribution and outside-spending pulls can be large and may need slower pacing to stay within OpenFEC rate limits.
- Schedule E line items can still require de-duplication decisions around notices and amendments depending on the analysis goal.
