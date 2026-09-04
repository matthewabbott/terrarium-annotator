"""Mocked unit tests for the gold-set harvest parser (no network)."""

from __future__ import annotations

import json

from terrarium_annotator.harvest_gold import LinkParser, harvest

PAGE = """
<html><body>
<a class="wikilink1" data-wiki-id="characters:mik"
   href="/banished/wiki/characters/mik">Mikhael</a>
<a class="wikilink2" href="/banished/wiki/magic/effects/leech">leech</a>
<a class="wikilink1" href="https://steelbea.me/banished/wiki/objects/oud">an oud</a>
<a href="/banished/wiki/thread/4">next thread</a>
<a class="wikilink1" href="/banished/wiki/start">home</a>
<a href="https://example.com/elsewhere">external</a>
<a class="wikilink1" data-wiki-id="objecst:typo"
   href="/banished/wiki/objecst/typo">typo namespace</a>
</body></html>
"""


class TestLinkParser:
    def test_labels_and_namespaces(self):
        p = LinkParser()
        p.feed(PAGE)
        assert ("characters", "mik", "Mikhael") in p.links
        assert ("magic/effects", "leech", "leech") in p.links  # path-parsed
        assert ("objects", "oud", "an oud") in p.links

    def test_non_wikilinks_and_thread_links_excluded(self):
        p = LinkParser()
        p.feed(PAGE)
        targets = {(ns, slug) for ns, slug, _ in p.links}
        assert ("thread", "4") not in targets
        assert not any(slug == "start" for _, slug, _ in p.links)
        assert all("elsewhere" not in label for _, _, label in p.links)

    def test_anomalous_namespace_preserved(self):
        p = LinkParser()
        p.feed(PAGE)
        assert ("objecst", "typo", "typo namespace") in p.links


class TestHarvestPartialFailure:
    def test_dead_pages_recorded_not_raised(self, tmp_path, monkeypatch):
        def fake_urlopen(req, timeout=30):
            raise OSError("403 Forbidden")

        monkeypatch.setattr(
            "terrarium_annotator.harvest_gold.urllib.request.urlopen",
            fake_urlopen,
        )
        out = harvest(tmp_path / "gold.json", threads=range(3, 5))
        assert len(out["pages"]) == 2
        assert all("error" in p for p in out["pages"])
        # Artifact still written and parseable.
        reloaded = json.loads((tmp_path / "gold.json").read_text())
        assert reloaded["pages"][0]["thread"] == 3
