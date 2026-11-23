# Pixel Startup

> Last updated: 2026-11-23 (Hemingway audit pass)

Pixel Startup is the operating platform for a persistent, multi-harness organization. Pixel Office is its visual half: the retro pixel-art environment shown here, with its characters (Curator, Charon, Wardog, Fixer, Tailor, Ghostface), rooms and speech bubbles.

> Formerly released as "Pixel Office" (npm package `pixel`). The platform identity moved to Pixel Startup with the Python backend migration (`docs/migration/`). Git history, upstream attribution and licenses are unchanged — see [CREDITS.md](CREDITS.md).

A pixel office, run by a **Python backend**. FastAPI covers office state, delegation with plan → owner-review → verify gates, a Chief of Staff LLM channel, and honest offline-first integrations. The node surface remains the visual component. Live since **v1.2.0**. CI builds the GHCR image on every push and tag.

> 🇪🇸 ¿Buscas la versión en español? Está al final de este documento, pero la referencia principal es ahora la versión en inglés.

## 🎬 Demo

- Video walkthrough: https://youtu.be/GGyWQJAv9_8
- Screenshots
  - **AI Director session**

    ![AI Director in action](docs/media/pixel-office-session.jpg)
  - **Agent & room editor**

    ![Agent editor](docs/media/pixel-dashboard-characters.jpg)
  - **Collision & IA layout editor**

    ![Collision editor](docs/media/pixel-dashboard-collisions.jpg)

## ✨ Highlights

- **Live pixel office.** Retro sprites, speech bubbles, five swappable original environments (Art Deco HQ, Mechanic Shop, Pirate Ship, Lost Boys Treehouse, **Silicon Garage**).
- **Python operational backend** (`src/pixel_startup/`): office state snapshots, capability-guarded automation, delegation with owner approval, audit log, SQLite persistence.
- **Chief of Staff**: `GET /api/v1/chief/advise` (office + public-repo context through your LiteLLM provider), delegated tasks with an LLM plan draft, owner-only review, independent verify-before-done (`verified` status; never a re-dispatch).
- **Move-in readiness** (`GET /api/move-in`): chief human present (Ghostface), startup direction set, goals on the board. Checklist + editing live on the dashboard's "Startup & Move-in" card.
- **Honest integrations**: Hermes + OpenClaw presence, Mattermost/email comms, ChromaDB memory, MinIO storage. Offline until configured, never fabricated.
- **Password-protected dashboard** (`dashboard.html`): edit agents, rooms and collision maps without touching code.
- **CI**: every push to `main` runs the full gate (pytest/ruff/pyright/pip-audit + node tests) and publishes `ghcr.io/bageera/pixel-startup-backend` (`latest` + semver tags).

## 🧱 Folder map

```
.
├── index.html, dashboard.html, server.js   # Pixel Office visual surface (node/express)
├── src/pixel_startup/                       # Python backend (FastAPI)
│   ├── api/             # office + capabilities + chief routes
│   ├── capabilities/    # executor, delegation adapters (plan/review/verify)
│   ├── integrations/    # infra (LiteLLM), comms, memory, storage, harnesses
│   └── persistence/     # SQLite stores + migration
├── assets/environments/                     # generated office backgrounds + manifests
├── tools/                                   # deterministic asset generators
├── deploy/pixel-stack.yaml                  # k8s manifest (both pods, pixel-secrets)
├── .github/workflows/python-image.yml       # CI: gate + GHCR publish
└── data/, sample-data/                      # runtime + versioned demo config
```

## 🚀 Quick start

**Python backend (preferred):**

1. ```bash
   make python-setup   # uv sync → .venv (uses uv.lock)
   ```
2. ```bash
   make python-test    # pytest + ruff + pyright + pip-audit gate
   ```
3. ```bash
   uv run uvicorn pixel_startup.main:app --host 0.0.0.0 --port 19000
   # GET http://localhost:19000/healthz → 200
   ```

**Node office (visual demo):**

1. ```bash
   cp -n .env.example .env   # PORT, PIXEL_DATA_DIR, DASHBOARD_PASSWORD…
   npm install
   ```
2. ```bash
   npm start
   # Office: http://localhost:19000/ — Dashboard: /dashboard.html
   ```

**Docker (local dev — stop / rebuild / start loop):**

```bash
make start    # build the python image from source (cached), run on :19000
              # mounts ./data + ~/.hermes (read-only; HERMES_HOME), .env secrets
make stop     # stop + remove the container
make restart  # stop → rebuild → start
make pull     # run-side: pull the CI-published image instead of building
```

The `◱ HUD` toggle hides the office HUD chrome. It persists locally.

The docker run is the same shape for deploy hosts. Add `-v ~/.hermes:/hermes-home:ro -e HERMES_HOME=/hermes-home -e PIXEL_HERMES_LIVENESS=file` when Hermes runs beside the container. Docker Desktop VMs cannot see host pids, so the lease files are the liveness evidence there.

## 🧑‍💼 Onboarding the startup (deploy to your cluster)

The rails exist. Each integration flips live from `deploy/pixel-stack.yaml`'s `pixel-secrets`. Offline stays honest if a key is absent.

**1. Deploy + chat (required first).**
```bash
kubectl apply -f deploy/pixel-stack.yaml   # after docker login ghcr.io
```
Required keys in `pixel-secrets`: `DASHBOARD_PASSWORD`, `PIXEL_SESSION_SECRET`, `PIXEL_API_TOKEN`, `LITELLM_API_KEY` (office chat, briefings, Chief's plan generation). Optional: `LITELLM_API_URL`/`LITELLM_MODEL` (defaults match `litellm.blacktech.dev`). Verify in-pod: `env | grep -c "^LITELLM_API_KEY="` → `1`, then a fetch to `$LITELLM_API_URL/v1/models` → status 200.

**2. Communications (`MATTERMOST_URL`, `MATTERMOST_TOKEN`, `MATTERMOST_CHANNELS`).**

The G13 comms adapter sends plan-review notifications and task reports. It is idempotent per correlation id and always checks the channel allowlist first. SMTP creds enable `email.compose` drafts. `email.send` stays owner-approval-gated. Verify: adapter configured at boot, a posted review lands in the channel, a replay adds no duplicate.

**3. Memory + storage (`CHROMA_URL`, `CHROMA_TOKEN`, `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`).**

Scoped ChromaDB memory (G11) and MinIO objects (G12) go live. Without them the office has no recall. Route over WireGuard if the endpoints aren't on-cluster.

**4. Autonomy — enable Hermes A2A (not a secret, an enable).**

On the host running Hermes: `hermes gateway setup` (A2A_PORT + peer token). Then verify the office pod binds the harness. This flips delegation dispatch on: plan → owner-review → verify. Mattermost is the notification surface. The approvals-row record is the only authorization.

**5. Later — OpenClaw (`OPENCLAW_GATEWAY_URL`/`TOKEN`) + Vaultwarden creds.**

Presence chips and binding polish. Not required to operate.

**6. Register a harness employee (the org side of onboarding).**

With the admin token: `POST /api/v1/office/onboard` — `{"employee_id", "display_name", "harness": "hermes", "native_agent_id", "harness_instance_id"}`. This registers, binds and verifies the native identity (native-identity uniqueness is the impersonation guard), activates the lifecycle, and returns the observed presence row. Re-onboarding refreshes a binding. A different employee claiming the same native identity is refused. `GET /api/v1/health/services` and the dashboard strip (Email / Communication / Memory & Storage buckets) confirm which integrations are connected — evidence only, never assumed.

## 🕹️ REST API (backend highlights)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET /healthz` | Liveness/readiness. |
| `GET /api/state` | Office snapshot (employees, rooms, tasks). |
| `POST /api/v1/capabilities/execute` | Capability-guarded automation (session or workload bearer). |
| `GET /api/v1/chief/advise` | Chief of Staff: office + repo context through LiteLLM (503-not-fabricated). |
| `GET /api/v1/delegate/{id}/plan` | Fetch-or-generate an execution plan (task holds in `planning`). |
| `POST /api/v1/delegate/{id}/review` | Owner decision: approve → dispatch, deny → cancel. |
| `GET /api/v1/delegate/{id}/result` | Result + verification verdict (`verified`/`divergent`/…). |
| `GET /api/v1/office/state` / `PUT /api/v1/office/state` | Office state v1 + settings (`app_settings` v3, write-through). |

Node office API: unchanged cheat sheet preserved in `server.js` (config, agents, rooms, collision, presence, environments).

## 🌆 Office environments

Five original packs ship generated (no copyrighted assets): `continental` (default), `mechanic-shop`, `pirate-ship`, `lost-boys-treehouse`, `garage`. The last is «Silicon Garage», the silicon-valley startup garage. Selection persists via `pixel-config.json`/`app_settings` and survives restart with graceful fallback. Regenerate deterministically:

```bash
python3 tools/generate-environments.py   # flat office packs (adds validate: BFS nav)
python3 tools/generate_environments.py   # isometric directory packs
```

## 🤖 Built-in automations

| Script | Purpose |
|--------|---------|
| `pep_email_checker.sh` | Watches `/tmp/email_alert_pending` and moves Pep with a speech bubble. |
| `marian_calendar_checker.sh` | Syncs Marian with agenda/OOO status. |
| `dustin_doc_tokens.sh` | Toggles CEO activity depending on workload. |
| `add_calendar_event.sh` | Helper to inject sample calendar events. |

## 📂 Data & assets

- `sample-data/*.sample.json` → version-controlled demo config. `data/` → live state (gitignored).
- `assets/characters/` → MetroCity Free Topdown Character Pack by [JIK-a-4](https://jik-a-4.itch.io/metrocity-free-topdown-character-pack).
- `assets/environments/*` → generated original art (see 🌆 above).
- `docs/media/` → screenshots. Keep 1920px width when updating marketing shots.

## 🎨 Credits & external assets

- Inspired by [pixel-agents](https://github.com/pablodelucca/pixel-agents) by Pablo Delucca. This project reuses the idea of a pixelated OpenClaw office and adapts it to this stack.
- Character sprites come from the [MetroCity Free Topdown Character Pack](https://jik-a-4.itch.io/metrocity-free-topdown-character-pack) by JIK-a-4. Review the itch.io license before redistributing; attribution is required.
- The office background was built with [Office Interior Tileset (16x16)](https://donarg.itch.io/officetileset) by Donarg (USD $2). This is the only part of Pixel Office that is not freely available; the tileset is **not** included here because of its license. To use it locally, purchase the pack, export your layout as `assets/oficina.png`, and the app will pick it up automatically.

See [CREDITS.md](CREDITS.md) for a handy table you can keep updated.

## 📦 Deploy notes

- **CI publishes the image.** Push to `main` → `ghcr.io/bageera/pixel-startup-backend:latest`. A `v*` tag → `1.2.0` + `latest`. Workflow: `.github/workflows/python-image.yml`, zero secrets — uses `GITHUB_TOKEN`. Pulling hosts: `docker login ghcr.io` once, then `docker pull ghcr.io/bageera/pixel-startup-backend:latest`.
- Makefile shortcuts: `make python-setup`, `make python-test` (the full gate), `make test` (node), `make build`/`make run`/`make push-image` (node-era container), `make tag` (annotated SemVer tag from `package.json`), `make contracts` (node ⇄ python fixture parity).
- Deployment manifests: `deploy/pixel-stack.yaml` (both pods inject `pixel-secrets` via `envFrom`). Secrets rule: values never in chat/docs, `[REDACTED]` in anything tracked.
- Releases: version lives in `package.json` + `pyproject.toml` (kept in sync with `uv.lock`). Fold `CHANGELOG.md` `[Unreleased]` **before** tagging.
- `.gitignore` already skips `.env`, `node_modules`, `data/`, backups and temp logs. Notable changes go to [CHANGELOG.md](CHANGELOG.md).

## ✅ Pre-share checklist

- [ ] `pixel-secrets` has the four required keys (chat verified in-pod).
- [ ] Comms: Mattermost row posted and replayed without duplicates.
- [ ] Memory/storage: ChromaDB heartbeat + MinIO round-trip from the pod.
- [ ] Dashboard login + environment switch tested.
- [ ] Screenshots + demo link still representative.

---

## 🇪🇸 Resumen en español

Pixel Office es el entorno visual de **Pixel Startup**: backend Python (FastAPI) con delegación (plan → revisión del propietario → verificación), Chief of Staff sobre LiteLLM, cinco entornos de oficina generados y CI que publica la imagen en GHCR en cada push. La incorporación (onboarding) es añadir las claves a `pixel-secrets`: `LITELLM_API_KEY` (chat), `MATTERMOST_*` (comunicaciones), `CHROMA_*`/`MINIO_*` (memoria/almacenamiento) y activar Hermes A2A con `hermes gateway setup` (autonomía). Los pasos de arranque local están arriba («Quick start»); `data/` queda fuera del repositorio.

Sprites: MetroCity Free Topdown Character Pack (JIK-a-4). Tileset: Office Interior Tileset de Donarg (no incluido; cópialo como `assets/oficina.png` si lo compras).
