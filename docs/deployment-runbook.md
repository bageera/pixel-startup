# Deployment Runbook (G19/G20 deliverable, 2026-11-21)

## Containers
- Combined current image: `ghcr.io/bageera/pixel-startup:latest` (JS baseline, 332 MB).
- Python production container: `Dockerfile.python` → `ghcr.io/bageera/pixel-startup-backend` (uv, non-root UID 1001, healthcheck on `/healthz`; measured 239 MB).
- Build: `docker build -f Dockerfile.python -t ghcr.io/bageera/pixel-startup-backend:latest .`

## Kubernetes (deploy/pixel-stack.yaml)
- Single PVC `pixel-data` (5Gi) shared by both deployments; **exactly one backend may hold it read-write at a time** — cutover sequence below enforces the single-writer window.
- `pixel-backend-python`: runAsNonRoot 1001, resources req 192Mi/100m → limit 512Mi/500m, liveness+readiness on `/healthz`, preStop drain. SecretRef `pixel-secrets`; BOTH pods inject ALL secret keys via `envFrom`. Required keys: `DASHBOARD_PASSWORD`, `PIXEL_SESSION_SECRET`, `PIXEL_API_TOKEN`, **`LITELLM_API_KEY`** (office chat + briefings run through the LiteLLM call in `server.js` `/api/ai/*`/`/api/briefing/ai`; without it node sends `Bearer ''` and the office chat surfaces the sanitized "Connection error with the AI API (LiteLLM)" — repro: 502 `{"error":"LiteLLM error","status":401}`). Optional keys: `LITELLM_API_URL`/`LITELLM_MODEL` (defaults match `litellm.blacktech.dev` + package default model), `CHROMA_*`, `MINIO_*`, `OPENCLAW_GATEWAY_URL`/`OPENCLAW_GATEWAY_TOKEN` (B1: G7 presence probe + Charon binding; absent = CANDIDATE honest-offline, never guessed), `MATTERMOST_URL`/`MATTERMOST_TOKEN`/`MATTERMOST_CHANNELS` (G13 comms adapter live rows — `Settings` has NO env prefix, so field `mattermost_url` binds to plain `MATTERMOST_URL`), `GITHUB_TOKEN`.
- In-pod verification after apply/secret change (prints names/counts only — no values):
  - `kubectl exec deploy/pixel-office-js -- sh -c 'env | grep -c "^LITELLM_API_KEY="'` → `1`
  - Egress/status check: `kubectl exec deploy/pixel-office-js -- node -e "fetch(process.env.LITELLM_API_URL+'/v1/models',{headers:{Authorization:'Bearer '+process.env.LITELLM_API_KEY}}).then(r=>console.log('litellm status',r.status)).catch(()=>console.log('litellm NET-FAIL'))"` → `status 200` (key/egress OK), `status 401` (key wrong), `NET-FAIL` (egress blocked → add an egress NetworkPolicy allowing DNS + 443 out). NetworkPolicy in this manifest is ingress-only; egress is open by default unless the cluster defaults otherwise.
- Apply: `kubectl apply -f deploy/pixel-stack.yaml` (JS stack stays authoritative until owner-authorized cutover).

## Backup / restore (verified conventional method)
```
python3 tools/backup.py backup  <db> <dest.bak>    # backup-API snapshot + integrity_check + sha256 sidecar
python3 tools/backup.py restore <dest.bak> <dir>   # ref restores if sha256 mismatches (corruption), integrity re-checked
make python-test            # includes tests/test_backup_tool.py
```
mdrc integration: **BLOCKED** — binary not installed on this host; research-format artifacts (sqlite-mdr `mdr/core` pack/unpack library) are not qualified as production backup tooling. Do not replace the verified backup path until mdrc ships a qualified CLI + restore guarantees.

## Cutover rehearsal (isolated; rerunnable)
```
tools/cutover_rehearsal.sh
```
Sequence: seed JS-era state → JS up → capture `/api/config` → STOP JS (single writer) → verified backup → Python up on the SAME data dir (auto-migrates `agents.json`/`pixel-config.json`, schema v5) → verify migrated roster + providers → owner-authed workflow (environment select persists) → **rollback** → JS up → owner state untouched.
Last run: REHEARSAL PASS (see ledger).

## Rollback (production, documented)
1. Scale `pixel-backend-python` to 0 (`kubectl scale deploy/pixel-backend-python --replicas=0`).
2. Restore the last verified backup if Python mutated persistent state:
   `python3 tools/backup.py restore <backup.bak> <data-dir>`.
3. Scale `pixel-office-js` back to 1 — it reads the same JSON files and serves the owner state (rehearsal-verified).
4. Python writes (`pixel_startup.sqlite3`, `app_settings`) are additive; JS ignores them, so rollback is non-destructive to JS state.
