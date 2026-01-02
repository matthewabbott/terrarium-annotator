# terrarium-annotator Specification

LLM-driven corpus annotation system for constructing glossaries of domain-specific terminology.

## 1. Purpose

Iteratively read a corpus via local LLM (Qwen3-Next-80B on vLLM via terrarium-agent), identify non-standard terms, and build a structured glossary with provenance tracking. Primary corpus: Banished Quest (fantasy forum story). Design permits future corpus adaptation.

## 2. Glossary Entry Schema

### 2.1 Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | INTEGER | Auto-increment PK |
| `term` | TEXT | Display name with disambiguation suffix if needed: `Dawn (character)` |
| `term_normalized` | TEXT | Lowercase for lookups |
| `definition` | TEXT | Markdown. Cross-refs via `[[Term]]` syntax |
| `status` | TEXT | `confirmed` \| `tentative` |
| `tags` | TEXT[] | Free-form: `character`, `location`, `mechanic`, `faction`, etc. |
| `first_seen_post_id` | INTEGER | Source post where term first identified |
| `first_seen_thread_id` | INTEGER | Thread context |
| `last_updated_post_id` | INTEGER | Most recent modification source |
| `last_updated_thread_id` | INTEGER | Thread context |
| `created_at` | TEXT | ISO8601 |
| `updated_at` | TEXT | ISO8601 |

### 2.2 Disambiguation

Homographs get separate entries: `Dawn (character)` vs `dawn (time)`. Agent classifies on encounter. Disambiguation suffix in parentheses is convention, not enforced.

### 2.3 Cross-References

Use `[[Term Name]]` in definition text. Parsed post-hoc for wiki generation. Not validated at write time.

### 2.4 Versioning

Entries overwritten in place. Revision history stored in separate `revision` table for human audit. Model does NOT see revision history.

### 2.5 Tentative Entries

New entries default to `tentative`. Promoted to `confirmed` by:
- Curator fork semantic judgment
- Agent explicit confirmation
- Human override

## 3. Context Window Management

### 3.1 Sliding Window Structure

```
[System Prompt]
[Cumulative Summary: threads 1..n-3]
[Thread n-2 Summary]
[Thread n-1 Full Context]
[Current Scene Posts]
[Relevant Glossary Entries]
[Tool Definitions]
```

### 3.2 Compaction

Trigger: 80% of `max_context_tokens` (configurable, default ~200K for Qwen3).
Target: Compact to 70%.

Priority order:
1. Summarize oldest completed thread in context
2. Merge old summaries into cumulative summary
3. Trim thinking tokens (preserve recent 4 turns)
4. Truncate old assistant responses

### 3.3 Thread Summaries

Hybrid format: plot highlights + annotation progress (entries created/updated).

### 3.4 Token Counting

Primary: vLLM `/v1/tokenize` endpoint.
Fallback: Character heuristic (chars / 4).
Strategy: Use heuristic until threshold (e.g., 60% capacity), then verify with vLLM.

### 3.5 Thinking Tokens

Preserve all `/think` blocks in context history. Do not strip.

## 4. Content Batching

Scene-aware: batch contiguous `qm_post`-tagged posts within a thread. A "scene" ends when next post lacks `qm_post` tag or thread boundary reached.

## 5. Agent Tools

### 5.1 Glossary Tools

| Tool | Purpose |
|------|---------|
| `glossary_search` | Query by term/tags/status. Returns entries without [[refs]] expanded unless `include_references=true` |
| `glossary_create` | Add new entry. Auto-sets `first_seen_*` from current post |
| `glossary_update` | Modify existing entry. Auto-sets `last_updated_*` |
| `glossary_delete` | Remove entry. Requires `reason`. Logged to revision history |

### 5.2 Corpus Tools

| Tool | Purpose |
|------|---------|
| `read_post` | Fetch historical post by ID. Optional: include adjacent posts |
| `read_thread_range` | Fetch post range within thread. Optional tag filter |

### 5.3 Context Tools

| Tool | Purpose |
|------|---------|
| `list_snapshots` | Query available snapshots by thread/type |
| `summon_snapshot` | Rehydrate old context for dialogue. Returns initial response |
| `summon_continue` | Continue multi-turn dialogue with summoned context |
| `summon_dismiss` | End summoned dialogue, log summary, discard |

### 5.4 Explicate Protocol

Agent quotes text from an entry definition. System:
1. Fuzzy-match quote to entry section
2. Retrieve source post via blame tracking
3. Return post content OR offer to summon authoring snapshot

## 6. Snapshot System

### 6.1 Snapshot Types

| Type | Trigger |
|------|---------|
| `checkpoint` | Thread boundary, periodic interval |
| `curator_fork` | Pre-curator context (discarded after) |
| `manual` | Human-triggered |

### 6.2 Storage

Serialize full `AnnotationContext`: system prompt, cumulative summary, thread summaries, conversation history, current position.

Also snapshot glossary entry states at that point (for blame).

### 6.3 Rehydration

Load snapshot into fresh context. Support multi-turn dialogue. Summoned context is read-only (cannot modify main glossary). Current agent continues after dismissal.

## 7. Curator Workflow

Trigger: Thread N completion.

1. Fork current context
2. Switch system prompt to curator role
3. Query tentative entries from thread N-1
4. For each entry: present context, ask for judgment
5. Decisions: `CONFIRM`, `REJECT`, `MERGE`, `REVISE`
6. Apply decisions to main glossary
7. Discard fork context

Curator uses semantic judgment, not frequency threshold.

## 8. Error Handling

### 8.1 vLLM Failures

Retry with exponential backoff (3 attempts). On persistent failure: checkpoint full state, halt cleanly. Human restarts after fixing.

### 8.2 Parse Failures

Malformed agent output: log warning, skip update, continue. Do not halt.

### 8.3 Tool Failures

Return error to agent in tool result. Agent can retry or skip.

## 9. Storage

### 9.1 Databases

| DB | Purpose |
|----|---------|
| `banished.db` | Corpus (read-only) |
| `annotator.db` | Glossary, snapshots, state, revisions |

### 9.2 Export

JSON/YAML export of glossary for human review. Separate command, not part of main loop.

## 10. Autonomy

Designed for unattended multi-day runs. Optional soft checkpoints at thread boundaries (configurable). No human-in-loop by default.

## 11. Future Scope (Out of Band)

- Wiki generation: separate tool reading `annotator.db`
- Embedding vocabulary: separate project after complete glossary
- Multi-corpus: abstract interfaces when second corpus added
