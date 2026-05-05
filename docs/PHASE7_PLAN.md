# Phase 7 build plan

For the hermes coding agent. Build spec — hermes implements, deploys,
writes its own short upgrade runbook (`infra/UPGRADE_PHASE7.md`), and
pushes to origin.

**Branch:** `claude/setup-project-architecture-t7WMs` (continue from HEAD).
**Repo:** `https://github.com/trackandkill/newsoft.git`

---

## Where we are

After Phase 6:
- Approvals support per-decision `execute_live` override (sticky
  global dry_run + opt-in live per action).
- Kill loop + postmortem path live (conservative; never auto-kills).
- npm audit clean (0 vulnerabilities).
- Key rotation runbook documented.

What surfaced during the Phase 6 smoke that Phase 7 fixes:

1. **Approval #20 was filed by the Validator as one atomic blob** —
   prospect list + draft copy + landing page + cold outreach +
   $19 Carrd spend, all bundled into one approval. That's wrong.
   Rejecting an approval like that loses the research work; approving
   it triggers external actions before the operator has reviewed any
   intermediate output.

2. **Agents have no memory of past firm decisions.** Every Board
   review, every CEO charter, every postmortem starts from a blank
   slate. Once we have postmortems, this is wasteful.

Phase 7 fixes both:

- **7A**: stage-gated experiments — Validator decomposes into
  research → outreach_draft → validation_run, each gated by its own
  approval. Approval #20 becomes a Stage 1 only.
- **7B**: vector memory — pgvector + Voyage embeddings, indexed across
  memos / postmortems / charters / lessons / decisions. Board, CEO,
  CTO, and Validator gain a `search_memory` tool.

## Operator decisions baked into this plan

| Question | Default | Override |
|---|---|---|
| Embedding provider | Voyage AI (Anthropic-recommended; cheap; ~$0.10/1M tokens) | `MEMORY_PROVIDER=openai\|voyage` env, optional |
| Vector dimension | 1024 (Voyage `voyage-3-lite`) | follows model |
| pgvector extension | required; `CREATE EXTENSION IF NOT EXISTS vector` | n/a |
| Embedding cadence | on-write + nightly catch-up | code default |
| Memory retrieval default | top 5 results, cosine distance | per-call argument |
| Stage 1 (research) cap | $0.50 in agent costs, $0 real-world | code constants |
| Stage 2 (outreach_draft) cap | $0.30 in agent costs, $0 real-world | code constants |
| Stage 3 (validation_run) cap | $0.20 in agent costs, $200 real-world cap (existing Phase 4 cap) | code constants |

## New operator inputs

| Var | What it's for |
|---|---|
| `VOYAGE_API_KEY` | embeddings for vector memory; optional. Without it, memory module logs a warning at boot and `search_memory` returns an empty result. Existing agents continue working unchanged. |

`MEMORY_PROVIDER`, `OPENAI_API_KEY` are optional alternative paths.

---

## Scope — two deliverables

### 7A. Stage-gated experiments (medium, must-ship)

Goal: Validator's experiment-design output is no longer one atomic
blob. It's a 3-stage state machine; each stage gates the next; each
has its own Approval; the operator can stop at any stage with full
context.

#### Stages

**Stage 1 — research_only** (cheap; no external actions; no spend):
- Build prospect signal map from public sources (Brave search,
  fetch_url): which forums, subreddits, threads, GitHub issues
  contain real evidence of the pain?
- Identify the specific ICP segment most likely to convert.
- Identify 1-3 candidate channels for outreach.
- No drafting, no contacting, no publishing.
- Output: `research.json` (signals found, ICP, channel hypotheses,
  proposed Stage 2 plan).

**Stage 2 — outreach_draft** (cheap; no spend; no sending):
- Draft cold email copy (3 variants for A/B/C).
- Draft a single DM template for the highest-signal channel.
- Draft a tight landing page brief (one paragraph + CTA).
- Operator reviews and edits before approving Stage 3.
- Output: `outreach_draft.json` (copy variants, DM template, brief).

**Stage 3 — validation_run** (real-world; gated; capped):
- Buy domain (existing Phase 4 path).
- Deploy landing page (existing Phase 5 path).
- (NEW) Send first batch of cold emails — operator-bounded count
  (default 10; per-day cap 50; per-experiment cap 100).
  Implementation lives in `tools/outreach.py`.
- (NEW) (Reserved) tiny ad probe via a future Phase 8 ads tool;
  Phase 7 punts on this — leaves the slot in the schema.
- Output: `validation_run.json` (signups, opens, replies,
  qualitative notes), feeding back to Board re-vote.

#### Data model

Replace single-blob `Experiment` with stages. New table
`experiment_stages`:
```
id,
experiment_id (FK experiments),
stage_name (str, one of: research_only, outreach_draft, validation_run),
stage_index (int, 1/2/3),
status (designed | pending_approval | running | done | failed | skipped),
design_json (JSON),       -- design produced by Validator
result_md (Text),         -- result narrative produced after run
result_json (JSON),       -- structured result data
approval_id (FK approvals nullable),
agent_run_id (FK agent_runs nullable),
cost_usd (Float),         -- agent cost only
real_money_usd (Float, default 0.0),
created_at, completed_at
```

Existing `Experiment` keeps its row but treats it as the parent /
container. Don't drop existing columns — back-compat for old rows.

Existing memo #14 + approval #20 cleanup:
- Hermes auto-rejects approval #20 (status='rejected', decided_by='system'
  with rationale "Phase 7 supersedes — staging required").
- The `validator_tick` then refiles a Stage 1 (research_only) approval
  for memo #14 on the next tick.
- Old Experiment rows older than approval #20 stay as-is; Phase 7
  doesn't migrate them backwards.

#### New approval actions

Three, all dispatched from `POST /api/approvals/{id}`:
- `run_experiment_stage_research` → `validator.run_stage_1`
- `run_experiment_stage_outreach_draft` → `validator.run_stage_2`
- `run_experiment_stage_validation` → `validator.run_stage_3`

Each one's payload contains `experiment_id`, `stage_index`, and a
short summary of what will happen (so operator sees it in
`/approvals` without clicking through).

The existing `run_experiment` action stays defined as a no-op alias
(redirects internally to `run_experiment_stage_research` for
back-compat with existing rows).

#### Validator updates

- `agents/validator.py`:
  - Split `design_experiment` into `design_stage_1`, etc.
  - Stage 1 prompt: Sonnet, with `web_search` + `fetch_url` tools.
    No money tools.
  - Stage 2 prompt: Sonnet, no tools (works from Stage 1's
    `research.json`). Produces only text/JSON.
  - Stage 3 prompt: Sonnet, with money tools available
    (domain_register, configure_dns, deploy_landing_page,
    outreach.send_email — each separately gated by its own
    sub-approval if the existing path requires it).
- `validator_tick` walks ExperimentStages in priority order: any
  stage with `status='designed'` and no Approval → file Approval; any
  stage with approval `approved` and `status != done` → run.

#### New tool: outreach.send_email

`orchestrator/orchestrator/tools/outreach.py`:
```python
def send_email(to: str, subject: str, body: str, *,
               experiment_id: int, dry_run: bool = True) -> dict:
    """Send a single cold email via Resend. Hard caps:
       - per-experiment: 100 max sends
       - per-day global: 50 max sends across all experiments
       - per-recipient: 1 send (idempotent on (experiment_id, to))
       - body must contain operator-supplied unsubscribe footer
       - operator's verified domain must own the From address
       Logs every send to a new outreach_sends table.
    """
```
New table `outreach_sends`:
```
id, experiment_id (FK), recipient_hash (sha256, not raw email),
subject, status (queued | sent | failed | dry_run),
provider_id (str nullable), dry_run (bool), created_at
```

Tight constraints:
- Cold-email batch sending only happens inside Stage 3 via
  Validator's planned action list.
- Each email send goes through the existing `MoneyTransaction` audit
  even though the cost is ~$0; this gives us a uniform spend ledger.
- The existing `system.dry_run` + per-approval `execute_live`
  controls apply (no separate flag).
- Hermes does NOT enable Stage 3 outreach in the smoke test for
  Phase 7. Smoke covers Stage 1 + Stage 2 only.

#### Dashboard

- `dashboard/app/experiments/page.tsx` — list of experiments with
  stages collapsed; status pills per stage.
- `dashboard/app/experiments/[id]/page.tsx` — stage-by-stage view
  with inline result_md, design_json (collapsed), and the gating
  approval link.
- Add nav link.

#### Acceptance

- Approval #20 auto-rejected with the documented rationale.
- A fresh Stage 1 approval is filed for memo #14 within one
  `validator_tick`.
- Approving Stage 1 (in simulate, since dry_run=true) runs the
  research, writes a Stage row with `status='done'` and a populated
  `result_md` plus `research.json`. Cost <= $0.50 in agent runs.
- Stage 2 approval auto-files after Stage 1 completes; operator
  approves; Stage 2 runs and produces drafts.
- Stage 3 approval auto-files; **operator does NOT approve in this
  phase's smoke**. Hermes verifies the approval shows up with the
  right summary in the UI, then leaves it pending.
- Existing seed venture is untouched (its tasks already use
  Phase 4/5 approvals; Stage flow only applies to memo-stage
  experiments).
- `/experiments/<id>` page shows three stage cards.

### 7B. Vector memory (medium)

Goal: agents can ask "what did the firm learn about Y?" and get back
the relevant chunks of memos, postmortems, charters, board reviews,
and lessons. Implemented as a tool the agent calls; not auto-injected
into prompts.

#### pgvector setup

- `init_db.py` runs `CREATE EXTENSION IF NOT EXISTS vector;` (one-time;
  needs the `newsoft` Postgres role to have CREATEROLE privileges OR
  the extension to be pre-installed by superuser. Document both
  paths in `infra/UPGRADE_PHASE7.md`).
- New table:
  ```
  memory_embeddings (
    id BIGSERIAL PRIMARY KEY,
    source_kind VARCHAR(40) NOT NULL,    -- 'memo', 'postmortem',
                                         -- 'charter', 'lesson',
                                         -- 'board_decision'
    source_id INT NOT NULL,
    chunk_index INT NOT NULL,
    content_chunk TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_kind, source_id, chunk_index)
  );
  ```
- HNSW index (or IVFFlat — pgvector docs default to HNSW for read-heavy):
  ```
  CREATE INDEX IF NOT EXISTS memory_embeddings_hnsw
    ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
  ```

#### Embedding provider

`orchestrator/orchestrator/memory.py`:
- `def embed_texts(texts: list[str]) -> list[list[float]]` — calls
  Voyage `voyage-3-lite` (or OpenAI `text-embedding-3-small` if
  `MEMORY_PROVIDER=openai`).
- Batches up to 128 texts per call.
- Cached: same text returns the cached embedding (in-memory LRU,
  256 entries; not persistent — speed not durability).
- If `VOYAGE_API_KEY` unset and provider unset, embed_texts raises
  `EmbeddingsUnavailable`.

#### Indexing

- `def reindex_source(source_kind: str, source_id: int) -> None`:
  fetch the source row's text, chunk it (~600 token chunks via the
  stdlib tokenizer or a regex on paragraphs — we don't need
  perfect), embed each, upsert into `memory_embeddings`.
- Wire to write-time hooks for the four kinds:
  - When a Memo is written, reindex it.
  - When a Postmortem is written, reindex content_md and lessons_md
    separately (lessons have higher priority weight).
  - When a Venture's charter changes, reindex.
  - When a BoardReview is written with a final decision, reindex
    the rationale chunked alongside the memo title.
- Nightly catch-up cron at 04:00:
  `def memory_reindex_tick()` — find any source row that lacks
  embeddings (or whose updated_at is newer than its newest embedding)
  and reindex.

#### Search

- `def search_memory(query: str, *, kinds: list[str] | None = None,
   limit: int = 5) -> list[dict]`:
  - Embeds the query.
  - Runs `SELECT ... ORDER BY embedding <=> :q LIMIT :limit` with
    optional `WHERE source_kind = ANY (...)`.
  - Returns `[{source_kind, source_id, content_chunk, distance,
     metadata}]`.

#### Tool registration

New tool definition for the Anthropic API tool registry:
```python
{
  "name": "search_memory",
  "description": "Search the firm's memory of past memos, "
                 "postmortems, charters, lessons, and board "
                 "decisions. Use this before making big judgments "
                 "to avoid repeating mistakes.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "kinds": {"type": "array", "items": {"type": "string",
        "enum": ["memo", "postmortem", "charter", "lesson",
                 "board_decision"]}},
      "limit": {"type": "integer", "default": 5}
    },
    "required": ["query"]
  }
}
```

Wire `search_memory` into the tool registry. Add it to:
- Board partners (Growth, Operator, Skeptic) — most valuable
- CEO charter agent
- Validator (all stages)
- CTO planning
- Postmortem writer (so it can cite past similar postmortems)

Do NOT wire it into Scout, Engineer, Copywriter, or Kill Evaluator
yet — they don't need history (Scout already has Brave; Engineer is
mechanical; Copywriter writes from charter; Kill Evaluator is
narrowly scoped).

#### Cost guard

- Embedding spend goes through a tiny existing-budget path: count it
  as an `agent_run` with model name `voyage-3-lite` and a
  per-million-token rate that gets summed into the existing daily
  LLM cap. (~$0.01/day at current activity. Negligible.)

#### Dashboard

- `dashboard/app/memory/page.tsx` — search box + results list
  (top 10). Useful for the operator to see what the firm "remembers."
- Optional nav link (only show if the API reports embeddings present).

#### Acceptance

- `CREATE EXTENSION vector` succeeded; `memory_embeddings` table
  exists.
- After `memory_reindex_tick()` runs once: at least one row per
  existing memo, postmortem (one — from 6B smoke), charter (one —
  seed venture).
- `search_memory("validation under $200")` returns ranked results
  with cosine distances < 0.7 (sensible).
- Re-running the Board on memo #14 after Phase 7 ships: the partner
  agents make at least one `search_memory` tool call (visible in
  agent_run.tool_calls).
- Without `VOYAGE_API_KEY`: orchestrator boots, logs a warning,
  every `search_memory` tool call returns an empty result; existing
  agent flows still complete.

---

## Cross-cutting requirements

### Migrations
- New table `experiment_stages` — `Base.metadata.create_all`.
- New table `outreach_sends` — `Base.metadata.create_all`.
- New table `memory_embeddings` — `Base.metadata.create_all` AFTER
  `CREATE EXTENSION vector` (init_db.py orders carefully).
- Optional column on existing `experiments`:
  `current_stage VARCHAR(40)` — idempotent ALTER. Useful for the
  list view.
- HNSW index on `memory_embeddings.embedding`.
- No destructive changes; no data loss.

### Env vars added in Phase 7
| Var | Required | Notes |
|---|---|---|
| `VOYAGE_API_KEY` | optional | enables vector memory |
| `MEMORY_PROVIDER` | optional | `voyage` (default) or `openai` |
| `OPENAI_API_KEY` | optional | only if `MEMORY_PROVIDER=openai` |

### Module layout (additions only)
```
orchestrator/orchestrator/
  agents/validator.py            (extend with stage functions)
  tools/
    outreach.py                  (new)
    memory.py                    (new — at /tools/memory.py for tool
                                  registration; underlying impl in
                                  memory.py module below)
  memory.py                      (new — embeddings, search, reindex)
  db/models.py                   (extend with ExperimentStage,
                                  OutreachSend, MemoryEmbedding)
  db/init_db.py                  (extend with CREATE EXTENSION)
  rituals/scheduler.py           (extend with memory_reindex_tick;
                                  validator_tick now stage-aware)
  api.py                         (extend endpoints)
dashboard/
  app/experiments/page.tsx       (new)
  app/experiments/[id]/page.tsx  (new)
  app/memory/page.tsx            (new)
  lib/api.ts                     (extend)
infra/
  UPGRADE_PHASE7.md              (new — hermes writes this)
docs/
  PHASE7_PLAN.md                 (this file)
```

### Things hermes should NOT do
- Do not enable cold-email sending (`outreach.send_email`) live in
  Phase 7 smoke. Stage 3 stays unapproved.
- Do not auto-include `search_memory` results in agent prompts.
  It's a tool the agent calls when relevant; not blanket context
  injection.
- Do not embed PII (lead emails, IPs) into `memory_embeddings`.
  Source kinds are limited to memos, postmortems, charters, lessons,
  board decisions — none of which contain user PII.
- Do not migrate to React 19.
- Do not change Phase 6 kill-loop or postmortem behavior.
- Do not raise the per-action money cap. $25 stays.
- Do not delete or rewrite existing `experiments` rows; the
  Stage table extends, doesn't replace.

### Acceptance for Phase 7 as a whole
- Code: pushed; new commit hash on origin.
- Migrations: `experiment_stages`, `outreach_sends`,
  `memory_embeddings` present; `vector` extension installed.
- Approval #20: rejected by hermes; Stage 1 approval auto-filed
  for memo #14.
- Stage 1 approved (simulate); Stage 2 auto-files; approved
  (simulate); Stage 3 auto-files but stays pending.
- `search_memory` tool call observed in at least one fresh Board
  partner run (rerun a board tick on any pending memo to verify).
- `/memory` page returns search results for a manually-typed query.
- Without `VOYAGE_API_KEY`: graceful unset everywhere.
- System ends ACTIVE, dry-run, $20 LLM cap, $50 money cap.

---

## Final report from hermes

| Section | Content |
|---|---|
| Code | latest commit hash; deliverables shipped (7A, 7B) |
| Migrations | confirm tables + extension |
| Approval #20 | rejected; replacement Stage 1 approval id |
| Stage flow | Stage 1 result_md head; Stage 2 drafts produced; Stage 3 pending status |
| Memory | rows in memory_embeddings by source_kind; sample search result |
| Provider | which embedding provider used; whether VOYAGE_API_KEY was set |
| Tools | which agents now have `search_memory`; first observed tool call |
| State | system active? caps? dry-run? today's spend (LLM and money) |
| Issues | non-fatal warnings, things deferred to Phase 8 |

End of plan.
