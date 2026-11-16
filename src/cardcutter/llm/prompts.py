"""Prompt templates live in llm/prompts/*.md so they can be tuned without touching code.

A template has a `# System` section and a `# User` section. `{{name}}` placeholders are
filled by `render`; a missing value is an error, so typos fail loudly.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from cardcutter.llm.base import Prompt

PROMPT_DIR = Path(__file__).parent / "prompts"
_VAR = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=None)
def load(name: str) -> tuple[str, str]:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = re.search(r"^# System\s*\n(.*?)^# User\s*\n(.*)\Z", raw, re.S | re.M)
    if not m:
        raise ValueError(f"prompt {name}.md needs '# System' and '# User' sections")
    return m.group(1).strip(), m.group(2).strip()


def fingerprint(name: str) -> str:
    """Short hash of a template, recorded in eval reports so prompt changes are traceable."""
    return hashlib.sha1((PROMPT_DIR / f"{name}.md").read_bytes()).hexdigest()[:8]


def render(name: str, **values: object) -> Prompt:
    system, user = load(name)

    def fill(text: str) -> str:
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in values:
                raise KeyError(f"prompt {name}.md uses {{{{{key}}}}} but no value was given")
            return str(values[key])
        return _VAR.sub(sub, text)

    return Prompt(system=fill(system), user=fill(user))
