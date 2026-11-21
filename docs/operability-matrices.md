# Provider Capability Matrix (G15 deliverable, 2026-11-21)

Registered in `provider_records` at boot; state = SQLite-synchronized truth.
Scope boundaries (company/household) are enforced at the capability-executor
layer; live rows BLOCKED means "no endpoint/credentials configured on this
host" — see the ledger blocker register.

| Provider | Category | State (this host) | Declared capabilities (read-only unless noted) | Qualification |
|---|---|---|---|---|
| pixel_office_js | operational_persistence | ACTIVE (priority 100) | persistence.health.counts | live baseline (JS) |
| pixel_startup_sqlite | operational_persistence | QUALIFIED (priority 10) | persistence.health.counts, employees.upsert, tasks.lifecycle | live (this repo) |
| openclaw_gateway | agent_harness | CANDIDATE | gateway.health, agent.discovery | synthetic; **B1 BLOCKED** live |
| hermes_home | agent_harness | QUALIFIED | health, discovery, task.submit*, task.result* | live probe ONLINE (mattermost, sessions); * submission ops explicit unsupported until A2A enabled (**B5**) |
| chromadb | memory_retrieval | CANDIDATE | memory.search, collections.list | synthetic; **B4 BLOCKED** live |
| minio | object_storage | CANDIDATE | object.put/get/list/stat | synthetic (SigV4, sha256, scopes); **B4 BLOCKED** live |
| mattermost | comms_internal | CANDIDATE | comm.post, comm.notify | synthetic (allowlist, idempotent, 429); **B3 BLOCKED** live |
| email | comms_external | CANDIDATE | email.compose, email.send (approval-gated) | synthetic; **B3 BLOCKED** live |
| odoo | activity_reporting | CANDIDATE | customer.lookup, projects.list, invoice.status, tasks.list | synthetic; live BLOCKED (no endpoint) |
| homeassistant | activity_reporting | CANDIDATE | entity.status, device.availability, sensor.read | synthetic; live BLOCKED |
| rundeck | automation | CANDIDATE | jobs.discover, execution.status, job.metadata | synthetic; live BLOCKED |
| grafana | monitoring | CANDIDATE | dashboards.discover, health.read | synthetic; live BLOCKED |
| github | automation | CANDIDATE | repo.meta, issues.list, prs.list, workflow.status | synthetic; live BLOCKED |
| freshrss | activity_reporting | CANDIDATE | feeds.list, unread.summaries, feed.meta | synthetic; live BLOCKED |
| litellm | monitoring | CANDIDATE | health.read, models.discover | synthetic; live BLOCKED |
| calendar | monitoring | CANDIDATE | events.list | synthetic; live BLOCKED |

Write operations: only two exist in the whole system — `synthetic.restricted.write`
and `vault.credential.op` (both approval-gated, executor-only) plus owner-authed
`PUT /api/environment`. No infra provider carries write capabilities by design.

# Agent Harness Compatibility Matrix (G14 deliverable)

| Harness | Health/discovery | Task submission | Result retrieval | Notes |
|---|---|---|---|---|
| OpenClaw | `gateway.health`/`agent.discovery` — supported, synthetic-tested; live **BLOCKED (B1: no gateway)** | unsupported → `supported:false`, dispatch cancels honestly | n/a | Charon's native harness; binding chain ready (G9) |
| Hermes | live probe ONLINE (filesystem interface: gateway state, sessions, platforms) | A2A plugin EXISTS in installed Hermes (documented JSON-RPC/SSE) but is NOT running → **BLOCKED (B5: owner enables A2A port + peer token)**; office API fallback is `supported:false` (no fabrication) | synthetic `task.result` contract tested | Charon→Hermes delegation protocol fully synthetic-qualified |
| Synthetic (tests) | n/a | full submit/ack/result contract | full | duplicate suppression ×1 dispatch, restart reconciliation, uncertain-outcome preservation |
