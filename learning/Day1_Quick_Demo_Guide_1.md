# Day 1 Quick Demo Guide — Fastest Path to a Live Demo (incl. GitHub Actions)

This is **not** a replacement for `Day1_Lab_Guide.md` — it's a shorter,
single-track path for one person demoing the Day 1 story end-to-end
(local repro → root cause → fix → the same gate enforced automatically
in GitHub Actions) as fast as possible. Skips the trainee/admin split,
the alternatives, and the background explanations `Day1_Lab_Guide.md`
has — if something below is unclear, that guide has the full version of
every step here, cross-referenced by section number.

**The story you're demoing, in one sentence:** a prompt change silently
makes the support agent worse at telling "vague complaint" apart from
"explicit refund request," an automated evaluation gate catches it and
blocks the release, you find and fix the one missing sentence, and the
same gate — running inside GitHub Actions this time, not your terminal —
automatically passes it and deploys.

**What you need before you start:**
- A GitHub repo you have admin rights on (a fork or your own copy of this
  repo) — you need to configure repo secrets/variables and Environments,
  which needs admin access.
- `az`, `azd` (1.9+), `gh`, `python3.12`, Docker — all installed and on
  your PATH.
- An Azure subscription with Owner, or Contributor + User Access
  Administrator.
- Azure OpenAI access approved on that subscription, with `gpt-5-mini`
  capacity in some region (`Day1_Lab_Guide.md` 0.1 has the exact check
  command if you're not sure).

Budget **~35–45 minutes**, most of it unattended Azure provisioning —
you're not typing for most of that time.

---

## Part A — Your own live sandbox (~20 min, ~15 of it waiting)

1. `git clone <repo-url> nimbus-support-copilot && cd nimbus-support-copilot`
2. `./scripts/seed_lab.sh` — free, offline. Confirm it ends with
   `All local checks passed`.
3. `az login` then `azd auth login`.
4. `azd env new nimbus-demo`
5. `azd up` — pick your subscription and a region with real `gpt-5-mini`
   capacity when prompted. This provisions everything (Foundry, Search,
   Content Safety, both Container Apps, the gateway) and deploys the app
   — one command, ~15–20 minutes, nothing to babysit. Save the
   `SERVICE_API_ENDPOINT_URL` it prints at the end.
6. Every terminal you use from here on:
   ```bash
   set -a
   source <(azd env get-values)
   set +a
   export LLM_GATEWAY_API_KEY=$(az keyvault secret show \
     --vault-name "$AZURE_KEY_VAULT_NAME" \
     --name litellm-master-key \
     --query value -o tsv)
   ```
7. ```bash
   pip install -r requirements.txt --break-system-packages --quiet
   python scripts/verify_deployment.py --url "$SERVICE_API_ENDPOINT_URL"
   ```
   Confirm five `[PASS]` lines and `ALL CHECKS PASSED`. Your sandbox is
   live.

---

## Part B — The bug, locally (~5 min, this is the core demo beat)

8. Show the correct behavior first:
   ```bash
   python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version baseline
   ```
   `tool_call.name` should be `create_support_ticket`.

9. Show the regression:
   ```bash
   python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version candidate_broken
   ```
   `tool_call.name` may now be `request_customer_credit` (real model
   calls aren't perfectly deterministic — if you get `create_support_ticket`,
   run it once or twice more, or just trust the dataset eval next, which
   is what actually gates the release). Point out `tool_result.state`
   is `PENDING_APPROVAL` either way — nothing executes automatically,
   regardless of which tool got picked. That distinction is worth saying
   out loud during the demo.

10. Show the automated gate catching it:
    ```bash
    python -m eval.run_agent_trajectory_eval \
      --dataset eval/datasets/tool_trajectory_eval.jsonl \
      --prompt-version candidate_broken \
      --out eval/results/agent_eval.json
    cat eval/results/agent_eval.json | python -m json.tool
    ```
    `tool_selection_accuracy` should be below the `0.90` threshold.

11. Show the root cause:
    ```bash
    diff app/prompts/system_prompt_billing_baseline.md app/prompts/system_prompt_billing_candidate_broken.md
    ```
    One sentence — the disambiguation rule — is missing from the
    candidate.

12. Show the fix resolving it:
    ```bash
    python -m eval.run_agent_trajectory_eval \
      --dataset eval/datasets/tool_trajectory_eval.jsonl \
      --prompt-version candidate_fixed \
      --out eval/results/agent_eval.json
    cat eval/results/agent_eval.json | python -m json.tool
    ```
    `tool_selection_accuracy` back to `1.0` (or comfortably above `0.90`).

If your demo is time-boxed and GitHub Actions isn't strictly needed,
**you can stop here** — Parts A and B alone are the complete story.
Part C is specifically for showing the same thing enforced automatically
in CI instead of from your terminal.

---

## Part C — The same gate, enforced in GitHub Actions (~15 min)

Unlike a full training class, this is a one-time demo, so turning on
the paid evaluation job for the duration of the demo is the right
trade-off — you want the audience to see it actually run, not see it
skipped. (`Day1_Lab_Guide.md` Part 0.9 has the full explanation of why
this is off by default for a class; here you're deliberately turning it
on.)

13. **Deploy credentials:**
    ```bash
    azd env new nimbus-ci   # throwaway — just gives azd pipeline config a subscription/location to read
    azd pipeline config
    ```
    If this fails partway through writing GitHub secrets, it's almost
    always `gh` not being authenticated with enough scope:
    ```bash
    gh auth login --hostname github.com --git-protocol https --scopes repo,workflow
    ```
    then re-run `azd pipeline config` — safe to run again.

14. **Shared eval environment** (this is the piece that costs real money
    per PR while it's on):
    ```bash
    azd env new nimbus-eval
    azd up
    ```
    Create the eval identity (a separate app registration from step 13's
    — full commands, including the three role grants it needs, are in
    `Day1_Lab_Guide.md` 0.9.b; do not skip the role grants, `cloud-eval`
    will fail without them even with a valid credential):
    ```bash
    APP_ID=$(az ad app create --display-name nimbus-eval-cloudeval --query appId -o tsv)
    az ad sp create --id "$APP_ID"
    az ad app federated-credential create --id "$APP_ID" --parameters '{
      "name": "github-pull-request",
      "issuer": "https://token.actions.githubusercontent.com",
      "subject": "repo:<org>/<repo>:pull_request",
      "audiences": ["api://AzureADTokenExchange"]
    }'
    SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
    SCOPE="/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$(azd env get-values | grep AZURE_RESOURCE_GROUP | cut -d'=' -f2- | tr -d '"')"
    az role assignment create --assignee "$SP_OBJECT_ID" --role "Cognitive Services User" --scope "$SCOPE"
    az role assignment create --assignee "$SP_OBJECT_ID" --role "Search Index Data Reader" --scope "$SCOPE"
    az role assignment create --assignee "$SP_OBJECT_ID" --role "Key Vault Secrets User" --scope "$SCOPE"
    ```
    Then in GitHub, **Settings → Secrets and variables → Actions**, add:

    | Add | Kind | Value |
    |---|---|---|
    | `EVAL_AZURE_CLIENT_ID` / `_TENANT_ID` / `_SUBSCRIPTION_ID` | secret | `$APP_ID`, your tenant ID, your subscription ID |
    | `EVAL_AZURE_OPENAI_DEPLOYMENT`, `EVAL_AZURE_SEARCH_ENDPOINT`, `EVAL_AZURE_CONTENT_SAFETY_ENDPOINT`, `EVAL_LLM_GATEWAY_ENDPOINT`, `EVAL_AZURE_KEY_VAULT_NAME` | variable | `azd env get-values` from `nimbus-eval` (`AZURE_OPENAI_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_CONTENT_SAFETY_ENDPOINT`, `LLM_GATEWAY_ENDPOINT`, `AZURE_KEY_VAULT_NAME`) |
    | `ENABLE_CLOUD_EVAL` | variable | `true` — this is the switch that actually turns `cloud-eval` on; everything above this row can exist without it costing anything per PR |

    No `EVAL_AZURE_OPENAI_ENDPOINT` — the agent never calls Foundry
    directly, only the LiteLLM gateway, so that value is never read by
    anything.

15. **GitHub Environments** — **Settings → Environments**, create
    `development`, `staging`, `production` (exact names, case-sensitive).
    Add a **required reviewer on all three**, not just production — this
    is what stops the pipeline from spending money unattended; each
    `deploy-*` job waits for an explicit approval click before it
    provisions or updates anything.

16. **Run the actual demo:**
    - Create a branch, set `PROMPT_VERSION: candidate_broken` in
      `.github/workflows/ai-release.yml`'s `env:` block, push, open a
      PR. Watch `deterministic-tests` and `code-scanning` pass, watch
      `cloud-eval` make real calls, watch `release-gate` report **HOLD**
      and post it as a PR comment — the same result you already saw
      locally in step 10, now happening inside GitHub.
    - Change `PROMPT_VERSION` to `baseline` (or `candidate_fixed`), push
      to the same PR. Watch all four checks pass, **PROMOTE**.
    - Merge the PR. Watch `deploy-dev` appear in the PR's **Deployments**
      view in a **Waiting** state. Click **Review deployments → Approve**
      — this is the moment to narrate: nothing deployed until a human
      just said yes. Watch it provision `nimbus-dev` and deploy. Approve
      `deploy-staging`, then `deploy-production`, the same way, if your
      demo wants to show the full three-environment promotion.

---

## Cleanup (do this right after the demo, not later)

Everything above costs money while it's standing. Once the demo's done:

```bash
azd down --purge   # run once per environment you created: nimbus-demo, nimbus-eval, and nimbus-dev/staging/production if you approved them
```

And in GitHub, set the `ENABLE_CLOUD_EVAL` variable back to unset (or
`false`) if this repo is going to keep existing after the demo — leaving
it `true` means every future PR against this repo keeps making real,
billed Azure calls, which is exactly the always-on cost
`Day1_Lab_Guide.md`'s default setup is designed to avoid.
