"""Extensible skill taxonomy.

The taxonomy is data, not code: ``app/core/skill_taxonomy.json`` holds
canonical names, aliases, categories, optional relations and an ``ambiguous``
flag. An extra JSON file can be merged in with the ``SKILL_TAXONOMY_PATH``
environment variable, so adding a new technology never touches the algorithm.

Loading is lazy and cached; call :func:`get_taxonomy` from services.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import BACKEND_DIR

logger = logging.getLogger("matchwise.taxonomy")

DEFAULT_TAXONOMY_PATH = BACKEND_DIR / "app" / "core" / "skill_taxonomy.json"


@dataclass(frozen=True)
class SkillEntry:
    canonical: str
    category: str | None = None
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    ambiguous: bool = False

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.canonical, *self.aliases)


@dataclass
class Taxonomy:
    entries: list[SkillEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        # lowercased name -> entry (aliases and canonical names)
        self._by_name: dict[str, SkillEntry] = {}
        self._by_canonical: dict[str, SkillEntry] = {}
        for entry in self.entries:
            self._by_canonical[entry.canonical.lower()] = entry
            for name in entry.all_names:
                key = name.lower()
                # first definition wins; canonical names take priority
                if key not in self._by_name or self._by_name[key].canonical != entry.canonical:
                    if key not in self._by_name:
                        self._by_name[key] = entry
            # canonical always wins over an alias from another entry
            self._by_name[entry.canonical.lower()] = entry

        # reverse relation map: skill -> canonicals that relate to it
        self._reverse_related: dict[str, set[str]] = {}
        for entry in self.entries:
            for rel in entry.related:
                self._reverse_related.setdefault(rel.lower(), set()).add(entry.canonical)

    # ------------------------------------------------------------------ lookups
    @property
    def canonical_names(self) -> list[str]:
        return [entry.canonical for entry in self.entries]

    @property
    def aliases(self) -> list[str]:
        names: list[str] = []
        for entry in self.entries:
            names.extend(entry.all_names)
        return names

    @staticmethod
    def _lower(values) -> set[str]:
        return {str(value).lower() for value in values}

    def normalize(self, phrase: str) -> str | None:
        """Return the canonical name for a phrase, or None when unknown."""
        if not phrase:
            return None
        entry = self._by_name.get(phrase.strip().lower())
        return entry.canonical if entry else None

    def entry_for_name(self, phrase: str) -> SkillEntry | None:
        return self._by_name.get((phrase or "").strip().lower())

    def entry_for_canonical(self, canonical: str) -> SkillEntry | None:
        return self._by_canonical.get((canonical or "").lower())

    def category_of(self, canonical: str) -> str | None:
        entry = self.entry_for_canonical(canonical)
        return entry.category if entry else None

    def is_ambiguous(self, phrase: str) -> bool:
        entry = self.entry_for_name(phrase)
        return bool(entry and entry.ambiguous)

    def related_to(self, canonical: str) -> set[str]:
        """Canonicals related to ``canonical`` (defensible relationship only)."""
        entry = self.entry_for_canonical(canonical)
        if entry:
            return self._lower(entry.related)
        return self._lower(self._reverse_related.get(canonical.lower(), set()))


# --------------------------------------------------------------------- loading
_lock = threading.Lock()
_cached: Taxonomy | None = None


def _read_entries(path: Path) -> list[SkillEntry]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    entries: list[SkillEntry] = []
    for raw in payload.get("skills", []):
        if not raw.get("canonical"):
            continue
        entries.append(
            SkillEntry(
                canonical=str(raw["canonical"]).strip(),
                category=(str(raw["category"]).strip() if raw.get("category") else None),
                aliases=tuple(str(a).strip() for a in raw.get("aliases", []) if str(a).strip()),
                related=tuple(str(r).strip() for r in raw.get("related", []) if str(r).strip()),
                ambiguous=bool(raw.get("ambiguous", False)),
            )
        )
    return entries


def load_taxonomy() -> Taxonomy:
    """Load the built-in taxonomy plus an optional extra file from the env."""
    entries = _read_entries(DEFAULT_TAXONOMY_PATH)

    extra_path = os.getenv("SKILL_TAXONOMY_PATH", "").strip()
    if extra_path:
        extra = Path(extra_path).expanduser()
        if extra.is_file():
            try:
                existing = {entry.canonical.lower() for entry in entries}
                for entry in _read_entries(extra):
                    if entry.canonical.lower() not in existing:
                        entries.append(entry)
                logger.info("Merged extra taxonomy from %s", extra)
            except (OSError, ValueError) as exc:
                logger.warning("Could not load SKILL_TAXONOMY_PATH %s: %s", extra, exc)
        else:
            logger.warning("SKILL_TAXONOMY_PATH does not exist: %s", extra)

    return Taxonomy(entries=entries)


def get_taxonomy() -> Taxonomy:
    global _cached
    if _cached is None:
        with _lock:
            if _cached is None:
                _cached = load_taxonomy()
    return _cached


def reset_taxonomy_cache() -> None:
    """Testing hook: force a reload on the next ``get_taxonomy()``."""
    global _cached
    with _lock:
        _cached = None
