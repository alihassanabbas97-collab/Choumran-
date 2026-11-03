"""Run the full pipeline on a normalised frame and build everything the UI shows."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features import build_features
from .model import TrainedModel, evaluate
from .schema import LABELS


@dataclass
class Analysis:
    df: pd.DataFrame  # transactions + predictions
    entities: pd.DataFrame  # persons and shops with risk stats
    edges: pd.DataFrame  # sender -> receiver aggregated links
    model: TrainedModel
    threshold: float
    evaluation: dict | None = None
    meta: dict = field(default_factory=dict)


def run(df: pd.DataFrame, model: TrainedModel, threshold: float = 0.5) -> Analysis:
    feats = build_features(df).loc[df.index]
    pred = model.predict(feats)
    out = df.copy()
    out["p_ordinary"] = pred["ordinary"]
    out["p_fraud"] = pred["fraud"]
    out["p_scam"] = pred["scam"]
    out["risk"] = pred["risk"]
    # T/F: suspicious when the combined fraud+scam probability crosses the threshold
    out["suspicious"] = out["risk"] >= threshold
    out["predicted"] = np.where(
        out["suspicious"], np.where(out["p_fraud"] >= out["p_scam"], "fraud", "scam"), "ordinary"
    )
    out["reasons"] = _reasons(feats, out)

    evaluation = evaluate(out["label"], out["predicted"]) if "label" in out else None
    entities = _entities(out)
    edges = _edges(out)
    return Analysis(df=out, entities=entities, edges=edges, model=model,
                    threshold=threshold, evaluation=evaluation)


def _reasons(feats: pd.DataFrame, out: pd.DataFrame) -> list[str]:
    """Reason codes (``|``-separated, translated via ``reason.<code>``) for suspicious rows."""
    rules = [
        (feats["is_night"] == 1, "night"),
        (feats["new_device_for_sender"] == 1, "new_device"),
        (feats["new_city_for_sender"] == 1, "new_city"),
        (feats["amount_vs_sender_avg"] >= 4, "high_amount"),
        (feats["sender_tx_24h"] >= 4, "burst"),
        (feats["new_receiver_for_sender"] == 1, "new_receiver"),
        (feats["is_round_amount"] == 1, "round_amount"),
        ((feats["receiver_age_days"] < 10) & (feats["receiver_unique_senders"] >= 5), "new_collector"),
    ]
    res = []
    sus = out["suspicious"].to_numpy()
    masks = [(m.to_numpy(), txt) for m, txt in rules]
    for i in range(len(out)):
        if not sus[i]:
            res.append("")
            continue
        res.append("|".join(txt for m, txt in masks if m[i]))
    return res


def _risk_level(score: float) -> str:
    if score >= 0.6:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _entities(df: pd.DataFrame) -> pd.DataFrame:
    fraud = df["predicted"] == "fraud"
    scam = df["predicted"] == "scam"

    s = df.assign(_fraud=fraud, _scam=scam).groupby("sender_id").agg(
        name=("sender_name", "first"), sent_count=("amount", "size"), sent_total=("amount", "sum"),
        fraud_sent=("_fraud", "sum"), scam_sent=("_scam", "sum"), max_risk_sent=("risk", "max"),
        partners_out=("receiver_id", "nunique"),
    )
    r = df.assign(_fraud=fraud, _scam=scam).groupby("receiver_id").agg(
        name=("receiver_name", "first"), rtype=("receiver_type", "first"),
        recv_count=("amount", "size"), recv_total=("amount", "sum"),
        fraud_recv=("_fraud", "sum"), scam_recv=("_scam", "sum"), max_risk_recv=("risk", "max"),
        partners_in=("sender_id", "nunique"),
    )
    ent = s.merge(r, how="outer", left_index=True, right_index=True, suffixes=("_s", "_r"))
    ent["name"] = ent["name_s"].fillna(ent["name_r"])
    ent = ent.drop(columns=["name_s", "name_r"])
    ent["type"] = np.where(ent["sent_count"].notna() | (ent["rtype"] == "person"), "person", "shop")
    ent = ent.drop(columns=["rtype"])
    num = [c for c in ent.columns if c not in ("name", "type")]
    ent[num] = ent[num].fillna(0)
    for c in ("sent_count", "recv_count", "fraud_sent", "scam_sent", "fraud_recv", "scam_recv",
              "partners_out", "partners_in"):
        ent[c] = ent[c].astype(int)

    total_n = ent["sent_count"] + ent["recv_count"]
    flagged = ent["fraud_sent"] + ent["scam_sent"] + ent["fraud_recv"] + ent["scam_recv"]
    ent["flagged"] = flagged
    # receivers collecting scams are the most dangerous; senders are usually victims
    ent["risk_score"] = np.clip(
        0.6 * (ent["scam_recv"] + ent["fraud_recv"]) / ent["recv_count"].replace(0, np.nan)
        + 0.4 * np.minimum(1, ent["scam_recv"] / 5),
        0, 1,
    ).fillna(0)
    victim_score = np.clip(flagged / total_n.replace(0, np.nan) * 3, 0, 1).fillna(0) * 0.5
    ent["risk_score"] = np.maximum(ent["risk_score"], victim_score)
    ent["risk_level"] = ent["risk_score"].map(_risk_level)

    def role(row) -> str:
        """Role code, translated via ``role.<code>``."""
        if row["scam_recv"] >= 3 or (row["recv_count"] and row["scam_recv"] / row["recv_count"] > 0.3):
            return "scam_suspect"
        if row["fraud_recv"] >= 3 and row["type"] == "shop":
            return "fraud_shop"
        if row["fraud_sent"] > 0:
            return "compromised"
        if row["scam_sent"] > 0:
            return "scam_victim"
        if row["fraud_recv"] > 0:
            return "fraud_received"
        return ""

    ent["role"] = ent.apply(role, axis=1)
    ent.index.name = "id"
    return ent.sort_values(["risk_score", "flagged"], ascending=False)


def _edges(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.assign(_fraud=df["predicted"] == "fraud", _scam=df["predicted"] == "scam")
        .groupby(["sender_id", "receiver_id"], as_index=False)
        .agg(count=("amount", "size"), total=("amount", "sum"), fraud=("_fraud", "sum"),
             scam=("_scam", "sum"), max_risk=("risk", "max"))
    )


# ---------------------------------------------------------------- views -----

def summary(a: Analysis) -> dict:
    df = a.df
    counts = df["predicted"].value_counts().reindex(LABELS, fill_value=0)
    amounts = df.groupby("predicted")["amount"].sum().reindex(LABELS, fill_value=0)

    day = df["timestamp"].dt.floor("D")
    per_day = df.groupby([day, "predicted"]).size().unstack(fill_value=0).reindex(columns=LABELS, fill_value=0)
    hours = df.groupby([df["timestamp"].dt.hour, "predicted"]).size().unstack(fill_value=0)
    hours = hours.reindex(index=range(24), columns=LABELS, fill_value=0)

    bins = [0, 25, 50, 100, 250, 500, 1000, 2500, 5000, np.inf]
    names = ["<25", "25-50", "50-100", "100-250", "250-500", "500-1K", "1K-2.5K", "2.5K-5K", "5K+"]
    bucket = pd.cut(df["amount"], bins=bins, labels=names, right=False)
    amt = df.groupby([bucket, "predicted"], observed=False).size().unstack(fill_value=0)
    amt = amt.reindex(index=names, columns=LABELS, fill_value=0)

    top_recv = a.entities[a.entities["recv_count"] > 0].sort_values(
        ["scam_recv", "fraud_recv"], ascending=False).head(10)
    top_recv = top_recv[(top_recv["scam_recv"] + top_recv["fraud_recv"]) > 0]

    return {
        "meta": a.meta,
        "threshold": a.threshold,
        "model_source": a.model.source,
        "model_metrics": a.model.metrics,
        "totals": {
            "transactions": int(len(df)),
            "amount": float(df["amount"].sum()),
            "suspicious": int(df["suspicious"].sum()),
            "suspicious_amount": float(df.loc[df["suspicious"], "amount"].sum()),
            "persons": int((a.entities["type"] == "person").sum()),
            "shops": int((a.entities["type"] == "shop").sum()),
            "high_risk_entities": int((a.entities["risk_level"] == "high").sum()),
            "date_from": df["timestamp"].min().strftime("%Y-%m-%d"),
            "date_to": df["timestamp"].max().strftime("%Y-%m-%d"),
        },
        "by_class": {l: {"count": int(counts[l]), "amount": float(amounts[l])} for l in LABELS},
        "per_day": {"labels": [d.strftime("%Y-%m-%d") for d in per_day.index],
                    **{l: per_day[l].astype(int).tolist() for l in LABELS}},
        "per_hour": {l: hours[l].astype(int).tolist() for l in LABELS},
        "amount_buckets": {"labels": names, **{l: amt[l].astype(int).tolist() for l in LABELS}},
        "top_receivers": [
            {"id": i, "name": r["name"], "type": r["type"], "scam": int(r["scam_recv"]),
             "fraud": int(r["fraud_recv"])}
            for i, r in top_recv.iterrows()
        ],
        "importances": a.model.importances(),
        "evaluation": a.evaluation,
    }


TX_COLUMNS = ["transaction_id", "timestamp", "sender_id", "sender_name", "receiver_id",
              "receiver_name", "receiver_type", "amount", "city", "device_id", "predicted",
              "risk", "p_fraud", "p_scam", "reasons", "label"]


def tx_records(df: pd.DataFrame) -> list[dict]:
    cols = [c for c in TX_COLUMNS if c in df.columns]
    d = df[cols].copy()
    d["timestamp"] = d["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    for c in ("risk", "p_fraud", "p_scam"):
        d[c] = d[c].round(3)
    d = d.astype(object).where(d.notna(), None)
    recs = d.to_dict(orient="records")
    for r in recs:
        if "reasons" in r:
            r["reasons"] = [c for c in (r["reasons"] or "").split("|") if c]
    return recs


def transactions(a: Analysis, page: int = 1, per_page: int = 50, cls: str = "",
                 q: str = "", entity: str = "", sort: str = "risk") -> dict:
    df = a.df
    if cls == "suspicious":
        df = df[df["suspicious"]]
    elif cls in LABELS:
        df = df[df["predicted"] == cls]
    if entity:
        df = df[(df["sender_id"] == entity) | (df["receiver_id"] == entity)]
    if q:
        ql = q.lower()
        hay = (df["transaction_id"] + " " + df["sender_id"] + " " + df["sender_name"] + " "
               + df["receiver_id"] + " " + df["receiver_name"]).str.lower()
        df = df[hay.str.contains(ql, regex=False)]
    if sort == "time":
        df = df.sort_values("timestamp", ascending=False)
    elif sort == "amount":
        df = df.sort_values("amount", ascending=False)
    else:
        df = df.sort_values(["risk", "amount"], ascending=False)
    total = len(df)
    page = max(1, page)
    chunk = df.iloc[(page - 1) * per_page: page * per_page]
    return {"total": total, "page": page, "per_page": per_page, "rows": tx_records(chunk)}


def entity_records(ent: pd.DataFrame) -> list[dict]:
    d = ent.reset_index().copy()
    for c in ("sent_total", "recv_total"):
        d[c] = d[c].round(2)
    d["risk_score"] = d["risk_score"].round(3)
    d["max_risk_sent"] = d["max_risk_sent"].round(3)
    d["max_risk_recv"] = d["max_risk_recv"].round(3)
    return d.to_dict(orient="records")


def entities(a: Analysis, etype: str = "", level: str = "", q: str = "", limit: int = 200) -> dict:
    ent = a.entities
    if etype in ("person", "shop"):
        ent = ent[ent["type"] == etype]
    if level in ("high", "medium", "low"):
        ent = ent[ent["risk_level"] == level]
    if q:
        ql = q.lower()
        ent = ent[(ent.index.str.lower().str.contains(ql, regex=False))
                  | ent["name"].str.lower().str.contains(ql, regex=False)]
    return {"total": int(len(ent)), "rows": entity_records(ent.head(limit))}


def graph(a: Analysis, mode: str = "suspicious", focus: str = "", max_nodes: int = 400) -> dict:
    """Person <-> shop network. ``mode``: suspicious | all; ``focus``: one entity + neighbours."""
    edges = a.edges
    ent = a.entities
    if focus:
        e = edges[(edges["sender_id"] == focus) | (edges["receiver_id"] == focus)]
    elif mode == "all":
        e = edges
    else:
        bad = edges[(edges["fraud"] + edges["scam"]) > 0]
        hot = set(bad["sender_id"]) | set(bad["receiver_id"])
        # suspicious links + the other links of high-risk receivers (shows the victim web)
        risky = set(ent.index[(ent["risk_level"] == "high") & (ent["recv_count"] > 0)])
        e = edges[edges.index.isin(bad.index) | edges["receiver_id"].isin(risky)]
        e = e[e["sender_id"].isin(hot) | e["receiver_id"].isin(hot)]

    truncated = False
    nodes_needed = set(e["sender_id"]) | set(e["receiver_id"])
    if len(nodes_needed) > max_nodes:
        truncated = True
        e = e.assign(_w=e["fraud"] + e["scam"] + e["max_risk"]).sort_values("_w", ascending=False)
        keep, seen = [], set()
        for idx, row in e.iterrows():
            new = {row["sender_id"], row["receiver_id"]} - seen
            if len(seen) + len(new) > max_nodes:
                continue
            seen |= new
            keep.append(idx)
        e = e.loc[keep].drop(columns="_w")
        nodes_needed = seen

    sub = ent.loc[ent.index.isin(nodes_needed)]
    nodes = [
        {"id": i, "name": r["name"], "type": r["type"], "risk_level": r["risk_level"],
         "risk_score": round(float(r["risk_score"]), 3), "role": r["role"],
         "volume": float(r["sent_total"] + r["recv_total"]),
         "tx": int(r["sent_count"] + r["recv_count"]), "flagged": int(r["flagged"])}
        for i, r in sub.iterrows()
    ]
    links = [
        {"from": r["sender_id"], "to": r["receiver_id"], "count": int(r["count"]),
         "total": round(float(r["total"]), 2), "fraud": int(r["fraud"]), "scam": int(r["scam"])}
        for _, r in e.iterrows()
    ]
    return {"nodes": nodes, "edges": links, "truncated": truncated}
