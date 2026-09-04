"""Harvest the human-curated gold set from the live thread wiki pages.

Reads steelbea.me/banished/wiki/thread/3..40 and extracts entity links.
Link structure (verified against live HTML): entity links are
`<a class="wikilink1|wikilink2" data-wiki-id="characters:mik"
href="/banished/wiki/characters/mik">label</a>`; we take `data-wiki-id`
when present and path-parse the href otherwise. Navigation/media links
are excluded by the wikilink class filter. Anomalous namespaces seen in
the wild (e.g. `objecst`, `race` vs `races`) are preserved verbatim —
they're review data, not errors to correct.

Output: data/exports/gold-set.json (gitignored run artifact).

Design: docs/design/dev-verification.md L5.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

THREAD_RANGE = range(3, 41)
BASE = "https://steelbea.me/banished/wiki/thread/"
PATH_RE = re.compile(
    r"^(?:https?://steelbea\.me)?/banished/wiki/([a-z0-9][a-z0-9/_-]*[a-z0-9])$"
)
SKIP_PREFIXES = ("thread/", "playground/", "wiki/", "irc/")


class LinkParser(HTMLParser):
    """Collects (namespace, slug, label) from wikilink anchors only."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self._open: tuple[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._open is not None:
            return
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if not any(c.startswith("wikilink") for c in classes):
            return
        wiki_id = attr.get("data-wiki-id")
        if wiki_id and ":" in wiki_id:
            namespace, slug = wiki_id.rsplit(":", 1)
        else:
            m = PATH_RE.match(attr.get("href") or "")
            if not m:
                return
            path = m.group(1)
            *ns_parts, slug = path.split("/")
            namespace = "/".join(ns_parts)
        if namespace + "/" in SKIP_PREFIXES or not slug:
            return
        self._open = (namespace, slug)
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open is not None:
            namespace, slug = self._open
            label = " ".join("".join(self._text).split())
            if label:
                self.links.append((namespace, slug, label))
            self._open = None
            self._text = []


def harvest(out_path: Path, threads=THREAD_RANGE) -> dict:
    """Fetch thread pages and extract wiki links. Tolerates failures
    per page (records them); never raises on a dead site."""
    pages: list[dict] = []
    for n in threads:
        url = f"{BASE}{n}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (gold-set harvest)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except (OSError, ValueError) as exc:  # site down/missing — record, continue
            pages.append({"thread": n, "url": url, "error": str(exc)})
            continue
        parser = LinkParser()
        parser.feed(html)
        pages.append(
            {
                "thread": n,
                "url": url,
                "links": [
                    {"namespace": ns, "slug": slug, "label": label}
                    for ns, slug, label in parser.links
                ],
            }
        )
    out = {"source": "steelbea.me thread pages 3-40", "pages": pages}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1))
    return out


def main() -> int:
    out = harvest(Path("data/exports/gold-set.json"))
    ok = [p for p in out["pages"] if "links" in p]
    failed = [p for p in out["pages"] if "error" in p]
    total = sum(len(p["links"]) for p in ok)
    unique = len({(l["namespace"], l["slug"]) for p in ok for l in p["links"]})
    hist = Counter(link["namespace"] for p in ok for link in p["links"])
    print(f"pages ok: {len(ok)}, failed: {len(failed)}")
    print(f"total links: {total}; unique entities: {unique}")
    print("namespace histogram:", dict(hist.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
