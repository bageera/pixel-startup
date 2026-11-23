"""Application factory: lifecycle, error envelope, DI seam, structured logging.

G4 criteria covered here:
- main.py lifecycle: startup, readiness flag, graceful shutdown handler.
- Consistent error envelope on every error path: {"error": {"code", "message"}}.
  Secrets/internals are never echoed (500s are opaque).
- Dependency injection seam: app.state.container (provider registry lands in G5).
- Docs endpoints disabled for the candidate backend (G2 hardening parity).
"""

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pixel_startup.api import (
    ai_compat,
    capabilities,
    chief,
    config_v1,
    gateway,
    health,
    office_compat,
    office_state,
)
from pixel_startup.api import environment as environment_api
from pixel_startup.config import Settings, load_settings
from pixel_startup.log import logger, setup_logging
from pixel_startup.persistence.db import Database
from pixel_startup.persistence.store import (
    ApprovalStore,
    AuditStore,
    EmployeeStore,
    OfficePersistenceProvider,
    SettingsStore,
    TaskStore,
    align_employee_roster,
)
from pixel_startup.providers.registry import (
    ProviderCategory,
    ProviderError,
    ProviderRegistration,
    ProviderRegistry,
    ProviderState,
)


@dataclass
class Container:
    """Dependency-injection seam; the G5 provider registry lives here."""

    providers: dict[str, object] = field(default_factory=dict)
    registry: ProviderRegistry = field(default_factory=ProviderRegistry)
    shutdowns: int = 0

    def register(self, provider_id: str, provider: object) -> None:
        self.providers[provider_id] = provider


def _register_deployed_js_stack(container: Container, settings: Settings) -> None:
    """The deployed JS implementation enters the registry top-priority and ACTIVE.

    Mandate G5: the currently deployed implementation retains priority until its
    replacement is qualified — modeled as an explicit promotion sequence, not a
    registration shortcut.
    """
    reg = container.registry.register(
        ProviderRegistration(
            provider_id="pixel_office_js",
            category=ProviderCategory.OPERATIONAL_PERSISTENCE,
            implementation="javascript",
            endpoint=f"http://127.0.0.1:{settings.port}",
            capabilities=["agents.config.read", "rooms.read", "actions.append"],
            priority=100,
            detail="deployed JS stack: authoritative until Python qualifies",
        )
    )
    container.registry.transition(reg.provider_id, ProviderState.CANDIDATE)
    container.registry.transition(reg.provider_id, ProviderState.QUALIFIED)
    container.registry.transition(reg.provider_id, ProviderState.ACTIVE)


def _register_python_persistence(container: Container, settings: Settings) -> None:
    """G6: open the operational store, migrate, and register as a QUALIFIED provider.

    Deliberately NOT activated — the deployed JS stack stays the active
    persistence provider until cutover (G20)."""

    db = Database(os.path.join(settings.pixel_data_dir, "pixel_startup.sqlite3"))
    version = db.migrate()
    handler = OfficePersistenceProvider(db)
    container.providers["db"] = db
    container.providers["employees"] = EmployeeStore(db)
    container.providers["tasks"] = TaskStore(db)
    container.providers["approvals"] = ApprovalStore(db)
    container.providers["audit"] = AuditStore(db)
    # G9: align the visual roster into the registry at boot — visual identity sync
    # only (bindings/lifecycle are never clobbered); Charon's OpenClaw intent is
    # recorded as a PENDING binding until a native agent id + instance proof exists.
    agents_path = os.path.join(settings.pixel_data_dir, "agents.json")
    if os.path.isfile(agents_path):
        report = align_employee_roster(
            db,
            agents_path,
            harness_intents={"employee.charon": "openclaw"},
        )
        AuditStore(db).append(
            {
                "event_id": f"audit.roster-align.{report['source_sha256'][:12]}",
                "actor": "system",
                "action": "office.roster.align",
                "outcome": "allow",
                "detail": {
                    "employees": report["employees_upserted"],
                    "pending_bindings": len(report["pending_bindings"]),
                },
            }
        )
    # G22: environment selection — Python store authoritative; seed from the JS
    # config file on first boot (single selection for both runtimes).
    settings_store = SettingsStore(db)
    if settings_store.get("environment") is None:
        seed = environment_api.DEFAULT_ENVIRONMENT
        config_path = Path(settings.pixel_data_dir) / "pixel-config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                if isinstance(data.get("environment"), str) and data["environment"].strip():
                    seed = data["environment"].strip()
            except (OSError, ValueError):
                pass  # unreadable config keeps the default
        settings_store.set("environment", seed)
    container.providers["settings"] = settings_store
    # G10: trusted capability executor — synthetic credential provider for
    # qualification, honest BLOCKED Vaultwarden provider, declared adapter set.
    from pixel_startup.capabilities.executor import DEFAULT_ADAPTERS, CapabilityExecutor
    from pixel_startup.capabilities.providers import (
        BlockedVaultwardenProvider,
        SyntheticCredentialProvider,
    )

    synthetic_credentials = SyntheticCredentialProvider()
    container.providers["synthetic_credentials"] = synthetic_credentials
    container.providers["executor"] = CapabilityExecutor(
        db=db,
        audit=container.providers["audit"],
        credential_providers={
            "synthetic": synthetic_credentials,
            "vaultwarden": BlockedVaultwardenProvider(),
        },
        adapters=dict(DEFAULT_ADAPTERS),
        workload_tokens={settings.pixel_api_token or "": "workload.automation"},
    )
    # G14: cross-harness delegation rides the executor; result retrieval exposed
    from pixel_startup.capabilities.delegation import (
        make_delegation_adapter,
        make_plan_flow,
        make_result_retriever,
    )

    container.providers["executor"].adapters["delegate_task"] = make_delegation_adapter(
        db, container.registry, container.providers["audit"]
    )
    container.providers["delegation_results"] = make_result_retriever(
        db, container.registry, container.providers["audit"]
    )
    container.providers["delegation_flow"] = make_plan_flow(  # G23-3a
        db, container.registry, container.providers["audit"]
    )
    from pixel_startup.capabilities.federation import make_federation_ingest

    container.providers["federation_ingest"] = make_federation_ingest(  # G23 federation
        db, container.registry, container.providers["audit"]
    )
    _register_comms(container, settings)
    _register_infra(container, settings)
    container.registry.register(
        ProviderRegistration(
            provider_id="pixel_startup_sqlite",
            category=ProviderCategory.OPERATIONAL_PERSISTENCE,
            implementation="python",
            handler=handler,
            endpoint=None,
            capabilities=["persistence.health.counts", "employees.upsert", "tasks.lifecycle"],
            priority=10,
            detail=f"candidate ops store (schema v{version}); activation deferred to G20",
        )
    )
    container.registry.transition("pixel_startup_sqlite", ProviderState.CANDIDATE)
    container.registry.transition("pixel_startup_sqlite", ProviderState.QUALIFIED)


def _register_openclaw(container: Container, settings: Settings) -> None:
    """G7: OpenClaw gateway adapter joins as CANDIDATE (live rows BLOCKED on host)."""
    from pixel_startup.harnesses.openclaw import OpenClawGatewayAdapter

    adapter = OpenClawGatewayAdapter(
        gateway_url=settings.openclaw_gateway_url,
        token=settings.openclaw_gateway_token,
    )
    container.registry.register(
        ProviderRegistration(
            provider_id=adapter.provider_id,
            category=ProviderCategory.AGENT_HARNESS,
            implementation=adapter.implementation,
            handler=adapter,
            endpoint=adapter.gateway_url,
            capabilities=["gateway.health", "agent.discovery"],
            priority=10,
            detail="candidate: contract-tested vs mock; live rows BLOCKED (no gateway on host)",
        )
    )
    container.registry.transition(adapter.provider_id, ProviderState.CANDIDATE)


def _register_memory(container: Container, settings: Settings) -> None:
    """G11: ChromaDB institutional-memory provider; promotion evidence-gated."""
    from pixel_startup.integrations.chromadb import ChromaMemoryProvider

    provider = ChromaMemoryProvider(
        settings.chroma_url, settings.chroma_api_token, timeout=settings.chroma_timeout_seconds
    )
    container.registry.register(
        ProviderRegistration(
            provider_id=provider.provider_id,
            category=ProviderCategory.MEMORY_RETRIEVAL,
            implementation="external",
            handler=provider,
            endpoint=settings.chroma_url,
            capabilities=["memory.search", "collections.list"],
            priority=10,
            detail=(
                "candidate: contract-tested vs MockTransport; live URL configured"
                if provider.configured
                else "candidate: contract-tested vs MockTransport; live rows BLOCKED"
                " (no endpoint configured)"
            ),
        )
    )
    container.registry.transition(provider.provider_id, ProviderState.CANDIDATE)
    if provider.configured:
        try:
            provider.health()
            container.registry.transition(provider.provider_id, ProviderState.QUALIFIED)
        except Exception:
            pass  # unconfigured/unreachable endpoint: candidate stays the honest state


def _register_storage(container: Container, settings: Settings) -> None:
    """G12: MinIO object storage; live client only when endpoint+creds exist."""
    from pixel_startup.integrations.minio import MinioStorageProvider, SigV4Client

    client = None
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        client = SigV4Client(
            settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key
        )
    provider = MinioStorageProvider(settings.minio_bucket, client)
    container.registry.register(
        ProviderRegistration(
            provider_id=provider.provider_id,
            category=ProviderCategory.OBJECT_STORAGE,
            implementation="external",
            handler=provider,
            endpoint=settings.minio_endpoint,
            capabilities=list(provider.capabilities),
            priority=10,
            detail=(
                "candidate: contract-tested vs FakeS3; live credentials configured"
                if client is not None
                else "candidate: contract-tested vs FakeS3; live rows BLOCKED"
                " (no endpoint configured)"
            ),
        )
    )
    container.registry.transition(provider.provider_id, ProviderState.CANDIDATE)
    container.providers["minio"] = provider
    if client is not None:  # G29: evidence-gated promotion + the startup access log
        from datetime import datetime, timezone

        try:
            probe = provider.probe()
            if probe.get("online"):
                container.registry.transition(provider.provider_id, ProviderState.QUALIFIED)
            if not probe.get("bucket_present"):
                logger.warning("minio bucket missing; create it before storing access logs")
        except Exception:  # noqa: BLE001 - probe failure stays candidate, honest
            pass
        try:  # the startup access log: one JSONL line appended per office start
            today = datetime.now(timezone.utc).date().isoformat()
            key = f"shared/access-logs/{today}.jsonl"
            try:
                existing, _meta = provider.get(key, "company", "office")
            except FileNotFoundError:
                existing = b""
            line = json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(), "event": "startup"}
            ).encode()
            provider.put(key, existing + line + b"\n", "company", "office", "application/x-ndjson")
            logger.info("startup access log written to object storage", extra={"key": key})
        except Exception:  # noqa: BLE001 - storage down must never block the boot
            logger.info("access log write skipped (object storage unreachable)")


def _register_comms(container: Container, settings: Settings) -> None:
    """G13: internal (Mattermost) + external (email) comms; honest BLOCKED rows."""
    from pixel_startup.integrations.comms import EmailAdapter, MattermostAdapter

    channels = tuple(c.strip() for c in settings.mattermost_channels.split(",") if c.strip())
    mattermost = MattermostAdapter(settings.mattermost_url, settings.mattermost_token, channels)
    email = EmailAdapter()  # transport injected when live SMTP creds exist (B3)
    for provider_id, category, handler, endpoint, caps, detail in (
        (
            "mattermost",
            ProviderCategory.COMMS_INTERNAL,
            mattermost,
            settings.mattermost_url,
            mattermost.capabilities,
            "candidate: contract-tested vs synthetic transport; live rows BLOCKED (no creds)",
        ),
        (
            "email",
            ProviderCategory.COMMS_EXTERNAL,
            email,
            None,
            email.capabilities,
            "candidate: contract-tested vs synthetic transport; live rows BLOCKED (no creds)",
        ),
    ):
        container.registry.register(
            ProviderRegistration(
                provider_id=provider_id,
                category=category,
                implementation="external",
                handler=handler,
                endpoint=endpoint,
                capabilities=list(caps),
                priority=10,
                detail=detail,
            )
        )
        container.registry.transition(provider_id, ProviderState.CANDIDATE)
    container.providers["comms_mattermost"] = mattermost
    container.providers["comms_email"] = email


def _register_infra(container: Container, settings: Settings) -> None:
    """G15: read-only operational providers (Odoo/HA/Rundeck/Grafana/GitHub/
    FreshRSS/LiteLLM/Calendar); write-ops deliberately absent per mandate."""
    from pixel_startup.integrations import infra

    container.providers["infra"] = {}
    for provider_cls, url_field in infra.PROVIDERS:
        token_field = url_field.replace("_url", "_api_key" if url_field == "odoo_url" else "_token")
        if provider_cls is infra.LiteLLMProvider:
            adapter = provider_cls(
                getattr(settings, url_field),
                getattr(settings, token_field, None),
                model=getattr(settings, "litellm_model", None),
            )
        else:
            adapter = provider_cls(
                getattr(settings, url_field), getattr(settings, token_field, None)
            )
        container.providers["infra"][adapter.provider_id] = adapter
        try:
            container.registry.register(
                ProviderRegistration(
                    provider_id=adapter.provider_id,
                    category=ProviderCategory.MONITORING
                    if adapter.provider_id in ("grafana", "litellm", "calendar")
                    else ProviderCategory.AUTOMATION
                    if adapter.provider_id in ("rundeck", "github")
                    else ProviderCategory.ACTIVITY_REPORTING,
                    implementation="external",
                    handler=adapter,
                    endpoint=getattr(settings, url_field),
                    capabilities=list(adapter.capabilities),
                    priority=10,
                    detail=(
                        "candidate: contract-tested vs synthetic transport;"
                        " live rows BLOCKED (no endpoint configured)"
                        if not adapter.configured
                        else "candidate: contract-tested; live URL configured"
                    ),
                )
            )
            container.registry.transition(adapter.provider_id, ProviderState.CANDIDATE)
            probe_fn = getattr(adapter, "probe", None)  # G18 evidence-gated promotion
            if callable(probe_fn):
                try:
                    probe = cast("dict", probe_fn())
                    if probe.get("online"):
                        container.registry.transition(adapter.provider_id, ProviderState.QUALIFIED)
                except Exception:  # noqa: BLE001 - probe failure stays candidate, honest
                    pass
        except ProviderError:
            pass  # already registered in this container (idempotent boot)


def _register_hermes(container: Container, settings: Settings) -> None:
    """G8: Hermes filesystem adapter; promotion to QUALIFIED is evidence-gated.

    The probe is the same file interface the deployed office reads; if it proves
    offline here, the provider stays CANDIDATE — no qualification without evidence."""
    from pixel_startup.harnesses.hermes import HermesHomeAdapter

    adapter = HermesHomeAdapter(
        hermes_home=settings.hermes_home,
        liveness_mode="file" if os.environ.get("PIXEL_HERMES_LIVENESS") == "file" else "pid",
    )
    container.registry.register(
        ProviderRegistration(
            provider_id=adapter.provider_id,
            category=ProviderCategory.AGENT_HARNESS,
            implementation=adapter.implementation,
            handler=adapter,
            endpoint=str(adapter.home),
            capabilities=["gateway.health", "session.status", "profile.discovery"],
            priority=10,
            detail="filesystem adapter ($HERMES_HOME sources of truth; G8)",
        )
    )
    container.registry.transition(adapter.provider_id, ProviderState.CANDIDATE)
    probe = adapter.probe()
    if probe["online"]:
        container.registry.transition(adapter.provider_id, ProviderState.QUALIFIED)


def build_error(status_code: int, message: str, detail: str | None = None) -> JSONResponse:
    body: dict = {"error": {"code": status_code, "message": message}}
    if detail:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    container: Container = app.state.container
    setup_logging()
    _register_deployed_js_stack(container, settings)
    _register_python_persistence(container, settings)
    _register_openclaw(container, settings)
    _register_hermes(container, settings)
    _register_memory(container, settings)
    _register_storage(container, settings)
    # G10/G6: snapshot live provider state into provider_records (upsert-only)
    from pixel_startup.persistence.store import sync_provider_records

    snapshot_db = container.providers["db"]
    if isinstance(snapshot_db, Database):
        sync_provider_records(snapshot_db, container.registry)
    logger.info(
        "backend starting",
        extra={
            "event": "startup",
            "component": "pixel_startup",
            "detail": (
                f"secrets: session_secret={'set' if settings.is_session_secret_set else 'UNSET'}, "
                f"api_token={'set' if settings.is_api_token_set else 'UNSET'}; "
                f"providers={len(container.registry.all())}"
            ),
        },
    )
    active_count = sum(1 for p in container.registry.all() if p.state is ProviderState.ACTIVE)
    logger.info(
        "provider snapshot",
        extra={
            "event": "providers.snapshot",
            "component": "registry",
            "detail": f"active={active_count}",
        },
    )
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        container.shutdowns += 1
        logger.info(
            "backend stopped gracefully", extra={"event": "shutdown", "component": "pixel_startup"}
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Pixel Startup backend",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings if settings is not None else load_settings()
    app.state.container = Container()
    app.state.ready = False
    # G20-B: node-parity office state (config/map/commands from the shared data dir)
    app.state.office = office_compat.OfficeState(app.state.settings)
    app.include_router(office_compat.router)
    app.include_router(config_v1.router)  # G26: native v1 editor surface (strangler rung)
    app.include_router(health.router)
    app.include_router(environment_api.router)
    app.include_router(capabilities.router)
    app.include_router(office_state.router)
    app.include_router(chief.router)  # G23 BEFORE the gateway catch-all: native wins
    app.include_router(ai_compat.router)  # AI Director chat/status: BEFORE the catch-all
    from pixel_startup.api import graph as graph_api

    app.include_router(graph_api.router)  # G30: living-startup organizational graph
    # G20-B asset serving: node's PUBLIC_FILES allowlist semantics
    # (root index.html + dashboard.html; /assets/* static; /favicon.ico 404).
    repo_root = Path(__file__).resolve().parents[2]

    @app.get("/", include_in_schema=False)
    async def _index() -> FileResponse:
        index = repo_root / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(index)

    @app.get("/dashboard.html", include_in_schema=False)
    async def _dashboard() -> FileResponse:
        page = repo_root / "dashboard.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(page)

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon() -> JSONResponse:  # node PUBLIC_FILES maps it to null -> 404
        return JSONResponse({"error": {"code": 404, "message": "not found"}}, status_code=404)

    assets_dir = repo_root / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    # G20 cut-over: gateway MUST be registered last — it is the catch-all that
    # routes not-yet-migrated surfaces to the deprecated node (or refuses).
    app.include_router(gateway.router)

    @app.exception_handler(HTTPException)
    async def _http_exc(_: Request, exc: HTTPException) -> JSONResponse:
        return build_error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # locations + messages only; input values are never echoed back.
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', 'invalid')}"
            for err in exc.errors()
        )
        return build_error(422, "validation failed", detail)

    @app.exception_handler(404)
    async def _not_found(_: Request, exc: HTTPException) -> JSONResponse:
        return build_error(404, "not found")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception) -> JSONResponse:
        return build_error(500, "internal error")

    return app


app = create_app()
