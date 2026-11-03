"""Choumran – money-transaction fraud & scam detection web app."""
from __future__ import annotations

import io
import os
import secrets
import threading
from collections import OrderedDict

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from fraudnet import analysis as an
from fraudnet.generator import generate_transactions
from fraudnet.model import default_model, train
from fraudnet.schema import COLUMNS, SchemaError, guess_mapping, normalise

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

# Everything is kept in memory only: uploaded data is never written to disk.
_MAX_KEEP = 8
_pending: OrderedDict[str, pd.DataFrame] = OrderedDict()
_analyses: OrderedDict[str, an.Analysis] = OrderedDict()
_custom_model = {"model": None}
_store_lock = threading.Lock()


def _remember(store: OrderedDict, value) -> str:
    key = secrets.token_urlsafe(8)
    with _store_lock:
        store[key] = value
        while len(store) > _MAX_KEEP:
            store.popitem(last=False)
    return key


def _get_analysis(aid: str) -> an.Analysis:
    a = _analyses.get(aid)
    if a is None:
        raise KeyError(aid)
    return a


@app.errorhandler(KeyError)
def _missing(_e):
    return jsonify(error="التحليل غير موجود أو انتهت صلاحيته. ارفع الملف من جديد."), 404


@app.errorhandler(SchemaError)
def _schema(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(413)
def _too_big(_e):
    return jsonify(error="الملف كبير كتير"), 413


def _read_table(file) -> pd.DataFrame:
    name = (file.filename or "").lower()
    data = file.read()
    if not data:
        raise SchemaError("الملف فاضي")
    if name.endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(data))
        except ImportError as e:
            raise SchemaError("قراءة ملفات Excel بدها مكتبة openpyxl") from e
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc, sep=None, engine="python")
        except UnicodeDecodeError:
            continue
        except Exception as e:  # parser errors
            raise SchemaError(f"ما قدرنا نقرأ الملف: {e}") from e
    raise SchemaError("ترميز الملف غير معروف")


def _finish(df: pd.DataFrame, meta: dict, do_train: bool, threshold: float, use_custom: bool):
    model = default_model()
    if do_train:
        if "label" not in df:
            raise SchemaError("للتدريب لازم تختار عمود التصنيف (label)")
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
        columns=[{"name": k, "required": v[0], "label": v[1]} for k, v in COLUMNS.items()],
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
                                seed=seed)
    df = normalise(raw, guess_mapping(list(raw.columns)))
    meta = {"source": "demo", "name": f"بيانات تجريبية ({n_persons} شخص، {days} يوم)", "rows": len(df)}
    return _finish(df, meta, do_train=False, threshold=_threshold(), use_custom=False)


@app.get("/api/sample.csv")
def api_sample():
    raw = generate_transactions(n_persons=120, n_shops=25, days=30, fraud_cases=10, scam_receivers=2, seed=11)
    return Response(raw.to_csv(index=False).encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_transactions.csv"})


@app.post("/api/upload")
def api_upload():
    file = request.files.get("file")
    if file is None:
        raise SchemaError("ما في ملف")
    raw = _read_table(file)
    if raw.empty:
        raise SchemaError("الملف ما فيه ولا سطر")
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
    meta = {"source": "upload", "name": body.get("filename") or "ملف مرفوع", "rows": len(df),
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
    what = request.args.get("what", "transactions")
    if what == "entities":
        data = a.entities.reset_index()
    else:
        data = pd.DataFrame(an.tx_records(a.df))
    return Response(data.to_csv(index=False).encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=choumran_{what}.csv"})


if __name__ == "__main__":
    # warm the model so the first request is fast
    threading.Thread(target=default_model, daemon=True).start()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
