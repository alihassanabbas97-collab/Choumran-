"""Translations shared by the server and the browser (static/i18n/<lang>.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "static" / "i18n"
DEFAULT_LANG = "en"
LANGS = ("en", "ar", "fr")


def norm_lang(lang: str | None) -> str:
    lang = (lang or "").strip().lower()[:2]
    return lang if lang in LANGS else DEFAULT_LANG


@lru_cache(maxsize=None)
def strings(lang: str) -> dict[str, str]:
    with open(I18N_DIR / f"{norm_lang(lang)}.json", encoding="utf-8") as f:
        return json.load(f)


def t(lang: str, key: str, **params) -> str:
    text = strings(norm_lang(lang)).get(key) or strings(DEFAULT_LANG).get(key) or key
    for k, v in params.items():
        text = text.replace("{" + k + "}", str(v))
    return text
