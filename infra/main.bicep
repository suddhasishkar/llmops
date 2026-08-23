// infra/main.bicep -- the azd entry point (subscription scope).
//
// `azd up` reads this file, creates the resource group, and deploys
// infra/resources.bicep into it. There is ONE deployable stack now -- no
// "core lab" vs. "enrichment" split, no `deploySearch`/`deployContentSafety`
// toggles that default to false. Azure OpenAI, Azure AI Search, and Azure
// AI Content Safety are all REQUIRED and always deployed together, because
// the application code that calls them (app/agent/azure_openai_client.py,
// app/retrieval/retrieval.py, app/agent/content_safety.py) has no local
// fallback left to fall back to. That is the direct fix for "it still
// forces local" -- there is no local code path left to force.
//
// Run:            azd up
// Tear down:      azd down --purge
// Wire up CI/CD:  azd pipeline config
//
// See Day1_Lab_Guide.md Part 1 for exactly what each stage of `azd up`
// does and roughly how long it takes.

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('azd environment name. Set once with: azd env new <name>. Becomes part of every resource name below.')
param environmentName string

@minLength(1)
@description('Azure region to deploy into, e.g. eastus2. Set with: azd env set AZURE_LOCATION <region>, or answer the prompt on first `azd up`. Must be a region with capacity for the chat model in infra/resources.bicep (gpt-5-mini as of this pass, see that file for why) -- Day1_Lab_Guide.md Part 1 still lists regions verified for the now-retired gpt-4o-mini and has not been re-verified for gpt-5-mini; check current capacity in the Foundry portal before assuming that list still applies.')
param location string

@description('Email address the restart alert pages. Optional -- the lab works with this left empty. Set with: azd env set ALERT_EMAIL you@example.com')
param alertEmail string = ''

// azd auto-populates this from the signed-in `azd auth login` user when a
// subscription-scope template declares a parameter named exactly
// `principalId` -- see main.parameters.json. It is used below to grant
// YOUR OWN account (not just the app's managed identity) read access to
// Azure AI Search and query access to Azure OpenAI / Content Safety, so
// the lab's manual-inspection steps (Day 2, checking the index by hand)
// work with a plain `az login`, no key copy-pasting required.
param principalId string = ''

@description('Azure AI Search SKU for this environment. Only ONE Free-tier Search service is allowed per subscription -- set "free" for exactly one azd environment (dev) and "basic" for the others. Set with: azd env set SEARCH_SKU basic. See docs/adr/0001-foundry-and-multiagent.md.')
@allowed(['free', 'basic', 'standard'])
param searchSku string = 'basic'

@description('Foundry model deployment capacity, in thousands-of-tokens-per-minute, for this environment. Keep this low (1-3) for dev/staging. Set with: azd env set OPENAI_CAPACITY 1')
param openAiCapacity int = 1

@description('Azure AI Content Safety SKU for this environment. Set with: azd env set CONTENT_SAFETY_SKU S0')
@allowed(['F0', 'S0'])
param contentSafetySku string = 'S0'

@description('Minimum Container App replicas for this environment. 0 = scale to zero when idle (dev/staging default); 1 = always-warm (only while actively presenting production). Set with: azd env set CONTAINER_APP_MIN_REPLICAS 0')
param containerAppMinReplicas int = 0

@description('Langfuse Cloud public key (free Hobby tier) for LLM trace observability via the LiteLLM gateway. Leave unset to run without tracing. Set with: azd env set LANGFUSE_PUBLIC_KEY pk-lf-... See docs/adr/0002-llm-gateway-and-observability.md.')
param langfusePublicKey string = ''

@description('Langfuse Cloud secret key. Leave unset to run without tracing. Set with: azd env set LANGFUSE_SECRET_KEY sk-lf-...')
@secure()
param langfuseSecretKey string = ''

@description('Langfuse host -- defaults to Langfuse Cloud. Override only if self-hosting Langfuse instead. Set with: azd env set LANGFUSE_HOST https://your-langfuse-host')
param langfuseHost string = 'https://cloud.langfuse.com'

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
}

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources-${resourceToken}'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    tags: tags
    alertEmail: alertEmail
    principalId: principalId
    searchSku: searchSku
    openAiCapacity: openAiCapacity
    contentSafetySku: contentSafetySku
    containerAppMinReplicas: containerAppMinReplicas
    langfusePublicKey: langfusePublicKey
    langfuseSecretKey: langfuseSecretKey
    langfuseHost: langfuseHost
  }
}

// Standard azd-recognized outputs (resource group + registry) plus every
// AZURE_* value the application and lab scripts need. `azd up` writes all
// of these into .azure/<env>/.env automatically -- nothing here is copied
// or exported by hand at any point in either lab.
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.acrLoginServer
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AZURE_OPENAI_DEPLOYMENT string = resources.outputs.openAiDeploymentName
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AZURE_CONTENT_SAFETY_ENDPOINT string = resources.outputs.contentSafetyEndpoint
output AZURE_CLIENT_ID string = resources.outputs.managedIdentityClientId
output SERVICE_API_ENDPOINT_URL string = 'https://${resources.outputs.containerAppFqdn}'
// The agent's actual LLM call target -- the LiteLLM gateway, not
// Foundry directly. See docs/adr/0002-llm-gateway-and-observability.md.
output LLM_GATEWAY_ENDPOINT string = resources.outputs.llmGatewayEndpoint
// Consumed by scripts/canary_deploy.sh (via `azd env get-values`) to
// address the api Container App's revisions directly -- see
// docs/adr/0003-deployment-reliability-and-observability.md.
output AZURE_CONTAINER_APP_API_NAME string = resources.outputs.containerAppName
// Where litellm-master-key and langfuse-secret-key actually live now --
// see docs/adr/0005-key-vault-for-gateway-secrets.md. Not a secret
// itself; Day1_Lab_Guide.md Part 0.7 uses this name with
// `az keyvault secret show` to get LLM_GATEWAY_API_KEY onto a
// trainee's own machine for local agent runs.
output AZURE_KEY_VAULT_NAME string = resources.outputs.keyVaultName
