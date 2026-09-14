# Prompt laddering and model onboarding (design note, 2026-09-12)

*From Matt's ideas: A/B prompt variants on a fixed slice, criteria-driven onboarding of new models (Deepseek v4.1 Flash), model provenance, usage circuit-breaking. Discussion for the next goal after the researcher adjudication pass.*

## The burn-rate fact that shapes this

The full-quest run covered **25/278 threads (~9%) on one full Kimi weekly quota**. Full corpus at current pace ≈ 10–11 weekly windows. Options that change the math: cheaper model for bulk reading (Deepseek Flash), fewer tool rounds per batch, tighter style prompt, or accept multi-week wall time.


## Train/test split (anti-Goodhart, Matt 2026-09-14)

Tune variants on threads 1–5; **validate the winner on a HOLDOUT slice —
the Anthus academy arc** (texture-heavy, book-dense: adjudication found the
relaxed prompt's texture admissions concentrate there). A variant that wins
on 1–5 but collapses on Anthus memorized the slice; the ladder reports the
collapse instead of shipping the prompt. If no variant is a clear winner on
the train slice, run the incumbent (reader-v2) on the holdout and report
the ambiguity — never pick by fiat. Prompts carry context and the admission
criterion, not rulebooks: the annotator's judgment is the instrument; the
prompt just tells it what we want and what Banished Quest looks like.

## Multi-pass production hypothesis (frame, not build — 2026-09-14)

Hypothesis: a conservative generalist pass + narrow specialist passes
(books/documents, mechanics) + researcher consolidation beats one liberal
pass on both cost and cleanliness. Cost model to MEASURE (all figures are
per-batch measured unless noted): liberal ≈ 3× prompt chars / 3× tool
calls per batch vs conservative; two conservative readthroughs ≈ 2×
baseline plus RAG growth on pass 2 (bigger card blocks). The 10–25× total
quota multiplier quoted on 2026-09-12 is an UNATTRIBUTED proxy (includes
researcher pass, chat, retries, interactive use) — never cite it as
measured; telemetry now gives per-variant truth. Evidence that would
confirm: a conservative-prompt ladder variant matching reader-v2's gold
coverage at its own (lower) cost, with texture rate near zero. Evidence
that would disconfirm: conservative coverage staying below liberal even
after alias-aware scoring — then the recall wins need the liberal prompt
or specialists.

## NG+ epistemics (Matt 2026-09-14 — design requirement for any reread)

Early-quest narration is in-character DEFICIENT — the protagonist learns
metaphysics/history progressively, and early accounts are wrong in the way
"you can't subtract a larger number from a smaller one" is wrong. Any
second-read (NG+) agent must NOT treat early-quest info as gospel: every
claim carries in-story provenance (who claimed it, when, epistemic mode),
and later corrections are recorded as conflicting accounts with per-source
attribution — never flattened into one story. Epistemic modes
(narrated/claimed/inferred) already exist on evidence; NG+ guidance builds
on them. This also constrains ladder judging: a gloss faithful to an early,
wrong account is faithful, not an error — if it marks the account's mode.

## Gold-coverage scoping (scorer contract)

The coverage denominator is ONLY the gold pairs from pages corresponding
to the threads a variant actually processed (train slice: pages 3–5;
threads 1–2 have no published pages). Out-of-scope pairs are counted and
reported separately — a 1–5 variant must never be penalized for entities
it could not have encountered, and the spoiler/meta caveat from
docs/design/thread-pages.md applies (first-read artifacts can't know
future reveals).

## Prompt ladder (variant A/B on a fixed slice)

Fixed slice: **threads 1–5** (chronological 30265887…30459080) — we already hold three baselines there (L3 smoke threads 1–2, shadow threads 3–5, t1–40 full coverage, plus the gold set for threads 3–5). Variants are just `(prompt_variant, model, pass_id)` tuples; one DB per variant.

Pipeline sketch (`experiments/` to build):

```
variant = {name, prompt_file, model, pass_id}
for v in variants:
    run --threads <slice> --annotator-db data/exp/<name>.db --pass-id <name>
    verify (exit 0 required or variant invalid)
scorecard per variant:
    exact-surface gold coverage (computable, we have the script)
    shadow-flag rate (distribution signal, not junk rate)
    entries/1k posts
    duplicate-pair count (Aleamond-class)
    gloss-style notes (LLM-judge on a sample: faithfulness vs quotes)
→ comparison table in docs/worklog
```

Semi-hard criteria already computable today; soft criteria (gloss faithfulness) via LLM-judge on a stratified sample — a stand-in until the critic exists.

**Prompts as data**: move `SYSTEM_PROMPT` out of `runner.py` into `prompts/<variant>.md` loaded by the runner (`--prompt prompts/reader-v2.md`). Laddering then becomes config, not code edits. Prompt vintage is part of pass identity (below).

## Model onboarding (criteria-driven)

Onboarding a model = run the ladder with the same slice and scorecard, first with the incumbent prompt as-is, then tuned variants. Deepseek v4.1 Flash: OpenAI-compatible API → `OpenAICompatibleClient` (no RPC needed; just base URL + key, env-sourced).

Model properties that differ and matter here: tool-call discipline (our `<tool_call>` text convention vs native tools), reasoning-empty responses (kimi's EmptyResponseError class of failure), verbosity/style. The scorecard catches all three.

## Model provenance

Already 80% present: `pass_id` blames every revision/source; `run_meta` stores config. Add to that: **model name in `run_meta`** (already implicit in config? make it explicit) and a convention that pass_id embeds model + prompt vintage (`full-v1/kimi-k2.5/reader-v2`). Rehydration (design §4) should default to the **generating model** for a given snapshot — different models rehydrate differently, and a snapshot generated by Deepseek should be interrogated by Deepseek.

## Usage circuit-breaking

- **Kimi path**: pre-batch quota check (`omp usage --json --provider kimi-code`); circuit-break at a configurable threshold (Matt suggested 80% weekly). Cleanest home: the Runner with an injected `quota_check()` callable (testable with fakes), halting cleanly at the threshold (checkpoint preserved) rather than dying on a rate-limit error. Supervisor backoff already handles windows.
- **API path (Deepseek etc.)**: accumulate cost per batch in `run_meta` (usage from response metadata if provided, else token estimate); circuit-break at `$x`. Also useful as an *experiment budget* for the ladder itself.
- Long-run budget planner: given the ~9%/week rate, decide bulk-model choice before restarting the full run, not mid-way.

## Separation of prompts

Per Matt: separate prompts by agent (annotator/researcher/critic — already separate) and by metrics (variant names with scorecard history, so a prompt's measured record travels with it). `prompts/<agent>/<variant>.md` + a scorecard log per variant.
