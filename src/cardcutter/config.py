"""Load config.toml. Every model name, path, and tunable lives there, never in code."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = "config.toml"


def find_config(start: Path | None = None) -> Path:
    """Find config.toml: $CARDCUTTER_CONFIG, else walk up from `start` (default: cwd)."""
    env = os.environ.get("CARDCUTTER_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / DEFAULT_CONFIG_NAME).is_file():
            return d / DEFAULT_CONFIG_NAME
    raise FileNotFoundError(f"No {DEFAULT_CONFIG_NAME} found from {here} upward; set CARDCUTTER_CONFIG.")


@dataclass(frozen=True)
class Config:
    path: Path
    data: dict[str, Any]

    @property
    def root(self) -> Path:
        return self.path.parent

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(f"config.toml is missing `{dotted}`")
        return value

    def path_of(self, dotted: str) -> Path:
        """A config value that is a path, resolved relative to config.toml."""
        p = Path(str(self.require(dotted))).expanduser()
        return p if p.is_absolute() else (self.root / p)


def load_config(path: Path | None = None) -> Config:
    path = path or find_config()
    with open(path, "rb") as f:
        return Config(path=path.resolve(), data=tomllib.load(f))
