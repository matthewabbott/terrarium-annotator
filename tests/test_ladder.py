"""L0 tests for the ladder scorecard: gold scoping (only processed
threads' pages), exact surface/alias coverage, N/A cost-per-entity on
zero coverage, pair-candidate diagnostics, cost aggregation from a
telemetry JSONL. Fabricated DBs only.
"""

from __future__ import annotations

import json

import pytest
from test_runner import build_corpus  # tests-dir sibling

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.glossary import Evidence, GlossaryStore, Provenance
from terrarium_annotator.ladder import (
    format_scorecard,
    gold_pairs_for_pages,
    scorecard,
    surface_coverage,
    token_subset_pair_candidates,
)
from terrarium_annotator.state import connect_annotator_db

GOLD = {
    "pages": [
        {
            "thread": 1,
            "links": [
                {"namespace": "characters", "slug": "mik", "label": "Mikhael"},
                {"namespace": "objects", "slug": "vys", "label": "Vys"},
                {"namespace": "meta", "slug": "soma", "label": "soma"},
            ],
        },
        {
            "thread": 3,
            "links": [
                {"namespace": "characters", "slug": "suresh", "label": "Suresh"},
                {"namespace": "locations", "slug": "old-fort", "label": "old fort"},
            ],
        },
    ]
}

PROV = Provenance(thread_id=101, pass_id="t")


@pytest.fixture
def variant_db(tmp_path):
    corpus_path = tmp_path / "corpus.db"
    build_corpus(corpus_path)
    corpus = CorpusReader(corpus_path)
    conn = connect_annotator_db(tmp_path / "variant.db")
    store = GlossaryStore(conn, corpus.post_body)
    store.propose_entry(
        term="Vys",
        gloss="Raw magical energy.",
        evidence=[Evidence(1001, "channeled Vys into the cloak")],
        provenance=PROV,
    )
    return corpus_path, conn, store


class TestGoldScoping:
    def test_only_processed_pages_count(self):
        in_scope, out = gold_pairs_for_pages(GOLD, {1})
        assert in_scope == {("characters", "mik"), ("objects", "vys")}
        assert out == 2  # page 3's two links out of scope
        # meta: links never enter scope at all
        in_scope_all, _ = gold_pairs_for_pages(GOLD, {1, 3})
        assert ("meta", "soma") not in in_scope_all
        assert ("characters", "suresh") in in_scope_all

    def test_zero_pages_means_zero_denominator(self):
        in_scope, out = gold_pairs_for_pages(GOLD, set())
        assert in_scope == set() and out == 4  # 5 links minus 1 meta


class TestCoverage:
    def test_exact_surface_and_alias(self, variant_db):
        _, conn, _ = variant_db
        covered, missed = surface_coverage(
            conn, {("objects", "vys"), ("characters", "suresh")}
        )
        assert covered == [("objects", "vys")]
        assert missed == [("characters", "suresh")]

    def test_alias_matches(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(tmp_path / "v.db")
        store = GlossaryStore(conn, corpus.post_body)  # noqa: F841 - conn setup
        # Coverage-unit test: seed entry + alias directly (the write gate
        # rightly rejects quote-free aliases; that path is tested in
        # test_glossary.py).
        entry_id = conn.execute(
            "INSERT INTO entry(term, term_normalized, gloss, pass_id,"
            " created_at, updated_at) VALUES ('Mikhael', 'mikhael',"
            " 'The protagonist.', 't', 'now', 'now')"
        ).lastrowid
        conn.execute("INSERT INTO entry_alias VALUES (?, 'mik', 'mik')", (entry_id,))
        conn.commit()
        covered, _ = surface_coverage(conn, {("characters", "mik")})
        assert covered == [("characters", "mik")]


class TestScorecard:
    def test_full_scorecard(self, variant_db, tmp_path):
        corpus_path, conn, _ = variant_db
        corpus = CorpusReader(corpus_path)
        usage = tmp_path / "u.jsonl"
        usage.write_text(
            json.dumps(
                {
                    "run_id": "r",
                    "call_type": "annotation",
                    "prompt_chars": 400,
                    "completion_chars": 40,
                    "tool_calls": 2,
                    "est_prompt_tokens": 100,
                    "est_completion_tokens": 10,
                    "attempts": [{"status": "success"}],
                }
            )
            + "\n"
        )
        # threads 101, 102 = pages 1, 2 (fabricated corpus order)
        sc = scorecard(conn, corpus, GOLD, [101, 102], usage)
        assert sc["entries"] == 1
        assert sc["story_posts"] == 5  # 3 + 2 story posts
        assert sc["gold_in_scope"] == 2  # page 1 only (page 2 has no entry)
        assert sc["gold_covered"] == 1  # vys via term surface
        assert sc["gold_coverage"] == 0.5
        assert sc["est_tokens_per_covered_entity"] == 100  # 100 / 1
        assert sc["cost"]["attempts"] == {"success": 1, "error": 0}
        assert sc["cost"]["tool_calls"] == 2

    def test_zero_coverage_is_na_not_zero_cost(self, variant_db, tmp_path):
        corpus_path, conn, _ = variant_db
        corpus = CorpusReader(corpus_path)
        usage = tmp_path / "u.jsonl"
        usage.write_text(
            json.dumps({"prompt_chars": 400, "est_prompt_tokens": 100}) + "\n"
        )
        gold = {
            "pages": [
                {
                    "thread": 1,
                    "links": [
                        {"namespace": "x", "slug": "absent-entity", "label": "x"}
                    ],
                }
            ]
        }
        sc = scorecard(conn, corpus, gold, [101], usage)
        assert sc["gold_covered"] == 0
        assert sc["est_tokens_per_covered_entity"] is None  # N/A, not 0

    def test_provider_tokens_preferred_and_labeled(self, variant_db, tmp_path):
        corpus_path, conn, _ = variant_db
        corpus = CorpusReader(corpus_path)
        usage = tmp_path / "u.jsonl"
        # Provider usage present: prompt 500 real vs est 100 in chars terms.
        usage.write_text(
            json.dumps(
                {
                    "run_id": "r",
                    "prompt_chars": 400,
                    "est_prompt_tokens": 100,
                    "duration_s": 2.0,
                    "usage": {
                        "prompt_tokens": 500,
                        "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 100},
                    },
                    "attempts": [{"status": "success"}],
                }
            )
            + "\n"
        )
        sc = scorecard(conn, corpus, GOLD, [101, 102], usage)
        assert sc["cost_source"] == "provider"
        assert sc["tokens_per_covered_entity"] == 500  # 500 real / 1 covered
        assert sc["est_tokens_per_covered_entity"] == 100  # est still shown
        assert sc["cost"]["provider_tokens_in"] == 500
        assert sc["cost"]["completion_tok_per_s_e2e"] == 10.0  # 20 / 2.0s
        text = format_scorecard("v", sc)
        assert "cost (provider)" in text

    def test_est_only_records_label_est(self, variant_db, tmp_path):
        corpus_path, conn, _ = variant_db
        corpus = CorpusReader(corpus_path)
        usage = tmp_path / "u.jsonl"
        usage.write_text(
            json.dumps(
                {
                    "run_id": "r",
                    "prompt_chars": 400,
                    "est_prompt_tokens": 100,
                    "attempts": [{"status": "success"}],
                }
            )
            + "\n"
        )
        sc = scorecard(conn, corpus, GOLD, [101, 102], usage)
        assert sc["cost_source"] == "est"
        assert sc["tokens_per_covered_entity"] == 100
        assert "provider_tokens_in" not in sc["cost"]

    def test_flag_rate_is_deferred_over_entries(self, variant_db):
        corpus_path, conn, _ = variant_db
        conn.execute(
            "INSERT INTO deferred_candidate(term, term_normalized, quote,"
            " post_id, thread_id, created_at) VALUES ('a', 'a', 'q', 1001,"
            " 101, 'now')"
        )
        conn.commit()
        corpus = CorpusReader(corpus_path)
        sc = scorecard(conn, corpus, GOLD, [101])
        assert sc["flagged"] == 1 and sc["flag_rate"] == 1.0

    def test_token_subset_pair_candidates_detected(self, tmp_path):
        corpus_path = tmp_path / "corpus.db"
        build_corpus(corpus_path)
        corpus = CorpusReader(corpus_path)
        conn = connect_annotator_db(tmp_path / "v.db")
        store = GlossaryStore(conn, corpus.post_body)
        store.propose_entry(
            term="Vys",
            gloss="Raw magical energy.",
            evidence=[Evidence(1001, "channeled Vys into the cloak")],
            provenance=PROV,
        )
        conn.execute(
            "INSERT INTO entry(term, term_normalized, gloss, pass_id,"
            " created_at, updated_at) VALUES ('Vys pool', 'vys pool',"
            " 'The reserve.', 't', 'now', 'now')"
        )
        conn.commit()
        assert token_subset_pair_candidates(conn) == [("vys", "vys pool")]

    def test_format_renders(self, variant_db):
        corpus_path, conn, _ = variant_db
        corpus = CorpusReader(corpus_path)
        sc = scorecard(conn, corpus, GOLD, [101])
        text = format_scorecard("v-test", sc)
        assert "v-test" in text and "gold" in text
