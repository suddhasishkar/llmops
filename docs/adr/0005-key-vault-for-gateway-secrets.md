# ADR 0005 — Move gateway/observability secrets into Azure Key Vault

**Status:** Accepted, in progress. **Date:** 2026-08-23.

## Context

`infra/resources.bicep`'s file header has said, since ADR 0001, "No Key
Vault (nothing here holds a secret — every service auths via managed
identity)." That was true at the time. ADR 0002 quietly broke the
premise: it introduced two real secrets — `litellmMasterKey` (the
LiteLLM gateway's shared bearer key) and `langfuseSecretKey` (Langfuse
Cloud's tracing credential) — stored as plain Container App-native
`secrets` (`@secure()` Bicep params → `secretRef`), and nobody revisited
the Key Vault decision or updated the now-stale comment.

This surfaced two concrete problems, not just a stale comment:

1. **No rotation, no audit trail.** Rotating `litellmMasterKey` today
   means a new Bicep deploy, not an API call. Reading it requires only
   Contributor/Reader on the resource group (`az containerapp secret
   list`), with no record of who read it or when.
2. **Trainees can't get `LLM_GATEWAY_API_KEY` onto their own machine.**
   `main.bicep` only outputs `llmGatewayEndpoint`, never the master key
   — so it never lands in `.azure/<env>/.env` via `azd env get-values`,
   the lab guides' one official way to load config. Both Day 1 and Day 2
   run the agent locally, repeatedly (`python -m app.agent.agent ...`,
   the eval scripts), and every one of those calls
   `get_client()` in `app/agent/azure_openai_client.py`, which hard-raises
   `RuntimeError: LLM_GATEWAY_API_KEY is not set` with no local fallback
   left to fall back to.

Rather than patch each of these separately (a Bicep `output` that puts
the key in plaintext in `.env`, or a manual `az containerapp secret show`
step bolted onto the lab guide), this ADR resolves both by finally doing
what the stale comment claimed was unnecessary: adding Key Vault.

## Research findings (verified, cited)

**`Microsoft.KeyVault/vaults` schema, confirmed GA (not Preview).**
Verified against the version-scoped raw ARM schema
(`https://raw.githubusercontent.com/Azure/azure-resource-manager-schemas/main/schemas/2023-07-01/Microsoft.KeyVault.json`,
`$id` confirms it's scoped to `2023-07-01`). Required properties:
`properties.tenantId`, `properties.sku` (`family: 'A'`, `name:
'standard'|'premium'`). `properties.enableRbacAuthorization: true` is
what makes access RBAC-based instead of access-policy-based — when
`true`, any `accessPolicies` block is ignored entirely, consistent with
this repo's existing pattern of granting access via
`Microsoft.Authorization/roleAssignments` for every other resource
(Search, Content Safety, ACR, the audit storage table) rather than a
resource-specific ACL mechanism.

**Key Vault Secrets User role**, confirmed via
[azadvertizer](https://www.azadvertizer.net/azrolesadvertizer/4633458b-17de-408a-b874-0445c86b69e6.html):
GUID `4633458b-17de-408a-b874-0445c86b69e6`, grants exactly
`Microsoft.KeyVault/vaults/secrets/getSecret/action` and
`.../readMetadata/action` — read-only, no write/delete, the correct
least-privilege role for both the app's managed identity and a trainee
who only needs to read the value out, matching the read-only pattern
this repo already uses for `searchIndexDataReaderRoleId` on the app
identity.

**Container Apps' Key Vault secret reference syntax**, confirmed
against the [ARM template reference for
`Microsoft.App/containerApps`](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/containerapps)
and Microsoft's own [manage-secrets
guidance](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets):
a `Secret` object's `name` is the only required field; supplying
`keyVaultUrl` + `identity` instead of `value` reads the secret from Key
Vault at deploy/restart time rather than storing it inline.
`identity` accepts either the literal string `'system'` or the full
resource ID of a user-assigned identity — this repo already uses a
single user-assigned identity (`identity.id`) everywhere, so that's what
gets passed here too, no new identity resource needed. Prerequisite
confirmed on the same page: the referencing identity needs the Key
Vault Secrets User role on the vault *before* the Container App revision
that references it deploys — enforced here the same way this repo
already orders ACR pull and Foundry role assignments, via `dependsOn`.

**Not independently re-verified this pass:** the exact behavior/timing
of Container Apps' documented "auto-rotation within 30 minutes" when a
Key Vault secret value changes without a redeploy — not relied on by
this ADR (every secret change here still goes through a Bicep
deployment), but worth confirming before treating live secret rotation
as a supported operational workflow.

## Decision

1. **Add one `Microsoft.KeyVault/vaults` resource** (`${namePrefix}-kv`,
   `enableRbacAuthorization: true`, `enableSoftDelete: true` with the
   minimum allowed `softDeleteRetentionInDays: 7` — soft-delete can't be
   disabled on current API versions regardless). Same
   "presentation-grade, not network-isolated" `publicNetworkAccess:
   'Enabled'` caveat this repo already states for Foundry and Content
   Safety.
2. **Store `litellmMasterKey` and `langfuseSecretKey` as
   `Microsoft.KeyVault/vaults/secrets` child resources**, named
   `litellm-master-key` and `langfuse-secret-key` — same names the
   Container App secrets already used, so the blast radius of this
   change is "where the value comes from," not "what anything is
   called." `langfusePublicKey` stays a plain env var, unchanged — it
   was never `@secure()` because it isn't a secret.
3. **Grant the existing user-assigned managed identity `Key Vault
   Secrets User`** on the vault — the same identity every Container App
   already uses, no new identity resource. **Also grant the signed-in
   azd user (`principalId`) the same role**, conditional on
   `!empty(principalId)`, exactly mirroring how this repo already grants
   the user Search/Content Safety/audit-storage access alongside the app
   identity. This is what actually resolves the lab-blocking problem:
   a trainee can now run
   `az keyvault secret show --vault-name <name> --name litellm-master-key --query value -o tsv`
   from their own terminal to get `LLM_GATEWAY_API_KEY`, using an RBAC
   grant instead of a value duplicated into `.env` or a Container-App-specific
   CLI command.
4. **Point both Container Apps' `secrets` blocks at Key Vault** instead
   of inline `value`s — `litellmGateway`'s `litellm-master-key` and
   `langfuse-secret-key` entries, and `containerApp`'s
   `llm-gateway-api-key` entry, all become `keyVaultUrl` + `identity:
   identity.id`. The `secretRef`/env-var wiring downstream of that
   (`LITELLM_MASTER_KEY`, `LANGFUSE_SECRET_KEY`, `LLM_GATEWAY_API_KEY`)
   is unchanged — this only changes where the secret's value is read
   from, not how it reaches the running process.
5. **Add `keyVaultSecretsUserForApp`/`keyVaultSecretsUserForUser` to
   both Container Apps' `dependsOn`**, so the RBAC grant lands before
   either app tries to resolve a Key Vault reference at deploy time —
   the same ordering pattern already used for `acrPullAssignment` and
   `openAiRoleForApp`.
6. **Output the vault's name** (`keyVaultName`, threaded through
   `main.bicep` as `AZURE_KEY_VAULT_NAME`) — not a secret itself, just a
   name, exposed via `azd env get-values` the same way
   `AZURE_CONTAINER_APP_API_NAME` already is for `scripts/canary_deploy.sh`.
   This is what the lab guide's new "fetch your gateway key" step reads.
7. **Update `resources.bicep`'s file-header comment**, which has been
   wrong since ADR 0002 shipped — it no longer claims "nothing here
   holds a secret."

## Consequences

- New resources: one `Microsoft.KeyVault/vaults`, two
  `Microsoft.KeyVault/vaults/secrets`, two new
  `Microsoft.Authorization/roleAssignments` (app identity + user,
  mirroring the existing per-resource role-assignment pattern).
- `infra/main.bicep` gains one new output
  (`AZURE_KEY_VAULT_NAME`) — no new input parameters; `litellmMasterKey`
  and `langfuseSecretKey` were already threaded through as `@secure()`
  params, only their destination changes.
- `Day1_Lab_Guide.md` Part 0.7 gains a step showing the `az keyvault
  secret show` command in place of the old (already-broken)
  `LLM_GATEWAY_API_KEY`-from-`.env` assumption — see this repo's own
  action-plan tracking for that guide update, done alongside this ADR.
- **Cost note:** Key Vault's `standard` SKU bills per-operation
  (fractions of a cent per 10K operations) — at lab scale (a handful of
  secret reads per trainee per day) this is effectively free, and adds
  no new idle/always-on cost the way a database or self-hosted service
  would. Consistent with the "minimum budget across three environments"
  constraint every other ADR in this repo has been held to.
- **Teardown note:** like `foundryAccount` and `contentSafety`, Key
  Vault soft-deletes by default and can't be created again with the
  same name until purged or the retention window (7 days here, the
  minimum allowed) elapses. `azure.yaml`'s documented `azd down --purge`
  already handles this for the two existing Cognitive Services
  resources; Key Vault is the same purge-on-delete pattern, not a new
  category of teardown behavior to learn.
- **Named, not solved:** this ADR does not add Key Vault-based
  versioned-secret references (e.g. pinning to a specific secret
  version rather than "latest") or a rotation *policy* — it closes the
  "secrets have no audit trail and can't reach a trainee's shell" gap,
  not the larger "we don't yet have a rotation cadence" question. That
  stays a future, explicitly out-of-scope item, the same way ADR 0003
  named its traffic-rule race window as accepted-but-not-closed rather
  than pretending it away.
