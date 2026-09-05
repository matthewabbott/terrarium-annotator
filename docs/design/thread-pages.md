# Thread-synopsis wiki pages — skill design + evaluation plan

*2026-09-05. Sources: raw DokuWiki markup via `https://mail.steelbea.me/newsletters/(NNN) Thread NN wiki page.txt` (read: threads 38, 40); rendered pages thread/3, /20, /40.*

## Published page format (from raw sources)

```text
====== Thread #40 ======

{{ :characters:baldev-armored.jpg?200 |}}

----

//We escape to Namek.//

===== Summary =====

Multi-paragraph prose. Entity references are [[namespace:slug|Display text]]
links — namespace taxonomy as in wiki-format.md. Content is what a reader
needs for *this thread*: who/what/where, outcomes, mechanics shown.

===== Play by Play =====

  * Beat bullets, terse, often jokes
  * Thread end

[[https://steelbea.me/banished/archive/32533307/|Archive Link]]
[[https://steelbea.me/voyage/32533307|Voyage Link]]

++++ Posts |
(raw post dump with tag markers)
```

## Skill design

Two separate skills, per Matt: **thread-synopsis pages** (this doc) and **entity pages** (later; they follow wiki-format.md's character-page anatomy). Thread pages are structurally simpler and have 38 published references.

Inputs (all stored during a reading pass): thread's story-log gists, the merge-tree thread summary, entries touched in the thread (`entry_source.thread_id`), corpus thread id.

- **Deterministic renderer** (`wiki/` module, planned): header, play-by-play from gists, archive + voyage links (`thread.id` = OP post id, verified for all 276 threads), posts fold from the corpus.
- **LLM step**: teaser (one line) + summary prose with `[[links]]`. The model gets the thread's digest + the touched entries' canonical terms so links use our namespace mapping.
- **When**: thread-close step in the runner (optional flag) or batch skill over a finished pass. Not in the annotator's critical path.

## Evaluation plan (the point of the skill)

Published thread pages 3–40 are reference outputs. Compare generated vs published:

- **Entity-link coverage**: which published `[[links]]` our page also links (recall vs the gold set, now page-level not just entry-level) and which extra links we generate (candidate over-linking).
- **Summary faithfulness**: LLM-judge the generated summary against the thread's posts (RAGAS-style), not against the published summary.
- **Spoiler caveat** (Matt): published pages contain future/meta knowledge (trivia sections, later reveals). Our pages are first-read artifacts — the comparator must not count "missing future knowledge" as a defect. Evaluate only on what was knowable at thread end. (A second-read pass with full-corpus knowledge is a later, legitimate variant.)

## Status

Design only. Not built. Depends on a reading pass producing per-thread state (the t1-40 run is exactly that).
