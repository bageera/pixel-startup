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

    def _normalize(self, status: int, body) -> dict | list:
        """Shared response contract: auth/4xx → InfraFailure, 5xx → InfraUnavailable,
        non-JSON / wrong-shape → InfraUnavailable. Never echoes tokens."""
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

    def _request(self, method: str, path: str) -> dict | list:
        if not self.configured:
            raise InfraUnavailable(f"{self.provider_id} not configured; live rows BLOCKED")
        if self._client is None:
            raise InfraUnavailable(f"{self.provider_id} has no live transport")
        try:
            status, body = self._client(method, path)
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise InfraUnavailable(f"{self.provider_id} unreachable") from exc
        return self._normalize(status, body)

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

CHAT_TIMEOUT_SECONDS = 30.0


def _make_post_client():
    """Live POST transport factory (synthetic clients replace the `client=` ctor arg;
    tests monkeypatch THIS, not httpx). Maps httpx errors onto stdlib so the shared
    normalization catch (ConnectionError/TimeoutError) holds."""
    global _POST_CLIENT
    if _POST_CLIENT is None:
        import httpx

        def _post(url: str, token: str | None, path: str, body: dict) -> tuple[int, str]:
            try:
                resp = httpx.request(
                    "POST",
                    f"{url.rstrip('/')}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"} if token else None,
                    timeout=CHAT_TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException as exc:
                raise TimeoutError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise ConnectionError(str(exc)) from exc
            return resp.status_code, resp.text

        _POST_CLIENT = _post
    return _POST_CLIENT


_POST_CLIENT = None


class LiteLLMProvider(_Base):
    """G16/G23: model invocation for office chat + the Chief of Staff perspective.
    chat.completions is a declared write-ish op (invokes an external model, mutates
    no pixel state); other ops stay read-only. Token never appears in results or
    errors."""

    provider_id = "litellm"
    capabilities = ("health.read", "models.discover", "chat.completions")

    def __init__(self, base_url, token, client=None, model: str | None = None):
        super().__init__(base_url, token, client=client)
        self._model = model

    def invoke(self, operation: str, payload: dict) -> dict:
        if not self.configured:
            raise InfraUnavailable(f"{self.provider_id} not configured; live rows BLOCKED")
        if operation == "chat.completions":
            return self._chat(payload)
        return super().invoke(operation, payload)

    def _chat(self, payload: dict) -> dict:
        messages = payload.get("messages")
        if not (
            isinstance(messages, list)
            and messages
            and all(
                isinstance(m, dict)
                and isinstance(m.get("role"), str)
                and isinstance(m.get("content"), str)
                for m in messages
            )
        ):
            raise InfraFailure(
                f"{self.provider_id} chat.completions requires non-empty messages"
                " with string role/content"
            )
        body: dict = {
            "model": payload.get("model") or self._model or "glm-5.3-flash",
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        if isinstance(payload.get("temperature"), (int, float)):
            body["temperature"] = payload["temperature"]
        if isinstance(payload.get("max_tokens"), int):
            body["max_tokens"] = payload["max_tokens"]
        try:
            if self._client is not None:
                status, raw = self._client("POST", "/v1/chat/completions", body)
            else:
                post = _make_post_client()
                status, raw = post(self._url, self._token, "/v1/chat/completions", body)
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise InfraUnavailable(f"{self.provider_id} unreachable") from exc
        data = self._normalize(status, raw)
        if not isinstance(data, dict):
            raise InfraUnavailable(f"{self.provider_id} malformed response")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") if isinstance(choice, dict) else None
        if not isinstance(content, str):
            raise InfraUnavailable(f"{self.provider_id} malformed response")
        return {"content": content, "model": data.get("model") or body["model"]}


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
