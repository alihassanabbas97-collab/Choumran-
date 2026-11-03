"""Synthetic money-transaction generator.

Produces realistic-looking transactions between persons and shops, with three
labelled behaviours injected so the model has patterns to learn:

* ordinary – people paying their usual shops / friends, daytime, usual device.
* fraud    – a person's account or card is taken over: a burst of large
             payments at night, from a new device and another city, at shops
             the person never used before.
* scam     – victims are tricked into sending money to a scam receiver (a fake
             shop or a "mule" person) that appears mid-period and quickly
             collects money from many unrelated people, often round amounts.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

FIRST_NAMES = [
    "محمد", "علي", "حسن", "حسين", "أحمد", "خالد", "عمر", "يوسف", "جورج", "إيلي",
    "شربل", "طوني", "رامي", "سامر", "وسيم", "كريم", "زياد", "فادي", "نادر", "بلال",
    "فاطمة", "زينب", "مريم", "رنا", "نور", "لينا", "ريم", "سارة", "ماريا", "كارلا",
    "جنى", "هبة", "دانة", "ليال", "رشا", "سلمى", "نادين", "ريتا", "منى", "هالة",
]
LAST_NAMES = [
    "حداد", "خوري", "الحسيني", "عيسى", "منصور", "شمعون", "نصرالله", "الأسمر",
    "فرحات", "عبود", "سليمان", "قاسم", "يزبك", "الحاج", "جابر", "عطية", "زعيتر",
    "غانم", "صعب", "بدران", "مراد", "طعمة", "حمادة", "سعادة", "الخطيب",
]
CITIES = ["بيروت", "طرابلس", "صيدا", "جونية", "زحلة", "صور", "جبيل", "عجلتون", "النبطية", "بعلبك"]
FOREIGN_CITIES = ["إسطنبول", "دبي", "لاغوس", "كييف", "مجهول"]

# category -> (name templates, log-normal mean, sigma)
SHOP_CATEGORIES = {
    "سوبرماركت": (["سوبرماركت {}", "ميني ماركت {}"], 3.4, 0.6),
    "مطعم": (["مطعم {}", "سناك {}"], 3.0, 0.5),
    "صيدلية": (["صيدلية {}"], 3.1, 0.6),
    "محطة وقود": (["محطة {}"], 3.6, 0.4),
    "ملابس": (["بوتيك {}", "أزياء {}"], 4.0, 0.6),
    "إلكترونيات": (["إلكترونيات {}", "موبايلات {}"], 5.2, 0.7),
    "مجوهرات": (["مجوهرات {}"], 5.8, 0.7),
}
SHOP_WORDS = ["الأرز", "النخيل", "الشمس", "الجبل", "البحر", "السلام", "الأمل", "الوادي",
              "النور", "الربيع", "الياسمين", "الضيعة", "المدينة", "القلعة", "الزيتون"]
HIGH_VALUE = ("إلكترونيات", "مجوهرات")
SCAM_SHOP_NAMES = ["متجر العروض الذهبية", "استثمار سريع أونلاين", "جوائز فورية",
                   "شحن دولي سريع", "تذاكر بأسعار خيالية", "ربح من المنزل"]


def generate_transactions(
    n_persons: int = 300,
    n_shops: int = 60,
    days: int = 60,
    fraud_cases: int = 25,
    scam_receivers: int = 5,
    seed: int | None = 42,
    start: datetime | None = None,
) -> pd.DataFrame:
    """Return a DataFrame of synthetic transactions with a ``label`` column."""
    rng = np.random.default_rng(seed)
    start = start or datetime(2026, 1, 1)

    # ---- entities -------------------------------------------------------
    persons = []
    used_names: set[str] = set()
    for i in range(n_persons):
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names or len(used_names) >= len(FIRST_NAMES) * len(LAST_NAMES):
                break
        used_names.add(name)
        persons.append({
            "id": f"P{i + 1:04d}",
            "name": name,
            "city": rng.choice(CITIES),
            "device": f"D{rng.integers(10**6, 10**7)}",
            "activity": rng.uniform(0.4, 2.0),  # tx per day
        })

    shops = []
    cats = list(SHOP_CATEGORIES)
    weights = np.array([0.25, 0.2, 0.12, 0.15, 0.13, 0.09, 0.06])
    for i in range(n_shops):
        cat = rng.choice(cats, p=weights / weights.sum())
        templates, mu, sigma = SHOP_CATEGORIES[cat]
        name = rng.choice(templates).format(rng.choice(SHOP_WORDS))
        if any(s["name"] == name for s in shops):
            name = f"{name} {i + 1}"
        shops.append({"id": f"S{i + 1:03d}", "name": name, "category": cat,
                      "city": rng.choice(CITIES), "mu": mu, "sigma": sigma})
    shop_by_cat = {c: [s for s in shops if s["category"] == c] for c in cats}

    for p in persons:
        k = int(rng.integers(3, 7))
        p["favorites"] = list(rng.choice(len(shops), size=k, replace=False))
        p["friends"] = list(rng.choice(n_persons, size=int(rng.integers(1, 4)), replace=False))

    rows: list[dict] = []

    def add(ts, sender, receiver, rtype, amount, device, city, label):
        rows.append({
            "timestamp": ts,
            "sender_id": sender["id"],
            "sender_name": sender["name"],
            "receiver_id": receiver["id"],
            "receiver_name": receiver["name"],
            "receiver_type": rtype,
            "amount": round(float(amount), 2),
            "device_id": device,
            "city": city,
            "label": label,
        })

    def day_time(day: int, night: bool = False) -> datetime:
        if night:
            hour = int(rng.choice([0, 1, 2, 3, 4, 23]))
        else:
            hour = int(np.clip(rng.normal(14, 3.5), 7, 22))
        return start + timedelta(days=day, hours=hour, minutes=int(rng.integers(0, 60)),
                                 seconds=int(rng.integers(0, 60)))

    # ---- ordinary behaviour ----------------------------------------------
    for p in persons:
        n = rng.poisson(p["activity"] * days)
        for _ in range(n):
            day = int(rng.integers(0, days))
            ts = day_time(day)
            device = p["device"] if rng.random() > 0.02 else f"D{rng.integers(10**6, 10**7)}"
            city = p["city"] if rng.random() > 0.1 else rng.choice(CITIES)
            r = rng.random()
            if r < 0.1:  # peer-to-peer to a friend
                friend = persons[int(rng.choice(p["friends"]))]
                if friend["id"] == p["id"]:
                    continue
                amount = rng.lognormal(3.8, 0.7)
                if rng.random() < 0.4:
                    amount = round(amount, -1) or 10
                add(ts, p, friend, "person", amount, device, city, "ordinary")
            else:
                if r < 0.85:
                    shop = shops[int(rng.choice(p["favorites"]))]
                else:
                    shop = shops[int(rng.integers(0, len(shops)))]
                amount = rng.lognormal(shop["mu"], shop["sigma"])
                add(ts, p, shop, "shop", amount, device, city, "ordinary")

    # ---- fraud: account takeover bursts ------------------------------------
    victims = rng.choice(n_persons, size=min(fraud_cases, n_persons), replace=False)
    for v in victims:
        p = persons[int(v)]
        day = int(rng.integers(5, days))
        ts = day_time(day, night=rng.random() < 0.8)
        device = f"D{rng.integers(10**6, 10**7)}"
        city = rng.choice(FOREIGN_CITIES) if rng.random() < 0.6 else rng.choice(CITIES)
        for _ in range(int(rng.integers(3, 9))):
            pool = [s for c in HIGH_VALUE for s in shop_by_cat[c]] or shops
            unfamiliar = [s for s in pool if shops.index(s) not in p["favorites"]] or pool
            shop = unfamiliar[int(rng.integers(0, len(unfamiliar)))]
            amount = rng.lognormal(6.3, 0.6)
            add(ts, p, shop, "shop", amount, device, city, "fraud")
            ts += timedelta(minutes=int(rng.integers(2, 40)))

    # ---- scam: fake receivers collecting from many victims -----------------
    for i in range(scam_receivers):
        is_shop = rng.random() < 0.6
        if is_shop:
            receiver = {"id": f"S{n_shops + i + 1:03d}", "name": SCAM_SHOP_NAMES[i % len(SCAM_SHOP_NAMES)]}
        else:
            receiver = {"id": f"P{n_persons + i + 1:04d}",
                        "name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"}
        first_day = int(rng.integers(days // 4, days - 10))
        span = int(rng.integers(4, 10))
        n_victims = int(rng.integers(12, 30))
        for v in rng.choice(n_persons, size=n_victims, replace=False):
            p = persons[int(v)]
            for _ in range(int(rng.choice([1, 1, 1, 2, 3]))):
                day = min(days - 1, first_day + int(rng.integers(0, span)))
                ts = day_time(day)
                amount = float(rng.choice([100, 200, 250, 300, 500, 1000, 1500, 2000]))
                if rng.random() < 0.2:
                    amount += rng.uniform(1, 50)
                add(ts, p, receiver, "shop" if is_shop else "person", amount,
                    p["device"], p["city"], "scam")

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    df.insert(0, "transaction_id", [f"T{i + 1:06d}" for i in range(len(df))])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return df


if __name__ == "__main__":  # pragma: no cover
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "sample_transactions.csv"
    data = generate_transactions()
    data.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"wrote {len(data)} rows to {out}")
    print(data["label"].value_counts())
