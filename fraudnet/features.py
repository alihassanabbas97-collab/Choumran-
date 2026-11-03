"""Behavioural feature engineering.

Every feature only looks at the *past* of the sender / receiver (relative to
the transaction's timestamp), so the same code works for training and for
scoring new data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "log_amount",
    "hour",
    "is_night",
    "day_of_week",
    "is_round_amount",
    "receiver_is_shop",
    "sender_prior_count",
    "amount_vs_sender_avg",
    "sender_tx_24h",
    "sender_amount_24h_log",
    "minutes_since_sender_prev",
    "new_receiver_for_sender",
    "new_device_for_sender",
    "new_city_for_sender",
    "receiver_age_days",
    "receiver_unique_senders",
    "receiver_senders_per_day",
    "receiver_tx_24h",
    "amount_vs_receiver_avg",
]

_DAY = np.int64(24 * 3600 * 10**9)


def _window_count_sum(times: np.ndarray, amounts: np.ndarray, window: np.int64):
    """Count and sum of events within ``window`` before each event (inclusive)."""
    idx = np.arange(len(times))
    left = np.searchsorted(times, times - window, side="left")
    csum = np.concatenate([[0.0], np.cumsum(amounts)])
    return idx - left + 1, csum[idx + 1] - csum[left]


def _rolling(df: pd.DataFrame, key: str) -> tuple[pd.Series, pd.Series]:
    counts = pd.Series(0, index=df.index, dtype="int64")
    sums = pd.Series(0.0, index=df.index)
    t = df["timestamp"].values.astype("datetime64[ns]").astype("int64")
    a = df["amount"].to_numpy(dtype=float)
    for _, pos in df.groupby(key, sort=False).indices.items():
        c, s = _window_count_sum(t[pos], a[pos], _DAY)
        counts.iloc[pos] = c
        sums.iloc[pos] = s
    return counts, sums


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a feature matrix (same index as ``df``, which must be time-sorted)."""
    df = df.sort_values("timestamp", kind="stable")
    f = pd.DataFrame(index=df.index)
    amount = df["amount"].clip(lower=0)
    ts = df["timestamp"]

    f["log_amount"] = np.log1p(amount)
    f["hour"] = ts.dt.hour
    f["is_night"] = ts.dt.hour.isin([0, 1, 2, 3, 4, 5]).astype(int)
    f["day_of_week"] = ts.dt.day_of_week
    f["is_round_amount"] = ((amount % 50 == 0) & (amount > 0)).astype(int)
    f["receiver_is_shop"] = (df["receiver_type"] == "shop").astype(int)

    g = df.groupby("sender_id", sort=False)
    f["sender_prior_count"] = g.cumcount()
    prior_sum = g["amount"].cumsum() - amount
    prior_mean = prior_sum / f["sender_prior_count"].replace(0, np.nan)
    f["amount_vs_sender_avg"] = (amount / prior_mean).fillna(1.0).clip(upper=100)
    cnt, s = _rolling(df, "sender_id")
    f["sender_tx_24h"] = cnt
    f["sender_amount_24h_log"] = np.log1p(s)
    prev = g["timestamp"].shift()
    f["minutes_since_sender_prev"] = ((ts - prev).dt.total_seconds() / 60).fillna(60 * 24 * 30).clip(upper=60 * 24 * 30)

    pair_first = df.groupby(["sender_id", "receiver_id"], sort=False).cumcount() == 0
    f["new_receiver_for_sender"] = (pair_first & (f["sender_prior_count"] > 0)).astype(int)

    for col, feat in (("device_id", "new_device_for_sender"), ("city", "new_city_for_sender")):
        if col in df and df[col].notna().any():
            first = df.groupby(["sender_id", col], sort=False).cumcount() == 0
            f[feat] = (first & (f["sender_prior_count"] > 0) & df[col].notna()).astype(int)
        else:
            f[feat] = 0

    r = df.groupby("receiver_id", sort=False)
    first_seen = r["timestamp"].transform("min")
    f["receiver_age_days"] = (ts - first_seen).dt.total_seconds() / 86400
    f["receiver_unique_senders"] = pair_first.astype(int).groupby(df["receiver_id"], sort=False).cumsum()
    f["receiver_senders_per_day"] = f["receiver_unique_senders"] / (f["receiver_age_days"] + 1)
    cnt, _ = _rolling(df, "receiver_id")
    f["receiver_tx_24h"] = cnt
    r_count = r.cumcount()
    r_prior_mean = (r["amount"].cumsum() - amount) / r_count.replace(0, np.nan)
    f["amount_vs_receiver_avg"] = (amount / r_prior_mean).fillna(1.0).clip(upper=100)

    return f[FEATURES].astype(float)
