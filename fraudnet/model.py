"""Training, scoring and evaluation of the transaction classifier."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

from .features import FEATURES, build_features
from .generator import generate_transactions
from .schema import LABELS, SchemaError

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "default_model.joblib"


@dataclass
class TrainedModel:
    clf: RandomForestClassifier
    source: str  # "synthetic" or "uploaded"
    metrics: dict = field(default_factory=dict)

    def predict(self, feats: pd.DataFrame) -> pd.DataFrame:
        proba = self.clf.predict_proba(feats[FEATURES])
        out = pd.DataFrame(0.0, index=feats.index, columns=LABELS)
        for i, cls in enumerate(self.clf.classes_):
            out[cls] = proba[:, i]
        out["predicted"] = out[LABELS].idxmax(axis=1)
        out["risk"] = 1.0 - out["ordinary"]
        return out

    def importances(self) -> list[dict]:
        imp = self.clf.feature_importances_
        order = np.argsort(imp)[::-1]
        return [{"feature": FEATURES[i], "importance": float(imp[i])} for i in order]


def _new_clf() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=200, min_samples_leaf=2, class_weight="balanced_subsample",
        n_jobs=-1, random_state=7,
    )


def evaluate(y_true: pd.Series, y_pred: pd.Series) -> dict:
    labels = [l for l in LABELS if l in set(y_true) | set(y_pred)]
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    flag_true = y_true != "ordinary"
    flag_pred = y_pred != "ordinary"
    per_class = {
        l: {
            "precision": float(precision_score(y_true, y_pred, labels=[l], average="macro", zero_division=0)),
            "recall": float(recall_score(y_true, y_pred, labels=[l], average="macro", zero_division=0)),
            "support": int((y_true == l).sum()),
        }
        for l in labels
    }
    return {
        "labels": labels,
        "confusion": cm.tolist(),
        "accuracy": float((y_true == y_pred).mean()),
        "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "per_class": per_class,
        "suspicious_precision": float(precision_score(flag_true, flag_pred, zero_division=0)),
        "suspicious_recall": float(recall_score(flag_true, flag_pred, zero_division=0)),
        "n": int(len(y_true)),
    }


def train(df: pd.DataFrame, source: str, test_size: float = 0.3, seed: int = 7) -> TrainedModel:
    """Train on a labelled, normalised frame; report metrics on a held-out split."""
    if "label" not in df:
        raise SchemaError("train_needs_label")
    feats = build_features(df).loc[df.index]
    y = df["label"]
    counts = y.value_counts()
    if len(counts) < 2:
        raise SchemaError("train_two_classes")
    stratify = y if counts.min() >= 2 else None
    X_tr, X_te, y_tr, y_te = train_test_split(feats, y, test_size=test_size, random_state=seed, stratify=stratify)
    clf = _new_clf().fit(X_tr, y_tr)
    model = TrainedModel(clf=clf, source=source)
    model.metrics = evaluate(y_te, model.predict(X_te)["predicted"])
    # refit on everything for actual use
    model.clf = _new_clf().fit(feats, y)
    return model


_default: TrainedModel | None = None
_lock = threading.Lock()


def default_model() -> TrainedModel:
    """Model trained on synthetic data; cached in memory and on disk."""
    global _default
    with _lock:
        if _default is None:
            if DEFAULT_MODEL_PATH.exists():
                try:
                    _default = joblib.load(DEFAULT_MODEL_PATH)
                except Exception:  # corrupt / incompatible pickle -> retrain
                    _default = None
            if _default is None:
                from .schema import normalise, guess_mapping

                frames = [generate_transactions(seed=s, n_persons=250) for s in (1, 2, 3)]
                # different seeds reuse ids; prefix them so entities don't collide
                for i, fr in enumerate(frames):
                    for c in ("sender_id", "receiver_id"):
                        fr[c] = f"G{i}-" + fr[c]
                raw = pd.concat(frames, ignore_index=True)
                df = normalise(raw, guess_mapping(list(raw.columns)))
                _default = train(df, source="synthetic")
                MODEL_DIR.mkdir(exist_ok=True)
                joblib.dump(_default, DEFAULT_MODEL_PATH)
        return _default
