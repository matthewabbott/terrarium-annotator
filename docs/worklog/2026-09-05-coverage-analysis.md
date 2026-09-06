# 2026-09-05 — Coverage analysis: missed backlinks and extra entries (t1–40 DB)

For Matt's review before the researcher/critic tier. Data: `data/annotator-t1-40.db` (177 entries) × `data/exports/gold-set.json` (216 in-scope pairs). Matching: exact normalized surface/alias, then token-subset fuzzy (diagnostic only).

## A. What we're missing (70 pairs missed entirely)

Full list in `2026-09-05-t1-40-completion.md`. The classes, with examples:

### Characters — the biggest hole (16 pairs)
- **Naming-mismatch trap**: gold slug `suresh` (labels: "Archmagos Megalos", "Suresh") — our entry is `Archmagos Megalos`, token sets don't intersect. The entity EXISTS; the slug↔name link doesn't. Same for `mik` — we have `Mikhael Abdul-Hakim` (full name) but nothing tokenizes to "mik". **The misses are partly alias-table holes, not content holes.**
- **Genuinely absent**: `demetrios` (6 thread pages), `karim`, `mazhar` (thread 3's antagonist!), `onur`, `lord-hodei`, `maximilien`, `old-sam`. Side characters read as scene furniture.
- **Late-thread entities** (pages 39–40): `papa-dracolich` (label "dragon"), `the-three`, `portal-guardian`, `void-messenger`, `zahak` (label "dragon egg") — the run covered those threads, so these are true recall failures, likely because they're named by description ("the dragon", "three drooling figures") rather than capitalized names.

### Books — near-total absence (5 pairs + fuzzy candidates)
`excidium`, `ninth-barrier`, `beginners-guide-elements`, `empire-rise-of-civilization`, `leaping-scroll` all missed. Books are read-as-texture, exactly as Matt predicted — despite their inventory/mechanical significance.

### Magic taxonomy (12 pairs)
`levels`/`master`/`grandmastery` (rank system), `enchanting:golem[s]`, `runes`, `pocket-dimension`, `metamagic:leaping`/`manipulation`, `techniques:defibrilation`, `ice`, `nature`, `meditation-on-reality` (a spoiler-labeled link). The annotator folds these into parents or skips them as generic words; the wiki splits them out.

### Objects (11 pairs)
`brooch` (10 thread pages! the communication network — a real omission), `oud`, `miks-spear`, `shockwave-sword`, `ring-of-sanity` (label "signet ring" — we have `Emperor's Hand signet`, another naming mismatch), `computor`, `gladius`, `purification-chamber`, `void_generators`, `money`, `hammock`.

### Metaphysics/culture
`sanity`, `spirit-geis`, `spirits`, `strings-of-reality`, `prthvi`, `srsti`, `voces-mysticae`, `dwoveth`, `bhataki` (we have `bhatak` — variant spelling mismatch), `dragon-cult`, `siege` (the game Mik founds — we have it? no), `royalty`/`Shahzada` (we have `Shahzadi` — gendered variant mismatch).

## B. What we have that the wiki doesn't (69 of 177 entries without a gold backlink)

This is the more encouraging direction. Reading all 69:

### Genuinely good captures the wiki lacks
- **The mechanics Q&A harvest**: `Destroy`, `Create`, `Slow/Speed`, `Animate`, `Permanency`, `Confuse`, `Weapon Affinity`, `attuned` — from the author's out-of-story mechanics posts. The wiki's mechanics pages are incomplete; we got these with quotes.
- **Language/culture texture**: `Senyana` / `sikarida` (the dung-fetcher insult exchange), `Hayat` (Chryssa's people's name for vys — cross-cultural mapping!), `bhatak` + `Jamav`, `Conium`, `Dominion of Sosae`, `Marsala Ve` variants.
- **Key items**: `Husk` (!), `Shahzadi`, `Emperor's Hand signet` (= gold's `ring-of-sanity`), `Antimage Core (Corrupted)`, `Administrator Unit` (Fulvia's body), `Technician's Armor`, `Projection Sword`.
- **People the wiki misses**: `Ozman`, `Chavdar`, `Agapios`, `Hani`, `Esmail`, `Diovis` (oath-deity).

### The junk tail (present, small)
`lift`, `mine shaft`, `private section`, `layered armor`, `magical experiments`, `headsman` — generic-descriptor texture. These are the graveyard's intake; all are quote-grounded so culling is safe.

### Systemic patterns visible
1. **"Revision:" gloss prefix** — several card glosses start with "Revision: …" (the update-narrowing issue in the wild; rename/revert ops now exist to repair).
2. **Nickname/surname linking is a metric AND product gap**: `mik`↔`Mikhael Abdul-Hakim`, `suresh`↔`Archmagos Megalos`, `ring-of-sanity`↔`Emperor's Hand signet` — the entities exist; the short-form aliases don't. Alias harvesting (from gold labels + in-text short forms) is cheap researcher work.
3. **Spelling variants**: `bhatak`/`bhataki`, `Shahzadi`/`Shahzada` — merge-queue material (do NOT auto-merge; same-name-different-thing is real in this story).

## So what

- The miss side is dominated by *linkability*, not absence: alias/short-form harvesting closes maybe a third of it (characters + objects with label variants).
- True capture holes: books (prompt callout), side characters (admission prompt: "name every named human your batch introduces"), taxonomy nodes (sub-entry rule at write time).
- The extra side shows the annotator already out-covers the wiki on mechanics and cross-cultural terms — the "glossary may be more sensitive than thread backlinks" expectation is being met with real value, and the junk tail is small, identifiable, and safely prunable.
