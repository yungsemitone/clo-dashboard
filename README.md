# CLO Trustee Report Monitor

A live dashboard that scrapes public CLO trustee reports (SEC EDGAR filings), parses deal-level metrics, and presents them through an interactive Streamlit app with AI-generated summaries.

## What It Does

- **Scrapes** new CLO filings from SEC EDGAR (10-D, ABS-15G) on an automated schedule
- **Parses** PDF trustee reports to extract OC/IC tests, collateral quality metrics, and waterfall data
- **Stores** everything in SQLite with full deal and snapshot history
- **Displays** an interactive dashboard with per-deal drilldowns, manager rankings, and trend charts
- **Summarizes** each report using Claude (Anthropic API), with a rule-based fallback

## Quick Start

```bash
# Clone and install
git clone <repo-url> && cd clo_scraper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Seed with demo data (optional — to see the dashboard immediately)
PYTHONPATH=. python seed_data.py

# Launch the dashboard
streamlit run app.py
```

## Dashboard Pages

- **Overview** — portfolio-wide metrics, OC cushion distribution, AUM by manager, trend lines
- **Deal Browser** — select any deal for full OC/IC history, collateral quality charts, waterfall data, and an AI-generated summary
- **Manager Rankings** — leaderboard sorted by OC cushion, default rate, diversity; scatter plot of risk vs. health
- **Report Feed** — chronological feed of new reports with expandable AI summaries

## Running the Scraper

```bash
# Scrape latest EDGAR filings
PYTHONPATH=. python main.py scrape --trustee sec_edgar --limit 50

# Parse downloaded reports
PYTHONPATH=. python main.py parse --input data/raw/

# Export to CSV/Excel
PYTHONPATH=. python main.py export --format both
```

## Deployment

### Streamlit Community Cloud (recommended)
1. Push to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo, set `app.py` as the main file
4. Add your `ANTHROPIC_API_KEY` in Settings > Secrets
5. Deploy — you'll get a public URL like `your-app.streamlit.app`

### Automated Scraping (GitHub Actions)
The included workflow (`.github/workflows/scrape.yml`) runs the EDGAR scraper twice daily and commits updated data back to the repo. Streamlit Cloud picks up the new data on the next app refresh.

## AI Summaries

If you set an `ANTHROPIC_API_KEY` environment variable (or add it to `.streamlit/secrets.toml`), the dashboard generates analyst-style summaries for each report using Claude. Without the key, it falls back to a rule-based summary engine that still produces readable output from the raw metrics.

## Tech Stack

Python, Streamlit, Plotly, SQLAlchemy/SQLite, pdfplumber, Anthropic API, GitHub Actions
