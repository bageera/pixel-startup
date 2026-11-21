"""G15: read-only operational-infrastructure adapters (Odoo, HA, Rundeck,
Grafana, GitHub, FreshRSS, LiteLLM, Calendar).

One shared HTTP pattern: injectable request callable (method, path, token) ->
(status, body); typed error normalization; declared READ-ONLY capabilities
(the mandate forbids write-features because the upstream APIs support them);
scope boundaries enforced by the capability executor when operations run;
unconfigured providers stay honest BLOCKED candidates.
"""

import json

READ_ONLY = tuple()


class InfraFailure(Exception):
    """Authorization/validation failure at the provider boundary."""


class InfraUnavailable(Exception):
    """Provider down, malformed, or not configured."""


class _Base:
    provider_id = "base"
    capabilities: tuple[str, ...] = ()

    def __init__(self, base_url: str | None, token: str | None, client=None):
        self._url = (base_url or "").rstrip("/")
        self._token = token
        self._client = client  # injectable (method, path) -> (status, body)
        self.configured = bool(base_url)

    def _request(self, method: str, path: str) -> dict | list:
        if not self.configured:
            raise InfraUnavailable(f"{self.provider_id} not configured; live rows BLOCKED")
        if self._client is None:
            raise InfraUnavailable(f"{self.provider_id} has no live transport")
        try:
            status, body = self._client(method, path)
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise InfraUnavailable(f"{self.provider_id} unreachable") from exc
        if status in (401, 403):
            raise InfraFailure(f"{self.provider_id} rejected the request ({status})")
        if status >= 500:
            raise InfraUnavailable(f"{self.provider_id} service error {status}")
        if status >= 400:
            raise InfraFailure(f"{self.provider_id} rejected the request ({status})")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, ValueError) as exc:
                raise InfraUnavailable(f"{self.provider_id} malformed response") from exc
        if not isinstance(body, (dict, list)):
            raise InfraUnavailable(f"{self.provider_id} malformed response")
        return body

    def health(self) -> dict:
        body = self._request("GET", "/health")
        _ = body
        return {"online": True}

    def invoke(self, operation: str, payload: dict) -> dict:
        if operation not in self.capabilities:
            raise InfraFailure(f"unsupported {self.provider_id} operation: {operation}")
        # declared read-only capability -> documented GET pattern; live providers
        # override path_for() when the real upstream route differs.
        body = self._request("GET", self.path_for(operation, payload))
        return dict(body) if isinstance(body, dict) else {"rows": body}

    def path_for(self, operation: str, payload: dict) -> str:
        base = "/" + operation.replace(".", "/")
        qs = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
        return f"{base}?{qs}" if qs else base


def _named(name: str, capabilities: tuple[str, ...], base_cls=_Base):
    return type(
        name,
        (base_cls,),
        {
            "provider_id": name.lower(),
            "capabilities": capabilities,
        },
    )


OdooProvider = _named("odoo", ("customer.lookup", "projects.list", "invoice.status", "tasks.list"))
HAssistantProvider = _named(
    "homeassistant", ("entity.status", "device.availability", "sensor.read")
)
RundeckProvider = _named("rundeck", ("jobs.discover", "execution.status", "job.metadata"))
GrafanaProvider = _named("grafana", ("dashboards.discover", "health.read"))
GithubProvider = _named("github", ("repo.meta", "issues.list", "prs.list", "workflow.status"))
FreshRSSProvider = _named("freshrss", ("feeds.list", "unread.summaries", "feed.meta"))
LiteLLMProvider = _named("litellm", ("health.read", "models.discover"))
CalendarProvider = _named("calendar", ("events.list",))
# writes are approval-gated capabilities only

PROVIDERS: list[tuple[type[_Base], str]] = [
    (OdooProvider, "odoo_url"),
    (HAssistantProvider, "homeassistant_url"),
    (RundeckProvider, "rundeck_url"),
    (GrafanaProvider, "grafana_url"),
    (GithubProvider, "github_url"),
    (FreshRSSProvider, "freshrss_url"),
    (LiteLLMProvider, "litellm_url"),
    (CalendarProvider, "calendar_url"),
]
