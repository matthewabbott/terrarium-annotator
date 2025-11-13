"""Codex persistence helpers for learned terminology."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class CodexEntry:
    term: str
    definition: str
    created_at: str
    updated_at: str
    first_post_id: Optional[int] = None
    last_post_id: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "CodexEntry":
        return cls(
            term=data["term"],
            definition=data["definition"],
            created_at=data.get("created_at") or _now(),
            updated_at=data.get("updated_at") or _now(),
            first_post_id=data.get("first_post_id"),
            last_post_id=data.get("last_post_id"),
        )

    def touch(self, post_id: Optional[int]) -> None:
        self.updated_at = _now()
        if self.first_post_id is None:
            self.first_post_id = post_id
        self.last_post_id = post_id

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CodexUpdate:
    term: str
    definition: str
    status: str  # "new"|"update"|"skip"
    source_post_id: Optional[int]


class CodexStore:
    """JSON-backed key/value store for codex entries."""

    def __init__(self, path: Path = Path("data/codex.json")) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: Dict[str, CodexEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        for item in data.get("entries", []):
            entry = CodexEntry.from_dict(item)
            self.entries[entry.term.lower()] = entry

    def save(self) -> None:
        payload = {
            "updated_at": _now(),
            "entries": [entry.to_dict() for entry in sorted(self.entries.values(), key=lambda e: e.term.lower())],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    def upsert(self, update: CodexUpdate) -> CodexEntry:
        key = update.term.lower()
        entry = self.entries.get(key)
        if not entry:
            entry = CodexEntry(
                term=update.term,
                definition=update.definition,
                created_at=_now(),
                updated_at=_now(),
                first_post_id=update.source_post_id,
                last_post_id=update.source_post_id,
            )
            self.entries[key] = entry
        else:
            entry.definition = update.definition
            entry.touch(update.source_post_id)
        return entry

    def matching_entries(self, text: str, max_matches: int = 5) -> List[CodexEntry]:
        matches: List[CodexEntry] = []
        haystack = text.lower()
        for entry in self.entries.values():
            if entry.term.lower() in haystack:
                matches.append(entry)
            if len(matches) == max_matches:
                break
        return matches

    def as_text_block(self, entries: Iterable[CodexEntry]) -> str:
        lines = ["<codex>"]
        for entry in entries:
            lines.append(f"<term name=\"{entry.term}\">{entry.definition}</term>")
        lines.append("</codex>")
        return "\n".join(lines)


CODEX_UPDATE_PATTERN = re.compile(r"<codex_updates>(.*?)</codex_updates>", re.DOTALL)


def extract_updates(message: str) -> List[CodexUpdate]:
    """Parse `<codex_updates>` JSON payloads from the assistant response."""

    match = CODEX_UPDATE_PATTERN.search(message)
    if not match:
        return []

    payload = match.group(1).strip()
    if not payload:
        return []

    try:
        raw_updates = json.loads(payload)
    except json.JSONDecodeError:
        return []

    updates: List[CodexUpdate] = []
    for item in raw_updates:
        term = item.get("term")
        definition = item.get("definition")
        if not term or not definition:
            continue
        updates.append(
            CodexUpdate(
                term=term.strip(),
                definition=definition.strip(),
                status=(item.get("status") or "update"),
                source_post_id=item.get("source_post_id"),
            )
        )
    return updates


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
