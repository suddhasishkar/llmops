"""Minimal FastAPI wrapper around app.agent.agent.run_turn, for the
"support web/chat UI" channel layer. Not the focus of the lab -- kept
intentionally small.
Run: uvicorn app.api.main:app --reload

MONITORING: this is the one place in the repo that turns on REAL
Application Insights telemetry. infra/main.bicep injects
APPLICATIONINSIGHTS_CONNECTION_STRING into the Container App's
environment -- configure_azure_monitor() (from the
`azure-monitor-opentelemetry` distro) is what plugs in the other end: it
auto-instruments FastAPI's request/response handling and wires up the
OpenTelemetry SDK so any span opened anywhere in the app -- including the
per-turn spans in app/agent/agent.py's run_turn() -- is actually exported
to Application Insights, not just written to a local eval/traces/*.json
file. With no connection string set (which never happens once this app
is deployed via `azd up` -- see Day1_Lab_Guide.md Part 0), this block is
skipped and the app behaves exactly as before.

IDENTITY, STATED PLAINLY: `customer_id` in the request body is trusted
exactly as given -- there is no bearer-token/JWT validation layer in
this rebuild. See `Agent_End_to_End_Architecture.md` Section 7 for what
a real deployment would add here.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

from app.agent.agent import run_turn
from app.agent import tool_policy, tools

_appinsights_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _appinsights_conn_str:
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=_appinsights_conn_str)

app = FastAPI(title="Nimbus Support Agent (lab)")


class ChatRequest(BaseModel):
    message: str
    customer_id: str = "CUST-1002"
    prompt_version: str = "baseline"


class ApprovalRequest(BaseModel):
    request_id: str
    approver: str


@app.get("/healthz")
def healthz():
    """Liveness probe -- see docs/adr/0003-deployment-reliability-and-observability.md.
    Deliberately cheap and dependency-free: answers "is this process up
    and able to handle HTTP at all," nothing more. Container Apps'
    liveness probe (infra/resources.bicep) hits this; a failing pod gets
    restarted, so this must never block on a downstream call."""
    return {"status": "ok"}


_READINESS_ENV_VARS = (
    "LLM_GATEWAY_ENDPOINT",
    "LLM_GATEWAY_API_KEY",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_CONTENT_SAFETY_ENDPOINT",
)


@app.get("/readyz")
def readyz():
    """Readiness probe -- confirms the configuration this process needs
    to actually serve a /chat request is present, WITHOUT making a live
    network call to any of them (a readiness probe that calls out to
    three real Azure services on every poll interval would itself become
    a cost/latency/rate-limit problem -- see the ADR). Container Apps'
    readiness probe uses this to decide whether to route traffic to a
    given revision at all; a revision that never becomes ready never
    receives traffic, which is what makes the canary rollout in
    .github/workflows/ai-release.yml safe to automate."""
    import json

    from fastapi import Response

    missing = [name for name in _READINESS_ENV_VARS if not os.environ.get(name)]
    if missing:
        return Response(
            content=json.dumps({"status": "not_ready", "missing_env": missing}),
            status_code=503,
            media_type="application/json",
        )
    return {"status": "ready"}


@app.post("/chat")
def chat(req: ChatRequest):
    return run_turn(
        req.message,
        customer_id=req.customer_id,
        prompt_version=req.prompt_version,
    )


@app.get("/approvals/pending")
def pending_approvals():
    return [r for r in tools.CREDIT_APPROVAL_REQUESTS.values() if r["state"] == "PENDING_APPROVAL"]


@app.post("/approvals/approve")
def approve(req: ApprovalRequest):
    return tool_policy.approve_credit_request(req.request_id, approver=req.approver)


@app.get("/approvals/ui")
def approvals_ui():
    """Minimal human-approval dashboard -- Phase C's AgentOps governance
    UI (docs/adr/0004-llmops-agentops-rigor.md). Server-rendered, no
    build step, no external CDN dependency (this page has no internet
    access guarantee once deployed behind whatever network boundary a
    real client adds). Calls the same /approvals/pending and
    /approvals/approve JSON endpoints above -- this page adds no new
    authorization logic of its own, it's a UI over the existing API.

    KNOWN, NAMED GAP: this endpoint has no auth of its own -- anyone who
    can reach the api Container App can view pending credit requests and
    approve them. That's consistent with this repo's stated
    presentation-grade scope (see ADR 0001/0002/0003's identical
    caveats), not an oversight -- a real deployment MUST put a real
    identity check in front of this page before using it for anything
    but a demo. Tracked as a Phase D item, not solved here.
    """
    from fastapi.responses import HTMLResponse

    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Nimbus Copilot -- Pending Approvals</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
  h1 { font-size: 1.25rem; }
  .warning { background: #fff3cd; border: 1px solid #ffe69c; padding: 0.75rem 1rem; border-radius: 6px; margin-bottom: 1.5rem; font-size: 0.9rem; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e5e5e5; font-size: 0.9rem; }
  button { padding: 0.35rem 0.9rem; border-radius: 6px; border: 1px solid #2563eb; background: #2563eb; color: white; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  button:disabled { opacity: 0.5; cursor: default; }
  #empty { color: #666; font-style: italic; }
</style>
</head>
<body>
  <h1>Pending credit-approval requests</h1>
  <div class="warning">Presentation-grade demo page -- no login required to view or approve. Do not expose this without adding real identity in front of it. See app/api/main.py's approvals_ui() docstring.</div>
  <table id="table"><thead><tr><th>Request ID</th><th>Customer</th><th>Amount (USD)</th><th>Reason</th><th>Created</th><th></th></tr></thead>
  <tbody id="rows"></tbody></table>
  <p id="empty" style="display:none">No pending requests.</p>
<script>
async function load() {
  const res = await fetch('/approvals/pending');
  const rows = await res.json();
  const tbody = document.getElementById('rows');
  const empty = document.getElementById('empty');
  tbody.innerHTML = '';
  if (rows.length === 0) { empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.request_id}</td><td>${r.customer_id}</td><td>$${r.amount.toFixed(2)}</td><td>${r.reason}</td><td>${r.created_at}</td><td></td>`;
    const btn = document.createElement('button');
    btn.textContent = 'Approve';
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = 'Approving...';
      await fetch('/approvals/approve', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({request_id: r.request_id, approver: 'ui-demo-approver'})
      });
      load();
    };
    tr.lastElementChild.appendChild(btn);
    tbody.appendChild(tr);
  }
}
load();
setInterval(load, 15000);
</script>
</body>
</html>""")
