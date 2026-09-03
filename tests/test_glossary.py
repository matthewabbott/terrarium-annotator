"""L0 tests for the glossary store + quote gate, per docs/plan T3.

Corpus access is faked with a dict of post bodies; the gate's contract
(verbatim substring + term/alias containment) is what matters.
"""

from __future__ import annotations

import pytest

from terrarium_annotator.glossary import (
    MAX_QUOTE_CHARS,
    DuplicateEntry,
    Evidence,
    GlossaryStore,
    Provenance,
    QuoteRejected,
    UnknownEntry,
)

POSTS = {
    100: "The Vatis gathered at dusk, their Vys reserves nearly spent.",
    200: "Soma channeled vys into the cloak. Nothing happened.",
    300: "Archmagos Suresh welcomed the Master vatis to the library.",
    400: " unrelated post about markets and silver ",
}

PROV = Provenance(thread_id=1, pass_id="test-pass", log_seq=0, tree_version=0)


def fake_post_body(post_id: int) -> str | None:
    return POSTS.get(post_id)


@pytest.fixture
def store(tmp_path):
    with GlossaryStore(tmp_path / "gloss.db", fake_post_body) as s:
        yield s


def propose(store, term="Vatis", gloss="A mage of the Rhynian hierarchy.", **kw):
    kw.setdefault("evidence", [Evidence(100, "The Vatis gathered at dusk")])
    kw.setdefault("provenance", PROV)
    return store.propose_entry(term=term, gloss=gloss, **kw)


class TestPropose:
    def test_happy_path_creates_tentative_with_revision_and_source(self, store):
        e = propose(store, tags=("magic", "faction"), keys=("vatis",))
        assert e.status == "tentative"
        assert e.tags == ("faction", "magic")
        assert e.aliases == ("vatis",)
        revs = store.revisions(e.id)
        assert len(revs) == 1
        assert revs[0].provenance.pass_id == "test-pass"
        rows = store._conn.execute(
            "SELECT post_id, quote FROM entry_source WHERE entry_id = ?", (e.id,)
        ).fetchall()
        assert rows == [(100, "The Vatis gathered at dusk")]

    def test_fts_finds_entry(self, store):
        propose(store)
        assert [e.term for e in store.search("Vatis")] == ["Vatis"]
        assert store.search("Rhynian hierarchy")

    def test_duplicate_term_rejected_case_insensitive(self, store):
        propose(store)
        with pytest.raises(DuplicateEntry):
            propose(store, term="vatis", gloss="different")
        with pytest.raises(DuplicateEntry):
            propose(store, term="VATIS", gloss="different")

    def test_alias_collision_rejected(self, store):
        propose(store, keys=("vatis",))
        with pytest.raises(DuplicateEntry):
            store.propose_entry(
                term="Mage",
                gloss="x",
                evidence=[Evidence(300, "Archmagos Suresh")],
                provenance=PROV,
                keys=("Vatis",),
            )


class TestQuoteGate:
    def test_paraphrased_quote_rejected(self, store):
        with pytest.raises(QuoteRejected, match="verbatim"):
            propose(store, evidence=[Evidence(100, "the vatis gathered at dusk.")])

    def test_quote_without_term_rejected(self, store):
        with pytest.raises(QuoteRejected, match="neither term nor alias"):
            propose(store, evidence=[Evidence(100, "gathered at dusk, their")])

    def test_quote_from_wrong_post_rejected(self, store):
        with pytest.raises(QuoteRejected, match="verbatim"):
            propose(store, evidence=[Evidence(200, "The Vatis gathered at dusk")])

    def test_unknown_post_rejected(self, store):
        with pytest.raises(QuoteRejected, match="not in corpus"):
            propose(store, evidence=[Evidence(999, "Vatis")])

    def test_oversized_quote_rejected(self, store):
        with pytest.raises(QuoteRejected, match="over"):
            propose(store, evidence=[Evidence(100, "Vatis" + " x" * MAX_QUOTE_CHARS)])

    def test_empty_evidence_rejected(self, store):
        with pytest.raises(QuoteRejected, match="at least one"):
            propose(store, evidence=[])

    def test_quote_via_alias_passes(self, store):
        # Term "Vys" does not appear in the quote; registered alias "vys" does.
        e = store.propose_entry(
            term="Vys",
            gloss="Raw magical energy.",
            evidence=[Evidence(200, "channeled vys into the cloak")],
            provenance=PROV,
            keys=("vys",),
        )
        assert e.term == "Vys"

    def test_case_sensitive_containment(self, store):
        # "vys" (lowercase term) does not literally occur in the verbatim
        # quote — containment is case-sensitive.
        with pytest.raises(QuoteRejected, match="neither term nor alias"):
            store.propose_entry(
                term="vys",
                gloss="x",
                evidence=[Evidence(100, "their Vys reserves")],
                provenance=PROV,
            )


class TestUpdate:
    def test_appends_revision_and_updates_card(self, store):
        e = propose(store)
        store.update_entry(
            "Vatis",
            gloss="A mage; masters channel Vys.",
            evidence=[
                Evidence(
                    100, "The Vatis gathered at dusk, their Vys reserves nearly spent."
                )
            ],
            provenance=Provenance(thread_id=2, pass_id="test-pass", log_seq=5),
        )
        updated = store.get("Vatis")
        assert updated.gloss == "A mage; masters channel Vys."
        revs = store.revisions(e.id)
        assert len(revs) == 2
        assert revs[0].gloss == "A mage of the Rhynian hierarchy."  # retained
        assert revs[1].provenance.log_seq == 5

    def test_update_unknown_term_rejected(self, store):
        with pytest.raises(UnknownEntry):
            store.update_entry(
                "Nonexistent",
                gloss="x",
                evidence=[Evidence(100, "The Vatis gathered at dusk")],
                provenance=PROV,
            )

    def test_update_still_gated(self, store):
        propose(store)
        with pytest.raises(QuoteRejected):
            store.update_entry(
                "Vatis",
                gloss="y",
                evidence=[Evidence(100, "paraphrased Vatis text")],
                provenance=PROV,
            )


class TestAlias:
    def test_add_alias_happy_and_find(self, store):
        propose(store)
        store.add_alias(
            "Vatis",
            "Master vatis",
            evidence=Evidence(300, "welcomed the Master vatis to the library"),
        )
        assert store.find("master vatis").term == "Vatis"
        assert store.get("Vatis").aliases == ("Master vatis",)
        # Alias registration leaves provenance in entry_source.
        (count,) = store._conn.execute(
            "SELECT COUNT(*) FROM entry_source WHERE entry_id = 1"
            " AND revision_id IS NULL"
        ).fetchone()
        assert count == 1

    def test_alias_quote_without_alias_rejected(self, store):
        propose(store)
        with pytest.raises(QuoteRejected, match="does not contain alias"):
            store.add_alias(
                "Vatis", "Wizard", evidence=Evidence(300, "Archmagos Suresh")
            )

    def test_alias_nonverbatim_quote_rejected(self, store):
        propose(store)
        with pytest.raises(QuoteRejected, match="verbatim"):
            store.add_alias(
                "Vatis", "Master vatis", evidence=Evidence(300, "Master vatis!")
            )


class TestMergeAndStatus:
    def test_merge_unions_evidence(self, store):
        a = propose(store)
        b = store.propose_entry(
            term="Vys",
            gloss="Energy.",
            evidence=[Evidence(200, "channeled vys into the cloak")],
            provenance=PROV,
            keys=("vys",),
        )
        store.merge_entries(a.id, b.id)
        survivor = store.get(a.id)
        assert survivor.aliases == ("vys",)
        assert len(store.revisions(a.id)) == 2  # both histories retained
        with pytest.raises(UnknownEntry):
            store.get(b.id)
        (count,) = store._conn.execute(
            "SELECT COUNT(*) FROM entry_source WHERE entry_id = ?", (a.id,)
        ).fetchone()
        assert count == 2

    def test_confirm_promotes(self, store):
        propose(store)
        store.confirm("Vatis")
        assert store.get("Vatis").status == "confirmed"
