# CLAUDE.md — CLO Dashboard

Streamlit dashboard that scrapes real CLO (Collateralized Loan Obligation) data from SEC
EDGAR NPORT-P filings and presents it through an interactive web app. Portfolio project by
Aden Juda for 37 Spruce, a CLO-focused startup. **Every number is real** — scraped from
quarterly portfolio disclosures filed by five public CLO equity funds. No simulated data.

## Run locally

```bash
cd ~/Documents/clo_scraper
source venv/bin/activate
export PYTHONPATH=.
streamlit run Overview.py
```

Re-scrape fresh data (rebuilds the DB from scratch — `run_pipeline.py` clears all tables
first, then re-scrapes the last 4 quarterly filings per fund):

```bash
python run_pipeline.py
```

Diagnostics: `python dump_managers.py` lists every manager name currently in the DB.

## Tech stack

Python 3.12+ · Streamlit · SQLAlchemy + SQLite (`data/clo_data.db`, **committed to git**) ·
Plotly · Pandas · Requests + BeautifulSoup (EDGAR scraping). GitHub repo
`yungsemitone/clo-dashboard` (private).

## Deploy (Fly.io)

Primary host is **Fly.io**: app `clo-dashboard` (region `iad`, personal org) →
https://clo-dashboard.fly.dev. Always-on (`min_machines_running = 1`, `auto_stop_machines =
"off"` in `fly.toml`). Containerized via `Dockerfile` (the committed DB is baked into the
image; `.dockerignore` keeps `secrets.toml` and scraper working dirs out).

```bash
fly deploy -a clo-dashboard          # build + ship (run from repo root)
fly status -a clo-dashboard          # machine health
fly secrets list -a clo-dashboard    # PASSWORD + ANTHROPIC_API_KEY (set as Fly secrets)
fly logs -a clo-dashboard
```

Secrets are **Fly secrets**, not in the image: `PASSWORD` (auth gate) and `ANTHROPIC_API_KEY`
(AI summaries). Set/rotate with `fly secrets set NAME=value -a clo-dashboard`. Fly does **not**
auto-deploy on git push — run `fly deploy` (or wire a `flyctl deploy` step into CI). Render was
the previous host (auto-deployed on push); it can be decommissioned.

## Data pipeline

Five public CLO equity funds file NPORT-P quarterly: **OXLC** (Oxford Lane), **ECC** (Eagle
Point), **OCCI** (OFS Credit), **PDCC** (Pearl Diver), **PRIF** (Priority Income).

`run_pipeline.py` → `src/scrapers/nport_scraper.py`:
1. Hits EDGAR submissions API for each fund, gets last 4 NPORT-P accession numbers.
2. Fetches each filing's `primary_doc.xml` (raw XML, not the XSLT-rendered HTML).
3. Regex-parses issuer name, title, par balance, market value, CUSIP.
4. Filters for CLO holdings (keywords: CLO, Loan Fund, Credit Fund, Funding Ltd).
5. Normalizes SPV/issuer names to canonical managers via `MANAGER_MAP` (200+ entries) in
   `nport_scraper.py`.
6. Stores in `deals` (one row per unique CLO) and `fund_holdings` (one row per
   fund-deal-filing-date).

Current data: ~749 deals, ~2,686 holdings, ~104 managers, 4 quarters per fund (latest
filings Feb–Mar 2026).

Key derived metric: **implied price** = market_value / par × 100 (cents on the dollar). See
`FundHolding.implied_price` in `schema.py`. Par is the *fund's position* face value, not
total deal size.

## Schema (`src/models/schema.py`)

- **deals**: `id, deal_name (unique), manager, trustee, deal_size_mm, status, source_url`
- **fund_holdings**: `id, deal_id (FK), source_fund, filing_date, par_amount, market_value,
  cusip` — unique on `(deal_id, source_fund, filing_date)`
- **report_snapshots**: empty. Reserved for trustee-report data (OC/IC tests, WARF,
  diversity, waterfall) once trustee portal access exists. Parsers in `src/parsers/` are
  built but unused.

## Pages

- `Overview.py` — top metrics, deals-by-manager chart, implied-price histogram, all-deals
  table.
- `pages/1_Deal_Browser.py` — pick a manager; deals grouped by series via `get_base_name()`;
  expander-based detail.
- `pages/2_Manager_Rankings.py` — leaderboard sortable by deals/par/avg price/funds.
- `pages/3_Cross_Fund.py` — deals held by 2+ funds with real prices (≥1¢); dot plot of fund
  marks sorted by widest valuation spread. Most analytically interesting page. Per-deal valuation
  reasoning (different tranche vs. genuine mark difference, detected via CUSIP) + a CLO primer,
  backed by `src/analytics/valuation_notes.py`; optional AI "analyst note" via
  `summarizer.generate_valuation_explainer` (falls back to the rule-based notes).
- `pages/4_Fund_Profiles.py` — per-fund view; metrics use **latest filing only**; positions,
  discounts, filing summary, historical filing cards. Most complex page.
- `pages/5_Filing_Detail.py` — hidden from sidebar (CSS); reached via historical filing cards;
  reads ticker/date from `st.session_state`.
- `pages/6_Position_Changes.py` — quarter-over-quarter holdings diff per fund (added/exited/
  resized). Backed by `src/analytics/position_changes.py`.
- `pages/7_Conviction.py` — manager/deal ranking by breadth of cross-fund ownership. Backed by
  `src/analytics/conviction.py` (`latest_holdings()` is a reusable "latest filing per fund" helper).
- `pages/8_Vintage.py` — implied price by CLO origination year. Backed by
  `src/analytics/vintage.py`; ~40% of deals use Roman-numeral/series names with no parseable year.
- `pages/9_Fund_Comparison.py` — two funds side by side: shared/unique managers and shared deals.
  Backed by `src/analytics/fund_compare.py`.

## Auth (`src/auth.py`)

Password gate on every page. Reads `st.secrets["password"]` or `os.environ["PASSWORD"]`.
Render sets `PASSWORD`. Fallback `clo2026`.

## Conventions / gotchas

- Always `export PYTHONPATH=.` before running anything — imports are rooted at repo top.
- The DB is **committed**, so the 4 quarters of history travel with the repo; re-scraping
  overwrites it (no append).
- Write-offs (implied price near 0) are filtered out of most price views — match existing
  thresholds (≥1¢) when adding price charts.
- `test_edgar*.py` and `parse_nport*.py` are gitignored local scratch — not part of the app.
- `seed_data.py` generates fake demo data and is **not** used in production.

## Known gaps

1. ~104 managers; some obscure SPVs still unmapped → show as raw names. Add to `MANAGER_MAP`.
2. Some deal names retain CUSIP-style abbreviations (`_clean_deal_name()` misses cases).
3. `report_snapshots` empty — blocked on trustee portal credentials.
4. Scraping cron is wired (`.github/workflows/scrape.yml`): weekly + manual, runs
   `run_pipeline.py`, sanity-checks row counts, commits `data/clo_data.db` back (→ Render
   redeploys). Requires repo Settings → Actions → Workflow permissions = "Read and write".
5. AI summaries (`src/summarizer.py`) are wired into Fund Profiles' "View Filing Summary".
   With `ANTHROPIC_API_KEY` set they use `claude-opus-4-8` (override via `CLO_SUMMARY_MODEL`);
   without a key they fall back to a rule-based paragraph. Key read from env or `st.secrets`.
6. The 4 quarters of history now feed the Position Changes page; most other pages still show
   only the latest snapshot.
