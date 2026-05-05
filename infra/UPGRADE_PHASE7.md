# NewSoft Phase 7 upgrade runbook

Phase 7 ships two changes:

1. Stage-gated experiments: `research_only` -> `outreach_draft` -> `validation_run`, each with its own Approval.
2. Vector memory: pgvector-backed `memory_embeddings` plus `search_memory` tool for Board, CEO, Validator, CTO, and Postmortem agents.

## Preconditions

- Start from branch `claude/setup-project-architecture-t7WMs` at commit `dd3334c` or later.
- Preserve `/opt/newsoft/.env`; do not overwrite secrets.
- Keep global `dry_run=true` sticky.
- Keep money daily cap unchanged (`$50`) and per-action cap unchanged (`$25`).
- Do **not** enable live cold-email outreach during Phase 7 smoke.

## pgvector installation paths

Phase 7 prefers a real pgvector extension and HNSW index.

### Path A — allow `newsoft` to create the extension

As Postgres superuser:

```bash
sudo -u postgres psql -d newsoft -c 'ALTER ROLE newsoft CREATEROLE;'
```

Then run the app migration/init as the service user. `init_db.py` runs:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Path B — pre-install by superuser

If you do not want `newsoft` to have extension privileges:

```bash
sudo -u postgres psql -d newsoft -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

Then run the app migration/init. The app will create `memory_embeddings` using `vector(1024)` and the HNSW cosine index.

If the OS package is missing, install the matching package for the server's Postgres major version, e.g. `postgresql-16-pgvector` / `postgresql-15-pgvector`, then create the extension. If pgvector is not installed yet, Phase 7 code remains bootable but vector memory is deferred.

## Env vars

Optional, graceful unset:

```text
VOYAGE_API_KEY      enables Voyage voyage-3-lite embeddings
MEMORY_PROVIDER    voyage (default) or openai
OPENAI_API_KEY     only needed when MEMORY_PROVIDER=openai
```

No key means `search_memory` returns `[]`, agents continue, and `/memory` shows empty results.

## Deploy

```bash
cd /tmp/newsoft
sudo -u newsoft git fetch origin claude/setup-project-architecture-t7WMs
sudo -u newsoft git checkout claude/setup-project-architecture-t7WMs
sudo -u newsoft git pull --ff-only origin claude/setup-project-architecture-t7WMs

sudo systemctl stop newsoft-orchestrator newsoft-dashboard
sudo rsync -a --delete --exclude '.env' --exclude '.ssh' /tmp/newsoft/ /opt/newsoft/
cd /opt/newsoft
sudo -u newsoft /opt/newsoft/orchestrator/.venv/bin/python -m orchestrator.db.init_db
cd /opt/newsoft/dashboard && sudo -u newsoft npm install && sudo -u newsoft npm run build
sudo systemctl start newsoft-orchestrator newsoft-dashboard
```

## Approval #20 cleanup

After deploy, reject the stale atomic validation approval:

```sql
UPDATE approvals
SET status='rejected', decided_by='system', decided_at=now(), rationale='Phase 7 supersedes — staging required'
WHERE id=20 AND status='pending';
```

Then run validator once:

```bash
curl -s -X POST http://127.0.0.1:8000/api/validator/run
```

Expected: a new Stage 1 `run_experiment_stage_research` approval is filed for memo #14.

## Smoke flow

1. Reject approval #20.
2. Trigger/wait for validator tick.
3. Approve Stage 1 with `execute_live=false`.
4. Confirm Stage 1 `result_md` and `result_json` populated.
5. Confirm Stage 2 approval auto-files.
6. Approve Stage 2 with `execute_live=false`.
7. Confirm Stage 2 drafts are populated.
8. Confirm Stage 3 approval auto-files.
9. STOP. Do not approve Stage 3 in Phase 7.
10. Re-run Board on a pending memo if needed and confirm a `search_memory` tool call is logged.
11. Visit `/memory` and run `validation under $200`.

## Rollback

- Stop services.
- Restore previous code from Git/backup.
- Do not drop Phase 7 tables unless explicitly requested; they are additive.
- Restart services and verify `/api/status`, dashboard, and Basic Auth behavior.

## Final report fields

- Code commit hash.
- Migration status: `experiment_stages`, `outreach_sends`, `memory_embeddings`, vector extension.
- Approval #20 cleanup and replacement approval ID.
- Stage 1/2 results and Stage 3 pending ID.
- Memory provider and row counts/search result.
- First observed `search_memory` tool call.
- Final state/caps/spend.
