// infra/resources.bicep -- everything that actually gets created, at
// resource-group scope. Deployed by infra/main.bicep as a module.
//
// Key Vault (below) holds the two real secrets this repo has --
// litellmMasterKey and langfuseSecretKey, both introduced by ADR 0002.
// Every OTHER service still auths via managed identity only, no key.
// See docs/adr/0005-key-vault-for-gateway-secrets.md for why this
// changed from the original "no Key Vault, nothing holds a secret"
// design and what it resolves. No per-service on/off toggles (Microsoft
// Foundry, Azure AI Search, and Azure AI Content Safety are all
// required, always deployed). Sizing (SKUs, capacity, min replicas) is
// parametrized per environment -- see docs/adr/0001-foundry-and-multiagent.md's
// environment-sizing table and infra/main.parameters.json for the
// dev/staging/production values. This keeps ONE template correct for
// all three environments rather than forking the file.
//
// The model layer is a Microsoft Foundry resource
// (Microsoft.CognitiveServices/accounts, kind: 'AIServices',
// allowProjectManagement: true) plus a child Foundry project -- not a
// bare kind:'OpenAI' account. It still exposes the same
// Azure-OpenAI-compatible inference endpoint app/agent/azure_openai_client.py
// already calls; only this file's resource shape changed. See ADR 0001
// for the research this is grounded in and its citations.

@description('Azure region')
param location string

@description('Short unique token used in every globally-unique resource name (ACR, Foundry, Search, Content Safety all require global uniqueness)')
param resourceToken string

@description('Tags applied to every resource -- azd uses the azd-env-name tag to find its own resources on `azd down`')
param tags object

@description('Email address the restart alert pages. Empty deploys the alert with no receiver.')
param alertEmail string = ''

@description('Object ID of the signed-in azd user, granted read/query access to Search, Foundry, and Content Safety alongside the app identity')
param principalId string = ''

@description('Azure AI Search SKU. Only ONE Free-tier Search service is allowed per subscription -- use "free" for exactly one environment (dev) and "basic" for the others. See ADR 0001.')
@allowed(['free', 'basic', 'standard'])
param searchSku string = 'basic'

@description('Foundry model deployment capacity, in thousands-of-tokens-per-minute. Keep this low (1-3) for non-production environments to minimize cost.')
param openAiCapacity int = 1

@description('Azure AI Content Safety SKU. F0 is the free tier if your subscription has free-tier quota available; falls back to S0 otherwise.')
@allowed(['F0', 'S0'])
param contentSafetySku string = 'S0'

@description('Minimum Container App replicas. 0 = scale to zero when idle (recommended for dev/staging); 1 = always-warm (recommended only while actively presenting production).')
param containerAppMinReplicas int = 0

@description('LiteLLM Proxy gateway master key -- the shared credential the api container app uses to call the gateway container app. Deterministic per environment (derived from resourceToken) rather than a random secret, matching this repo\'s presentation-grade scope. See docs/adr/0002-llm-gateway-and-observability.md.')
@secure()
param litellmMasterKey string = guid(resourceToken, 'litellm-master-key')

@description('Langfuse Cloud (free Hobby tier, MIT-licensed core -- see ADR 0002) public key for LLM trace observability. Leave empty to run the gateway without tracing.')
param langfusePublicKey string = ''

@description('Langfuse Cloud secret key. Leave empty to run the gateway without tracing.')
@secure()
param langfuseSecretKey string = ''

@description('Langfuse host. Defaults to Langfuse Cloud; override only if self-hosting Langfuse instead (not this repo\'s default -- see ADR 0002).')
param langfuseHost string = 'https://cloud.langfuse.com'

@description('Escape hatch for a real Azure race between the Foundry account\'s managed identity propagating and project creation validating it -- see main.bicep\'s param doc-comment and Day1_Lab_Guide.md\'s troubleshooting table for the two-phase `azd provision` workaround. Leave true for a normal deployment.')
param deployFoundryProject bool = true

var namePrefix = 'nimbus-${resourceToken}'

// ---------------------------------------------------------------------
// OBSERVABILITY
// ---------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-law'
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------
// CONTAINER PLATFORM
// ---------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${namePrefix}acr', '-', '')
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // managed identity only, never admin credentials
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-11-02-preview' = {
  name: '${namePrefix}-cae'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-identity'
  location: location
  tags: tags
}

var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------
// KEY VAULT -- holds the two real secrets this repo has:
// litellmMasterKey and langfuseSecretKey (both ADR 0002). RBAC
// authorization (enableRbacAuthorization: true), not access policies --
// consistent with every other resource in this file, which grants
// access via Microsoft.Authorization/roleAssignments rather than a
// resource-specific ACL. See docs/adr/0005-key-vault-for-gateway-secrets.md
// for the schema verification and why this was added after ADR 0002
// shipped secrets without it.
// ---------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${namePrefix}-kv'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7 // minimum allowed -- soft-delete itself can't be disabled on this API version
    publicNetworkAccess: 'Enabled' // presentation-grade default -- same caveat this file already states for Foundry/Content Safety
  }
}

resource litellmMasterKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'litellm-master-key'
  properties: {
    value: litellmMasterKey
  }
}

resource langfuseSecretKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'langfuse-secret-key'
  properties: {
    value: empty(langfuseSecretKey) ? 'unset' : langfuseSecretKey
  }
}

var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

// Both Container Apps below reference these secrets via keyVaultUrl +
// identity: identity.id -- this grant has to exist before either app's
// revision deploys, enforced via dependsOn on both resources.
resource keyVaultSecretsUserForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Lets a trainee run, from their own terminal:
//   az keyvault secret show --vault-name <AZURE_KEY_VAULT_NAME> --name litellm-master-key --query value -o tsv
// to get LLM_GATEWAY_API_KEY locally -- this is what actually resolves
// the "local agent runs can't reach this secret" gap named in ADR 0005,
// mirroring how every other resource in this file grants principalId
// the same access as the app identity (search, content safety, audit
// storage).
resource keyVaultSecretsUserForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(keyVault.id, principalId, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// MICROSOFT FOUNDRY -- required, always deployed. Backs
// app/agent/azure_openai_client.py (unchanged -- a Foundry AIServices
// account exposes the same Azure-OpenAI-compatible inference endpoint a
// bare kind:'OpenAI' account did). The `allowProjectManagement: true` +
// child `projects` resource below is what makes this a real Foundry
// resource rather than a plain Cognitive Services multi-service account
// -- see docs/adr/0001-foundry-and-multiagent.md for the exact API
// version this was verified against and why. One shared deployment
// serves the Manager, BillingAgent, and AccountAgent (app/agent/*.py) --
// different system prompts, same underlying model, to keep cost down.
// ---------------------------------------------------------------------

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${namePrefix}-foundry'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  // SystemAssigned identity is REQUIRED here, not optional, because
  // allowProjectManagement: true below creates a child `projects`
  // resource (foundryProject) -- Azure rejects that child-resource
  // creation outright with "BadRequest: Unsupported configuration. To
  // create projects, you must enable a managed identity on your
  // resource" if the parent account has no identity at all. This
  // account's own properties still never READ that identity (no CMK/
  // encryption, no outbound data-source reference) -- every other
  // identity reference in this file is the shared `identity` resource
  // being GRANTED a role to call INTO this account (openAiRoleForApp,
  // below) -- but Azure requires SOME identity to exist for project
  // creation to succeed regardless of whether this template ever uses
  // it directly. A `UserAssigned` block here was previously confirmed
  // to be rejected outright -- Cognitive Services accounts of kind
  // 'AIServices' at this API version only accept "None,SystemAssigned"
  // -- so SystemAssigned is the only identity type that both satisfies
  // the projects requirement above and passes that validation.
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: '${namePrefix}-foundry'
    publicNetworkAccess: 'Enabled' // presentation-grade default -- add a private endpoint before treating this as production
  }
}

// Conditional on deployFoundryProject (default true, so a normal `azd up`
// creates this in the same pass) purely as an escape hatch for the
// account-identity-propagation race described on that param above --
// nothing else in this file depends on this resource existing, so
// skipping it here is always safe to do temporarily.
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = if (deployFoundryProject) {
  parent: foundryAccount
  name: '${namePrefix}-project'
  location: location
  tags: tags
  properties: {
    displayName: 'Nimbus Support Copilot'
    description: 'Manager/Specialist support-agent project -- see docs/adr/0001-foundry-and-multiagent.md'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundryAccount
  name: 'nimbus-copilot-chat'
  // 'Standard' (plain regional) is not an offered deployment type for
  // gpt-5-mini at all -- confirmed against Microsoft's per-model region/
  // deployment-type availability table -- only the global and data-zone
  // variants (GlobalStandard, DataZoneStandard, and their Provisioned-
  // Managed counterparts) are. GlobalStandard routes requests to
  // whichever region has capacity rather than pinning to `location`;
  // that's a real behavior change from the old regional Standard
  // deployment, not just a config rename -- acceptable for this
  // presentation-grade lab, worth naming if this pattern gets reused
  // somewhere data residency matters.
  sku: { name: 'GlobalStandard', capacity: openAiCapacity }
  properties: {
    // gpt-4o-mini (2024-07-18) was retired for new Standard deployments
    // 2026-03-31 ("ServiceModelDeprecated"); gpt-5-mini (2025-08-07) is
    // Microsoft's current GA replacement for the 4o-mini/4.1-mini tier
    // as of this pass -- verified against live deployment errors and
    // Microsoft Q&A guidance, not assumed. Re-check regional capacity
    // for gpt-5-mini in your target AZURE_LOCATION before relying on
    // Day1_Lab_Guide.md Part 1's region list, which still documents
    // gpt-4o-mini capacity and has not been re-verified for gpt-5-mini.
    model: { format: 'OpenAI', name: 'gpt-5-mini', version: '2025-08-07' }
  }
}

var openAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

resource openAiRoleForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, identity.id, 'CognitiveServicesOpenAIUser')
  scope: foundryAccount
  properties: {
    roleDefinitionId: openAiUserRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource openAiRoleForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(foundryAccount.id, principalId, 'CognitiveServicesOpenAIUser')
  scope: foundryAccount
  properties: {
    roleDefinitionId: openAiUserRoleId
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// LLM GATEWAY -- LiteLLM Proxy (MIT license, github.com/BerriAI/litellm),
// required, always deployed alongside the api container app. Every
// agent role (app/agent/manager_agent.py, billing_agent.py,
// account_agent.py) calls THIS instead of Foundry directly, via
// app/agent/azure_openai_client.py -- centralizes routing, request
// logging, and LLM tracing (Langfuse, below) in one place instead of
// instrumenting three agent modules separately. Stateless -- no
// Postgres provisioned for it -- so per-caller virtual keys/budgets
// aren't available, only one shared master key; see
// docs/adr/0002-llm-gateway-and-observability.md for that trade-off.
//
// External ingress, same as the api container app below, protected
// only by the master key -- not network-isolated. Presentation-grade
// default, the same caveat already noted on Foundry/Content Safety
// above -- add private networking/VNet integration before treating
// this as a real production boundary.
// ---------------------------------------------------------------------

resource litellmGateway 'Microsoft.App/containerApps@2023-11-02-preview' = {
  name: '${namePrefix}-gateway'
  location: location
  // azd-service-name is required here, same as the api container app
  // below -- without it `azd deploy`/`azd up` has no way to match the
  // `gateway` service (azure.yaml) to THIS resource, so it can never
  // overwrite the mcr.microsoft.com/azuredocs/containerapps-helloworld
  // bootstrap image below with the real LiteLLM build. Missing this tag
  // was a real bug: it silently left every environment's gateway stuck
  // on the placeholder image forever, listening on :80 instead of the
  // real proxy on :4000.
  tags: union(tags, { 'azd-service-name': 'gateway' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      // Multiple, not Single -- required for the weighted-traffic
      // canary rollout in .github/workflows/ai-release.yml's
      // deploy-* jobs. Default traffic rule below still routes 100% to
      // whatever revision is latest; the workflow temporarily overrides
      // that during each canary window. See ADR 0003.
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: 4000
        traffic: [
          { latestRevision: true, weight: 100 }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
      // Both read from Key Vault (docs/adr/0005-key-vault-for-gateway-secrets.md)
      // rather than an inline value -- identity: identity.id is the
      // same user-assigned identity used everywhere else in this file,
      // granted Key Vault Secrets User below via keyVaultSecretsUserForApp.
      secrets: [
        { name: 'litellm-master-key', keyVaultUrl: '${keyVault.properties.vaultUri}secrets/litellm-master-key', identity: identity.id }
        { name: 'langfuse-secret-key', keyVaultUrl: '${keyVault.properties.vaultUri}secrets/langfuse-secret-key', identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'litellm-gateway'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' // overwritten by `azd deploy`'s gateway service build -- same bootstrap-then-overwrite pattern as the api container app below
          // 1 vCPU / 4Gi, not the api container app's smaller footprint --
          // this matches LiteLLM's own published minimum
          // (https://docs.litellm.ai/docs/proxy/prod: "Give each pod 1 vCPU
          // and 4Gi of memory... provision below 4Gi and a single large
          // write is enough to push the pod past its limit and have the
          // kernel OOM-kill it, which surfaces as a crash loop"). This was
          // previously 0.5 vCPU / 1Gi, which is a real bug, not a
          // presentation-grade simplification: the gateway container
          // OOM-kills during its own startup import (Prisma's query engine
          // + the rest of the litellm package) before it ever binds port
          // 4000, so Container Apps never gets a successful liveness probe
          // and the revision permanently shows `ActivationFailed` /
          // `Deployment Progress Deadline Exceeded` -- an OOM-kill is a
          // kernel SIGKILL, so the container also never gets a chance to
          // write a single log line, which is why this failure mode looks
          // completely silent in `az containerapp logs show`.
          resources: {
            cpu: json('1.0')
            memory: '4Gi'
          }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: foundryAccount.properties.endpoint }
            { name: 'AZURE_CREDENTIAL', value: 'ManagedIdentityCredential' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'LITELLM_MASTER_KEY', secretRef: 'litellm-master-key' }
            { name: 'LANGFUSE_PUBLIC_KEY', value: langfusePublicKey }
            { name: 'LANGFUSE_SECRET_KEY', secretRef: 'langfuse-secret-key' }
            { name: 'LANGFUSE_HOST', value: langfuseHost }
          ]
          // Both paths verified against LiteLLM's own source
          // (litellm/proxy/health_endpoints/_health_endpoints.py) --
          // unauthenticated, dependency-free, never call out to Foundry.
          // See docs/adr/0003-deployment-reliability-and-observability.md.
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health/liveliness', port: 4000 }
              initialDelaySeconds: 10
              periodSeconds: 15
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/readiness', port: 4000 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: containerAppMinReplicas
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency-autoscale'
            http: {
              metadata: { concurrentRequests: '20' }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPullAssignment
    openAiRoleForApp
    keyVaultSecretsUserForApp
  ]
}

// ---------------------------------------------------------------------
// AZURE AI SEARCH -- required, always deployed. Backs
// app/retrieval/retrieval.py, which has no local index to fall back to.
// The index itself (fields/schema) is created by
// scripts/build_search_index.py in the postprovision hook, not here --
// Bicep has no stable resource type for "a search index," only the
// service that hosts one.
// ---------------------------------------------------------------------

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${namePrefix}-search'
  location: location
  tags: tags
  sku: { name: searchSku }
  // No identity block: same reasoning as foundryAccount above, plus a
  // confirmed hard RP restriction -- Microsoft.Search/searchServices at
  // this API version does not accept a pure UserAssigned identity at
  // all (only None/SystemAssigned; combined SystemAssigned+UserAssigned
  // isn't accepted either). A copy-pasted UserAssigned block here was
  // the other resource named in the same preflight validation failure.
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    authOptions: {
      aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' }
    }
  }
}

var searchIndexDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
var searchServiceContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
var searchIndexDataReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')

// The app's own managed identity only ever QUERIES the index at request
// time -- read-only, deliberately narrower than what
// scripts/build_search_index.py needs.
resource searchRoleForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, identity.id, 'SearchIndexDataReader')
  scope: search
  properties: {
    roleDefinitionId: searchIndexDataReaderRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// The signed-in azd user gets write access -- this is what lets
// `azd up`'s postprovision hook (running as YOU, via `az`/`azd` login, not
// as the app identity) create the index schema and upload documents, and
// what lets scripts/inject_stale_doc.py and scripts/build_search_index.py
// keep working from your own machine throughout both labs.
resource searchIndexRoleForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(search.id, principalId, 'SearchIndexDataContributor')
  scope: search
  properties: {
    roleDefinitionId: searchIndexDataContributorRoleId
    principalId: principalId
    principalType: 'User'
  }
}

resource searchServiceRoleForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(search.id, principalId, 'SearchServiceContributor')
  scope: search
  properties: {
    roleDefinitionId: searchServiceContributorRoleId
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// AZURE AI CONTENT SAFETY -- required, always deployed. Backs
// app/agent/content_safety.py, which has no stub to fall back to.
// ---------------------------------------------------------------------

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${namePrefix}-safety'
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: { name: contentSafetySku }
  // No identity block -- same reasoning as foundryAccount above. This
  // account's own identity wasn't named in this validation pass's error
  // (different API version than foundryAccount's), but it was equally
  // unused, so removing it here too rather than leaving a latent copy
  // of the same footgun for the next API-version bump.
  properties: {
    customSubDomainName: '${namePrefix}-safety'
    publicNetworkAccess: 'Enabled' // presentation-grade default -- see foundryAccount's identical caveat above
  }
}

var cognitiveServicesUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource contentSafetyRoleForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(contentSafety.id, identity.id, 'CognitiveServicesUser')
  scope: contentSafety
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource contentSafetyRoleForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(contentSafety.id, principalId, 'CognitiveServicesUser')
  scope: contentSafety
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleId
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// AUDIT TRAIL STORAGE -- required, always deployed. Backs
// app/agent/audit_log.py's persistent governance record (tool-call
// attempts/allows/denies, credit requests/approvals, safety blocks) --
// separate from and in addition to app/agent/tools.py's in-memory mock
// business data. Standard_LRS is the cheapest redundancy tier
// (appropriate for a presentation-grade audit trail, not a compliance
// system of record) -- see docs/adr/0004-llmops-agentops-rigor.md.
// ---------------------------------------------------------------------

resource auditStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: replace('${namePrefix}aud', '-', '')
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource auditTableService 'Microsoft.Storage/storageAccounts/tableServices@2023-01-01' = {
  parent: auditStorage
  name: 'default'
}

resource auditTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-01-01' = {
  parent: auditTableService
  name: 'auditlog'
}

var storageTableDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')

resource auditStorageRoleForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(auditStorage.id, identity.id, 'StorageTableDataContributor')
  scope: auditStorage
  properties: {
    roleDefinitionId: storageTableDataContributorRoleId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource auditStorageRoleForUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(auditStorage.id, principalId, 'StorageTableDataContributor')
  scope: auditStorage
  properties: {
    roleDefinitionId: storageTableDataContributorRoleId
    principalId: principalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------------
// THE CONTAINER APP -- azd recognizes it as service `api` (azure.yaml)
// via the azd-service-name tag below, deploys a public bootstrap image
// the first time (`azd provision`), then immediately overwrites it with
// the real built image (`azd deploy`, also part of `azd up`). No
// NIMBUS_*_BACKEND env vars anywhere -- there is nothing left to select
// between.
// ---------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2023-11-02-preview' = {
  name: '${namePrefix}-api'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      // Multiple, not Single -- required for the weighted-traffic
      // canary rollout in .github/workflows/ai-release.yml's deploy-*
      // jobs (new revision gets a small traffic slice, gets smoke
      // tested via scripts/verify_deployment.py pointed at its own
      // revision FQDN, then is promoted to 100% or rolled back by
      // restoring the previous revision's weight). Default traffic rule
      // below still routes 100% to whatever revision is latest -- the
      // workflow only overrides that temporarily during each canary
      // window. See docs/adr/0003-deployment-reliability-and-observability.md.
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: 8000
        traffic: [
          { latestRevision: true, weight: 100 }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
      // Same underlying secret as litellmGateway's 'litellm-master-key'
      // above, read from Key Vault under this app's own secret name --
      // see docs/adr/0005-key-vault-for-gateway-secrets.md.
      secrets: [
        { name: 'llm-gateway-api-key', keyVaultUrl: '${keyVault.properties.vaultUri}secrets/litellm-master-key', identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'nimbus-copilot'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
            { name: 'OTEL_SERVICE_NAME', value: 'nimbus-support-copilot' }
            // The agent calls the LiteLLM gateway, never Foundry
            // directly -- see app/agent/azure_openai_client.py and
            // docs/adr/0002-llm-gateway-and-observability.md.
            { name: 'LLM_GATEWAY_ENDPOINT', value: 'https://${litellmGateway.properties.configuration.ingress.fqdn}' }
            { name: 'LLM_GATEWAY_API_KEY', secretRef: 'llm-gateway-api-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: chatDeployment.name }
            { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
            { name: 'AZURE_CONTENT_SAFETY_ENDPOINT', value: contentSafety.properties.endpoint }
            // Persistent audit trail (app/agent/audit_log.py) -- see
            // docs/adr/0004-llmops-agentops-rigor.md.
            { name: 'AZURE_STORAGE_TABLE_ENDPOINT', value: auditStorage.properties.primaryEndpoints.table }
            // Search and Content Safety still auth via managed identity
            // directly -- only the LLM call routes through the gateway.
            // No API keys to Azure services anywhere -- every client in
            // app/ falls back to
            // DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
            // whenever no key env var is set, which is exactly this identity.
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
          // /healthz is deliberately dependency-free (never blocks on a
          // downstream call -- see app/api/main.py). /readyz checks
          // required config is present without making a live network
          // call. See docs/adr/0003-deployment-reliability-and-observability.md.
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 15
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: containerAppMinReplicas
        maxReplicas: 3
        rules: [
          {
            name: 'http-concurrency-autoscale'
            http: {
              metadata: { concurrentRequests: '20' }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPullAssignment
    openAiRoleForApp
    searchRoleForApp
    contentSafetyRoleForApp
    auditStorageRoleForApp
    auditTable
    keyVaultSecretsUserForApp
  ]
}

// ---------------------------------------------------------------------
// MONITORING -- one alert, kept deliberately simple. Fires if the
// Container App restarts more than twice in 15 minutes: what a
// crash-looping bad deploy looks like from the outside.
// ---------------------------------------------------------------------

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${namePrefix}-ag'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'nimbusAI'
    enabled: true
    emailReceivers: empty(alertEmail) ? [] : [
      { name: 'oncall-email', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

resource restartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-restart-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Nimbus Copilot Container App is restarting repeatedly -- likely a crash-looping bad deploy.'
    severity: 2
    enabled: true
    scopes: [containerApp.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighRestartCount'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 2
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

// ---------------------------------------------------------------------
// PHASE B ADDITIONS -- error-rate + latency alerts (Application
// Insights-based, GA metrics -- deliberately not the native
// Microsoft.App/containerApps `Requests`/`ResponseTime` metrics, which
// are Preview and whose StatusCodeCategory dimension values were not
// independently confirmed during research; requests/failed and
// requests/duration on the App Insights resource are GA and
// well-documented) plus one synthetic-monitoring availability test. See
// docs/adr/0003-deployment-reliability-and-observability.md for the
// SLO thresholds these encode and the research this is grounded in.
// ---------------------------------------------------------------------

resource errorRateAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-error-rate-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Nimbus Copilot is returning failed HTTP requests -- see docs/adr/0003-deployment-reliability-and-observability.md for the SLO this backs.'
    severity: 2
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'FailedRequests'
          metricName: 'requests/failed'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Count'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

resource latencyAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-latency-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Nimbus Copilot average response time exceeds the SLO target -- see docs/adr/0003-deployment-reliability-and-observability.md.'
    severity: 3
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'AverageResponseTime'
          metricName: 'requests/duration'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 5000
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      { actionGroupId: actionGroup.id }
    ]
  }
}

// Synthetic monitoring -- pings /healthz every 5 minutes from two
// Azure regions, independent of whether any real user traffic is
// happening (important for a presentation-grade demo environment that
// may otherwise sit idle for days between demos). Location IDs below
// are the two most commonly used in Microsoft's own webtest examples
// (South Central US, West Europe) -- not independently re-verified
// against the current live TestLocations list in this pass; confirm
// with `az rest` against the webtest locations API before relying on
// this for a real on-call rotation.
resource availabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${namePrefix}-availability-test'
  location: location
  tags: union(tags, {
    'hidden-link:${appInsights.id}': 'Resource'
  })
  properties: {
    SyntheticMonitorId: '${namePrefix}-availability-test'
    Name: 'Nimbus Copilot /healthz availability'
    Enabled: true
    Frequency: 300
    Timeout: 30
    Kind: 'ping'
    RetryEnabled: true
    Locations: [
      { Id: 'us-tx-sn1-azr' }
      { Id: 'emea-nl-ams-azr' }
    ]
    Configuration: {
      WebTest: '<WebTest Name="Nimbus Copilot /healthz availability" Id="${guid(namePrefix, 'availability-test')}" Enabled="True" Timeout="30" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Items><Request Method="GET" Url="https://${containerApp.properties.configuration.ingress.fqdn}/healthz" ThinkTime="0" Timeout="30" ParseDependentRequests="False" FollowRedirects="True" RecordResult="True" Cache="False" ExpectedHttpStatusCode="200" IgnoreHttpStatusCode="False" /></Items></WebTest>'
    }
  }
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output managedIdentityClientId string = identity.properties.clientId
output openAiEndpoint string = foundryAccount.properties.endpoint
output openAiDeploymentName string = chatDeployment.name
output llmGatewayEndpoint string = 'https://${litellmGateway.properties.configuration.ingress.fqdn}'
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output contentSafetyEndpoint string = contentSafety.properties.endpoint
output appInsightsConnectionString string = appInsights.properties.ConnectionString
// Not a secret -- just the vault's name, so scripts and lab-guide steps
// can do `az keyvault secret show --vault-name <this>` for the two
// secrets it holds. See docs/adr/0005-key-vault-for-gateway-secrets.md.
output keyVaultName string = keyVault.name