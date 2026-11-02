"""Keyless FRED CSV downloads for the macro overlay."""

import io
import logging
from datetime import date

import pandas as pd
import requests

from src.models.schema import MacroSeries

logger = logging.getLogger(__name__)

FRED_SERIES = {
    "BAMLH0A0HYM2": "US High Yield OAS (%)",
    "BAMLH0A3HYC": "US CCC & Lower OAS (%)",
    "BAMLH0A1HYBB": "US BB OAS (%)",
    "DGS10": "10Y Treasury (%)",
    "SOFR": "SOFR (%)",
    "DCOILWTICO": "WTI Crude ($/bbl)",
    "UMCSENT": "UMich Consumer Sentiment",
    "RSAFS": "Retail Sales ($mm)",
    "INDPRO": "Industrial Production Index",
    "HOUST": "Housing Starts (k, SAAR)",
    "NASDAQCOM": "NASDAQ Composite",
    "VIXCLS": "VIX",
}


def fetch_series(series_id: str, start: str = "2024-01-01") -> pd.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    r = requests.get(url, timeout=60, headers={"User-Agent": "CLO-Research-Tool/1.0"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["obs_date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["obs_date"] = pd.to_datetime(df["obs_date"]).dt.date
    return df.dropna()


def refresh_macro(session, series=FRED_SERIES) -> dict:
    counts = {}
    for sid in series:
        try:
            df = fetch_series(sid)
        except Exception as e:  # keep old data on failure
            logger.error("FRED %s failed: %s", sid, e)
            counts[sid] = 0
            continue
        session.query(MacroSeries).filter(MacroSeries.series_id == sid).delete()
        session.bulk_insert_mappings(MacroSeries, [
            {"series_id": sid, "obs_date": d, "value": v} for d, v in zip(df.obs_date, df.value)])
        session.commit()
        counts[sid] = len(df)
    return counts
