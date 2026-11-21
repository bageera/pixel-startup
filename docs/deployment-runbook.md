# Deployment Runbook (G19/G20 deliverable, 2026-11-21)

## Containers
- Combined current image: `ghcr.io/bageera/pixel-startup:latest` (JS baseline, 332 MB).
- Python production container: `Dockerfile.python` → `ghcr.io/bageera/pixel-startup-python` (uv, non-root UID 1001, healthcheck on `/healthz`; measured 239 MB).
- Build: `docker build -f Dockerfile.python -t ghcr.io/bageera/pixel-startup-python:latest .`

## Kubernetes (deploy/pixel-stack.yaml)
- Single PVC `pixel-data` (5Gi) shared by both deployments; **exactly one backend may hold it read-write at a time** — cutover sequence below enforces the single-writer window.
- `pixel-backend-python`: runAsNonRoot 1001, resources req 192Mi/100m → limit 512Mi/500m, liveness+readiness on `/healthz`, preStop drain. SecretRef `pixel-secrets` (DASHBOARD_PASSWORD, PIXEL_SESSION_SECRET, PIXEL_API_TOKEN, optional CHROMA_*/MINIO_*/MATTERMOST_*/GITHUB_TOKEN...). NetworkPolicy restricts ingress to the cluster edge.
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
