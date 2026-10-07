"""Statistical anomaly detection for numeric columns (IQR + Z-score).

Anomalies are surfaced and explained, never silently dropped — in a business
setting an outlier is usually the most valuable insight in the dataset.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_SAMPLE = 8  # need a minimum of data before outlier talk is meaningful


def iqr_bounds(s: pd.Series) -> tuple[float, float]:
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def zscore_outliers(s: pd.Series, threshold: float = 3.0):
    mean, std = s.mean(), s.std()
    if std == 0 or np.isnan(std):
        return s.apply(lambda _: False), mean, std
    z = (s - mean) / std
    return z.abs() > threshold, mean, std


def detect_column(df: pd.DataFrame, column: str, method: str = "iqr") -> dict | None:
    s = pd.to_numeric(df[column], errors="coerce")
    vals = s.dropna()
    if len(vals) < MIN_SAMPLE or vals.nunique() < 3:
        return None

    if method == "zscore":
        flags, mean, std = zscore_outliers(vals)
        lo = hi = None
    else:
        lo, hi = iqr_bounds(vals)
        flags = (vals < lo) | (vals > hi)
        mean, std = vals.mean(), vals.std()

    idx = vals.index[flags]
    result: dict = {
        "column": column,
        "method": method,
        "count": int(len(idx)),
        "lower_bound": None if lo is None else float(lo),
        "upper_bound": None if hi is None else float(hi),
        "mean": float(mean) if pd.notna(mean) else None,
        "std": float(std) if pd.notna(std) else None,
    }
    if len(idx):
        sample = df.loc[idx].head(5).astype(str)
        result["sample"] = sample.to_dict(orient="records")
    return result


def analyse_dataframe(df: pd.DataFrame, method: str = "iqr", columns: list[str] | None = None) -> dict:
    numeric = list(df.select_dtypes(include="number").columns)
    if columns:
        numeric = [c for c in columns if c in numeric] or numeric
    results = [r for c in numeric if (r := detect_column(df, c, method))]
    return {"method": method, "columns": results}
