from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from bot.settings import PROJECT_ROOT

log = logging.getLogger("bot.i18n")

LOCALES_DIR = PROJECT_ROOT / "locales"

_translations: dict[str, dict[str, str]] = {}
_default_lang: str = "zh"
_fallback_lang: str = "en"


def _flatten(prefix: str, data: Any, out: dict[str, str]) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            _flatten(key, v, out)
    elif isinstance(data, list):
        out[prefix] = "\n".join(str(x) for x in data)
    else:
        out[prefix] = str(data)


def load_locales(default_lang: str = "zh") -> None:
    global _default_lang, _translations
    _default_lang = default_lang
    _translations = {}
    if not LOCALES_DIR.exists():
        log.warning("locales dir missing: %s", LOCALES_DIR)
        return
    for yml in LOCALES_DIR.glob("*.yaml"):
        lang = yml.stem
        try:
            raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            log.exception("failed to load locale %s", yml)
            continue
        flat: dict[str, str] = {}
        _flatten("", raw, flat)
        _translations[lang] = flat
        log.info("loaded locale %s (%d keys)", lang, len(flat))


def available_langs() -> list[str]:
    return sorted(_translations.keys())


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    chosen = lang or _default_lang
    table = _translations.get(chosen) or {}
    template = table.get(key)
    if template is None:
        fallback = _translations.get(_fallback_lang) or {}
        template = fallback.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template
