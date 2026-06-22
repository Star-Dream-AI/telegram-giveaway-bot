from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from bot.settings import PROJECT_ROOT

log = logging.getLogger("bot.utils.render")

TEMPLATES_DIR = PROJECT_ROOT / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(template_name: str, lang: str = "zh", **ctx: Any) -> str:
    candidates = [
        f"{template_name}.{lang}.j2",
        f"{template_name}.en.j2",
        f"{template_name}.j2",
    ]
    seen: set[str] = set()
    last_err: Exception | None = None
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        try:
            tpl = _env.get_template(name)
        except TemplateNotFound as e:
            last_err = e
            continue
        return tpl.render(**ctx)
    log.error("template missing: %s (tried %s)", template_name, candidates)
    raise last_err or TemplateNotFound(template_name)
