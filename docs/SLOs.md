# Service-level objectives — Nimbus Support Copilot

Presentation-grade targets, not contractual SLAs -- see
`docs/adr/0001-foundry-and-multiagent.md` for why `nimbus-production`
here is not built for real customer traffic. These exist to give the
alerts and dashboard in
`docs/adr/0003-deployment-reliability-and-observability.md` a concrete
target to be measured against, which is the point of naming them at
all: an alert with no stated SLO behind it is just a number someone
picked.

| SLI | SLO target | Backed by |
|---|---|---|
| Availability (`/healthz` reachable) | 99% over any rolling 30 days per environment | `availabilityTest` (Application Insights web test, 5-minute interval, 2 regions) in `infra/resources.bicep` |
| Error rate | Fewer than 5 failed HTTP requests per 5-minute window | `errorRateAlert` (`requests/failed`, App Insights) |
| Latency | Average response time under 5 seconds per 5-minute window | `latencyAlert` (`requests/duration`, App Insights) |
| Deploy safety | Zero unattended full-traffic exposure to a broken revision for longer than the canary soak window | `scripts/canary_deploy.sh` -- 30s soak (dev/staging), 90s soak (production) -- see the known gap noted in ADR 0003 about the brief full-traffic window between `azd deploy` and the canary script's first traffic-weight call |
| Restart stability | Fewer than 2 container restarts per 15 minutes | `restartAlert` (`RestartCount`, native Container Apps metric) |

**Why these numbers and not stricter ones:** this is a demo/reference
architecture running on minimum-budget, scale-to-zero infrastructure
(`containerAppMinReplicas`), not a paid production service -- a cold
start from zero replicas alone can take longer than a stricter latency
SLO would tolerate. Tighten these once `containerAppMinReplicas` is set
to 1+ permanently for a given environment, and treat the values above as
a starting point to negotiate with a real client, not a claim about what
this specific deployment already achieves.

**What's intentionally not covered here:** cost-based SLOs (see
`Platform_Maturity_Roadmap.md` Phase D for budget-alert plans), LLM
response-quality SLOs (covered separately by `release-policy.yaml`'s
evaluation gates, which are pre-deploy, not runtime SLOs), and per-tool
success-rate SLOs (would need `app/agent/cost_tracking.py` or a new
module to emit tool-call outcome metrics -- not built yet).
