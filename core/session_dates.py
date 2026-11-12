"""Exchange-local dates for daily candles and execution timestamps (no I/O).

Naive values are already IST. Aware timestamps are converted to IST before
removing their zone; UTC midnight date markers retain their calendar date.
"""
import pandas as pd


def ist_naive(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_convert("Asia/Kolkata").tz_localize(None) if ts.tzinfo else ts


def session_date(value) -> pd.Timestamp:
    return ist_naive(value).normalize()


def daily_dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values.map(session_date))


def daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["DATE"] = daily_dates(out["DATE"])
    if out["DATE"].isna().any():
        raise ValueError("daily candle has no session date")
    return out.sort_values("DATE").drop_duplicates("DATE", keep="last").reset_index(drop=True)
