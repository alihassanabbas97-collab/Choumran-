import io

import pandas as pd
import pytest

from app import app
from fraudnet import analysis as an
from fraudnet.features import FEATURES, build_features
from fraudnet.generator import generate_transactions
from fraudnet.model import default_model, train
from fraudnet.schema import SchemaError, guess_mapping, normalise


@pytest.fixture(scope="module")
def small():
    raw = generate_transactions(n_persons=80, n_shops=15, days=30, fraud_cases=8, scam_receivers=2, seed=5)
    return normalise(raw, guess_mapping(list(raw.columns)))


def test_generator_has_all_labels(small):
    assert set(small["label"]) == {"ordinary", "fraud", "scam"}
    assert small["timestamp"].is_monotonic_increasing


def test_features_shape_and_no_nan(small):
    f = build_features(small)
    assert list(f.columns) == FEATURES
    assert len(f) == len(small)
    assert not f.isna().any().any()


def test_features_use_only_past(small):
    """Adding future rows must not change features of earlier rows."""
    half = small.iloc[: len(small) // 2]
    full = build_features(small).loc[half.index]
    part = build_features(half)
    pd.testing.assert_frame_equal(full, part)


def test_guess_mapping_aliases():
    m = guess_mapping(["Date", "customer_id", "merchant_name", "merchant_id", "Amount"])
    assert m["timestamp"] == "Date" and m["sender_id"] == "customer_id"
    assert m["receiver_id"] == "merchant_id" and m["receiver_name"] == "merchant_name"
    assert m["amount"] == "Amount"


def test_normalise_missing_required():
    with pytest.raises(SchemaError):
        normalise(pd.DataFrame({"a": [1]}), {"timestamp": "a"})


def test_normalise_label_aliases_and_bad_rows():
    raw = pd.DataFrame({
        "date": ["2026-01-01 10:00", "bad", "2026-01-02 11:00"],
        "from": ["A", "B", "C"], "to": ["S1", "S1", "S2"],
        "amt": ["1,200", "5", "30"], "y": ["normal", "fraud", "Scam"],
    })
    df = normalise(raw, {"timestamp": "date", "sender_id": "from", "receiver_id": "to", "amount": "amt", "label": "y"})
    assert len(df) == 2
    assert df["amount"].tolist() == [1200.0, 30.0]
    assert df["label"].tolist() == ["ordinary", "scam"]


def test_default_model_detects_patterns(small):
    a = an.run(small, default_model())
    ev = a.evaluation
    assert ev["suspicious_recall"] > 0.75
    assert ev["suspicious_precision"] > 0.75
    # scam receivers should top the entity ranking
    top = a.entities.head(2)
    assert (top["scam_recv"] > 0).all()


def test_train_on_uploaded(small):
    m = train(small, source="uploaded")
    assert m.source == "uploaded" and "accuracy" in m.metrics


def test_graph_and_views(small):
    a = an.run(small, default_model())
    g = an.graph(a)
    ids = {n["id"] for n in g["nodes"]}
    assert g["edges"] and all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    assert an.graph(a, max_nodes=10)["truncated"]
    s = an.summary(a)
    assert s["totals"]["transactions"] == len(small)
    t = an.transactions(a, cls="suspicious", per_page=5)
    assert t["rows"] and all(r["predicted"] != "ordinary" for r in t["rows"])


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_api_upload_flow(client, small):
    raw = generate_transactions(n_persons=60, n_shops=12, days=20, fraud_cases=5, scam_receivers=2, seed=9)
    raw = raw.rename(columns={"timestamp": "Date", "sender_id": "customer_id", "amount": "Amount"})
    buf = io.BytesIO(raw.to_csv(index=False).encode("utf-8-sig"))
    r = client.post("/api/upload", data={"file": (buf, "t.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.json
    up = r.json
    assert up["mapping"]["timestamp"] == "Date"
    r = client.post("/api/analyze", json={"token": up["token"], "mapping": up["mapping"]})
    assert r.status_code == 200, r.json
    aid = r.json["id"]
    for path in ("summary", "transactions?cls=suspicious", "entities", "graph", "export.csv"):
        assert client.get(f"/api/a/{aid}/{path}").status_code == 200
    ent = client.get(f"/api/a/{aid}/entities?limit=1").json["rows"][0]["id"]
    assert client.get(f"/api/a/{aid}/entity/{ent}").status_code == 200
    assert client.post(f"/api/a/{aid}/threshold", json={"threshold": 0.3}).status_code == 200


def test_api_errors(client):
    assert client.get("/api/a/nope/summary").status_code == 404
    buf = io.BytesIO(b"x,y\n1,2\n")
    up = client.post("/api/upload", data={"file": (buf, "t.csv")}, content_type="multipart/form-data").json
    r = client.post("/api/analyze", json={"token": up["token"], "mapping": up["mapping"]})
    assert r.status_code == 400 and "error" in r.json


# ------------------------------------------------------------------ i18n --
import json
import re
from pathlib import Path

from fraudnet.i18n import LANGS, strings, t

I18N = Path(__file__).resolve().parent.parent / "static" / "i18n"


def test_translation_files_have_same_keys():
    keys = {lang: set(json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))) for lang in LANGS}
    for lang in LANGS:
        assert keys[lang] == keys["en"], lang


def test_all_backend_codes_are_translated(small):
    a = an.run(small, default_model())
    reasons = {c for r in a.df["reasons"] for c in r.split("|") if c}
    roles = {r for r in a.entities["role"] if r}
    assert reasons and roles
    for lang in LANGS:
        d = strings(lang)
        for c in reasons:
            assert f"reason.{c}" in d
        for r in roles:
            assert f"role.{r}" in d
        for f in FEATURES:
            assert f"feat.{f}" in d


def test_frontend_keys_exist():
    root = Path(__file__).resolve().parent.parent
    html = (root / "templates" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-html|-placeholder|-aria)?="([^"]+)"', html))
    js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    keys |= set(re.findall(r'\bt\("([a-z_]+\.[a-z_.]+)"', js))
    d = strings("en")
    assert keys and not [k for k in keys if not k.endswith(".") and k not in d]


def test_error_messages_follow_language(client):
    for lang, word in (("en", "expired"), ("fr", "expirée"), ("ar", "التحليل")):
        r = client.get(f"/api/a/nope/summary?lang={lang}")
        assert r.status_code == 404 and word in r.json["error"]
    buf = io.BytesIO(b"x,y\n1,2\n")
    up = client.post("/api/upload", data={"file": (buf, "t.csv")}, content_type="multipart/form-data").json
    r = client.post("/api/analyze", json={"token": up["token"], "mapping": up["mapping"]}, headers={"X-Lang": "fr"})
    assert r.status_code == 400 and r.json["code"] == "missing_columns" and "Montant" in r.json["error"]


def test_generator_locales_same_structure():
    frames = {loc: generate_transactions(n_persons=40, n_shops=10, days=15, fraud_cases=3, scam_receivers=2, seed=3, locale=loc)
              for loc in ("en", "ar", "fr")}
    assert frames["en"]["amount"].equals(frames["ar"]["amount"])
    assert frames["en"]["label"].equals(frames["fr"]["label"])
    assert frames["en"]["sender_name"].iloc[0] != frames["ar"]["sender_name"].iloc[0]
    assert t("fr", "label.scam") == "Arnaque"


def test_export_is_translated(client):
    r = client.post("/api/demo", json={"persons": 40, "days": 15, "seed": 1})
    aid = r.json["id"]
    csv_fr = client.get(f"/api/a/{aid}/export.csv?lang=fr").data.decode("utf-8-sig")
    assert "Normale" in csv_fr
    ent_ar = client.get(f"/api/a/{aid}/export.csv?what=entities&lang=ar").data.decode("utf-8-sig")
    assert "شخص" in ent_ar


# -------------------------------------------------------------- password --
def test_password_protection(monkeypatch, client):
    import base64

    import app as app_module

    monkeypatch.setattr(app_module, "_PASSWORD", "s3cret")
    assert client.get("/").status_code == 401
    assert "WWW-Authenticate" in client.get("/api/schema").headers
    assert client.get("/healthz").status_code == 200
    bad = base64.b64encode(b"x:wrong").decode()
    assert client.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"anyone:s3cret").decode()
    assert client.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_no_password_means_open(client):
    assert client.get("/").status_code == 200
