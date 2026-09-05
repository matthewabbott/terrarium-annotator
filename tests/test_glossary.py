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
    GlossaryError,
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
    500: "They whispered among themselves in that strange language.",
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

    def test_readding_same_alias_is_idempotent_noop(self, store):
        propose(store)
        ev = Evidence(300, "welcomed the Master vatis to the library")
        store.add_alias("Vatis", "Master vatis", evidence=ev)
        store.add_alias("Vatis", "Master vatis", evidence=ev)  # same alias again
        assert store.get("Vatis").aliases == ("Master vatis",)


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


class TestEpistemicMode:
    def test_mode_stored_on_source(self, store):
        e = propose(
            store,
            evidence=[Evidence(100, "The Vatis gathered at dusk", mode="claimed")],
        )
        rows = store._conn.execute(
            "SELECT mode FROM entry_source WHERE entry_id = ?", (e.id,)
        ).fetchall()
        assert rows == [("claimed",)]

    def test_default_mode_is_narrated(self, store):
        e = propose(store)
        rows = store._conn.execute(
            "SELECT mode FROM entry_source WHERE entry_id = ?", (e.id,)
        ).fetchall()
        assert rows == [("narrated",)]

    def test_unknown_mode_rejected(self, store):
        with pytest.raises(GlossaryError, match="epistemic mode"):
            propose(
                store,
                evidence=[Evidence(100, "The Vatis gathered at dusk", mode="vibes")],
            )

    def test_mode_in_db_must_be_valid(self, store):
        # CHECK constraint: direct SQL with a bogus mode fails.
        propose(store)
        with pytest.raises(Exception, match="CHECK"):
            store._conn.execute("UPDATE entry_source SET mode = 'vibes'")


class TestRenameRevert:
    def test_rename_aliasing_and_revision_note(self, store):
        e = propose(store)
        renamed = store.rename_entry(
            e.id, "Vatis (order)", Provenance(thread_id=1, pass_id="t")
        )
        assert renamed.term == "Vatis (order)"
        assert renamed.aliases == ("Vatis",)
        assert store.find("vatis").id == e.id  # old title resolves
        revs = store.revisions(e.id)
        assert revs[-1].note == "renamed from 'Vatis'"

    def test_rename_collision_rejected(self, store):
        propose(store)
        store.propose_entry(
            term="Suresh",
            gloss="The Archmagos.",
            evidence=[Evidence(300, "Archmagos Suresh")],
            provenance=PROV,
        )
        with pytest.raises(DuplicateEntry):
            store.rename_entry("Vatis", "suresh", Provenance(thread_id=1, pass_id="t"))

    def test_revert_restores_gloss_append_only(self, store):
        e = propose(store)
        store.update_entry(
            "Vatis",
            gloss="A degraded gloss.",
            evidence=[Evidence(100, "The Vatis gathered at dusk")],
            provenance=PROV,
        )
        rev = store.revert_entry("Vatis", 1, Provenance(thread_id=2, pass_id="t"))
        assert rev.reverts == 1
        assert rev.note == "revert to r1"
        assert store.get("Vatis").gloss == "A mage of the Rhynian hierarchy."
        revs = store.revisions(e.id)
        assert len(revs) == 3  # history retained
        # Evidence carried over to the revert revision.
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM entry_source WHERE revision_id = ?",
            (rev.id,),
        ).fetchone()
        assert rows[0] == 1

    def test_revert_unknown_revision_rejected(self, store):
        propose(store)
        with pytest.raises(GlossaryError, match="no revision"):
            store.revert_entry("Vatis", 99, Provenance(thread_id=1, pass_id="t"))


class TestShadowGate:
    def test_generic_term_logged_but_created(self, store):
        e = propose(
            store,
            term="strange language",
            evidence=[Evidence(500, "in that strange language")],
        )
        assert store.get(e.id).term == "strange language"  # never blocked
        rows = store.deferred_candidates()
        assert len(rows) == 1 and rows[0][1] == "strange language"
        assert rows[0][3] == 500  # post_id recorded

    def test_specific_terms_not_logged(self, store):
        propose(store)  # "Vatis" — uppercase
        store.propose_entry(
            term="खुनी",
            gloss="Brand: slayer.",
            evidence=[Evidence(100, "their Vys reserves nearly spent")],
            provenance=PROV,
            keys=("Vys",),
        )
        assert store.deferred_candidates() == []


class TestGateHeuristic:
    def test_generic_detection(self):
        from terrarium_annotator.glossary.gate import is_generic_term

        assert is_generic_term("strange language")
        assert is_generic_term("old fort")
        assert not is_generic_term("Vys")
        assert not is_generic_term("खुनी")
        assert not is_generic_term("Rikāmā Rahivāsī")
        assert not is_generic_term("District 9")
        assert not is_generic_term("")


class TestSearchSanitization:
    def test_apostrophe_query_does_not_crash(self, store):
        propose(store)
        hits = store.search("Surya's")
        assert isinstance(hits, list)  # no fts5 syntax error

    def test_fts_operators_treated_as_phrase(self, store):
        propose(store)
        # Raw FTS5 would parse these; quoted, they are literal text.
        assert store.search("Vatis OR Suresh") == []
        assert store.search('Vatis AND "nope"') == []
        assert store.search("NEAR(Vatis, Suresh)") == []

    def test_phrase_search_finds_multiword(self, store):
        propose(store, gloss="A mage of the Rhynian hierarchy.")
        hits = store.search("Rhynian hierarchy")
        assert [h.term for h in hits] == ["Vatis"]

    def test_search_still_finds_terms(self, store):
        propose(store)
        assert [h.term for h in store.search("Vatis")] == ["Vatis"]
