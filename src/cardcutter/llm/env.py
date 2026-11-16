"""Read API keys from the environment or a git-ignored .env. Never logs values."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> list[str]:
    """Load KEY=VALUE lines into os.environ (existing variables win). Returns key names."""
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def get_key(env_name: str) -> str | None:
    value = os.environ.get(env_name, "").strip()
    return value or None


def redact(text: str) -> str:
    """Remove any configured key values from a string before it is printed or logged."""
    for k, v in os.environ.items():
        if v and len(v) >= 12 and ("KEY" in k or "TOKEN" in k or "SECRET" in k):
            text = text.replace(v, f"<{k}>")
    return text
