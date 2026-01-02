# Tool Interfaces

Agent tools exposed via OpenAI function calling format.

## Glossary Tools

### glossary_search

Query glossary entries.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | yes | Search term (fuzzy matched against term and definition) |
| `tags` | string[] | no | Filter by tags |
| `status` | string | no | `confirmed` \| `tentative` \| `all` (default: `all`) |
| `include_references` | bool | no | If true, expand `[[refs]]` to include referenced entries |
| `limit` | int | no | Max results (default: 10) |

**Response:**
```xml
<glossary_results count="3">
  <entry id="42" term="vatis" status="confirmed" tags="mechanic,magic">
    A practitioner of [[vys]]-based magic. Vatis can manipulate...
  </entry>
  <entry id="15" term="vys" status="confirmed" tags="mechanic,magic">
    The fundamental magical energy in the setting, analogous to mana...
  </entry>
  <entry id="89" term="Rhynia" status="tentative" tags="location,faction,historical">
    The ruined fantasy Roman-like empire...
  </entry>
</glossary_results>
```

**Notes:**
- Exact matches prioritized over fuzzy
- Always check before creating new entries

---

### glossary_create

Create a new glossary entry.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `term` | string | yes | Term with disambiguation suffix if needed: `Dawn (character)` |
| `definition` | string | yes | Markdown text. Use `[[Term]]` for cross-references |
| `tags` | string[] | yes | Classification tags: `character`, `location`, `mechanic`, etc. |
| `status` | string | no | `tentative` (default) \| `confirmed` |

**Response:**
```xml
<glossary_created id="43" term="Soma" status="tentative">
  Entry created. Tags: questmaster, character.
</glossary_created>
```

**Notes:**
- `first_seen_post_id` and `first_seen_thread_id` auto-set from current context
- Prefer `tentative` status; curator will evaluate

---

### glossary_update

Modify an existing glossary entry.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `entry_id` | int | yes | ID of entry to update |
| `term` | string | no | New term name (for corrections or disambiguation changes) |
| `definition` | string | no | New definition text |
| `tags` | string[] | no | Replace all tags with this list |
| `status` | string | no | New status |

**Response:**
```xml
<glossary_updated id="43" term="Soma" fields="definition,status">
  Previous status: tentative, new status: confirmed.
</glossary_updated>
```

**Notes:**
- Only provided fields are modified
- `last_updated_post_id` and `last_updated_thread_id` auto-set
- Changes logged to revision history

---

### glossary_delete

Remove a glossary entry.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `entry_id` | int | yes | ID of entry to delete |
| `reason` | string | yes | Reason for deletion (logged for audit) |

**Response:**
```xml
<glossary_deleted id="43">
  Entry 'Soma' deleted. Reason: Duplicate of entry #12.
</glossary_deleted>
```

**Notes:**
- Use sparingly; prefer status changes
- Deletion logged to revision history

---

## Corpus Tools

### read_post

Fetch a historical post by ID.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `post_id` | int | yes | The post ID to retrieve |
| `include_adjacent` | bool | no | If true, include 2 posts before and after |

**Response:**
```xml
<corpus_post id="12345" thread_id="30265887" timestamp="2014-02-16T01:24:30" author="Soma">
  <body>
    The post content here with full text...
  </body>
</corpus_post>
```

**With `include_adjacent=true`:**
```xml
<corpus_posts center_id="12345">
  <post id="12343" thread_id="30265887" author="Anon">...</post>
  <post id="12344" thread_id="30265887" author="Anon">...</post>
  <post id="12345" thread_id="30265887" author="Soma" center="true">...</post>
  <post id="12346" thread_id="30265887" author="Anon">...</post>
  <post id="12347" thread_id="30265887" author="Soma">...</post>
</corpus_posts>
```

---

### read_thread_range

Fetch a range of posts from a thread.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `thread_id` | int | yes | The thread ID |
| `start_post_id` | int | no | First post ID (inclusive) |
| `end_post_id` | int | no | Last post ID (inclusive) |
| `tag_filter` | string | no | Only include posts with this tag (e.g., `qm_post`) |

**Response:**
```xml
<corpus_thread id="30265887" count="15">
  <post id="12345" author="Soma" tags="qm_post,story_post">...</post>
  <post id="12350" author="Soma" tags="qm_post,story_post">...</post>
  ...
</corpus_thread>
```

---

## Context Tools

### list_snapshots

Query available snapshots.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `thread_id` | int | no | Filter to snapshots from this thread |
| `snapshot_type` | string | no | `checkpoint` \| `curator_fork` \| `manual` \| `all` |
| `limit` | int | no | Maximum results (default: 20) |

**Response:**
```xml
<snapshots count="5">
  <snapshot id="15" type="checkpoint" thread="42" thread_position="42"
            entry_count="156" tokens="85432" created="2024-01-15T12:30:00Z"/>
  <snapshot id="14" type="checkpoint" thread="41" thread_position="41"
            entry_count="148" tokens="79201" created="2024-01-15T11:15:00Z"/>
  ...
</snapshots>
```

---

### summon_snapshot

Rehydrate a past context for multi-turn dialogue.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `snapshot_id` | int | yes | ID of the snapshot to summon |
| `query` | string | yes | Initial question to ask the summoned context |

**Response:**
```xml
<summoned_context snapshot_id="15" thread_position="42">
  <response>
    The summoned agent's response to your query...

    Based on what I was reading at that point, the term 'vys' referred to...
  </response>
  <available_actions>
    Use summon_continue to ask follow-up questions, or summon_dismiss to end.
  </available_actions>
</summoned_context>
```

**Notes:**
- Summoned context is read-only
- Only one summoned context active at a time
- Must dismiss before summoning another

---

### summon_continue

Continue dialogue with a summoned snapshot context.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message` | string | yes | Follow-up message to the summoned context |

**Response:**
```xml
<summoned_response turn="2">
  The summoned agent's response to your follow-up...
</summoned_response>
```

---

### summon_dismiss

End dialogue with summoned context.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `summary` | string | yes | Brief summary of what was learned from the dialogue |

**Response:**
```xml
<summoned_dismissed snapshot_id="15" turns="3">
  Dialogue ended. Summary logged.
</summoned_dismissed>
```

**Notes:**
- Summary is logged for audit
- Summoned context is discarded
- Main annotation loop resumes

---

## Explicate Protocol

Not a tool, but a system behavior.

When the agent quotes text from a definition and requests explication:

1. **Agent action**: Include quoted text in message with explication request
2. **System behavior**:
   - Fuzzy-match quote against entry definitions
   - Look up blame (which snapshot wrote that section)
   - Return source post content OR offer to summon authoring snapshot

**Example agent message:**
```
I need clarification on this part of the vys entry:
"the vys flows through crystalline channels in the body"

Can you show me the source for this?
```

**System response options:**
```xml
<explication entry_id="15" term="vys">
  <source_post id="12567" thread_id="30265890">
    <body>Original post content...</body>
  </source_post>
  <authored_by snapshot_id="8"/>
</explication>
```

Or:
```xml
<explication entry_id="15" term="vys">
  <no_exact_match/>
  <similar_sections>
    <section snapshot_id="8">"vys flows through channels..."</section>
  </similar_sections>
  <suggestion>Use summon_snapshot with snapshot_id=8 to discuss with authoring context</suggestion>
</explication>
```
