# ADR 0003 — Deployment reliability (canary + rollback) and runtime observability (probes, alerts, synthetic monitoring)

**Status:** Accepted, in progress. **Date:** 2026-08-20.

## Context

This is Phase B of `Platform_Maturity_Roadmap.md`'s sequencing, run
after Phase 0 (ADR 0001, ADR 0002). Before this pass, neither Container
App had liveness/readiness probes, both deployed with
`activeRevisionsMode: 'Single'` (a bad deploy overwrites the only
serving revision immediately, no rollback path), and the only alert in
the whole system was a restart-count alert. That's a real gap for
anything presented as "moderately mature" — this ADR closes it.

## Research findings (verified, cited)

**LiteLLM health endpoints**, confirmed directly from LiteLLM's own
source (`litellm/proxy/health_endpoints/_health_endpoints.py`, `main`
branch): `/health/liveliness` and `/health/readiness` are
unauthenticated, dependency-free (never call out to Foundry), and
distinct from the generic `/health` endpoint, which LiteLLM's own code
comments say NOT to use for probing ("🚨 USE `/health/liveliness` to
health check the proxy 🚨") because it makes live backend calls. Used
exactly as documented in `infra/resources.bicep`'s `litellmGateway`
probes.

**Container Apps metrics — chose Application Insights over native ACA
metrics for alerting.** `Microsoft.App/containerApps` does expose a
native `Requests` metric with a `StatusCodeCategory` dimension and a
`ResponseTime` metric, confirmed via [Microsoft Learn's supported-metrics
page](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-app-containerapps-metrics)
— but `ResponseTime` is explicitly marked Preview, and the exact
enumerated values of the `StatusCodeCategory` dimension (e.g. whether
it's literally `"5xx"`) are not documented on that page. Rather than
hardcode an unverified dimension-filter value into a metric alert that
would deploy successfully but silently never fire if the value is
wrong, `errorRateAlert` and `latencyAlert` use the Application
Insights-based `requests/failed` and `requests/duration` metrics
instead — both GA, both documented with exact names and units on
[Microsoft Learn's `microsoft.insights/components` metrics
page](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-insights-components-metrics).
This app already emits these via the existing
`azure-monitor-opentelemetry` instrumentation
(`app/api/main.py`) — no new instrumentation needed.

**`az containerapp` commands for traffic/revision control**, confirmed
against [Microsoft Learn's CLI
reference](https://learn.microsoft.com/en-us/cli/azure/containerapp/ingress/traffic)
and the [Container Apps revisions REST
schema](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/container-apps-revisions/list-revisions):
`az containerapp ingress traffic set --revision-weight <name>=<weight> ...`
(space-separated `name=weight` pairs), `az containerapp revision list`,
`az containerapp revision show --query properties.fqdn`, and
`az containerapp revision deactivate` all exist and were used with
their confirmed syntax in `scripts/canary_deploy.sh`. The script's exact
JMESPath query for "newest two active revisions" uses confirmed
property names (`properties.active`, `properties.createdTime`) but is
not itself a documented Microsoft example — flagged in the script's own
comments rather than asserted as tested-by-Microsoft.

**Probe schema for `Microsoft.App/containerApps@2023-11-02-preview`**
was verified against the version-exact generated ARM schema
(`https://raw.githubusercontent.com/Azure/azure-resource-manager-schemas/main/schemas/2023-11-02-preview/Microsoft.App.json`,
`$id` confirms it's scoped to this exact API version) after
`learn.microsoft.com` itself was unreachable from this research
environment. `probes` on `Container`, and `ContainerAppProbe`'s
`type` (`Liveness`/`Readiness`/`Startup`), `httpGet.path`,
`httpGet.port`, `initialDelaySeconds`, `periodSeconds`, and
`failureThreshold` all confirmed present with exactly the casing used
in `infra/resources.bicep`.

**Availability web test schema** (`Microsoft.Insights/webtests`) was
not re-verified against current docs in this pass — it follows the
long-standing, widely-used ping-test ARM pattern, but the two
`Locations` IDs used (`us-tx-sn1-azr`, `emea-nl-ams-azr`) should be
confirmed against the live `TestLocations` list before treating the
resulting alert as production-trustworthy — see the caveat comment
directly above the resource in `infra/resources.bicep`.

## Decision

1. **Add liveness + readiness probes** to both Container Apps.
   `app/api/main.py` gained a real `/readyz` endpoint (checks required
   env vars are present, no live network call — a readiness probe that
   calls out to three Azure services on every poll interval would
   itself become a cost/rate-limit problem) alongside the existing
   dependency-free `/healthz`.
2. **Switch both Container Apps to `activeRevisionsMode: 'Multiple'`**
   — required for weighted-traffic canary releases; `Single` mode makes
   rollback impossible by construction (there's only ever one revision).
3. **Automate canary + rollback in CI**, via `scripts/canary_deploy.sh`:
   after `azd deploy` creates a new revision, shift a small percentage
   of traffic to it, smoke-test the new revision's own FQDN directly
   with `scripts/verify_deployment.py`, then either promote to 100%
   (and deactivate the old revision) or roll back to 100% on the
   previous revision and fail the pipeline. Production uses a smaller
   initial slice and longer soak (10%, 90s) than dev/staging (50%, 30s).
4. **Add two new alerts** (`errorRateAlert`, `latencyAlert`) backed by
   GA Application Insights metrics, plus one **synthetic availability
   test** pinging `/healthz` every 5 minutes from two regions —
   independent of real user traffic, which matters for environments
   that may sit idle between demos.
5. **Name explicit SLO targets** (`docs/SLOs.md`) so the alerts above
   are measured against a stated target instead of an arbitrary number.

## Consequences

- **Known, accepted gap — not solved in this pass:** `infra/resources.bicep`'s
  traffic rule (`{ latestRevision: true, weight: 100 }`) means a newly
  deployed revision receives 100% of traffic the instant it's healthy,
  *before* `scripts/canary_deploy.sh` gets to shift it down to a canary
  slice. Bicep's declarative traffic model can't express "0% until an
  external script verifies it" — only imperative `az containerapp
  ingress traffic set` calls can, and those can only run after the
  revision already exists. This script narrows that exposure window as
  much as possible (it's the very next CI step after `azd deploy`) but
  does not eliminate it. A stricter version would deploy with the new
  revision intentionally suffixed and initially inactive, then
  explicitly activate it into a 0%-traffic canary slot — left as a
  documented follow-up, not built here, to avoid shipping unverified
  `az containerapp` revision-suffix/activation syntax in a client-facing
  asset without the same verification rigor applied to everything else
  in this ADR.
- `app/api/main.py`, `infra/resources.bicep`, `infra/main.bicep`,
  `.github/workflows/ai-release.yml`, and `scripts/canary_deploy.sh`
  (new file) all changed — see each file's own comments for specifics.
- `release-policy.yaml` and the roadmap's Phase B row should be updated
  to reference this ADR — tracked, not done in this pass.
- Cost impact: negligible. Probes and alerts don't provision new
  billable resources beyond the one new `webtests` resource (free tier
  covers low-frequency ping tests) and `deactivate`d old revisions
  actively reduce idle replica cost rather than adding to it.
