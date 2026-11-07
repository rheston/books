from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    rss_url: str = ""
    csv: str = ""
    shelf: str = "to-read"
    libraries: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=lambda: ["ebook", "audiobook"])
    languages: list[str] = field(default_factory=lambda: ["en"])
    prefer: list[str] = field(default_factory=lambda: ["ebook", "audiobook"])
    cache_dir: str = ".cache"
    availability_ttl_minutes: float = 30
    workers: int = 4


def load(path: str | Path | None) -> Settings:
    """Load settings from a TOML file. A missing default path is fine (CLI flags can
    supply everything); a missing explicitly-requested path is an error."""
    s = Settings()
    if path is None:
        return s
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    gr, libby, run = data.get("goodreads", {}), data.get("libby", {}), data.get("run", {})
    known = {"goodreads": {"rss_url", "csv", "shelf"}, "libby": {"libraries", "types", "languages", "prefer"},
             "run": {"cache_dir", "availability_ttl_minutes", "workers"}}
    for section, allowed in known.items():
        unknown = set(data.get(section, {})) - allowed
        if unknown:
            raise ConfigError(f"{path}: unknown key(s) in [{section}]: {sorted(unknown)} (typo?)")
    for k in data:
        if k not in known:
            raise ConfigError(f"{path}: unknown section [{k}]")

    s.rss_url = gr.get("rss_url", s.rss_url)
    s.csv = gr.get("csv", s.csv)
    s.shelf = gr.get("shelf", s.shelf)
    s.libraries = list(libby.get("libraries", s.libraries))
    s.types = list(libby.get("types", s.types))
    s.languages = list(libby.get("languages", s.languages))
    s.prefer = list(libby.get("prefer", s.prefer))
    s.cache_dir = run.get("cache_dir", s.cache_dir)
    s.availability_ttl_minutes = run.get("availability_ttl_minutes", s.availability_ttl_minutes)
    s.workers = int(run.get("workers", s.workers))
    if s.rss_url and s.csv:
        raise ConfigError(
            f"{path}: [goodreads] has both rss_url and csv. Keep only the one you want "
            "(delete or comment out the other line)."
        )
    return s
