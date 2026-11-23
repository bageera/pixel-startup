"""OpenClaw gateway adapter (G7, execution surface added 2026-11-23).

Interface discipline (mandate): use ONLY documented surfaces. Two are:
- `GET {gateway}/v1/models`      — OpenAI-compatible model listing (G7 probe).
- `POST {gateway}/v1/chat/completions` — OpenAI-compatible single-turn
  completion. This is the documented compatibility surface of the gateway,
  so the harness now executes tasks on it: `task.submit` = one
  chat-completion round with the task objective, `task.result` = the stored
  response for that task id.

The gateway's WebSocket RPC surface (hello-ok snapshot.presence and friends)
is NOT implemented here — operations that only exist on WS return
`supported: False` explicitly; no endpoints or event schemas are invented.
Connectivity failures are honest offline results, never simulated successes.
"""

import os
import time

import httpx

WS_ONLY_OPERATIONS = (
    "session.status",
    "task.cancel",
    "activity.observe",
    "native_agent.discover",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class OpenClawGatewayAdapter:
    """G5 handler contract: invoke(operation, payload) -> dict."""

    provider_id = "openclaw_gateway"
    category = "agent_harness"
    implementation = "python"

    def __init__(
        self,
        gateway_url: str | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 2.5,
        execution_timeout: float = 60.0,
        default_model: str | None = None,
    ) -> None:
        self.gateway_url = (
            gateway_url or os.environ.get("OPENCLAW_GATEWAY_URL") or "http://127.0.0.1:18789"
        ).rstrip("/")
        self.token = token if token is not None else os.environ.get("OPENCLAW_GATEWAY_TOKEN")
        self.timeout = timeout
        self.execution_timeout = execution_timeout
        self.default_model = default_model or os.environ.get("OPENCLAW_DEFAULT_MODEL")
        self._transport = transport  # injectable MockTransport for contract tests
        self._results: dict[str, dict] = {}  # task_id -> last execution result

    def _client(self, timeout: float) -> httpx.Client:
        client_kwargs: dict = {"timeout": timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        return httpx.Client(**client_kwargs)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def probe_models(self) -> dict:
        try:
            with self._client(self.timeout) as client:
                response = client.get(f"{self.gateway_url}/v1/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return {"online": False, "auth": "n/a", "models": [], "error": type(exc).__name__}
        if response.status_code == 401:
            return {"online": True, "auth": "rejected", "models": []}
        if response.status_code != 200:
            return {
                "online": True,
                "auth": "n/a",
                "models": [],
                "error": f"HTTP {response.status_code}",
            }
        try:
            body = response.json()
            models = [m["id"] for m in body.get("data", []) if isinstance(m, dict) and "id" in m]
        except ValueError:
            return {"online": True, "auth": "ok", "models": [], "error": "non-JSON body"}
        return {"online": True, "auth": "ok", "models": models}

    def _execute(self, task_id: str, objective: str, model: str | None) -> dict:
        """One chat completion = one task execution. Single-turn, synchronous."""
        body = {
            "model": model or self.default_model or "",
            "messages": [{"role": "user", "content": objective}],
        }
        if not body["model"]:
            probe = self.probe_models()
            if probe["models"]:
                body["model"] = probe["models"][0]
        try:
            with self._client(self.execution_timeout) as client:
                response = client.post(
                    f"{self.gateway_url}/v1/chat/completions", json=body, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            return {"state": "failed", "error": f"gateway unreachable: {type(exc).__name__}"}
        if response.status_code == 401:
            return {"state": "failed", "error": "gateway auth rejected"}
        if response.status_code != 200:
            return {"state": "failed", "error": f"gateway HTTP {response.status_code}"}
        try:
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            output = message.get("content") or message.get("reasoning_content") or ""
        except ValueError:
            return {"state": "failed", "error": "non-JSON body from gateway"}
        return {"state": "completed", "output": output, "model": data.get("model")}

    def invoke(self, operation: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        if operation in ("gateway.health", "agent.discovery"):
            probe = self.probe_models()
            if operation == "gateway.health":
                return {"supported": True, "kind": "health", "checked_at": _now(), "result": probe}
            if not probe["online"]:
                return {
                    "supported": True,
                    "kind": "discovery",
                    "checked_at": _now(),
                    "result": {
                        "gateway_models": [],
                        "native_agent_discovery": "unavailable: gateway offline",
                    },
                }
            return {
                "supported": True,
                "kind": "discovery",
                "checked_at": _now(),
                "result": {
                    "gateway_models": probe["models"],
                    "native_agent_discovery": (
                        "unsupported: model listing is the documented surface at this gateway"
                    ),
                },
            }
        if operation == "task.submit":
            task_id = payload.get("task_id") or ""
            objective = payload.get("objective") or ""
            if not task_id or not objective:
                return {
                    "supported": False,
                    "operation": operation,
                    "reason": "task_id and objective required",
                }
            probe = self.probe_models()
            if not probe["online"]:
                return {
                    "supported": False,
                    "operation": operation,
                    "reason": "gateway offline",
                }
            model = payload.get("model") or (probe["models"][0] if probe["models"] else None)
            result = self._execute(task_id, objective, model)
            self._results[task_id] = result
            return {
                "supported": True,
                "kind": "execution",
                "checked_at": _now(),
                "result": {"taskId": task_id, **result},
            }
        if operation == "task.result":
            task_id = payload.get("task_id") or ""
            if task_id not in self._results:
                return {
                    "supported": False,
                    "operation": operation,
                    "reason": "no recorded execution for this task (single-shot sync surface)",
                }
            return {
                "supported": True,
                "kind": "execution_result",
                "checked_at": _now(),
                "result": {"taskId": task_id, **self._results[task_id]},
            }
        if operation in WS_ONLY_OPERATIONS:
            return {
                "supported": False,
                "operation": operation,
                "reason": (
                    "this gateway surface documents only OpenAI-compatible HTTP; "
                    "the WebSocket RPC surface is not implemented in this adapter"
                ),
            }
        return {"supported": False, "operation": operation, "reason": "unknown operation"}
