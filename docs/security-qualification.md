# G17 — Security Qualification Report (2026-11-21)

Scope: Python backend, office frontend, agent interfaces. Method: adversarial
test matrix (tests/test_security_matrix.py), the G10 executor's adversarial
suite (tests/test_capabilities.py), G22 assets/validation, code sweeps, and
dependency scans. No destructive testing against live infrastructure.

## Findings and dispositions

| # | Area | Test/evidence | Disposition |
|---|------|---------------|-------------|
| 1 | Unauthenticated access | Route matrix: `/healthz`, `/api/environment`, `/api/v1/office/state` public by design (display data); everything privileged → 401/403 fail-closed | **No finding** — public surfaces are sanitized read models |
| 2 | Session lifecycle | Cookie `v1.<expiry>.<hmac>`; expiry inclusive at TTL, invalid +1 ms; tampered/skipped/short cookies refused; wrong secret refused | **No finding** |
| 3 | WebSocket | No WS endpoint exists in the Python backend (G16 uses stateless verified polling) | **N/A** by architecture |
| 4 | Identity spoofing / cross-harness impersonation | G10 suite: caller employee_id mismatch denied; unverified/revoked binding denied | **No finding** |
| 5 | Cross-scope access | G10 suite: company/household scope + cross-scope credential refs denied; G11 query filter double-enforced; G12 scope-prefix chokepoint | **No finding** |
| 6 | Credential disclosure | G10 leaky-adapter test: value absent from result/executions/audit; G16 office model: `binding_verified` boolean only; runtime leak-scan asserts no `endpoint`/grants/binding internals in any response | **No finding** |
| 7 | Prompt injection | G10: payload is inert data — authorization fields resolved only from registry/store rows | **No finding** (structural) |
| 8 | Approval bypass / provider fallback / replay | G10 suite covers all three (approvals-before-side-effects, no-fallback with pinned call counts, request-key PK) | **No finding** |
| 9 | XSS | Frontend interpolations in the G16 status chips now HTML-escaped (`esc()`); audit payload echo refused (inputs never echoed); dashboard surfaces render escaped content only | **Fixed this gate** (defense-in-depth: backend values are roster-controlled, not user-controlled) |
| 10 | CSRF | Cookie is `HttpOnly; SameSite=Lax` (JS login route); Lax blocks cross-site POSTs carrying the session cookie; privileged Python POSTs additionally accept bearer auth | **No finding (SameSite=Lax + bearer-only Python writes)** |
| 11 | SSRF / command injection | No `subprocess`/`os.system` and no user-URL fetch anywhere in `src/pixel_startup`; adapters fetch only owner-configured endpoint constants (`config.py`) | **No finding** |
| 12 | Path traversal | G22 asset manifest serving is allowlisted (missing → 404); G12 object keys rejected pre-flight | **No finding** |
| 13 | Malformed input | FastAPI validation returns locations-only errors (never echoes values); executor refines malformed capability requests | **No finding** |
| 14 | Dependency vulnerabilities | `npm audit --omit=dev` → **0 vulnerabilities**; `pip-audit` **not installed** — Python dep scan NOT RUN (recorded below) | **Partial**: JS clean; Python scan outstanding |
| 15 | Secret scanning | `git log`/tree scan: only `075e1f1` (initial release) contains a historical `.env` with one credential-shaped value; current `.env` gitignored; fixtures carry generic defaults | **FINDING (pre-existing)**: rotate that credential + optional history purge — **owner action required** |
| 16 | Audit sanitization | G10 redaction proven across result/executions rows/audit payloads; audit detail carries redaction counts only | **No finding** |
| 17 | Revoked bindings | G10+G14 suites: revoked identities die mid-flight | **No finding** |
| 18 | Stale office-state | G16 `generated_at` + client-side >30 s STALE marker; stale ≠ fabricated | **No finding** |
| 19 | Privileged-infrastructure disclosure | Office read model strips `endpoint` from every provider row (asserted at runtime) | **No finding** |

## Unresolved items requiring owner action
- **G17-F1 (LOW/MED, pre-existing):** rotate the credential committed in `075e1f1`; optional BFG/`git filter-repo` history purge + force-push is available on request (needs separate authorization — it rewrites history).
- **G17-G1 (INFO):** Python dependency vulnerability scan NOT RUN (`pip-audit` not installed on this host); JS deps verified clean.

## Verdict
**PASS with dispositions** — no unresolved critical or high findings in enabled
MVP capabilities. All disabled capabilities (comms/storage/memory live ops,
OpenClaw/Hermes submission) fail closed through their candidate-state
adapters and cannot be reached indirectly through the executor (declared-only
registry) or the public read models.
