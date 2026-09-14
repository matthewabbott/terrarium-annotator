"""Ladder scorecard: per-variant quality + cost in one number.

Design: docs/design/prompt-laddering.md. A variant is (prompt_file, model,
pass_id); its annotator DB plus its telemetry JSONL yield the scorecard.
Headline metric: est prompt tokens per covered gold entity (N/A when
coverage is zero — a failed variant must not win by denominator artifact).

Gold coverage contract (see design doc §Gold-coverage scoping): the
denominator is ONLY the (namespace, slug) pairs from gold pages
corresponding to the threads the variant processed. Wiki page N maps to
the Nth thread in chronological order (the mapping the 2026-09-04/05
calibration analyses used). Our DB stores no wiki namespace, so matching
is namespace-unresolved exact surface/alias: slug normalized
('-' → ' ', casefold) equals entry term_normalized or alias_normalized.
Shadow-flag rate is a distribution signal, NOT a junk metric — texture
truth comes from adjudication.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from terrarium_annotator.corpus import CorpusReader
from terrarium_annotator.llm.telemetry import iter_records


def _norm_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().casefold()


def gold_pairs_for_pages(
    gold_set: dict, pages: set[int]
) -> tuple[set[tuple[str, str]], int]:
    """(in-scope (namespace, slug) pairs, out-of-scope pair count).

    meta: links are excluded from scope entirely (QM/platform, correctly
    out of glossary scope per the 2026-09-05 denominators analysis)."""
    in_scope: set[tuple[str, str]] = set()
    out_of_scope = 0
    for page in gold_set["pages"]:
        if "links" not in page:
            continue
        for link in page["links"]:
            pair = (link["namespace"], link["slug"])
            if pair[0] == "meta":
                continue
            if page["thread"] in pages:
                in_scope.add(pair)
            else:
                out_of_scope += 1
    return in_scope, out_of_scope


def surface_coverage(
    conn: sqlite3.Connection, in_scope: set[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(covered, missed) pairs by exact normalized surface/alias match."""
    surfaces = {r[0] for r in conn.execute("SELECT term_normalized FROM entry")} | {
        r[0] for r in conn.execute("SELECT alias_normalized FROM entry_alias")
    }
    covered = sorted(p for p in in_scope if _norm_slug(p[1]) in surfaces)
    missed = sorted(set(in_scope) - set(covered))
    return covered, missed


def token_subset_pair_candidates(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """HEURISTIC PROXY, not a duplicate count: entry pairs whose normalized
    terms share a token subset (Aleamond-class fragments). Counts legitimate
    related terms too (e.g. "vys" / "vys pool"). Diagnostic for human
    review — never part of winner selection, never auto-merge."""
    terms = [
        (r[0], set(r[0].split()))
        for r in conn.execute("SELECT term_normalized FROM entry")
    ]
    pairs = []
    for i, (a, ta) in enumerate(terms):
        for b, tb in terms[i + 1 :]:
            if ta and tb and (ta <= tb or tb <= ta):
                pairs.append((a, b))
    return sorted(pairs)


def scorecard(
    conn: sqlite3.Connection,
    corpus: CorpusReader,
    gold_set: dict,
    thread_ids: list[int],
    usage_path: str | Path | None = None,
) -> dict:
    """One variant's scorecard. thread_ids are corpus thread IDs; gold
    pages are 1-based chronological ordinals of those threads."""
    order = [t.id for t in corpus.thread_order()]
    pages = {order.index(tid) + 1 for tid in thread_ids if tid in order}
    in_scope, out_of_scope = gold_pairs_for_pages(gold_set, pages)
    covered, missed = surface_coverage(conn, in_scope)

    entries = conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
    deferred = conn.execute("SELECT COUNT(*) FROM deferred_candidate").fetchone()[0]
    posts = sum(
        len(list(corpus.story_posts(tid))) for tid in thread_ids if tid in order
    )

    cost: dict = {}
    if usage_path is not None and Path(usage_path).exists():
        chars_in = chars_out = tool_calls = 0
        attempts = {"success": 0, "error": 0}
        est_in = est_out = 0
        for rec in iter_records(usage_path):
            chars_in += rec.get("prompt_chars") or 0
            chars_out += rec.get("completion_chars") or 0
            tool_calls += rec.get("tool_calls") or 0
            est_in += rec.get("est_prompt_tokens") or 0
            est_out += rec.get("est_completion_tokens") or 0
            for a in rec.get("attempts") or []:
                s = a.get("status")
                if s in attempts:
                    attempts[s] += 1
        cost = {
            "chars_in": chars_in,
            "chars_out": chars_out,
            "tool_calls": tool_calls,
            "est_tokens_in": est_in,
            "est_tokens_out": est_out,
            "attempts": attempts,
        }

    return {
        "entries": entries,
        "story_posts": posts,
        "entries_per_1k_posts": round(entries / posts * 1000, 1) if posts else None,
        "flagged": deferred,
        "flag_rate": round(deferred / entries, 3) if entries else None,
        "gold_in_scope": len(in_scope),
        "gold_out_of_scope": out_of_scope,
        "gold_covered": len(covered),
        "gold_coverage": round(len(covered) / len(in_scope), 3) if in_scope else None,
        "gold_missed": [f"{ns}:{slug}" for ns, slug in missed],
        "token_subset_pair_candidates": token_subset_pair_candidates(conn),
        "cost": cost,
        # Headline: N/A when coverage is zero — never divide by zero.
        "est_tokens_per_covered_entity": (
            round(cost["est_tokens_in"] / len(covered)) if cost and covered else None
        ),
    }


def format_scorecard(name: str, sc: dict) -> str:
    lines = [
        (
            f"{name}: entries={sc['entries']} "
            f"({sc['entries_per_1k_posts']}/1k posts), "
            f"flagged={sc['flagged']} ({sc['flag_rate']}), "
            f"gold {sc['gold_covered']}/{sc['gold_in_scope']} "
            f"({sc['gold_coverage']})"
        ),
    ]
    if sc["cost"]:
        c = sc["cost"]
        lines.append(
            f"  cost: chars_in={c['chars_in']} chars_out={c['chars_out']} "
            f"tool_calls={c['tool_calls']} est_tokens_in={c['est_tokens_in']} "
            f"attempts={c['attempts']} "
            f"tokens/covered={sc['est_tokens_per_covered_entity']}"
        )
    if sc["token_subset_pair_candidates"]:
        lines.append(
            f"  token-subset pair candidates (heuristic): "
            f"{sc['token_subset_pair_candidates']}"
        )
    if sc["gold_missed"]:
        lines.append(f"  missed gold: {', '.join(sc['gold_missed'])}")
    return "\n".join(lines)
