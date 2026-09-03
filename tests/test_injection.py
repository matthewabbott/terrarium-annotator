"""L0 tests for card injection, per docs/plan T4: trigger matching,
budget enforcement, drop order, recursion cap. Token counter is faked."""

from __future__ import annotations

import pytest

from terrarium_annotator.inject import CardView, matches, select_cards


def count(text: str) -> int:
    """Fake token counter: 1 token per 4 chars (rough heuristic)."""
    return max(1, len(text) // 4)


def card(term, gloss="gloss", keys=(), updated="2026-01-01"):
    return CardView(term=term, keys=keys, gloss=gloss, updated_at=updated)


class TestMatches:
    def test_whole_word_only(self):
        assert matches("the Vys pool", "Vys")
        assert not matches("Vysalia rose", "Vys")  # prefix, not whole word
        assert not matches("myVys", "Vys")

    def test_case_sensitive(self):
        assert not matches("channeled vys", "Vys")
        assert matches("channeled vys", "vys")

    def test_multiword_and_unicode(self):
        assert matches("met Rikāmā Rahivāsī today", "Rikāmā Rahivāsī")
        assert not matches("met Rikāmā today", "Rikāmā Rahivāsī")


class TestSelection:
    def test_term_and_alias_trigger(self):
        cards = [card("Vys", keys=("vys",)), card("Suresh")]
        scene = "Soma channeled vys into the cloak."
        selected = select_cards(scene, cards, 1000, count)
        assert [s.term for s in selected] == ["Vys"]

    def test_no_match_no_injection(self):
        selected = select_cards("nothing here", [card("Vys")], 1000, count)
        assert selected == []

    def test_recursion_one_round_only(self):
        # Scene mentions A; A's gloss mentions B; B's gloss mentions C.
        a = card("Alpha", gloss="related to Beta")
        b = card("Beta", gloss="related to Gamma")
        c = card("Gamma", gloss="alone")
        selected = select_cards("Alpha appears", [a, b, c], 1000, count)
        by_term = {s.term: s.depth for s in selected}
        assert by_term == {"Alpha": 0, "Beta": 1}  # Gamma never fires

    def test_budget_drops_lowest_priority(self):
        old_long = card("Oldie", gloss="x" * 200, updated="2026-01-01")
        new_short = card("Newbie", gloss="hi", updated="2026-02-01")
        scene = "Oldie and Newbie both appear"
        budget = count("Newbie: hi") + 1
        selected = select_cards(scene, [old_long, new_short], budget, count)
        assert [s.term for s in selected] == ["Newbie"]

    def test_recency_beats_length_priority(self):
        old_short = card("OldShort", gloss="hi", updated="2026-01-01")
        new_long = card("NewLong", gloss="x" * 40, updated="2026-02-01")
        scene = "OldShort NewLong"
        budget = count("NewLong: " + "x" * 40) + 1
        selected = select_cards(scene, [old_short, new_long], budget, count)
        assert [s.term for s in selected] == ["NewLong"]

    def test_round0_beats_round1_under_pressure(self):
        direct = card("Direct", gloss="tiny", updated="2026-01-01")
        indirect = card("Indirect", gloss="tiny", updated="2026-06-01")
        linker = card("Linker", gloss="mentions Indirect", updated="2026-01-01")
        scene = "Direct and Linker"
        budget = count("Linker: mentions Indirect") + count("Direct: tiny") + 1
        selected = select_cards(scene, [direct, indirect, linker], budget, count)
        terms = {s.term for s in selected}
        assert "Indirect" not in terms  # round-1 yields to round-0 despite recency
        assert terms == {"Direct", "Linker"}

    def test_budget_must_be_positive(self):
        with pytest.raises(ValueError):
            select_cards("x", [], 0, count)

    def test_token_costs_accounted(self):
        c1 = card("A", gloss="g" * 40)
        c2 = card("B", gloss="g" * 40)
        scene = "A B"
        budget = count("A: " + "g" * 40) + count("B: " + "g" * 40) - 1
        selected = select_cards(scene, [c1, c2], budget, count)
        assert sum(s.tokens for s in selected) <= budget
        assert len(selected) == 1

    def test_recursion_reads_glosses_not_terms(self):
        # A triggers via alias "X-Ray" (scene has no "Alph"). B's key is
        # A's *term* "Alph", which appears in A's displayed card text but
        # not in A's gloss — gloss-only recursion must not fire B.
        a = card("Alph", keys=("X-Ray",), gloss="unrelated content")
        b = card("Beta", keys=("Alph",))
        selected = select_cards("saw an X-Ray today", [a, b], 1000, count)
        assert [s.term for s in selected] == ["Alph"]
