# Wiki Format Notes — steelbea.me/banished/wiki

*Harvested 2026-09-02 (site back online). Target format for tier-2 pages and future wiki export.*

The wiki is **DokuWiki**. Pages live in type-namespaces: `characters`, `locations`, `mechanics`, `objects`, `books`, `races`, `magic` (with sub-namespaces like `magic:levels:master`, `magic:effects:paralysis`), `culture`, `thread`, `irc`. Our entry tags should map 1:1 to these namespaces.

## Character page (`characters:sadik`)

- `# Old Man Sadik` — display-name title (not the slug)
- Lead: image + a characteristic in-universe **quote** with attribution
- Opening paragraph: **bolded canonical name** + 2–4 sentence standalone summary (role, why notable, relationship to protagonist)
- Sections as applicable: `## Appearance`, `## Background` (mysteries stated as *known/unknown*, with who said what), `## Abilities` (explicitly separates demonstrated fact from in-universe speculation), `## Plot` (chronological narrative of appearances)
- Itemized list sections where natural (`## Items Purchased from Sadik` — with prices)
- Prose is dense with `[links]` to other pages; aliases/relations inline

## Thread page (`thread:20`)

- `# Thread #20` + one-line italic teaser ("We meet the monster inside our cloak and start a wargame company.")
- `## Summary` — multi-paragraph narrative, densely linked to entity pages
- `## Play by Play` — bullet list of beats (maps directly to our scene gists)
- Footer: **Archive Link** `steelbea.me/banished/archive/<thread_id>/` and **Voyage Link**. Verified against `banished.db`: `thread.id` equals the OP post's `post.id` for **all 276 threads, no exceptions**, and the archive URL keys on it. Exporter rule: **derive** the archive id from the thread's `op_post`-tagged post and *assert* `post.id == thread_id` at export time — don't assume the convention; a future corpus rebuild could break it, and a failed assertion beats a wrong backlink.
- Raw posts embedded in a folded block, tags visible (`qm_post`, `story_post`, `vote_choices`, `tally_post`)

**Uniformity caveat**: observed on two thread pages. Structure is typical, not guaranteed — thread:20's fold is titled "Posts" with an explicit Archive/Voyage footer; thread:5's is "Story Posts". Spot-check more pages before writing the exporter.

## Consequences for v2

1. **Tier-2 page template**: title, optional quote, standalone summary (= tier-1 gloss, expanded), typed sections, Plot/appearances narrative generated from `entry_source` rows, link-dense prose.
2. **Thread↔entry backlinks**: thread pages list entries touched; entry pages list threads. Both directions are derivable from `entry_source` + revisions (`log_seq`). Archive URLs: `https://steelbea.me/banished/archive/<thread_id>/` (verified: `thread.id` = OP `post.id` across all 276 threads).
3. **Fact vs. speculation separation** (Sadik's Abilities section) is a house convention worth encoding: definitions should mark confidence — aligns with our `tentative`/`confirmed` status.
4. Namespace = tag; disambiguation suffix (`Dawn (character)`) maps to choosing the right namespace.

## Gold set (added 2026-09-03)

Thread pages 3–40 exist and follow the format above (verified: thread/3, thread/20, thread/40). Their summaries' `[links]` are human-curated "terms worth an entry" per thread — a partial **gold-standard entry list** for measuring recall/precision of our generated glossary (dev-verification L5). Harvest by scraping the thread pages and extracting link targets. Note the links use namespace slugs (`characters:mik`, `objects:oud`), which also seeds our tag→namespace mapping.
