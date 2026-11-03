"""Input schema, column auto-detection and normalisation of uploaded data."""
from __future__ import annotations

import pandas as pd

LABELS = ["ordinary", "fraud", "scam"]
LABEL_AR = {"ordinary": "عادية", "fraud": "احتيال", "scam": "نصب"}

# canonical column -> (required?, Arabic description, aliases)
COLUMNS: dict[str, tuple[bool, str, list[str]]] = {
    "transaction_id": (False, "رقم العملية", ["id", "txn_id", "tx_id", "reference", "ref", "رقم العملية"]),
    "timestamp": (True, "التاريخ والوقت", ["date", "datetime", "time", "created_at", "transaction_date", "التاريخ", "الوقت"]),
    "sender_id": (True, "رقم المرسل (الشخص)", ["customer_id", "client_id", "account_id", "from_id", "payer_id", "user_id", "رقم المرسل"]),
    "sender_name": (False, "اسم المرسل", ["customer_name", "client_name", "from_name", "payer_name", "name", "اسم المرسل", "الاسم"]),
    "receiver_id": (True, "رقم المستلم (محل أو شخص)", ["merchant_id", "shop_id", "store_id", "to_id", "payee_id", "beneficiary_id", "رقم المستلم", "رقم المحل"]),
    "receiver_name": (False, "اسم المستلم", ["merchant_name", "shop_name", "store_name", "to_name", "payee_name", "beneficiary", "اسم المستلم", "اسم المحل"]),
    "receiver_type": (False, "نوع المستلم (shop/person)", ["merchant_type", "payee_type", "to_type", "نوع المستلم"]),
    "amount": (True, "المبلغ", ["value", "amt", "total", "sum", "المبلغ", "القيمة"]),
    "device_id": (False, "رقم الجهاز", ["device", "device_fingerprint", "terminal_id", "الجهاز"]),
    "city": (False, "المدينة / الموقع", ["location", "country", "region", "المدينة", "الموقع"]),
    "label": (False, "التصنيف المعروف (اختياري للتدريب)", ["class", "is_fraud", "fraud", "target", "category", "التصنيف"]),
}

LABEL_ALIASES = {
    "ordinary": "ordinary", "normal": "ordinary", "legit": "ordinary", "legitimate": "ordinary",
    "genuine": "ordinary", "ok": "ordinary", "0": "ordinary", "false": "ordinary", "no": "ordinary",
    "عادية": "ordinary", "عادي": "ordinary", "سليمة": "ordinary",
    "fraud": "fraud", "fraudulent": "fraud", "1": "fraud", "true": "fraud", "yes": "fraud",
    "احتيال": "fraud",
    "scam": "scam", "scamming": "scam", "نصب": "scam", "2": "scam",
}


class SchemaError(ValueError):
    pass


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def guess_mapping(columns: list[str]) -> dict[str, str | None]:
    """Guess which uploaded column corresponds to each canonical column."""
    normed = {_norm(c): c for c in columns}
    mapping: dict[str, str | None] = {}
    taken: set[str] = set()
    for canon, (_, _, aliases) in COLUMNS.items():
        found = None
        for cand in [canon, *aliases]:
            c = normed.get(_norm(cand))
            if c is not None and c not in taken:
                found = c
                break
        mapping[canon] = found
        if found:
            taken.add(found)
    return mapping


def normalise(raw: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    """Rename/clean an uploaded frame into the canonical schema."""
    missing = [c for c, (req, _, _) in COLUMNS.items() if req and not mapping.get(c)]
    if missing:
        raise SchemaError("أعمدة ناقصة: " + "، ".join(COLUMNS[c][1] for c in missing))
    for canon, src in mapping.items():
        if src and src not in raw.columns:
            raise SchemaError(f"العمود '{src}' غير موجود في الملف")

    df = pd.DataFrame({canon: raw[src] for canon, src in mapping.items() if src})

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["amount"] = pd.to_numeric(
        df["amount"].astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False),
        errors="coerce",
    )
    bad = df["timestamp"].isna() | df["amount"].isna()
    if bad.all():
        raise SchemaError("ما قدرنا نقرأ التاريخ أو المبلغ في أي سطر. تأكد من اختيار الأعمدة الصحيحة.")
    df = df[~bad].copy()

    for c in ("sender_id", "receiver_id"):
        df[c] = df[c].astype(str).str.strip()
    if "transaction_id" not in df:
        df["transaction_id"] = [f"T{i + 1:06d}" for i in range(len(df))]
    df["transaction_id"] = df["transaction_id"].astype(str)
    if "sender_name" not in df:
        df["sender_name"] = df["sender_id"]
    if "receiver_name" not in df:
        df["receiver_name"] = df["receiver_id"]
    df["sender_name"] = df["sender_name"].fillna(df["sender_id"]).astype(str)
    df["receiver_name"] = df["receiver_name"].fillna(df["receiver_id"]).astype(str)

    if "receiver_type" in df:
        t = df["receiver_type"].astype(str).str.strip().str.lower()
        df["receiver_type"] = t.map(lambda v: "person" if v in ("person", "p2p", "individual", "شخص") else "shop")
    else:
        # a receiver that also sends money is a person, otherwise treat as shop
        senders = set(df["sender_id"])
        df["receiver_type"] = df["receiver_id"].map(lambda r: "person" if r in senders else "shop")

    if "label" in df:
        lab = df["label"].astype(str).str.strip().str.lower().map(LABEL_ALIASES)
        unknown = df.loc[lab.isna(), "label"].astype(str).unique()[:5]
        if len(unknown) and lab.notna().any():
            raise SchemaError("قيم تصنيف غير معروفة: " + "، ".join(unknown)
                              + " (المسموح: ordinary / fraud / scam)")
        if lab.isna().all():
            df = df.drop(columns="label")
        else:
            df["label"] = lab

    return df.sort_values("timestamp").reset_index(drop=True)
