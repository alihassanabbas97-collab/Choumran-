"""Choumran – money-transaction fraud & scam detection web app."""
from __future__ import annotations

import hmac
import io
import os
import secrets
import threading
from collections import OrderedDict

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from fraudnet import analysis as an
from fraudnet.generator import generate_transactions
from fraudnet.i18n import norm_lang, t
from fraudnet.model import default_model, train
from fraudnet.schema import COLUMNS, SchemaError, guess_mapping, normalise

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

# Everything is kept in memory only: uploaded data is never written to disk.
# Because of this the app must run as a single process (e.g. gunicorn --workers 1).
_MAX_KEEP = int(os.environ.get("MAX_ANALYSES", "8"))
_pending: OrderedDict[str, pd.DataFrame] = OrderedDict()
_analyses: OrderedDict[str, an.Analysis] = OrderedDict()
_custom_model = {"model": None}
_store_lock = threading.Lock()


# --------------------------------------------------------------- password --
# Set APP_PASSWORD to protect the whole site (browser login prompt, any username).
_PASSWORD = os.environ.get("APP_PASSWORD", "")
_OPEN_PATHS = ("/healthz",)


@app.before_request
def _require_password():
    if not _PASSWORD or request.path in _OPEN_PATHS:
        return None
    auth = request.authorization
    given = (auth.password or "") if auth else ""
    if hmac.compare_digest(given.encode(), _PASSWORD.encode()):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Choumran", charset="UTF-8"'})


def _warm_up():
    """Load the model and run one tiny analysis so the first real request is fast."""
    raw = generate_transactions(n_persons=30, n_shops=10, days=14, fraud_cases=3, scam_receivers=2, seed=1)
    an.run(normalise(raw, guess_mapping(list(raw.columns))), default_model())


if os.environ.get("PRELOAD_MODEL") == "1":
    threading.Thread(target=_warm_up, daemon=True).start()


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _remember(store: OrderedDict, value) -> str:
    key = secrets.token_urlsafe(8)
    with _store_lock:
        store[key] = value
        while len(store) > _MAX_KEEP:
            store.popitem(last=False)
    return key


def _lang() -> str:
    return norm_lang(request.args.get("lang") or request.headers.get("X-Lang"))


def _err(code: str, status: int, **params):
    if "cols" in params:  # list of canonical column names -> translated labels
        params["cols"] = ", ".join(t(_lang(), f"col.{c}") for c in params["cols"])
    return jsonify(error=t(_lang(), f"err.{code}", **params), code=code), status


def _get_analysis(aid: str) -> an.Analysis:
    a = _analyses.get(aid)
    if a is None:
        raise KeyError(aid)
    return a


@app.errorhandler(KeyError)
def _missing(_e):
    return _err("not_found", 404)


@app.errorhandler(SchemaError)
def _schema(e):
    return _err(e.code, 400, **e.params)


@app.errorhandler(413)
def _too_big(_e):
    return _err("too_big", 413)


def _read_table(file) -> pd.DataFrame:
    name = (file.filename or "").lower()
    data = file.read()
    if not data:
        raise SchemaError("empty_file")
    if name.endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(data))
        except ImportError as e:
            raise SchemaError("excel_lib") from e
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc, sep=None, engine="python")
        except UnicodeDecodeError:
            continue
        except Exception as e:  # parser errors
            raise SchemaError("parse_error", detail=str(e)[:200]) from e
    raise SchemaError("encoding")


def _finish(df: pd.DataFrame, meta: dict, do_train: bool, threshold: float, use_custom: bool):
    model = default_model()
    if do_train:
        if "label" not in df:
            raise SchemaError("train_needs_label")
        model = train(df, source="uploaded")
        _custom_model["model"] = model
    elif use_custom and _custom_model["model"] is not None:
        model = _custom_model["model"]
    a = an.run(df, model, threshold=threshold)
    a.meta = meta
    aid = _remember(_analyses, a)
    return jsonify(id=aid, summary=an.summary(a))


def _threshold() -> float:
    try:
        t = float((request.get_json(silent=True) or {}).get("threshold", 0.5))
    except (TypeError, ValueError):
        t = 0.5
    return min(0.95, max(0.05, t))


# ------------------------------------------------------------------ pages --

@app.get("/")
def index():
    return render_template("index.html")


# -------------------------------------------------------------------- api --

@app.get("/api/schema")
def api_schema():
    return jsonify(
        columns=[{"name": k, "required": v[0]} for k, v in COLUMNS.items()],
        has_custom_model=_custom_model["model"] is not None,
    )


@app.post("/api/demo")
def api_demo():
    body = request.get_json(silent=True) or {}
    n_persons = int(min(1500, max(30, int(body.get("persons", 300)))))
    days = int(min(365, max(14, int(body.get("days", 60)))))
    seed = body.get("seed")
    seed = int(seed) if seed not in (None, "") else None
    raw = generate_transactions(n_persons=n_persons, n_shops=max(10, n_persons // 5), days=days,
                                fraud_cases=max(3, n_persons // 12), scam_receivers=max(2, n_persons // 60),
                                seed=seed, locale=_lang())
    df = normalise(raw, guess_mapping(list(raw.columns)))
    meta = {"source": "demo", "persons": n_persons, "days": days, "rows": len(df)}
    return _finish(df, meta, do_train=False, threshold=_threshold(), use_custom=False)


@app.get("/api/sample.csv")
def api_sample():
    raw = generate_transactions(n_persons=120, n_shops=25, days=30, fraud_cases=10, scam_receivers=2, seed=11,
                                locale=_lang())
    return Response(raw.to_csv(index=False).encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_transactions.csv"})


@app.post("/api/upload")
def api_upload():
    file = request.files.get("file")
    if file is None:
        raise SchemaError("no_file")
    raw = _read_table(file)
    if raw.empty:
        raise SchemaError("no_rows")
    raw.columns = [str(c).strip() for c in raw.columns]
    token = _remember(_pending, raw)
    preview = raw.head(5).astype(object).where(raw.head(5).notna(), None)
    return jsonify(
        token=token, filename=file.filename, rows=int(len(raw)), columns=list(raw.columns),
        mapping=guess_mapping(list(raw.columns)),
        preview=preview.astype(str).to_dict(orient="records"),
    )


@app.post("/api/analyze")
def api_analyze():
    body = request.get_json(silent=True) or {}
    raw = _pending.get(body.get("token", ""))
    if raw is None:
        raise KeyError("token")
    mapping = {k: (v or None) for k, v in (body.get("mapping") or {}).items() if k in COLUMNS}
    df = normalise(raw, mapping)
    meta = {"source": "upload", "name": body.get("filename") or "", "rows": len(df),
            "dropped_rows": int(len(raw) - len(df))}
    return _finish(df, meta, do_train=bool(body.get("train")), threshold=_threshold(),
                   use_custom=bool(body.get("use_custom")))


@app.post("/api/a/<aid>/threshold")
def api_rethreshold(aid):
    old = _get_analysis(aid)
    cols = [c for c in old.df.columns if c not in
            ("p_ordinary", "p_fraud", "p_scam", "risk", "suspicious", "predicted", "reasons")]
    a = an.run(old.df[cols], old.model, threshold=_threshold())
    a.meta = old.meta
    with _store_lock:
        _analyses[aid] = a
    return jsonify(id=aid, summary=an.summary(a))


@app.get("/api/a/<aid>/summary")
def api_summary(aid):
    return jsonify(an.summary(_get_analysis(aid)))


@app.get("/api/a/<aid>/transactions")
def api_transactions(aid):
    a = _get_analysis(aid)
    args = request.args
    return jsonify(an.transactions(
        a, page=args.get("page", 1, type=int), per_page=min(200, args.get("per_page", 50, type=int)),
        cls=args.get("cls", ""), q=args.get("q", "").strip(), entity=args.get("entity", ""),
        sort=args.get("sort", "risk"),
    ))


@app.get("/api/a/<aid>/entities")
def api_entities(aid):
    a = _get_analysis(aid)
    args = request.args
    return jsonify(an.entities(a, etype=args.get("type", ""), level=args.get("level", ""),
                               q=args.get("q", "").strip(), limit=min(1000, args.get("limit", 200, type=int))))


@app.get("/api/a/<aid>/entity/<eid>")
def api_entity(aid, eid):
    a = _get_analysis(aid)
    if eid not in a.entities.index:
        raise KeyError(eid)
    rec = an.entity_records(a.entities.loc[[eid]])[0]
    e = a.edges
    out = e[e["sender_id"] == eid].merge(a.entities[["name", "type"]], left_on="receiver_id", right_index=True)
    inn = e[e["receiver_id"] == eid].merge(a.entities[["name", "type"]], left_on="sender_id", right_index=True)

    def links(df, other):
        df = df.sort_values(["scam", "fraud", "total"], ascending=False).head(30)
        return [{"id": r[other], "name": r["name"], "type": r["type"], "count": int(r["count"]),
                 "total": round(float(r["total"]), 2), "fraud": int(r["fraud"]), "scam": int(r["scam"])}
                for _, r in df.iterrows()]

    return jsonify(entity=rec, paid_to=links(out, "receiver_id"), received_from=links(inn, "sender_id"))


@app.get("/api/a/<aid>/graph")
def api_graph(aid):
    a = _get_analysis(aid)
    return jsonify(an.graph(a, mode=request.args.get("mode", "suspicious"),
                            focus=request.args.get("focus", ""),
                            max_nodes=min(1500, request.args.get("max_nodes", 400, type=int))))


@app.get("/api/a/<aid>/export.csv")
def api_export(aid):
    a = _get_analysis(aid)
    lang = _lang()
    what = request.args.get("what", "transactions")
    if what == "entities":
        data = a.entities.reset_index()
        data["type"] = data["type"].map(lambda v: t(lang, f"type.{v}"))
        data["risk_level"] = data["risk_level"].map(lambda v: t(lang, f"level.{v}"))
        data["role"] = data["role"].map(lambda v: t(lang, f"role.{v}") if v else "")
    else:
        data = pd.DataFrame(an.tx_records(a.df))
        for col in ("predicted", "label"):
            if col in data:
                data[col] = data[col].map(lambda v: t(lang, f"label.{v}") if v else "")
        data["receiver_type"] = data["receiver_type"].map(lambda v: t(lang, f"type.{v}"))
        data["reasons"] = data["reasons"].map(lambda rs: "; ".join(t(lang, f"reason.{r}") for r in rs))
    return Response(data.to_csv(index=False).encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=choumran_{what}.csv"})


if __name__ == "__main__":
    if os.environ.get("PRELOAD_MODEL") != "1":  # warm up so the first request is fast
        threading.Thread(target=_warm_up, daemon=True).start()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
