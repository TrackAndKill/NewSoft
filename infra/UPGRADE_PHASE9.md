# Phase 9 upgrade — venture budgets, teardown approvals, clarifications

Phase 9 adds operational controls for running multiple ventures in parallel:

- Per-venture LLM and real-money budgets layered on top of existing global caps.
- Killed-venture site teardown after a 24h grace period, always approval-gated.
- Operator clarification inbox with answers indexed into vector memory.

No new environment variables are required.

## Migration order

1. Pull the Phase 9 commit.
2. Stop or leave services running; migrations are idempotent additive ALTERs only.
3. Run the DB initializer from the orchestrator venv:

```bash
cd /opt/newsoft/orchestrator
/opt/newsoft/orchestrator/.venv/bin/python -m orchestrator.db.init_db
```

4. Restart services:

```bash
sudo systemctl restart newsoft-orchestrator newsoft-dashboard
```

5. Verify services and protected dashboard:

```bash
systemctl is-active newsoft-orchestrator newsoft-dashboard nginx postgresql redis-server
curl -sk -o /dev/null -w 'firm_https=%{http_code}\n' https://firm.profithub.me/
```

## Schema changes

`ventures` gets seven additive columns:

- `daily_llm_cap_usd FLOAT NOT NULL DEFAULT 5.0`
- `daily_money_cap_usd FLOAT NOT NULL DEFAULT 25.0`
- `total_money_cap_usd FLOAT NOT NULL DEFAULT 50.0`
- `llm_spend_today_usd FLOAT NOT NULL DEFAULT 0.0`
- `money_spend_today_usd FLOAT NOT NULL DEFAULT 0.0`
- `money_spend_lifetime_usd FLOAT NOT NULL DEFAULT 0.0`
- `spend_day TIMESTAMPTZ NOT NULL DEFAULT now()`

`agent_runs` gets:

- `venture_id INT NULL REFERENCES ventures(id)`

`money_transactions` gets:

- `venture_id INT NULL REFERENCES ventures(id)`

New table:

- `clarifications`

New indexes:

- `ix_agent_runs_venture_id`
- `ix_money_transactions_venture_id`
- `ix_clarifications_status`
- `ix_clarifications_venture_id`

## venture_id backfill note

Existing `agent_runs` and `money_transactions` rows intentionally stay `NULL`.

Only firm-level work is naturally nullable: Discovery, Board, and CEO charter runs are run on the firm account, not a venture. New venture-scoped work stamps `venture_id` where a venture context exists.

Do not run a destructive backfill. Historical rows before Phase 9 are valid with `venture_id = NULL`.

## Per-venture caps

Defaults per venture:

- daily LLM: `$5`
- daily money: `$10`
- lifetime money: `$50`

Caps are additive. Existing global caps still apply and must not be lowered by this upgrade.

Operator override endpoint:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/ventures/<slug>/budget \
  -H 'content-type: application/json' \
  -d '{"daily_llm_cap_usd":5,"daily_money_cap_usd":10,"total_money_cap_usd":50}'
```

## Teardown flow

`teardown_tick` runs hourly.

For ventures with `status='killed'` and `killed_at` older than 24h, it files a pending `teardown_site` approval. It does not execute teardown itself.

Dry-run approval simulates teardown and writes `site_torn_down_simulated` without touching DNS, nginx, certs, or site files.

Live approval:

- best-effort deletes root/www DNS records through Porkbun when credentials are configured,
- removes `/etc/nginx/newsoft-sites/<slug>.conf`,
- reloads nginx through the existing helper,
- archives `/var/lib/newsoft/sites/<slug>` to `/var/lib/newsoft/sites/.archived/<slug>-<timestamp>`,
- leaves certs alone to expire naturally,
- never deletes venture data.

Revive endpoint cancels pending teardown approvals:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/ventures/<slug>/revive
```

Grace extension endpoint:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/ventures/<slug>/extend_grace \
  -H 'content-type: application/json' \
  -d '{"hours":24}'
```

## Clarifications

Agents with `search_memory` now also have `ask_operator`. The tool opens a non-blocking clarification row and returns immediately.

Endpoints:

```bash
curl -sS http://127.0.0.1:8000/api/clarifications?status=open
curl -sS -X POST http://127.0.0.1:8000/api/clarifications/<id>/answer \
  -H 'content-type: application/json' \
  -d '{"answer":"...","answered_by":"founder"}'
curl -sS -X POST http://127.0.0.1:8000/api/clarifications/<id>/dismiss
```

Answered clarifications are indexed as `source_kind='clarification'` into `memory_embeddings` when embeddings are available.

Operator answers are not echoed to systemd logs.

## Post-upgrade smoke

- Confirm all new columns and `clarifications` table exist.
- Set seed venture daily LLM cap to `0.01`, trigger a venture-scoped run, observe `VentureBudgetExceeded`, then restore `5`.
- Confirm fresh venture-scoped `agent_runs.venture_id` and `money_transactions.venture_id` are populated.
- Backdate killed seed venture by >24h, run teardown tick, approve dry-run teardown, confirm simulated event, revive and confirm pending teardown is cancelled.
- Create and answer a clarification; confirm status `answered` and search_memory returns it when embeddings are configured.
- End with system active, dry-run true, global caps unchanged.
