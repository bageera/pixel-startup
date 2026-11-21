# Pixel Office — Migration Ledger

Governed by the Python MVP gauntlet mandate (2026-11-20). Active stack until qualification: JavaScript (`server.js` + Express). Branch: `migration/python-mvp`. Verdicts restricted to **PASS / FAIL / PARTIAL / BLOCKED / DEFERRED / NOT RUN**; component qualification and live deployment qualification are tracked separately.

Evidence convention: every gate cites executed commands + artifacts. Mock success ≠ live success. Implementation complete ≠ deployment authorized.

---

## G0 — Repository reconciliation

**Objective:** pin repo state, document all active/obsolete behavior, reconcile drift, protect user changes, no secrets in reports. Criteria registered before any implementation work beyond the baseline pin.

**Acceptance criteria (registered pre-execution):**
- C0.1 Repository state pinned: branch, HEAD, tags, clean tree recorded.
- C0.2 Every public HTTP route enumerated with auth status and persistence effects (`api-inventory.md` table).
- C0.3 All integration modules classified with live-availability evidence (`integration-inventory.md`).
- C0.4 Security findings enumerated with source evidence + severity, no secret values reproduced (`security-findings.md`).
- C0.5 Obsolete/drifted references reconciled and recorded (OLLAMA_*, GEMINI_API_KEY, sample-data/, removed gammaclaw modules).
- C0.6 Uncommitted user changes preserved (baseline pin commit) and enumerated.
- C0.7 No live secret values appear in any G0 artifact.

**Test/probe names:** `git status/log/tag/remote/ls-files` pin capture; `grep` route enumeration cross-checked against G1 runtime probes; `npm test` baseline (11/11).

**Evidence:**
- 2026-11-20 `git status` clean after pin; pin commit `a345661` on `main` (12 files, +534/−32), branch `migration/python-mvp` created from it.
- Route table: 36 routes + catch-all, static enumeration at server.js lines cited; zero authenticated routes; session-less login confirmed by source read (server.js:267-276).
- Full-file reads: server.js (910 L), vault.js (291 L), email.js head+config (475 L total, config §1-90 + structure), contacts.js (291 L, injection paths), integrations.js (own session work).
- `git tag -l` → empty. `git remote -v` → github.com:bageera/pixel-office.git (mandate URL matches).
- Drift items: 4 recorded (§G0 inventory §3, integration-inventory §Reconciliation).
- npm test baseline: 11 PASS / 0 FAIL (2026-11-20).

**Verdict: PASS.** Files: docs/migration/{G0-repository-inventory,api-inventory,integration-inventory,security-findings}.md + this ledger. Commit: `96f3ed5`. Limitations: email/vault/CRM availability unverified from this host (missing creds/keys) — recorded as blockers, not failures. Remaining: none for G0.

**Security implications:** findings S-01..S-04 are pre-deployment blockers; remediation staged in G2 before any new integration.
**Rollback implications:** baseline pin commit is additive; reverting G0 docs = revert docs commits only.

---

## G1 — Behavioral baseline

**Objective:** reproducible baseline of the active JS app: startup, routes, frontend behavior, auth behavior, restart recovery, sanitized fixtures, compatibility matrix, known-defect register, perf baseline.

**Registered status: criteria registered, work begins on migration branch immediately after G0 commit.**

Acceptance criteria:
- C1.1 Every enumerated route probed live (200/4xx/5xx recorded) with sanitized fixtures committed.
- C1.2 Frontend walk recorded for: office render, character select, log/email/contacts tabs, briefing button, dashboard login flow.
- C1.3 Restart recovery behavior recorded (agents.json reload, in-memory command loss acknowledged).
- C1.4 Baseline performance snapshot (startup time, RSS) recorded.
- C1.5 Unsafe behaviors classified separately (security-findings S-*) and NOT preserved as contract.

**Evidence (2026-11-20, migration/python-mvp):**
- Baseline suite `node tests/api-baseline.js`: **34 assertions, 0 failures** (`npm test` wiring updated: presence suite + baseline run together).
- Fixtures committed: `docs/migration/fixtures/g1-probes.json` (35 probed routes with status + sanitized response shapes), `g1-recovery.json` (config persists; commands do not).
- Browser walk (browser automation, screenshots): office render (6 client chars / 3 rooms / collision 25), tabs (log 12 filters, email 11 chips), dashboard login reject/accept, i18n toggle — details + known defects D-01..D-06 in `G1-baseline.md`.
- Per-startup RSS snapshot: ≈70 MiB idle, boot 314 ms (node v26).
- Real-baseline surprises corrected in the probe (documented, not trimmed): collision 500-on-fresh, 11 email accounts, 200-with-error degradation, Ghostface reinsertion on restart.

**Verdict: PASS.** Commit: (see G1 commit). Limitations: live LLM/email/vault behaviors not part of the deterministic suite; live-path evidence held in session records + integration-inventory. Frontend walk covers office + dashboard + log/email/contacts tabs; briefing button exercised live in earlier session runs only.

Now G2 (criteria above) proceeds on this branch.

---

## G2 — Immediate security remediation

**Objective:** close deployment-blocking criticals/highs in the active JS app before any new integration; disable unsafe capabilities; serve explicit public dir; consistent auth on sensitive routes.

Acceptance criteria:
- C2.1 `GET /.env`, `GET /server.js`, `GET /package.json` return 404 (only allowlisted public assets served).
- C2.2 `GET /api/vault/item/:id` returns 403 (capability disabled); vault search/status gated or disabled.
- C2.3 `POST /api/email/send` returns 403 (disabled until authorized draft flow).
- C2.4 `GET /api/contacts/search?q=<breakout>` returns error/403 without shell execution (capability disabled until parameterized rewrite).
- C2.5 Mutating + sensitive routes require a session cookie; unauthenticated requests → 401; `POST /api/auth/login` issues HTTP-only signed cookie.
- C2.6 Default password `pixeladmin` no longer silently accepted (boot refusal or KIOSK read-only documented).
- C2.7 `POST /api/ai/chat` requires session and ignores client `systemPrompt`.
- C2.8 Office UI (unauthenticated viewer flows) still works; dashboard login now functional end-to-end (regression).
- C2.9 `npm test` + new security regression suite green.

**Objective:** close S-01..S-08 (+S-11 partial) in the active JS app before any new integration; disable unsecurable capabilities; consistent auth on sensitive routes; keep office viewer and dashboard working.

**Implementation (smallest sufficient, zero new deps):**
- `server.js`: static allowlist (index/dashboard + `/assets/**` with `path.resolve` containment + image extension check), strict 404 catch-all (both pages have no client-side routing), HMAC-signed `pixel_session` HttpOnly cookie (12h, timing-safe compare), `requireAuth` middleware (cookie OR `Bearer PIXEL_API_TOKEN`), login rate limit (5 fails/15min/IP), boot guard (`DASHBOARD_PASSWORD` required unless `PIXEL_ALLOW_NO_PASSWORD=1`), `POST /api/ai/chat` gated + client `systemPrompt` ignored (server-owned prompt only), `/api/briefing*` gated, vault CRUD routes gated, `/api/vault/item/:id` → 403, `/api/email/send` → 403, `/api/contacts/search` + `/api/orgs/search` → 403, default password removed.
- `index.html`: 🔒 Owner login chip (prompt → login → cookie), 401-aware chat/briefing errors (bilingual `loginNeeded` key), client `systemPrompt` no longer sent.
- Scripts (`pep_email_checker.sh`, `marian_calendar_checker.sh`, `dustin_doc_tokens.sh`): `AUTH_ARGS=( -H "Authorization: Bearer $PIXEL_API_TOKEN" )` when token set, inserted into their `send_command` curls.
- `.env.example` / README: `PIXEL_API_TOKEN`, `PIXEL_SESSION_SECRET`, boot-guard + disabled-capability documentation. CHANGELOG updated.

**Test commands + results:**
- `npm test` → presence 11 PASS + `tests/api-baseline.js` **44 PASS / 0 FAIL** (includes the security regression section: static 404s incl. `/assets/../server.js` traversal, 401s on all mutating/sensitive routes w/o auth, token path 200s, cookie issuance, disabled-route 403s, rate-limit 429, unknown-path 404, restart recovery).
- In-page/browser evidence: office renders intact post-gating (6 chars, chips); briefing button shows localized "Owner login required"; `ownerLogin()` → "Owner session established"; subsequent briefing fails 500 at the (deliberately offline) LLM — i.e., auth passed, external dependency honestly failed; dashboard login flow + save works with cookie.
- Cookie-vs-no-cookie curl proof: `POST /api/config` 401 unauthenticated / **200 with login cookie jar** / 200 with token.

**Design decisions:** auth-before-existence on unknown agents (401 over 404 for unauth); read-mostly public office surface (config/log/presence/email reads) — documented residual; contacts `:id`/`interactions`/`sync/status` left public (integer-param safe) pending G10 authorization; error-message redaction deferred to G4 (Python standardizes response shapes) — recorded as PARTIAL for S-11.

**Verdict: PASS** (C2.1–C2.9 executed and satisfied; S-11 redaction sweep explicitly deferred with rationale). Commit: `c571249`. Files: server.js, index.html, 3 scripts, .env.example, README.md, CHANGELOG.md, tests/api-baseline.js, fixtures. Limitations: in-memory rate-limit + per-boot session secret (reset on restart) — acceptable for MVP office scale, noted. Security implications: no new crypto (Node stdlib HMAC); secrets never logged. Rollback: revert the G2 commit; scripts + README document behavior change.

---

## G21 — End-to-end MVP acceptance (registered early for scope control)

Statuses recorded as: PASS / FAIL / BLOCKED / SKIP / NOT RUN per workflow MVP-01..15. Current global state: **NOT RUN** (dependencies G7+). Mock-based success will be logged as component qualification only.

---

## G−1 — Project identity and strategic reorientation

**Objective:** transition platform identity from Pixel Office to Pixel Startup; establish naming conventions; preserve environment name, assets, history, licensing, deployment compatibility and operational data. Executed 2026-11-20 before continuing the gauntlet.

**Naming conventions (binding for all subsequent gates):**

| Identity | Value |
|---|---|
| Product / platform | Pixel Startup |
| Repository name | `pixel-startup` (remote GitHub rename = owner authorization REQUIRED, deferred; local checkout dir unchanged) |
| npm package | `pixel-startup` (was `pixel`) |
| Python package (G4+) | `pixel_startup` |
| CLI entry point (G19+) | `pixel-startup` |
| Visual environment | Pixel Office (in-app visual, sprites, rooms, characters) |
| Executive agent | Charon (OpenClaw harness) |

**Reference inventory & dispositions** (grep `pixel.office|pixel office|pixeloffice` across repo, excluding node_modules and historical migration docs):

| Reference | Disposition |
|---|---|
| `README.md` title + ES summary | **Renamed** to Pixel Startup with Pixel Office retained as visual environment and attribution note |
| `SKILL.md` frontmatter `name: pixel-office`, headings | **Renamed** to `pixel-startup` (no consumer references this skill name in-repo) |
| `CHANGELOG.md` scope line + new Unreleased entry | **Renamed** + reorientation recorded |
| `package.json` + lockfile `name` | **Renamed** `pixel` → `pixel-startup` (private package, no URL/API consumers) |
| `Makefile` IMAGE `pixel-office` → `pixel-startup` | **Renamed** build tag (local artifact; legacy local image left in place — no volume/data binding) |
| `.env.example` header | **Renamed** with environment clarification |
| `server.js` boot log "Pixel Office v3" | **Renamed** log string ("Pixel Startup v3 — Pixel Office environment") |
| `index.html` / `dashboard.html` `<title>` | Kept Pixel Office as primary (visual component) with platform suffix appended |
| `server.js` AI persona "…of Nocturnal Inc Pixel Office" | **Retained** (in-world office language, not product branding) |
| localStorage keys `pixel_office_*`, `pixelOfficeRooms` + export filename `pixel_office_config.json` | **Retained** — renaming would orphan saved dashboards (data loss, req 8) |
| Data files `pixel_actions.jsonl`, `pixel-config.json`, `pixel_collision.json`, `pixel_rooms.json`, `agents.json`, `agent_<id>_message.txt`, `pep_arrived_*` | **Retained** — persistent storage formats; no rename without migration plan (req 8) |
| `pixel_session` cookie | **Retained** — mid-flight browser sessions would drop (req 9) |
| `vault.js` `deviceIdentifier=pixel-office-1` | **Retained** — Vaultwarden device trust identity (req 8) |
| `PIXEL_*` env namespace (`PIXEL_DATA_DIR`, `PIXEL_SESSION_SECRET`, `PIXEL_API_TOKEN`, …) | **Retained** — bash scripts, Docker config and operators depend on them (req 7/9) |
| `docs/media/pixel-office-*.jpg` filenames | **Retained** (README image links unchanged) |
| `tests/api-baseline.js` synthetic password string | **Retained** (test fixture value only) |
| `docs/migration/G0/G1 docs` (written at gate time) | **Retained as historical record** — accurate for their gates; this section supersedes naming going forward |
| Remote repository name, service URLs, ports (19000) | **Unchanged** (req 9/10) |

**Deprecated naming conventions:** "Pixel Office" as the *platform* name (docs/npm/image tags) is deprecated as of this gate; valid only as the visual-environment name and inside retained identifiers above. New Python artifacts must use `pixel_startup` / `pixel-startup` exclusively.

**Backward compatibility requirements:** existing `.env` files need no changes; script invocations unchanged; saved dashboard exports still import; the legacy `pixel-office` Docker image keeps running its last built config; all 36 API routes keep their paths.

**Rollback considerations:** single commit revert restores the former identity in files (no data migrations occurred; nothing at rest changed). Lockfile name-only diff reverts identically. No remote/config/volume state touched.

**Acceptance evidence:** reference grep re-run post-edit → product-identity references updated; retained-identifier list matches grep (localStorage keys, cookie, deviceIdentifier, data files). `npm test` re-run after rename: presence 11 + baseline 44 = **55 PASS / 0 FAIL** (existing functionality intact). Boot smoke via baseline suite startup. Characters/sprites/credits/licensing untouched (CREDITS.md unchanged). No remote operations performed.

**Verdict: PASS.** Commit: `9fd9108`. Python naming (`pixel_startup` package, `pixel-startup` CLI) is established by this record and applies when G4/G19 create those artifacts — no placeholder code was written for components that do not yet exist.

**Follow-through (2026-11-20, owner-authorized):**
- Remote GitHub repository renamed to `bageera/pixel-startup` via `gh repo rename`; local remote URL auto-updated (now HTTPS); `git ls-remote` verified against the new name. Old-name URLs redirect server-side (req 9 preserved).
- Real `.env` provisioned (chmod 600, gitignored): PORT + non-secret template preserved verbatim from `.env.example`; `DASHBOARD_PASSWORD`, `PIXEL_API_TOKEN`, `PIXEL_SESSION_SECRET` generated in-shell (values never entered chat/tool output — retrieve/edit via `sed` or editor). Boot verified with the real config; authed `GET /api/vault/status` → 200 with bearer, 401 without; the three automation scripts carry matching `AUTH_ARGS` wiring.

---

## Gates G3-G20 — registered next

### G23 — Chief of Staff (owner-registered 2026-11-21; office feature: repo perspective + improvement requests)

**Criteria (owner ask):** a chief-of-staff channel that reads the public repo (github.com/bageera/pixel-startup), gives perspective on what can be added to the office (e.g. email access, a budget tool), and SUBMITS improvement requests. Steps, one gate each:
1. ✅ **Executed 2026-11-21 — LiteLLM `chat.completions` op (prerequisite for steps 2-3 AND the node-independent chat):** declared op on the G15 `LiteLLMProvider` (`infra.py`), model default from `Settings.litellm_model`, live POST transport via module factory `_make_post_client()` (httpx errors mapped to stdlib so shared normalization holds; synthetic `client=(method, path, body)` contract for tests); `Settings` honors the node env names via `AliasChoices` (`LITELLM_API_URL`/`LITELLM_API_KEY`/`LITELLM_MODEL`, plain `LITELLM_*` too, `populate_by_name=True`) so ONE secret feeds both backends. Response normalizes to `{content, model}`; unconfigured → BLOCKED message; 401/403 → InfraFailure; 5xx/timeout/malformed → InfraUnavailable; token never in results or error text (asserted). No new providers registered → the pinned `COUNT(*) == 16` provider-records test unchanged.
2. ✅ **Executed 2026-11-21 — `GET /api/v1/chief/advise` (owner auth) in `api/chief.py`:** reuses the sanitized G16 office-state snapshot + a public token-free GitHub read (repo meta, last 10 commit subjects, <=200 tree paths) through a module-level `_make_github_client()` factory; LLM via thin seam `_invoke_llm` → `registry.invoke_provider("litellm", "chat.completions")` (audited in the G5 usage audit); unavailable sources degrade HONESTLY (`sources.github: false` + note; advise still answers — never a fabricated repo read), unset/broken LLM → 503 envelope, no detail leak. Router registered BEFORE the gateway catch-all (native wins). 4 route tests (401 unauth; both contexts reach the LLM; honest degradation; 503-not-fabricated). **145 pytest / ruff+format clean / pyright 0 / pip-audit 0 / npm 57.**
3a. ✅ **Executed 2026-11-21 — plan → review → dispatch gate (ChatDev-pattern, owner-gated):** `payload.plan_required` on a delegation holds the task in a NEW `planning` status (CAS transition map, db.py; payload merge via new `TaskStore.attach` — NO schema migration), creates the pending approvals row (deterministic `request.plan.<digest>` id), and the LLM plan draft lands in `tasks.payload` (`GET /api/v1/delegate/{id}/plan`, owner auth; fetch-or-generate via the G23 chat op). Decision = `POST /api/v1/delegate/{id}/review` (owner session): approvals-row record is the ONLY authorization (plan TEXT is inert — test-pinned: stale already-decided approval → loud reconcile cancel, never dispatch); denied → cancelled, approved → shared `_dispatch_task` (entry-state aware: fresh/plan paths) through the honest B1/B5 contract. Mattermost, when configured, is the NOTIFICATION surface for the review request (idempotent comm.post; unconfigured → honest note; the channel text never authorizes — inbound voting stays deferred, B3 listener scope). 2 tests.
3b. ✅ **Executed 2026-11-21 — verify-before-done (independent re-poll, NO retry):** tasks whose payload carries `verify: true` get `_verify_result` at completion: ONE fresh harness `task.result` re-poll compared against the stored result — equal → new `verified` status (transition map: `completed → verified`); divergent/unavailable → stays `completed` with a verbatim `{verdict, detail, checked_at}` verdict in payload + audited; NEVER a re-dispatch (side effects are never re-run — G14 mandate, test-pinned via `submissions == 1`). Result retrieval returns the verdict + parses the stored result body on re-fetch. 3 tests.
- **Pending:** ③ requests route (task-row + owner-approval-gated `issues.create` with `GITHUB_TOKEN`, blocker B6) · ④ office UI Chief button + plan-review panel · ⑤ Chief as visible office character.
- **Tests + results (3a/3b):** `make python-test` → **150 passed** (was 145: +5 delegation tests), ruff clean+formatted, pyright 0 errors, pip-audit 0 vulnerabilities; npm 57/0. Transition-map addition is behavior-additive (completed stays terminal for unverified tasks).
4. Office UI: Chief button + render block in the owner-locked overlay (briefing styling, esc(), en/es keys).
5. Chief as visible office character (sprite + employee row via deterministic tooling) — polish, last.

**Tests + results (step 1):** 5 new tests (declared/normalized, messages validation, auth/5xx/malformed/live-factory, unconfigured BLOCKED, env-alias roundtrip); `make python-test` → **141 passed** (was 136), ruff clean+formatted, pyright 0 errors, `uv run pip-audit` → 0 vulnerabilities; `npm test` → 57/0.
**Limitations:** chat is invoked in-process via the provider contract; no HTTP route surface yet (steps 2-3); no approval-gating change (model invocation mutates no pixel state).

**Image (owner-authorized "commit and push" 2026-11-21):** `ghcr.io/bageera/pixel-startup-python:latest` = **sha256:fe439805b0f658e15f2dde52e7dec98d0020b97226d0559cec586d349145ccfa**, content = commit `7f140f9` (G23 steps 1-2). Smoke on the PULLED tag: /healthz 200 @9 s, / 200, /api/v1/chief/advise 401 unauth (route present + auth-gated). Pulling hosts: `docker pull` + `kubectl apply` unchanged; chat stays honest-503 via LITELLM until the owner adds `LITELLM_API_KEY` to `pixel-secrets`.

**Release v1.2.0 (2026-11-21):** tagged annotated `v1.2.0` @ `efc8824` (CHANGELOG folded: G22 packs + G23 chief rails/plan/verify; package.json + pyproject + uv.lock synced to 1.2.0). Image `ghcr.io/bageera/pixel-startup-python:latest` = **sha256:6f6a36417eb58469db2aaed3224f69ac004ba15ce8158d8590b7042a5bacc504**, content = `efc8824` (v1.2.0 tree). Pulled-tag smoke: /healthz 200 @9 s, / 200, plan route 401 unauth (G23-3 routes present). Pulling hosts: `docker pull` + `kubectl apply -f deploy/pixel-stack.yaml`.

**Post-release (2026-11-21):** CI pipeline landed — `.github/workflows/python-image.yml` (full gates → GHCR publish on `main` + `v*` tags, GITHUB_TOKEN only; repo default workflow token read/write); CI's first runs exposed three host-dependent tests (~/.hermes probes) → fixed hermetically (conftest fixture `hermes_home_qualified` + node fabricated `HERMES_HOME` + unused-local lint catch). New office environment: `garage` («Silicon Garage», the silicon-valley startup garage — whiteboard/neon/shutter/string lights/server rack/pizza boxes, bare concrete) via `tools/generate-environments.py`, validate BFS green, `tests/api-baseline.js` 4→5 packs (npm 58/0). README rewritten for the python-first reality + the onboarding checklist. **Image renamed** `ghcr.io/bageera/pixel-startup-python` → `ghcr.io/bageera/pixel-startup-backend`: the old package predates the workflow (manual push, linked to no repo) and Actions-access ACLs have no public API (2026-09 community discussion) → GITHUB_TOKEN 403 on push; a FIRST-TIME GITHUB_TOKEN push auto-creates the package linked to the repo with admin rights, so CI publishes the new name from then on (manifest/runbook/README updated; the old private package remains pullable until the owner deletes it with a delete:packages token). Repo Actions permissions were simultaneously flipped `local_only` → `all` (the `startup_failure` runs; GitHub-owned actions now allowed).

**G24 (2026-11-21, owner token attempt → fallback completed):** `GET /api/v1/health/services` (python) surfaces per-service connectivity from the provider registry (state/configured/connected + live `.probe()` where cheap — comms honestly stay candidate+unconnected until B3's live transport exists). `GET /api/move-in` (node): chief-human/direction/goals readiness — `startup:{direction,goals}` on `POST /api/config` (no-erase merge); dashboard "Startup & Move-in" card; dashboard auto-unlock via `GET /api/auth/check` (same admin `pixel_session`; the stale sessionStorage flag is no longer a gate); office HUD tag from public `GET /api/version`. Root cause catch: `POST /api/config` with empty `agents`/`rooms` arrays wiped the roster in memory+disk (Ghostface invariant only ran at load) — empty arrays now mean "not provided"; owner survives any partial save.

**G25 (2026-11-21):** dashboard.html modernized — 32-bit truecolor chrome (CSS-variable theme: deep-space bg, sky→violet gradient accents, glass panels, glow; alpha effects 8-bit palettes can't do) over the intentionally-preserved 8-bit art layer (sprites/grid `pixelated`); backend ops strip wired to the api/v1 surface (`/api/v1/health/services` + `/api/v1/office/state`), honest "python backend not reachable" in node-only mode; `scrolelbar` typo fixed. Verified live in-browser: login, auto-unlock, chars + map tabs, ops strip degraded mode.

**G26 (2026-11-21) — strangler rung, dashboard on native api/v1:** new `src/pixel_startup/api/config_v1.py` — `GET/POST /api/v1/config`, `GET/POST /api/v1/rooms`, `GET/POST /api/v1/collision`, `GET /api/v1/move-in`, `GET /api/v1/version` (pyproject), `GET /api/v1/auth/check` (office_write_auth: session/bearer/kiosk mirror) — all backed by the SAME OfficeState byte-parity stores (no node hop for the dashboard's editor surface). OfficeState.save_config upgraded to post-G24 semantics for BOTH compat and v1 (roster/room wipe guards, Ghostface invariant on every save, startup direction/goals no-erase merge; config files now carry `startup`). dashboard.html: `fetchApi(method, pathV1, pathLegacy, body)` helper tries the v1 route first, falls back to legacy compat on 404/5xx/offline origins; all editor calls migrated. Verified live against a REAL python origin (uvicorn): dashboard login → ops strip shows the registry service matrix (pixel_office_js qualified, rest honestly candidate/offline without secrets), version card, move-in green after API set, wipe-guard roundtrip. 157 pytest (+6) / 65 npm / pyright 0.

**G23 federation milestone (2026-11-21) — one capability, one execution authority:** `src/pixel_startup/capabilities/federation.py` — MANIFEST (email.send/email.classify → openclaw, messaging.post → openclaw, memory.share → pixel/chroma; owner, scopes, approval flag, reporting mode, rule notes) + `resolve()` (unknown capability is refused — pixel invents nothing at request time) + `make_federation_ingest` which rides the EXISTING delegation adapter: event id = correlation id = deterministic task id (provider retries replay, never re-dispatch), manifest approval flag drives the G23-3a CHO plan gate, missing employee on the owner harness is an honest "onboarding gap" failure (never coded around). Routes: `GET /api/v1/federation/manifest`, `POST /api/v1/federation/events` (bearer). Qualification (synthetic OpenClaw): one event → one task in planning; same event re-posted → replay, no second approval row; CHO approve → exactly one task.submit; another retry → still one send. No second email/memory/messaging engine built — the org consumes harness events. Live OpenClaw end-to-end remains B1 (owner secrets). 161 pytest (+4) / 65 npm / pyright 0.

**CI-published image (first green run):** the first-green CI run `44923853334` published `ghcr.io/bageera/pixel-startup-backend` (package auto-created by the GITHUB_TOKEN push — repository: `bageera/pixel-startup`, visibility: public, linked). Pulled `:latest` digest **sha256:16ed6824d73c1a4f12aee49b1bd99959452c71932d884698e88c6d1590de85af**, smoke on the pulled image: /healthz 200 @8 s, plan route 401 unauth. Tag `latest` needed an explicit `flavor: latest=true` (`latest=auto` does not apply to branch pushes — first green run shipped only `:main`). Note: a public package needs no docker login for pulls (`docker pull` + `kubectl apply -f deploy/pixel-stack.yaml`; only the OLD unpublished-name package is private).

### G20-B-REPAIR — Post-deployment integration gauntlet (2026-11-21, commit `5262bfa`)

**P0 deployed reality (honest scope):** no reachable cluster API from this host (local kubeconfig targets a dead 127.0.0.1:6443 — rancher-desktop); direct pod inspection BLOCKED. Evidence therefore comes from reproducing the EXACT deployed artifacts: the published image `sha256:7fe24e84…` (G20-A, pre-G20-B) run with `NODE_UPSTREAM` to a real node + the local checkout at HEAD. Sanitized traces below are from those live processes.
**Drift found (deployed vs local):** (1) `Dockerfile.python` at the deployed digest did NOT copy index.html/dashboard.html/assets — the python pod cannot serve the office itself, everything UI-ish rides the proxy; fixed at HEAD (now copies + serves `/_index`, `/assets/*`). (2) `deploy/pixel-stack.yaml` had **no `$HERMES_HOME` mount on either deployment** — the runbook's docker-run flow (`-v $(HOME)/.hermes:ro`) was never translated into the manifests. (3) Published image predates G20-B (task/command routes still proxied; old node leaks `$HERMES_HOME` in presence payloads).

**P1 briefing (traced end-to-end):** browser → python gateway `/api/briefing|/api/briefing/ai` → proxy → node handler → **LiteLLM `/v1/chat/completions` directly** (server.js 474-546). It invokes NO OpenClaw/Hermes agent, no harness discovery, deterministic aggregated briefing + LLM summary. "Briefing works" = LiteLLM reachable + `LITELLM_API_KEY` valid from the secretRef. **Recorded as a legacy briefing capability — not proof of Charon/OpenClaw connectivity.** Repro without the key: `502 {"error":"LiteLLM error","status":401,"detail":"Malformed API Key…"}` (sanitized; no key echoed, no harness state in the error body).
**P2 providers (matrix, from the repro + in-pod exec):** hermes_home — python probe reads `$HERMES_HOME` state files; WITHOUT the mount: observed online:false, roster empty (negative control, G20-B image, no mount); WITH a mounted valid home: online:true, platforms ['mattermost'], roster entries, office/state `observed-poll` true (positive control). openclaw_gateway — honest `registry` observed:null until a gateway is configured (B1 stands: never reached, never fabricated). Status normalization stays distinct: offline ≠ unregistered ≠ blocked; no heartbeat fabrication (test-pinned).
**P3 task creation (separated create vs dispatch):** office chat flow = `/api/ai/chat` (LLM parse, proxied) → actions → client moves → `/api/agent/:id/command` POSTs — these are NATIVE in G20-B and succeed with node STOPPED (consume-once 30 s TTL, ack idempotent, arrivals idempotent). The G14 delegation path (capability executor) already persists the task FIRST, then dispatches; with the harness unavailable it cancels honestly with the blocker recorded (`blocker: B1/B5`, `dispatched: false`) and never marks dispatched/running — existing tests (test_integration_delegation) + office_compat regression pins. Frontend error surfacing already exists (catch → bilingual message; nothing silent).
**Root causes:** (a) Hermes offline in pods = missing `$HERMES_HOME` mount (+ a latent env-name trap: `Settings` has NO env prefix — `pydantic` `hermes_home` maps to plain `HERMES_HOME`, not `PIXEL_HERMES_HOME`; the placeholder name would have silently failed — verified in-pod that with HERMES_HOME the probe goes online). (b) OpenClaw offline = expected B1 (gateway never configured) — not a regression. (c) Briefing-works is LiteLLM, orthogonal to harnesses. (d) Task-creation failure cannot be definitively located without the browser's network tab — the strongest candidate from the repro is authentication on the proxied authed surfaces (login cookie flow) or LiteLLM key/parse errors; the code-side contract (persist before dispatch, truthful states) is verified locally.

**P4 patch set (minimal, evidence-justified):** hermes-home ro `hostPath` volume + `HERMES_HOME=/hermes-home` env on BOTH deployments (runbook: sync the host dir to /data/hermes-home or run the pod on the hermes host); Dockerfile.python builder copies frontend + assets; presence-offline honesty regression pin (unmounted home → observed online:false, empty roster, never fabricated online). No security weakening; no new agents; bindings untouched.
**P5 regression:** 136 pytest (new presence-honesty pin) · ruff clean+formatted · pyright 0 errors · npm 56 · four environments verified live earlier in G20-B (catalog + assets 200s). All offline/unavailable states truthful (negative + positive controls above).
**Deploy/push:** WITHHELD per mandate. To apply on the deployment host: build/push the updated python image from this commit, mount/sync `~/.hermes` → hostPath `/data/hermes-home`, `kubectl apply -f deploy/pixel-stack.yaml`. Remaining blockers: B1 (OpenClaw gateway), browser Network-tab capture for the exact task-creation 4xx if it persists, `pip-audit`. Nothing to roll back: local commits only (`afe2040`, `5262bfa`).

**Image push (owner-authorized 2026-11-21):** `docker tag pixel-python-ga2:latest ghcr.io/bageera/pixel-startup-python:latest && docker push` → **latest: digest: sha256:f67d5f332fc34b75519ee86a7da47887e0fa71b7b6279e25a915749c1e252c5d size: 856** (240 MB, 2 layers fresh: 7aefc0f7d119, b9973a93dad3). Image content = commit `5262bfa` tree (G20-B routes + frontend in image). Git commit push NOT performed — `main` stays ahead-of-origin until authorized. Deploy-host steps remain: sync hermes home → `/data/hermes-home`, `kubectl apply -f deploy/pixel-stack.yaml`.

### G21 — End-to-end MVP acceptance (2026-11-21)

**Final quality run (exact commands, this checkout):**
- `uv run pytest` → **114 passed**, 0 failed
- `uv run ruff check src tests` → **All checks passed**
- `uv run pyright src tests` → **0 errors** (1 accepted jsonschema-stubs warning)
- `npm test` → **56 passed, 0 failed** (presence + API regression)
- `make contracts` → **39 checks passed**, 0 failed
- `docker build -f Dockerfile.python` → clean, 239 MB non-root image
- `uv run python tools/perf_benchmark.py` → measured table in G18
- `sh tools/cutover_rehearsal.sh` → **REHEARSAL PASS**
- `npm audit --omit=dev` → 0 vulnerabilities; migration tests + provider-auth tests + security matrix all inside `uv run pytest` counts above
- No browser regression suite exists beyond the node API suites (`frontend browser regression tests` = npm suite; noted as an honest environment limit: no real-browser harness installed on this host)

**MVP acceptance matrix** (verdicts are per-row; `LIVE` = real service evidence on this host, `SYN` = synthetic-qualified, `BLOCKED` = missing owner-controlled dependency):

| MVP | Acceptance item | Verdict | Evidence |
|---|---|---|---|
| 01 | Owner communicates with real Charon | **BLOCKED B1** | no OpenClaw gateway on host; Charon identity/binding chain ready (G9) |
| 02 | Charon retrieves authorized company info | SYN | `vault.credential.op` scoped retrieval, approval-gated, credentials redacted |
| 03 | Charon retrieves authorized household info | SYN | cross-scope rules enforced; household ref under company request denied |
| 04 | Charon delegates to Hermes | SYN / **BLOCKED B5** | delegation protocol synthetic-green; A2A exists but not running |
| 05 | Hermes returns verified result | SYN / **BLOCKED B5** | `task.result` contract tested; live requires A2A |
| 06 | Office displays real runtime activity | **LIVE** | G16 smoke: hermes observed-poll live (mattermost, 2 sessions); openclaw registry-candidate honestly labeled |
| 07 | Authorized memory retrieval succeeds | SYN | G11 scoped search + provenance; live **B4** |
| 08 | Authorized MinIO round-trip | SYN | G12 sha256-verified round-trip; live **B4** |
| 09 | Executor operates without credential disclosure | SYN→LIVE | leaky-adapter redaction proof; Vaultwarden live **B2** |
| 10 | Cross-scope access denied | **LIVE (tests)** | G10/G11/G12/G14 suites |
| 11 | External email draft requires delivery authorization | SYN | no send without `{status: approved, by}` owner record |
| 12 | Persistent assignments survive restart | **LIVE** | restart-recovery tests (G6/G10/G14) on real store |
| 13 | External service failure accurately represented | **LIVE** | registry `candidate`/`observed: null` honesty asserted |
| 14 | Runtime identity impersonation rejected | **LIVE (tests)** | spoof + unverified-binding + revoked-binding denials |
| 15 | Python cutover + JS rollback demonstrated | **LIVE (rehearsal)** | G20 REHEARSAL PASS ×3 |
| 16–19 | Four environments load (continental default) | **LIVE (tests+rehearsal)** | G22 suites; mechanic/pirate/treehouse manifests+assets served |
| 20 | Environment switching preserves identity/assignments/authz | **LIVE (tests)** | G22 persistence suites |

**Remaining blocker register (requires OWNER action — no credential requests in chat):**
- **B1 OpenClaw** — reachable gateway + auth + supported agent/session interface + Charon native identity. Unblocks G7-live, G14-live, MVP-01.
- **B2 Vaultwarden** — authorized API credentials (endpoint ALIVE: HTTP 200/27 ms). Unblocks G10-live rows.
- **B3 Communications** — Mattermost token + approved test destinations + SMTP/transport creds. Unblocks G13-live.
- **B4 Memory/storage** — ChromaDB + MinIO endpoints (+ WireGuard routing). Unblocks G11/G12-live.
- **B5 Hermes submission** — **enable the already-installed A2A platform** (A2A_PORT + peer token via `hermes gateway setup`), then wire-office binding verification. Unblocks G14-live, MVP-04/05.
- **G17-F1** — rotate the historical credential in `075e1f1` (+ optional history purge, needs separate rewrite authorization).
- **G17-G1 — CLOSED 2026-11-21:** pip-audit installed as dev dependency (`uv add --dev pip-audit`); `uv run pip-audit` → **No known vulnerabilities found** (single benign skip: the project itself — not on PyPI — not auditable); wired into `make python-test` so every gate runs it. JS side stays `npm audit` 0.
- Each blocker has a safe resolution path, requires owner-controlled secrets/endpoints delivered OUTSIDE chat (approved secret mechanism), and ALL independent work continued regardless.

**Final commit inventory (local, unpushed this gauntlet):** `7c273c9` G11 · `8b63444` G12 · `876534a` G13+G14 · `125a0b5` G15 · `7285c36` G17 · `efeb74a` G18 · this commit G19/G20/G21 (+ `deploy/`, `tools/backup.py`, `tools/cutover_rehearsal.sh`, `tools/perf_benchmark.py`, docs).

**Verdict: PASS-SYNTHETIC overall, with per-row verdicts above** — the platform is code-complete and synthetic-qualified end-to-end; production readiness requires the owner-controlled live rows (B1–B5) and explicit cutover authorization. Deployment promotion is NOT authorized by this run.

### G19 — Deployment and backup qualification (executed 2026-11-21)

**Packaging:** `Dockerfile.python` (uv + uvicorn, **non-root UID 1001**, `/healthz` HEALTHCHECK) → built `pixel-python:g19` = **239 MB** (vs 332 MB combined JS image). K8s manifests `deploy/pixel-stack.yaml`: JS baseline deployment + Python deployment side-by-side, single PVC with enforced single-writer cutover, resource req/limits, liveness+readiness probes, `preStop` drain, runAsNonRoot securityContexts, `pixel-secrets` SecretRef, ingress-restricting NetworkPolicy. JS deployment capability preserved untouched.
**Backup (G6 addendum):** `tools/backup.py` — verified conventional method: SQLite **backup-API** snapshot (consistent across live WAL) + `integrity_check` + SHA-256 sidecar; restore re-verifies sha256 (corruption → refusal) and integrity, clears stale WAL sidecars, snapshot semantics proven (post-backup writes NOT in snapshot). Tests: `tests/test_backup_tool.py` (2, incl. bit-rot refusal).
**mdrc: BLOCKED** — binary not installed on this host; sqlite-mdr ships a research pack/unpack *library*, not a qualified production CLI. Per mandate the verified conventional path is retained; mdrc may only replace it after shipping a qualified CLI + restore guarantees. Never replaced the known-good with an unverified archive.
**No production deployment performed.** **Verdict: PASS** (clean build, deployment test, backup/restore pass; rollback documented in `docs/deployment-runbook.md`).

### G20 — JavaScript-to-Python cutover (2026-11-21; **cut-over EXECUTED and IMAGE PUBLISHED**)

**Deploy (owner: "deploy"):** `3857e3c` pushed to origin; **`ghcr.io/bageera/pixel-startup-python:latest` = sha256:7fe24e840df5bcf5a571b92b6214cdc3cf8f4d22c2a197a36bdc0ebb26f354de** (239 MB, includes the gateway cut-over). Container smoke: boot ~10 s to listening, `/healthz` 200, 16 providers, logs clean. Pulling hosts: `kubectl apply -f deploy/pixel-stack.yaml` (public `pixel-office` Service → python; node internal-only `pixel-legacy-node`; python proxies un-migrated routes through it).


**Owner authorization:** "lets deprecate the node" — the G20 activation decision recorded here per the standing invariant ("activation is the cutover decision, recorded in the ledger when Jon authorizes").
**Strangler flip:** `api/gateway.py` catch-all (registered LAST — native routes always win) fronts the entire `/`+`/api` surface: unimplemented routes proxy to the deprecated node (`NODE_UPSTREAM`) with cookie/bearer/header passthrough (incl. Set-Cookie so node's `/api/auth/login` keeps issuing sessions), or answer an honest 501 deprecation envelope when no upstream is set; the G2-hardened node files (`/server.js`, `/.env`, `/package.json`) stay 404 and never proxy; upstream unreachable → 502.
**Cookie parity CLOSED:** `security/auth.py` now verifies BOTH formats — legacy node `<issued_ms>.<hex>` (HMAC `pixel-office-session:ts`, 12 h TTL, exact) and the python `v1.<expiry>.<hex>`; end-to-end test proves a node-issued cookie authorizes a privileged python route, and expired/forged/malformed variants fail closed.
**Manifest flip (`deploy/pixel-stack.yaml`):** public Service `pixel-office` now selects the PYTHON deployment (env `NODE_UPSTREAM=http://pixel-legacy-node`); node is the internal-only `pixel-legacy-node` Service labeled `deprecated: node` — rollback remains documented in `docs/deployment-runbook.md` (scale python to 0, restore backup, js serves untouched).
**Node marked deprecated:** boot banner in `server.js` ("strangler mode; migrate routes to python and retire this process once /api is fully native").
**Tests:** `tests/test_gateway.py` (6: native-routes-wins with ZERO upstream calls, passthrough+Set-Cookie, hardened files 404, no-upstream 501, node-cookie authorizes python route, JS TTL/forgery rejects) + updated health/error-envelope + security-matrix expectations (unknown 404 / deprecated 501 / method 405).
**Suite:** 120 pytest · ruff clean · pyright 0 errors · npm 56.
**Verdict: PASS (cutover to python-as-front-door); route-by-route native migration is follow-up work — node stays as the deprecated upstream until each `/api` route lands natively, then the process retires.**

**Route coverage (parity map):** JS serves `/api/config`, `/api/environments`, `/api/presence`, `/api/ai/*`, `/api/log`, `/api/briefing*`, `/api/messages`, `/api/agent/:id/*`; Python serves `/healthz`, `/api/environment` (GET/PUT authed), `/api/v1/office/state` (G16), `/api/v1/capabilities/execute` + `/api/v1/delegate/{id}/result` (G10/G14 persistence/auth/executor). The office frontend consumes BOTH today (G16 additive chips; JS routes untouched) — full route parity is the G20-remaining item and is tracked as the cutover prerequisite, not a regression.
**Rehearsal (`tools/cutover_rehearsal.sh`, isolated temp data dir, single-writer windows):** seed JS-era state → JS up + `/api/config` captured → verified snapshot → JS stopped → **Python up on the SAME data dir** (migrated roster from `agents.json`; 16 provider rows; schema v5) → owner-authed workflow exercised (environment select persists; office read model serves migrated roster) → **rollback** → JS up → owner state byte-intact. **REHEARSAL PASS** (strict `set -e`; verified 3×).
**Data integrity:** JSON owner state never rewritten by Python (additive sqlite files only); backup verified before any Python boot.
**Verdict: PASS-SYNTHETIC** (isolated rehearsal + rollback demonstrated; production promotion remains pending explicit owner authorization).

### G20-B — Node retirement gauntlet (executed 2026-11-21; commit `e9e7ccd` + this record)

**Owner mandate:** retire node; preserve G20-A gateway. **Nothing deployed, pushed, merged, or removed** — legacy node deployment untouched.

**1. Route audit (all proxied node routes + consumers):** MVP-required natives = `/api/auth/login` (frontend login flow), `/api/config` GET/POST, `/api/rooms` GET/POST, `/api/collision` GET/POST, `/api/messages`, `/api/agent/:id/{command,command/ack,arrived,move}`, `/api/log` GET + `/api/log/clear`, `/api/presence`, `/api/environments`. Non-MVP, stays node-parity/501: `briefing*`, `ai/{chat,status}`, `email/{accounts,unread,inbox,:id}` (frontend consumers guarded `|| []`), `sync/status`, `contacts/:id{,/interactions}`, vault reads. Hardened 403s ported verbatim: S-02 `vault/item/:id`, S-03 `email/send`, S-04 `contacts/search` + `orgs/search`.

**2. Native implementation (`api/office_compat.py`, single module):** byte-parity with server.js handlers over the SAME data files (pixel-config.json, agents.json, map.json, pixel_actions.jsonl, agent_<i>_message.txt, pep_arrived_* markers => zero data migration, operational data preserved), same boot migration (drop "Off Duty", ensure Ghostface — server.js 239-284), map merge + updatedAt semantics, 30 s consume-on-read command TTL, log last-50 + any-bad-line→[] rule, login rate limit 5 / 15 min. **Python now issues sessions**: `v1.<expiry>.<hmac>` cookie `HttpOnly; SameSite=Lax; Path=/; Max-Age=43200`; legacy node cookies stay verifiable during transition (no login dependency on node remains — no legacy session can be created that python rejects). Kiosk mirror scoped: office-UI writes fail open ONLY under explicit `PIXEL_ALLOW_NO_PASSWORD` (node `AUTH_ENABLED` parity); executor/capabilities/`environment PUT` keep strict `require_auth` even in kiosk (test-pinned).
**3. Presence native:** G8/G7 harness probes shaped like integrations.js `{hermes, openclaw, agents[], checkedAt}`; security-improvement divergence: node's roster leaked the internal gateway URL into `detail` and probes leaked `$HERMES_HOME` — python redacts both (test asserts no `http://`, `127.0.0.1`, `/Users/`, `:18789` in the payload).
**4. Assets:** `/` + `/dashboard.html` FileResponses, `/assets/*` StaticFiles mount (traversal-safe), `/favicon.ico` 404 (node `PUBLIC_FILES` parity). `Dockerfile.python` copies index.html/dashboard.html/assets.
**5. Gateway hardening (G20-A preserved):** blocklist matched on decoded paths (encoded `%2e`/`%2f`/pct-encoded filename bypass attempts stay 404 — tested); redirect `Location` never passes through (only set-cookie/www-authenticate cross the boundary — tested: node 302's internal location doesn't reach clients); no upstream → honest 501; unreachable → 502. Duplicate side-effect protection: command consume-once, ack idempotent, arrived marker writes idempotent (tested).
**6. NODE_UPSTREAM removal qualification:** `tests/test_gateway.py::test_no_upstream_honest_501` + `tests/test_office_compat.py::test_node_upstream_absent_leaves_mvp_native` — with NO upstream configured every MVP route answers 200 natively (login → cookie → config PUT → commands/messages/log/presence/environments/office-state → `/`); only non-MVP surfaces answer 501. K8s manifests may drop `NODE_UPSTREAM` + the legacy Service once the owner retires node — NOT done here.
**7. Live MVP demo, node stopped (pgrep node = 0, uvicorn :19061):** `/healthz` 200; `/` serves the office UI; `/api/environments` → all FOUR packs with `continental` default:true (mechanic, pirate, treehouse) + all four `assets/environments/*.png` HTTP 200 (9.2–9.7 KB); `/api/config` → real roster (Curator, Charon, Wardog, Fixer, Tailor, Ghostface), 3 rooms, persisted environment; `/api/presence` → LIVE hermes (gateway running, mattermost channel, CLI session roster entry), openclaw honestly offline (no gateway); `/api/messages` → 6-slot contract shape; backend log shows every MVP route 200 with node absent. Runtime `data/pixel-config.json` environment restored to `continental` (file + app_settings store) after rehearsal/test writes — owner default preserved.
**8. Rollback re-run:** `tools/cutover_rehearsal.sh` → **REHEARSAL PASS** again on this build (JS capture → verified snapshot → python migrates same dir → owner-authed environment workflow → rollback → JS serves owner state byte-intact). Runbook unchanged and independently tested.

**Suites:** 135 pytest (+16 compat; gateway tests updated: MVP routes native with no upstream) · ruff clean + formatted · pyright 0 errors · npm 56 · contracts included in pytest counts. **Verdict: PASS** (removal documented, not executed; deploy/push withheld per mandate).

### G18 — Performance and reliability (measured 2026-11-21, host: macOS/arm, this repo checkout)

Method: `tools/perf_benchmark.py` — both backends on isolated ports/data dirs, 50-sample latencies, 10-thread concurrency, per-worker SQLite connections for contention (no shared-handle fudge). Full JSON: run the script; figures below are the actual output.

| Measurement | JavaScript baseline | Python (1 emp) | Python (5) | Python (10) | Python (25) |
|---|---|---|---|---|---|
| Cold start (→ first 200) | 0.66 s | 0.29 s | 0.34 s | 0.34 s | 0.33 s |
| Warm probe p50 / p95 (ms) | 0.53 / 1.65 | 0.44 / 0.67 | 0.34 / 0.63 | 0.32 / 0.55 | 0.49 / 1.30 |
| Office state p50 / p95 (ms) | — (JS has no equivalent) | 0.67 / 1.36 | 0.49 / 0.77 | 0.61 / 0.89 | 0.69 / 1.06 |
| 10 parallel worst (ms) | 6.69 | 4.82 | 2.76 | 3.22 | 4.53 |
| Idle RSS (KB) | 73,680 | 59,184 | 58,880 | 59,392 | 58,944 |

SQLite (Python store): 500 sequential task inserts **90.1 ms** (~5.5 k/s); 800 inserts across 8 concurrent connections **121.5 ms** (~6.6 k/s, 8/8 workers OK) — WAL + `BEGIN IMMEDIATE` handles contention without data loss.

Failure injection: all external providers unconfigured → office state still serves 200 with `observed: null` honest rows (1 harness row reported unavailable, no fabricated online states); browser reconnect is stateless polling (no session corruption); restart recovery covered by G6/G10/G14 suites (provider rows + tasks + executions persist).

Container footprint: deployed image 332 MB (combined node+python, current stack). MVP resource budget implied by measurements: **≤ 128 MB RSS per Python backend process**, ~30 MB for a 25-employee office state workload spike; CPU near-idle at measured request rates.

**Deployment-blocking regressions: none.** No optimization performed beyond this gate (none warranted by evidence).
**Verdict: PASS** (measured workloads only).

### G15 — Existing infrastructure integrations (executed 2026-11-21)

**Files:** `integrations/infra.py` (Odoo, Home Assistant, Rundeck, Grafana, GitHub, FreshRSS, LiteLLM, Calendar — one normalized read-only pattern), `config.py` (endpoint/token fields), `main.py` (`_register_infra`, registry rows 8×candidate), `tests/test_integration_infra.py` (5).
**Implementation:** declared capabilities are the ONLY dispatch surface (`invoke` refuses undeclared ops — write ops deliberately absent); injected transport for synthetics; typed normalization (401/403→InfraFailure, 5xx/unreachable/malformed→InfraUnavailable); unconfigured → BLOCKED, never fabricated.
**Results:** 5/5 synthetic (read-only op normalized, undeclared write refused, unconfigured BLOCKED, auth/5xx normalization, unreachable; boot sync → 16 provider_records rows; office read-model strips endpoints).
**Live rows: BLOCKED** — none of the 8 endpoints is configured on this host. **Verdict: PASS-SYNTHETIC.**

### G17 — Security qualification (executed 2026-11-21)

**Evidence:** `tests/test_security_matrix.py` (4: unauth route matrix + no-echo, cookie expiry/tamper, runtime leak-scan of office/state rows, staleness contract) on top of the G10/G14/G22 adversarial suites. **Report: `docs/security-qualification.md`** (19-row dispositions table).
**Fixed this gate:** XSS-hardened the G16 status chips (all backend interpolations HTML-escaped client-side).
**Findings:** no unresolved critical/high in enabled capabilities. Owner items recorded: rotate the historical credential in `075e1f1` (+ optional history purge, needs authorization). Python dep scan **CLOSED 2026-11-21**: `pip-audit` installed (dev dep, `make python-test` gate) → No known vulnerabilities found; `npm audit` 0 vulnerabilities.
**Verdict: PASS with dispositions.**

### G11 — Memory Palace / ChromaDB (executed 2026-11-21)

**Objective:** qualify the remote ChromaDB service as institutional-memory provider.
**Files:** `integrations/chromadb.py`, `config.py` (chroma_url/token/timeout), `main.py` (`_register_memory`), `tests/test_integration_chromadb.py` (7).
**Implementation:** documented REST v2 surface only (`heartbeat`, `collections`, scoped `query`); scope isolation enforced server-side (`where: {scope}`) AND client-side (metadata mismatch dropped); provenance (provider/collection/document_id) on every hit; errors normalized (401/403 → IntegrationDenied, 5xx/timeout/malformed → ProviderUnavailable); token never logged or repr'd (asserted); configurable endpoint+auth+timeout; unconfigured → `not configured; live rows BLOCKED`. Production collections are never rebuilt or overwritten (query-only adapter).
**Results:** 7/7 synthetic (authorized search, scope filter in request body, cross-scope drop, household filter, auth fail-closed, 503/malformed/unconfigured, secret-free errors/repr, invoke contract + arbitrary-op refusal). Registry: `chromadb` candidate/detailed; boot health-promotes to QUALIFIED **only when a URL is configured**.
**Live rows: BLOCKED** — no endpoint configured anywhere in the repo (verified name-scan); non-destructive reachability probe impossible without guessing, which the mandate forbids. **Verdict: PASS-SYNTHETIC.**

### G12 — MinIO object storage (executed 2026-11-21)

**Files:** `integrations/minio.py`, `config.py` (minio_endpoint/bucket/access/secret), `main.py` (`_register_storage`), `tests/test_integration_minio.py` (9).
**Implementation:** `S3Client` protocol + minimal SigV4 path-style client (stdlib hmac/sha256, zero deps); scope-prefix chokepoint `validate_key` (all ops route through it): traversal/`\`/null/absolute refused, prefixes `company/|shared/|household/` enforced against caller scope; SHA-256 verified on GET (tamper → fail closed), X-Amz-Meta-Sha256 written on PUT; 25 MB limit; 401/403→AccessDenied, 404→FileNotFoundError, 5xx/unreachable→StorageUnavailable; duplicate PUTs idempotent (same digest); unconfigured → BLOCKED.
**Results:** 9/9 synthetic (roundtrip+integrity, traversal/prefix guards, cross-subject isolation incl. household→company refusal, tamper+oversize, unauthorized/unavailable/BLOCKED, scoped listing+idempotent dup, revocation mid-flight, sigv4-shape + no-secret-in-source).
**Live rows: BLOCKED** — no endpoint configured. **Verdict: PASS-SYNTHETIC.**

### G13 — Communications (executed 2026-11-21)

**Files:** `integrations/comms.py`, `config.py` (mattermost_url/token/channels), `main.py` (`_register_comms`; registry rows `mattermost` COMMS_INTERNAL + `email` COMMS_EXTERNAL), `tests/test_integration_comms.py` (5).
**Implementation:** Mattermost — channel ALLOWLIST enforced first, correlation-id idempotent delivery (replays add no POST), single 429 retry, fail-closed normalization, unconfigured BLOCKED. Email — compose is harmless by construction (`pending_approval`), recipient validation + dedupe, attachment name/type/size allowlist, subject header-injection refusal, `send()` requires `{status: approved, by: ...}` owner approval, replay dedupe, no send merely because a draft exists.
**Results:** 5/5 synthetic incl. cross-scope channel refusal, forged-Bcc and forged-From refusals, approval-bypass attempts denied.
**Live rows: BLOCKED (B3)** — no Mattermost/email creds on this host. **Verdict: PASS-SYNTHETIC.**

### G14 — Cross-harness delegation (executed 2026-11-21)

**Discovery (mandate-first):** the installed Hermes ships a documented A2A (JSON-RPC/SSE) inbound platform (`plugins/platforms/a2a/`: `message/send`, `message/stream`, `tasks/*`, agent-card discovery, optional peer tokens, localhost-only default) — but it is NOT running (`gateway_state.json` platforms = `['mattermost']`). Verdict recorded, nothing fabricated: **the submission mechanism exists and is implementable; it is disabled on this host → B5 resolution is ONE owner action (enable A2A port + peer token)**.
**Files:** `capabilities/delegation.py`, `capabilities/registry.py` (`delegate.hermes.task`), `capabilities/api route GET /api/v1/delegate/{task_id}/result`, `main.py` wiring (adapter + retriever per-container), `persistence/db.py` schema **v5 `task_results`**, `tests/test_integration_delegation.py` (10).
**Protocol:** rides the G10 executor (workload auth, verified-binding identity, grants, scope, replay protection, audit). Task persisted (created) BEFORE dispatch; deterministic `task.id = sha256(correlation|target|objective)` → duplicate submissions replay, dispatch happens exactly once; ack → authorized → dispatched → running; unconfigured/unsupported harness → **cancelled** with `blocker: B1|B5` (persisted truth, no fabricated ack); result retrieval completes → completed + `task_results` row; dispatched-without-ack on restart → `reconcile_stale` cancels honestly (never marks success); uncertain outcomes stay uncertain (no blind retries).
**Results (mandated scenarios):** owner→employee dispatch+result ✓; Charon→OpenClaw ✗→BLOCKED B1 asserted; Hermes result retrieval + delivery ✓; office activity carries delegation ✓; authorization failure (missing grant) ✓; unavailable harness ✓; duplicate submission (1 dispatch) ✓; restart persistence + reconciliation ✓; revoked runtime binding ✓; unsupported harness operations remain `supported: false` (honest).
**Live rows: BLOCKED** — B1 (no gateway) and B5 (A2A not enabled). **Verdict: PASS-SYNTHETIC.**

### G16 — Frontend compatibility and live activity (criteria 2026-11-20, executed same session)

**Criteria (registered pre-execution):**
- C16.1 One sanitized read model (`GET /api/v1/office/state`): employees (lifecycle, binding-verified as BOOLEAN ONLY — no grants/binding internals), provider health (states — no endpoints/secrets), observed harness status vs registry state (explicitly distinguished), task progress, pending approvals, sanitized activity history.
- C16.2 Status driven by verified polling (mandate allows polling); response carries `generated_at`; no invented activity — sprites keep behaving exactly as the existing simulation does, and backend absence degrades silently.
- C16.3 Frontend consumes it in the EXISTING `#runtimeChips` overlay (no layout/identity change); stale data detected client-side (>30 s → STALE marker); polling failure keeps the office fully functional.
- C16.4 Existing frontend compatibility preserved: no JS API route removed or reshaped; npm regression suite stays green.
- C16.5 Charon→OpenClaw and Hermes status rows display the honest adapter verdict (candidate/BLOCKED vs live-qualified) — observed and registry states never merged.

**Implementation:** `api/office_state.py` (single handler, public read, sanitized), `main.py` router include, `index.html` polling strip, `tests/test_office_state.py`.

**Execution evidence (2026-11-20/21):**
- Read model verified live on this host: `GET /api/v1/office/state` → employees (id/name/sprite/kind/lifecycle/harness + `binding_verified: bool` ONLY — grants, verified_binding internals, asserted_id and provider endpoints stripped, asserted), tasks (counts + active, objective text stays internal — asserted), approvals (pending), providers (state only), sanitized activity (audit rows are G10-sanitized by construction), `generated_at` for stale detection.
- **Observed vs registry never merged (C16.5):** live smoke — `hermes_home` → `source: "observed-poll"`, `observed.online: true`, mattermost connected, 2 live sessions (real Hermes state on this host); `openclaw_gateway` → `source: "registry"`, `observed: null`, registry_state `candidate` (no gateway exists — honest, no invented activity).
- Frontend (C16.3): `#officeStatusChips` in the existing `#ui` overlay; 15 s verified polling; client-side STALE marker at >30 s; backend-unreachable → chip only if the backend was previously reachable, otherwise the office renders exactly as before (no invented activity, sprite simulation untouched).
- Compatibility (C16.4): zero JS routes removed/reshaped; `npm test` 56/56; python `uv run pytest` → **73 passed** (3 G16: shape+sanitization+provenance, counts-from-stores, public-read).
- Reconnect handling: stateless polling (no sockets, no session corruption — reconnection re-fetches a fresh snapshot).
**Limitations:** Charon→OpenClaw live runtime mapping remains BLOCKED (no gateway — displays the honest registry verdict `candidate`); no websocket push (polling per mandate); task objective text deliberately not exposed until a UI needs it.
**Verdict: PASS** (component + this-host live read model; frontend regression green).

### G10 — Secrets and capability execution (executed 2026-11-20; owner-authorized scope)

**Objective:** trusted capability executor over the G9 identity proof chain; 8-step pipeline (authenticate → resolve verified binding → capability+scope validation → authorization/approval → credential via declared provider → constrained adapter → redaction → sanitized attributable audit). Synthetic-credential track only; Vaultwarden live rows stay BLOCKED.

**Files:** `src/pixel_startup/capabilities/{__init__,registry,providers,executor}.py`, `api/capabilities.py` (`POST /api/v1/capabilities/execute`), `main.py` (executor wiring + `sync_provider_records` at lifespan end), `persistence/db.py` (schema v4: `executions`, `capability_approvals`), `persistence/store.py` (`EmployeeStore.revoke_binding`, `sync_provider_records`), `tests/test_capabilities.py` (15 tests).

**Pipeline architecture (mandate §G10):**
- **Declared-only capability registry** (`registry.py`): `synthetic.report` (read, no side effect), `synthetic.restricted.write` (approval-gated side effect), `vault.credential.op` (scoped credential consumed INSIDE the boundary; approval-gated). Unknown ids fail closed before any resolution — arbitrary credential lookup has no surface.
- **Workload authentication:** token map with `secrets.compare_digest`; no default identity; empty map entry can never authenticate. API layer re-uses `require_auth` evidence: bearer → workload re-verified by the executor's token map; session cookie → pre-authenticated `workload.owner` (require_auth already proved it in-process).
- **Identity = verified binding proof only:** `resolve_identity()` reads `runtime_bindings` (active=1) and requires the employee's `verified_binding` to carry that exact binding_id; lifecycle must be actionable (available/working/waiting/on_demand). Caller-supplied `employee_id` is corroboration: a mismatch is a spoofing attempt → denied (asserted); an UNVERIFIED binding (bind() without verify()) proves nothing (asserted).
- **Capability + scope:** capability must be in the employee's `authorization_context` (granted by owner in G6 schema — discovery auto-grants nothing); scope must be valid for the capability AND match the employee's operational scope (`shared` covers both scopes). Cross-scope credential refusal enforced additionally at the reference level: refs are scope-namespaced (`company.*`), a `household.*` ref under a company request is denied (asserted).
- **Approvals before side effects:** `capability_approvals` (schema v4) checked BEFORE execution; refused executions leave no completed rows (asserted zero side effects on approval failure).
- **Replay/idempotency:** `executions.request_key` PRIMARY KEY; completed → recorded result returned with `replayed: true` (no re-execution); terminal-but-failed keys refused; new keys execute normally (asserted).
- **Credential resolution with NO fallback:** only the capability's DECLARED provider id is consulted — a failing provider is terminal ("provider failed; operation refused"); an undeclared-but-mapped provider is never consulted (call-count asserted 1 and 0); `BlockedVaultwardenProvider` raises the honest BLOCKED condition and can never fabricate a value.
- **Redaction:** result deep-scan replaces any string containing a credential value + redacts secret-named keys (`[REDACTED]`); proven with a deliberately leaky adapter — value absent from HTTP result, `executions.result` row and ALL audit payloads; audit detail carries only redaction counts.
- **Prompt-injection inertness:** payload content is untyped data to the pipeline — authorization fields come from registry lookups and store rows only; an injection attempting to grant capabilities/escalate scope/claim another employee changes nothing (asserted denied), while legitimate grants execute with the payload carried verbatim (asserted).
- **Fail closed everywhere:** authentication failure, unknown capability, missing grant, bad scope, unapproved, unverified/revoked binding, provider failure, adapter absence, malformed requests — all raise `CapabilityDenied` → HTTP 403 with a single generic message ("capability execution denied"); nothing sensitive is echoed; every denial audited with attribution to the RESOLVED employee id.

**Revocation paths (both mandated):** `EmployeeStore.revoke_binding` deactivates the lease AND withdraws `verified_binding` → executor refuses the binding afterwards (asserted); credential revocation (seed → revoke → execute) fails at the provider step with no fallback (asserted).

**G6 leftover closed:** `sync_provider_records` snapshots the live registry into `provider_records` at the end of every lifespan (upsert-only; no other data touched) — verified across restart (4 rows, states preserved; roster row count intact).

**Schema migrations:** v4 = `executions(request_key PK → replay protection, employee FK, capability, scope, status, result, workload, timestamps)` + `capability_approvals` + employee index. Versioned; upgrade-in-place verified (`migrate() == 4`, idempotent).

**Acceptance evidence — actual results:** `uv run pytest` → **70 passed** (15 G10: positive+attribution, auth fail-closed ×2, caller-ID spoof/unverified-binding, binding+credential revocation, escalation, scope isolation incl. cross-scope ref, approval-before-side-effect, replay/idempotency/terminal-refusal, secret-redaction across result+executions+audit with a leaky adapter, no-fallback ×2, honest BLOCKED provider, prompt-injection ×2, malformed+retired-lifecycle, provider-records restart recovery, API 401/403/200+replay); ruff clean; pyright 0 errors; `npm test` 56/56 (regression); `make build` image green.

**Verdicts (recorded separately as required):**
- **Synthetic executor qualification: PASS** (component level; positive/negative/adversarial suites green; no live dependency exercised).
- **Vaultwarden live integration qualification: BLOCKED** — endpoint reachable (see below) but auth is NOT configured on this host (no client credentials per C0.7); the `BlockedVaultwardenProvider` ensures no code path can proceed to a live vault until the integration is qualified.
- **Deployment readiness: NOT RUN** — the executor is component-qualified inside the Python backend; the deployed image still runs the JS baseline (`node server.js`) until G20, and no live Vaultwarden connectivity-with-auth exists. No deployment claim is made.

**Security findings:**
1. **Vaultwarden endpoint reachable:** `https://bw.theblacktech.com/alive` → HTTP 200, 27 ms (root also 200; DNS 108.189.128.9). Reachability is NOT qualification: auth remains the blocker (client credentials absent from this host), and the executor treats vault access as unavailable until G10-live rows are executed with owner-provisioned credentials.
2. **No-fallback semantics are load-bearing:** the executor's no-fallback guarantee is what prevents a compromised/failing provider from bypassing an authorization evaluated against a different provider. Tests pin the provider call counts.
3. **Redaction is value-based plus key-based:** exact-credential-value replacement + secret-key sanitization; the leaky-adapter test proves defense against adapter-level disclosure, the realistic failure mode.
4. **Session-cookie workload mapping is provisional:** `workload.owner` is derived from `require_auth("session")`; per-user workload identity differentiation (beyond owner) belongs to G13/G16-era identity work, not this gate.
5. **The prompt-injection control is structural (payload is inert data):** any future feature that lets agent-authored text influence `capability`/`scope`/`binding_id` resolution would void this test — flagged as a standing review item.

**Parallel independent work (authorization item 2):**
- **ChromaDB / Memory Palace:** NO endpoint is configured anywhere in the repo (`.env` names scanned for CHROMA/MINIO/S3/MEMORY/RUSTFS — names only, no values printed; integration-inventory already marks the integration "not integrated"). **Nothing to probe without an owner-provided endpoint** — recorded as the blocker, not guessed.
- **MinIO / S3:** same result — no configured endpoint name → no probe performed; discovering hypothetical hosts would be exactly the forbidden discovery behavior, so it was not attempted.
- **Vaultwarden probe** (above) used the one endpoint configured in the repo.
- **G13/G15/G16 continuation:** not started this gate — G10 saturated the session budget; **G16 (registry→display wiring of lifecycle/verified status) is the recommended next credential-free increment**. Hermes task-submission support remains explicitly `supported: false` (no fabrication).

**Test commands:** `make python-test` (pytest+ruff+pyright), `npm test`, `make build`; reproduction chain above.
**Rollback implications:** schema v4 tables are additive (drop `executions`/`capability_approvals` to revert); removing `capabilities/` + the API route + the lifespan sync returns to the G9/G22 state; JS stack and all prior gate behavior untouched (npm suite green).

**Verdict: PASS** (synthetic executor qualification; live Vaultwarden rows and deployment readiness remain separately BLOCKED/NOT RUN as recorded above).

### G22 — Isometric environments and sprite system (criteria registered 2026-11-20; execution follows)

**Criteria (registered pre-execution):**
- C22.1 Four original environment packs load and render: continental (default), mechanic, pirate, treehouse — original art, no copyrighted production artwork.
- C22.2 Continental is the default on first deployment (no config / missing assets → legacy office behavior preserved).
- C22.3 Reusable manifests (JSON) carry: id, name, art style + palette, background asset path, collision grid, anchors, workstation/interaction coordinates, sprite scale. Collision contract: 20×25 grid, 0 = walkable, 1 = wall, 2 = door, 3 = chair (matching the proven office map).
- C22.4 Existing characters, agent bindings, IDs and coordinates are untouched by the environment layer (per-env manifests keep the same proven walkable topology; identity/task/permission/memory state is environment-independent by construction).
- C22.5 Shared sprite + animation system (existing 7×3 sheets / walk / sit / work / waiting / idle / depth-sorted painter's algorithm) keeps working for every environment.
- C22.6 Environment selection: dashboard selector → persisted config → office honors it at load; selection survives restart.
- C22.7 Python configuration + persistence API (GET/PUT /api/environment) with SQLite app_settings (schema v3), validation data-driven from the manifest directory (new packs need NO backend change).
- C22.8 Assets load on demand (background fetched only when the environment is active); missing manifest/background fails gracefully to the previous/legacy environment.
- C22.9 Automated tests: per-environment manifest validation + BFS navigation checks (anchors reach workstations without crossing solids); npm regression suite extended (manifest serving, missing-asset 404, environment persistence through config).
- C22.10 Performance within MVP budgets: same single draw pass + one on-demand image decode; smoke startup/RSS measured.
- C22.11 Documentation for adding future environment packs.

**Execution constraints:** no 3D/game-engine dependencies; static serving stays allowlisted; Python API qualifies at component level (deployed image still runs the JS baseline until G20 cutover — recorded as a limitation, not a mock).

**Execution evidence (2026-11-20, main @ pre-commit):**
- **Packs (C22.1, C22.3):** `tools/generate-environments.py` generates four original deterministic packs (continental / mechanic / pirate / treehouse) + manifests from the deployed office's own collision map (read from index.html at generation time — single source, no hand copy). Validator inside the script: grid 20×25, value contract, anchors on-grid, workstations on chair cells, **BFS: every workstation reachable from reception without crossing solids, per pack**.
- **Default (C22.2):** missing/absent selection → `continental` everywhere (server default, office default, Python default); first-deployment behavior asserted in node + pytest.
- **Identities/bindings preserved (C22.4):** envs are a visual/topology layer only; all four packs share the proven walkable topology, coordinates unchanged; Python alignment (G9) untouched.
- **Sprites/animations (C22.5):** shared system untouched — existing 7×3 sheets, walk/sit/work/waiting/idle actions, painter's depth sort (sort by y, pre-existing) verified by regression suite; `sprite_scale` manifest field honored via `window.ENV_SCALE` (packs currently ship value 2 — future packs can drive scale without code change).
- **Selection + persistence (C22.6, C22.7):** dashboard selector (env catalog from `GET /api/environments`, data-driven from the manifest directory — no backend change for packs); writes ride the authed `POST /api/config`; server-side guard: legacy bodies without `environment` carry the previous selection (never erased); selection loads on BOTH boot paths (root-cause fix during execution: agents.json-first boot branch initially skipped the legacy-config read); Python `GET/PUT /api/environment` on SQLite `app_settings` (schema v3, tested reopen-survival) with write-through to `pixel-config.json` (single selection for both runtimes).
- **Assets on demand + graceful failure (C22.8):** background decodes once per active env (identical-src switch short-circuits); missing manifest → 404 (asserted), missing bg → legacy fallback image; broken pack in catalog → skipped, server unaffected.
- **Automated tests (C22.9):** npm baseline 56 asserts (12 new G22: catalog, allowlisted manifest serving, per-pack manifest+png 200, missing-pack 404, default selection, switch/no-erase/survives-restart, post-env roster recovery) — 56 passed, 0 failed; pytest 55 passed (4 G22); ruff clean; pyright 0 errors.
- **Performance (C22.10):** container smoke 2026-11-20: boot serving catalog + packs, RSS 18.58 MiB (G1 budget ~70 MiB), single-draw loop unchanged (one decode per env switch).
- **Reproduction:** `python3 tools/generate-environments.py && npm test && make python-test && make build && PORT=19001 make run` + curl `/api/environments`.
- **Security:** no new dependencies; manifests served from the existing allowlisted `/assets` path (extension to `.json` documented); env switch requires auth (dashboard/POST) like all mutations.
- **Rollback:** delete `assets/environments/*` + the selection key → office falls back to the legacy placeholder behavior (explicitly asserted as the default path).
- **Limitations:** true diamond-isometric rendering is deferred (MVP ships pseudo-isometric staggered tiles over the proven square-grid navigation — geometry contract documented); Python API is component-qualified until the Python backend deploys (image still runs node CMD); per-pack scale is plumbed but unused at value 2.

**Verdict: PASS** (all four default environments load; original art; identity preservation and persistence verified by automated tests + container smoke; future-pack documentation shipped).

### G9 — Employee and character alignment (executed 2026-11-20)

**Objective:** separate visual characters from runtime identities; mandatory binding proof; lifecycle discipline; impersonation refusal; decorative vs operational distinction; no false availability from probe output (mandate G9, incl. all acceptance bullets).

**Files:** `persistence/db.py` (migration 2 + `EMPLOYEE_LIFECYCLE_TRANSITIONS`), `persistence/store.py` (lifecycle CAS, `bind`/`verify_binding`, non-destructive visual sync, `align_employee_roster`), `main.py` (boot-time alignment + audit), `tests/test_alignment.py` (5 tests).

**Schema v2:** `employees.kind` ('operational'|'decorative'); partial UNIQUE index `(harness, native_harness_id) WHERE harness != 'none' AND native != ''` — the impersonation guard at the storage layer; `runtime_bindings.provenance`. v1 databases upgrade in place on next `migrate()` (idempotent ALTERs behind version pin, asserted for v2).

**Lifecycle machine (mandate states incl. on_demand):** discovered→unassigned→registered→available→working/waiting/blocked/offline; on_demand branch; retired terminal. CAS-guarded like tasks (UPDATE ... WHERE lifecycle = expected); illegal moves and stale views raise `StorageError`. Employee lifecycle is deliberately NOT derived from harness probe results — session activity is external fact, registry lifecycle is governance (test: observed offline probe left lifecycle unchanged and passed through verbatim).

**Binding proof chain (acceptance: no impersonation; instance + native id mandatory):** `bind()` requires harness ∈ {openclaw, hermes} + non-empty native_agent_id + harness_instance_id; unique-index refusal surfaces as `StorageError("impersonation refusal: …")` with NO row left behind (asserted); `verified_binding` is granted ONLY by `verify_binding()` against an existing active runtime_bindings row owned by that employee; foreign binding refused.

**Visual sync root-cause fix (G6 latent flaw):** `migrate_office_json` was `INSERT OR REPLACE` — every re-run would have clobbered real harness bindings on employees. Now `ON CONFLICT(employee_id) DO UPDATE` touching ONLY display_name/sprite_id/updated_at; test proves a bound employee survives a re-run and a second boot.

**Alignment (`align_employee_roster`) + boot wiring:** runs in the Python persistence registration whenever `data/agents.json` exists: visual identity sync (never binding clobber), `kind` classification, harness INTENTs (Charon→openclaw recorded at boot but never auto-bound — pending_bindings row states "awaiting native agent id + instance proof"), observed probe results passed through verbatim as separate report data. Alignment event audited once per roster sha (idempotent: two boots → one event, asserted); visual source file untouched; agents.json mutation by the JS office is irrelevant to the registry (visual layer owns positions).

**Acceptance evidence:** sprites functional (JS office/visual layer untouched — only registry reads the roster json); identities survive restarts (boot-loop test); impersonation refused at the storage layer; offline/unknown never displayed as available (lifecycle/probe separation test); decorative characters distinguishable (`kind` in registry + report).

**Test commands + results:** `uv run pytest` → 51 passed (5 G9; G6 schema-pin test updated to v2); ruff clean; pyright 0 errors; `npm test` 44/44; `make build` image green. Reproduction: `make python-test`.
**Security implications:** bindings and verified identity are now server-side facts (G10's trust boundary can resolve them); no permissions auto-granted on discovery (mandate); alignment writes audited.
**Rollback:** schema v2 additions are additive; dropping the index/columns returns to v1 shape; boot alignment can be disabled by removing the agents.json path check.
**Limitations:** Charon's OpenClaw binding remains PENDING (gateway BLOCKED per G7 — binding completes when a live native id + instance proof exist); registry→office display wiring of lifecycle/verified status is G16; provider_records write path (ledger G6 note) deferred to G10-era employee sync.

**Verdict: PASS** (component qualification; live harness bindings pending G7 gateway).

### G8 — Hermes adapter (executed 2026-11-20; LIVE-qualified this host)

**Objective:** independent Hermes adapter via its own supported interface — zero coupling to OpenClaw (mandate §G8).

**Files:** `src/pixel_startup/harnesses/{__init__,hermes}.py`, `tests/test_harnesses_hermes.py` (5 tests), `main.py` wiring (`_register_hermes`, evidence-gated promotion), `config.py` (`HERMES_HOME` field).

**Interface:** the filesystem Hermes itself maintains — the same two source-of-truth files the deployed office's `integrations.js` reads (`$HERMES_HOME/runtime/active_sessions.json`, `$HERMES_HOME/gateway_state.json`) with identical semantics: pid-liveness filtering (`os.kill(pid, 0)`, PermissionError = alive foreign process), `gateway_state == "running"` + alive pid, connected-platform filter. No private imports; task ops (`submit/result/cancel/observe`) return `supported: false` explicitly (no documented programmatic task interface — Hermes drives the office REST API itself).
**Live evidence (2026-11-20, this host):** probe → `online=True`, gateway `running` (pid alive), platforms `['mattermost']` (connected), 1 live session lease, profiles `['default']`. Same data the office presence chips already display.
**Acceptance evidence:** `gateway.health`/`session.status` live shapes; dead session leases filtered (synthetic fixture: live pid + dead pid); missing/empty home → honest `online: false`; profile discovery lists `$HERMES_HOME/profiles/*` + `default`; **evidence-gated promotion** wired in the app lifespan: probe online → QUALIFIED, offline home → stays CANDIDATE, boot unaffected (both branches asserted). `hermes_home` provider: category `agent_harness`, priority 10, endpoint `$HERMES_HOME` path.
**Hermes failure does not disable OpenClaw:** independent providers, independent probes (test: empty hermes home runs clean startup with openclaw provider untouched).
**Test commands + results:** `uv run pytest` → 46 passed (5 G8); ruff clean; pyright 0 errors; `npm test` 44/44. Reproduction: `make python-test`.
**Security implications:** reads only Hermes' own state files (no secrets read/exposed); read-only; no cross-harness routing (G8 prohibition honored).
**Rollback:** remove harnesses/hermes.py + registration; hermes_home setting harmless if unset.
**Limitations:** task submission into Hermes sessions remains unsupported pending a documented programmatic interface (G14's delegation path may need a capability executor or CLI surface — deferred); profile binding is discovery-only until G9.

**Verdict: PASS** (component + this-host live file-interface qualification).

### G7 — OpenClaw adapter (executed 2026-11-20; component-qualified, live rows BLOCKED)

**Objective:** adapter over the OpenClaw gateway using documented interfaces only; honest capability matrix; Charon maps to the real gateway once infrastructure exists. Eligibility: adapter+contract work independent of live infrastructure.

**Files:** `src/pixel_startup/harnesses/{__init__,openclaw}.py`, `tests/test_harnesses_openclaw.py` (6 tests), `main.py` wiring (`_register_openclaw`), `config.py` (`OPENCLAW_GATEWAY_URL` / `OPENCLAW_GATEWAY_TOKEN` — no secret defaults).

**Interface discipline (the load-bearing decision):** the adapter uses exactly ONE documented surface — `GET {gateway}/v1/models` — the same probe `integrations.js` already performs in production. `session.status`, `task.submit`, `task.result`, `task.cancel`, `activity.observe`, `native_agent.discover` return `{"supported": false, "reason": "no documented gateway interface for this operation"}` with ZERO wire traffic (asserted in test: mock counter stays 0). No endpoints or event schemas invented, per mandate.

**Acceptance evidence (component qualification):**
- `gateway.health`: online → `/{online, auth: ok|rejected, models[]}`; 401 → `auth: rejected` with `online: true` (gateway alive, auth bad — mirrors integrations.js semantics); connect failure → `online: false` + error, never a fabricated success (test: offline adapter yields explicit unavailability; discovery on offline gateway returns "unavailable: gateway offline").
- Registered via G5 machinery: `openclaw_gateway` (category `agent_harness`, implementation `python`, endpoint from `OPENCLAW_GATEWAY_URL`, priority 10) — state stays CANDIDATE (`register()` cannot promote; qualification withheld because live rows are BLOCKED); `select(agent_harness)` honestly raises "no active provider" (no active harness exists yet).
- Token handling: bearer added only when configured; value never logged.
- Tests: 41 passed total (6 G7: health online + models, auth-rejected honesty, offline honesty, unsupported-operations matrix with zero wire traffic, unknown-op explicit, lifespan registration state). `ruff check`/`format --check` clean; pyright 0 errors; `npm test` 44/44.

**Reproduction:** `make python-test`; live probe attempted 2026-11-20: `curl http://127.0.0.1:18789/v1/models` → connection error (exit 000); `.env` has `OPENCLAW_*` commented out (no gateway install/address on this host).
**BLOCKED rows:** agent discovery against real gateway, session status, task submission/result/observation/cancellation against Charon's runtime, Charon identity resolution — all pending an installed/reachable OpenClaw gateway; adapter surfaces already return explicit `supported: false` for the undocumented ones.
**Security implications:** token only via env, no logs; unsupported operations fail closed; no authorization bypass possible through the adapter (no fallback).
**Rollback:** remove harnesses package + registration; nothing on the JS stack or live infra was touched (probe was read-only curl).
**When unblocked:** owner provisions gateway URL/token → re-run live probe → qualification rows → Charon identity resolution in G9.

### G3 — Language-neutral component contracts (criteria registered 2026-11-20, NOT RUN)

Eligibility: independent (no live infrastructure required). Order: before G4 module shapes.

**Execution evidence (2026-11-20, main @ bcf2766 → G3 worktree):**

- Files created: `contracts/schemas/{AgentIdentity,RuntimeBinding,AgentStatus,AgentCapability,Task,TaskReceipt,TaskResult,ActivityEvent,ApprovalRequest,ApprovalResult,ProviderDescriptor,DocumentReference,MemoryReference,CredentialReference,AuditEvent,HealthStatus}.schema.json` (16, Draft 2020-12, envelope `id`/`type`/`version`), `contracts/openapi.json` (OpenAPI 3.1, all 16 as components), `contracts/README.md` (evolution rules + subset semantics + hygiene), `tests/contracts/fixtures/valid/*.valid.json` (16), `tests/contracts/fixtures/invalid/*.invalid.json` (16 missing-required + 4 special: `AgentIdentity.conflated`, `AgentIdentity.asserted_as_binding`, `AgentStatus.version_future`, `CredentialReference.value_leak`), validators `tests/contracts/validate_contracts.py` + `validate_contracts.js`, `make contracts` target.
- Criteria C3.1 ✓ (16 versioned schemas). C3.2 ✓ (openapi.json 3.1). C3.3 ✓ — parity run, all three implementations: `PYTHON_VALIDATOR=jsonschema` 39/39 (venv: `~/.hermes/cache/scratch/g3venv`, jsonschema 4.25.1, off-repo), `PYTHON_VALIDATOR=subset` 39/39, `node tests/contracts/validate_contracts.js` 39/39. 36 fixture expectations + 3 structural checks each; identical accept/reject sets across languages. JS validator: zero new dependencies (hand-rolled subset — recorded choice).
- C3.4 ✓ 8 separation fields distinct+required in AgentIdentity; verified by both validators plus `AgentIdentity.conflated.invalid.json` rejection. C3.5 ✓ `asserted_id` (string, caller-supplied) vs `verified_binding` (object, null-able) enforced; `asserted_as_binding` fixture rejected. C3.6 ✓ version const + `AgentStatus.version_future` (version 2) rejected explicitly; evolution rules in `contracts/README.md` (additive-without-bump constraints, new-file versioning, unknown version → explicit error/4xx mapping). C3.7 ✓ automated value-bearing-key scan (both validators): zero offenders.
- Design decisions: schemas stay inside a documented subset of JSON Schema features so the zero-dep JS validator and the python subset fallback have IDENTICAL semantics to jsonschema Draft 2020-12; expanding features requires adding a JS dependency (owner-gated). Pydantic-generated models deferred to G4 per criterion. Python `jsonschema` availability handled by dual-implementation validator (primary library, fallback subset) so CI needs zero setup.
- Reproduction: `make contracts`; for the library path `python3 -m venv ~/.hermes/cache/scratch/g3venv && ~/.hermes/cache/scratch/g3venv/bin/pip install jsonschema && ~/.hermes/cache/scratch/g3venv/bin/python tests/contracts/validate_contracts.py`.
- Security implications: contracts carry identifiers/policy only; CredentialReference structurally rejects value payloads (`additionalProperties: false` proven by `value_leak` fixture); AuditEvent requires `sanitized: true`. No network I/O in validation.
- Rollback implications: additive directory; delete `contracts/`, `tests/contracts/` and the make target to revert — Node app untouched (`npm test` unchanged, 55 asserts).

**Verdict: PASS** (C3.1–C3.7 executed and satisfied; both languages validate the same fixtures with identical accept/reject sets). Component qualification only — no HTTP layer exists yet; OpenAPI consumption begins in G4.

- C3.1 Versioned JSON Schema files exist under `contracts/` for all 16 mandate types (AgentIdentity, RuntimeBinding, AgentStatus, AgentCapability, Task, TaskReceipt, TaskResult, ActivityEvent, ApprovalRequest, ApprovalResult, ProviderDescriptor, DocumentReference, MemoryReference, CredentialReference, AuditEvent, HealthStatus), each carrying `id`, `type`, `version`.
- C3.2 An OpenAPI document exposes the same contracts for HTTP payloads.
- C3.3 A shared fixture set (valid + invalid per contract) validates identically through BOTH a Python validator (pydantic/jsonschema from generated models) and a JS validator (node, no new deps: hand-rolled subset validator or ajv — record the choice).
- C3.4 Invalid fixtures are rejected in both languages; every fixture asserts the 8 identity separations for AgentIdentity (organizational / sprite / native-harness / harness-instance / session / task / scope / authorization-context fields exist and are NOT conflated).
- C3.5 Caller-supplied agent identifiers are never treated as authenticated identity — schema distinguishes `asserted_id` from `verified_binding`.
- C3.6 Schema evolution rules documented ( additive version bumps, unknown/unsupported version → explicit 4xx, never silent).
- C3.7 No raw credential fields anywhere in the 16 contracts (grep audit).

### G4 — Python application foundation (criteria registered 2026-11-20, NOT RUN)

Eligibility: independent of live integrations; depends on nothing else. Structure per mandate §G4, adapted (existing tree stays put; Python additive).

- C4.1 `pyproject.toml` + `uv.lock` pin the stack (fastapi, uvicorn, pydantic, httpx, pytest, ruff, pyright) — reproducible install via `uv sync` on a clean checkout.
- C4.2 `src/pixel_startup/` provides: `main.py` (lifecycle: startup, readiness, graceful shutdown), `config.py` (pydantic-settings from env, NO secret defaults), health/readiness endpoints (`/healthz`, `/readyz`) versioned under `/api/v1/*` where applicable, structured logging in JSON to stdout, per-request validation + consistent error envelope, auth middleware mirroring the G2 model (session cookie or bearer token), and a dependency-injection seam for future providers.
- C4.3 `pytest` suite green: config, health endpoints via httpx ASGI transport (no network), auth middleware (reject/accept), graceful shutdown handler.
- C4.4 `ruff check` + `ruff format --check` + `pyright` (basic strictness) all pass with zero errors.
- C4.5 The Node.js application remains fully operational during the phase — `npm test` still 55 asserts (11 presence + 44 baseline) PASS and Docker build still succeeds.
- C4.6 No secrets in source or `.env.example` values; CI-able single command documented (`make python-setup && make python-test`).

**Execution evidence (2026-11-20, main @ 470c369):**

- Files: `pyproject.toml` (fastapi/uvicorn/pydantic/pydantic-settings/httpx; dev group pytest/ruff/pyright; hatchling build for `src/pixel_startup`), `uv.lock` (committed lockfile), `.gitignore` (.venv/caches), `Makefile` (`python-setup`, `python-test`), `src/pixel_startup/{__init__,config,log,main}.py`, `src/pixel_startup/api/{__init__,health}.py`, `src/pixel_startup/security/{__init__,auth}.py`, `tests/test_config.py`, `test_health.py`, `test_auth.py`, `test_lifecycle.py` (14 tests).
- C4.1 ✓ `make python-setup` → `uv sync` reproducible on clean checkout; `uv.lock` pinned (httpx 0.28.1, fastapi 0.165.0, pydantic 2.14.4).
- C4.2 ✓ main.py lifecycle (startup JSON log, readiness flag, graceful shutdown handler counting teardown); config.py pydantic-settings with NO secret defaults (presence flags `is_*_set` for logging); `/healthz` + `/readyz` (unversioned) + `/api/v1/ping` (first protected /v1 route); structured JSON logging to stdout (`log.py`); consistent error envelope `{"error":{"code","message"}}` on HTTP errors, 404 catch-all and opaque 500s (validation messages exclude input values); auth middleware = session-cookie-or-bearer, fail-closed, timing-safe compares (mirrors G2; candidate cookie format `v1.<expiry>.<hmac>` is NOT yet wire-compatible with the JS cookie — parity deferred to G20 shadow testing); DI seam `app.state.container` (`Container.register`) for G5 providers.
- C4.3 ✓ pytest 14/14: secret-default absence + env roundtrip; health/ready/404/401-envelope via httpx ASGITransport (no network); bearer accept/reject, cookie accept, expired/bad-sig/garbage rejects, fail-closed-when-secrets-unset, cookie-helper roundtrip; lifespan startup→ready→graceful shutdown exercised via `lifespan_context`; DI seam registration.
- C4.4 ✓ `ruff check` clean, `ruff format --check` clean (13 files), `pyright` **0 errors** (1 warning: jsonschema type stubs not installed in the candidate venv — the G3 fallback import; accepted, not an error).
- C4.5 ✓ `npm test` presence + 44/44 baseline PASS with the Python tree present; `make build` exit 0 (JS image unchanged in behavior — new dirs are inert to the Node runtime).
- C4.6 ✓ single documented command (`make python-setup && make python-test` — both verified); no secret values in source (settings default `None`; `.env.example` untouched); only clearly synthetic creds in tests (`tok-test`, `sectest`).
- Design decisions: candidate backend serves no JS-replacement routes yet (ping only) — coexistence by design; docs/redoc/openapi endpoints disabled (G2 hardening parity); ASGITransport is async-only in httpx 0.28 → request tests drive `anyio.run` (anyio already ships with FastAPI, zero new deps).
- Reproduction: `make python-setup && make python-test`; `npm test`; `make build`.
- Security implications: all `/api/v1/*` routes fail closed when secrets are unset; secret values never logged (presence flags only); no secrets in repo. Rollback: remove `pyproject.toml`, `uv.lock`, `src/`, Python tests, Makefile targets — JS stack unaffected.
- Limitations: no SQLite persistence yet (G6); no provider registry yet (G5); readiness is single-process; cookie format not yet cross-runtime.

**Verdict: PASS** (C4.1–C4.6 executed). Component qualification only: Python app boots, serves health, enforces auth, shuts down gracefully. No live deployment qualification (G19/G20).

### G5 — Provider and component architecture (executed 2026-11-20)

**Objective:** lightweight provider registry — mandate categories/states, explicit promotion, deterministic selection, no unapproved fallback, auditable usage. Eligibility: independent of live infrastructure.

**Files:** `src/pixel_startup/providers/{__init__,registry}.py` (`ProviderCategory` ×12, `ProviderState` ×7, `ProviderRegistration`, `ProviderRegistry`, legal-transition table), `tests/test_providers.py` (11 tests), `src/pixel_startup/main.py` wiring (`Container.registry`; lifespan registers + promotes the deployed JS stack), `src/pixel_startup/log.py` (`logger` export).

**Acceptance evidence:**
- Categories/states exactly the mandate sets (12/7). Legal transitions: discovered→registered→candidate→qualified→active→deprecated→disabled; skip-jumps rejected (test: candidate→active raises `ProviderError`).
- `register()` cannot promote (only discovered/registered/candidate accepted); promotion only via explicit `transition()`; `qualified_at` stamped on first qualification.
- Deterministic selection: highest `priority`, id tiebreak (test: three active providers, `(priority, id)` order verified).
- **No fallback / no silent switch after permission failure:** `invoke()` on a higher-priority provider raising `PermissionError` audits `deny` and re-raises; lower-priority provider NOT called (test proves 0 calls), no retry (call count 1). Generic errors: audited `error`, re-raised, no retry. No automatic retries of side-effecting operations anywhere (idempotency not established yet).
- Existing deployed implementation remains usable AND top priority: lifespan registers `pixel_office_js` (category `operational_persistence`, implementation `javascript`, endpoint `http://127.0.0.1:<port>`, priority 100) through the full explicit promotion sequence to ACTIVE — the office's own state store until G20 promotion.
- Candidates testable independently without activation (`invoke_provider` on a candidate/registered provider).
- Usage audit: append-only `registry.audit` (ts, provider, category, operation, allow/deny/error, sanitized detail) — persistence lands in G6.
- Contract tie-in: `registry.to_descriptor()` emits a provider descriptor that validates against the G3 `ProviderDescriptor.schema.json` (subset checker, zero errors in test).
- Honesty: external (JS) provider selected but in-process `invoke` raises `ProviderError "no in-process handler"` and audits `error` — no fabricated results.

**Test commands + results:** `uv run pytest` → 25 passed (11 G5 + 14 G4); `uv run ruff check src tests` clean; `ruff format --check` clean; `uv run pyright` 0 errors; `npm test` 44/44 (coexistence unchanged). Reproduction: `make python-test`.

**Design decisions:** in-process handler contract (`invoke(operation, payload) -> dict`) is the minimal capability surface; selection priority is data (integer), so G20 cutover is promotion-order bookkeeping, not code change; audit entries mirror the G3 `AuditEvent` semantics (`allow|deny|error`) for later G6 persistence.
**Security implications:** authorization failures are terminal (no fallback), usage auditable from day one; no credentials involved (credential_execution remains unpopulated until G10).
**Rollback implications:** delete providers package + test file; main.py reverts to DI-less container; JS unaffected.
**Limitations:** audit is in-memory only (G6); no HTTP exposure of registry yet (a G16+/G19 concern); cross-runtime invocation of external providers is endpoint-only (documented, honest).

**Verdict: PASS.** Component qualification only (registry has no live external integrations to qualify yet — those are G7/G11/G12/G13).

### G6 — Operational persistence (executed 2026-11-20)

**Objective:** versioned SQLite operational store (employee registry, runtime bindings, task lifecycle, approvals, audit references, provider configuration) with idempotent non-destructive JSON migration, atomic state changes, restart recovery, backup/restore.

**Files:** `src/pixel_startup/persistence/{__init__,db,store}.py`, `tests/test_persistence.py` (10 tests), `main.py` wiring (lifespan opens + migrates the store; stores on the DI seam; `pixel_startup_sqlite` provider QUALIFIED, priority 10).

**Schema (v1):** `schema_migrations(version, applied_at)`; `employees` (G3 AgentIdentity-aligned columns incl. asserted_id/verified_binding), `runtime_bindings` (FK employees), `tasks` (lifecycle CHECK-free; transition map guards), `approvals`, `audit_events`, `provider_records`; indexes on tasks(correlation_id), tasks(status). Scope discipline honored: no customer/financial/repo/vector/object data — only the mandate-allowed operational tables.

**Acceptance evidence:**
- Versioned migrations: v1 applied inside `BEGIN IMMEDIATE` transactions with rollback-on-error; `db.migrate()` replays safely (assert: repeated run adds no rows).
- **Idempotent JSON migration:** `migrate_office_json` reads the live roster shape `data/agents.json` (fixture with 3 agents incl. inactive), upserts keyed by stable `employee_id` (`employee.<name>`), marks visual characters `harness='none'` (runtime binding is G7/G8). Re-run: same upsert count, row count stable (3→3) — no duplicates. Source file byte-identical after migration (rollback-safe; originals remain in place).
- **Atomic transitions (CAS):** `TaskStore.transition` is `UPDATE ... WHERE task_id = ? AND status = <fresh-read>`; illegal moves rejected with `StorageError`; out-of-band writer simulation proves a competitor holding an outdated expected status is refused (rowcount 0) while a fresh view completes its legal move.
- Idempotent task identifiers: double-create of the same task_id returns the original row (objective unchanged), table count stays 1.
- **Restart recovery:** close → reopen → data intact (assert objective survives reopen).
- **Backup/restore:** `Database.backup()` (SQLite backup API) → mutate → close → `restore_from` → post-backup writes gone (count back to 1). Note recorded for G19: restore requires releasing WAL connections first (close before copy).
- Concurrency: WAL journal asserted; second connection observes committed writes across connections.
- Provider coexistence: `pixel_startup_sqlite` registered through explicit promotion to QUALIFIED (priority 10, handler `health.counts` proven via `registry.invoke_provider`; unsupported op raises StorageError and is audited error) — JS `pixel_office_js` remains ACTIVE priority 100 (select still returns JS). Activation deferred to G20 as mandated.
- Audit persistence available (`AuditStore.append/count`) for the G5 in-memory audit entries → ledger rows.

**Test commands + results:** `uv run pytest` → 35 passed (10 G6); ruff check/format clean; pyright 0 errors; `npm test` 44/44; reproduction: `make python-test`.
**Design decisions:** stdlib `sqlite3` only (no ORM — zero new deps); CAS transitions instead of lock tables (WAL + BEGIN IMMEDIATE sufficient at MVP scale); `updated_at` bumps on every transition for reconciliation; migration of roster keys employees by name-derived stable ids because JS agents.json has no durable employee id (documented — revisit when G7 native identities land).
**Security implications:** store contains no credential values (C3 contracts alignment); file created with default umask under data/ (already gitignored); no network exposure.
**Rollback implications:** delete the sqlite file + persistence package; original JSON untouched (non-destructive migration); JS stack authoritative throughout.
**Limitations:** no foreign-key enforcement yet on approvals→tasks beyond FK pragmas (SQLite enforces with foreign_keys=ON — active); provider_records table exists but G5 registry state is not yet auto-persisted to it (write path lands with G9 employee sync); single-writer assumption at MVP scale.

**Verdict: PASS.** Component qualification only: store migrates, is recoverable and non-destructive; not yet the active operational store (JS remains authoritative — G20).

**Addendum 2026-11-20 (backup-archive candidate, owner-directed probe):** evaluated `mdrc` (bageera/sqlite-mdr, `mdr/research` @ eb7334170) as the backup/restoration archive layer. Verified live against a real G6 store (WAL mode, connection open, 200 tasks + employees): `mdrc pack --backup` captured a consistent snapshot via the same backup API G6 uses; verify OK; unpack restored `PRAGMA integrity_check` = ok with all rows; logical dump identical to a fresh backup-API snapshot; 118,784 → 4,721 B (~96% smaller; stored/raw fallback arm guarantees no expansion); tamper protection by construction (crc32 per segment + footer sha256 — repo gates 5/5, 9/9 rejections). **Engine verdict: sqlite-mdr is NOT an engine change** — it is byte-level archive tooling over stock SQLite 3.55.0; its MDR containers are never read by the engine (ADR-001), so the operational store stays plain SQLite in both stacks. **Adopted decision:** wire mdrc as the optional backup-compression/tamper layer at G19 (deployment packaging owns backup guidance), with plain backup-API `.sqlite` copies as the always-working fallback; G6 itself is unchanged (verified: raw main file of a live WAL db is not the byte-exactness target — snapshot ↔ restore integrity/logical equivalence is).

### G5-G20 dependencies and known blockers (for scheduling)

- G7 (OpenClaw) live qualification BLOCKED on this host: no OpenClaw install/gateway address; proceed adapter-vs-contract + mock, record BLOCKED for live rows.
- G10/G13 live rows BLOCKED on Vaultwarden API creds / SSH key availability on this host; synthetic-credential component qualification proceeds.
- G11 (Memory Palace), G12 (MinIO) need endpoint reachability verification from this host before adapter work (WireGuard/gamma network) — verify early in those gates.
- G18/G19/G20 execute after G8+G12+G13 (needed load profile + packaging).
- G21 workflows classified per final evidence.

