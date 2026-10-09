from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from bot.settings import PROJECT_ROOT
from bot.utils.esc import esc

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
    for yml in sorted(LOCALES_DIR.glob("*.yaml")):
        lang = yml.stem
        try:
            raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            log.exception("failed to load locale %s", lang)
            continue
        flat: dict[str, str] = {}
        _flatten("", raw, flat)
        _translations[lang] = flat
        log.info("loaded locale %s (%d keys)", lang, len(flat))
    _warn_missing_keys()


def _warn_missing_keys() -> None:
    """Report keys present in one locale but not the other.

    A half-translated locale silently degrades to the fallback language, which
    is very hard to notice in production.  Log it at startup instead.
    """
    base = _translations.get(_fallback_lang)
    if not base:
        return
    for lang, table in _translations.items():
        if lang == _fallback_lang:
            continue
        missing = sorted(set(base) - set(table))
        extra = sorted(set(table) - set(base))
        if missing:
            log.warning("locale %s is missing %d key(s): %s", lang, len(missing), missing)
        if extra:
            log.warning("locale %s has %d key(s) absent from %s: %s", lang, len(extra), _fallback_lang, extra)


def available_langs() -> list[str]:
    return sorted(_translations.keys())


def missing_key_report() -> dict[str, list[str]]:
    """Per-language missing/extra keys — used by the test suite."""
    base = _translations.get(_fallback_lang, {})
    out: dict[str, list[str]] = {}
    for lang, table in _translations.items():
        if lang == _fallback_lang:
            continue
        out[lang] = sorted((set(base) ^ set(table)))
    return out


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate ``key`` and interpolate HTML-escaped keyword arguments.

    Values wrapped in ``Raw()`` are inserted verbatim; everything else is
    escaped, which is what made title/description injection safe.
    """
    chosen = lang or _default_lang
    table = _translations.get(chosen) or {}
    template = table.get(key)
    if template is None:
        fallback = _translations.get(_fallback_lang) or {}
        template = fallback.get(key)
        if template is None:
            log.warning("missing i18n key: %s", key)
            template = key
    if kwargs:
        safe = {k: esc(v) for k, v in kwargs.items()}
        try:
            return template.format(**safe)
        except (KeyError, IndexError, ValueError, AttributeError):
            log.warning("bad placeholder in i18n key %s", key, exc_info=True)
            return template
    return template
