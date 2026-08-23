# Answer Key — Scenario & Coding Questions

Answers to the second set (scenario-based + coding). The first set's "anchor" lines already double as short-form answers to those 34 questions — say the word if you want those expanded into full answers too.

---

## Part 1 — Scenario Answers

**1. The silent regression.**
Hypothesis: the prompt edit removed or softened a disambiguation instruction the model relied on to route between "answer directly" and "call a tool," and nothing that changed is visible as a functional diff — it's a wording change, not a logic change. What to check: diff the old and new prompt sentence-by-sentence (not just read it) looking for removed *instructions*, not just changed *tone*; then run the agent trajectory eval against both prompt versions and compare `tool_selection_accuracy` specifically. Why review missed it: a human reviewer reads a prompt for clarity and brevity, not for "does this still contain the exact disambiguation clause the eval dataset depends on" — that's exactly why probabilistic evaluation (Layer 3/4) exists as a gate separate from code review, and why prompts belong in the same release-gated unit as code.

**2. The 3am page.**
First: check whether the error rate is concentrated on one endpoint/route or spread evenly — a spread failure points upstream (model provider, gateway), a concentrated one points at a specific code path. Second: check the gateway and model provider status directly — is Foundry or the LiteLLM proxy itself erroring, versus your own app logic throwing. Third: check if a deploy happened in the last probe interval — correlate the alert timestamp against your deploy log before assuming it's unrelated. Fourth: pull a handful of failed traces (not aggregate metrics) and read the actual error — "elevated failed requests" is a symptom, the trace tells you the mechanism. Fifth: check whether it's isolated to one environment — if staging is fine and production isn't, that's a strong signal it's config/scale, not code, since it's the same artifact in both.

**3. Customers are getting confidently wrong answers.**
Hypothesis: knowledge/retrieval drift — the source-of-truth document changed (a policy update) but the search index wasn't refreshed, so retrieval is returning stale-but-well-formed chunks the model cites with full confidence. Fastest confirmation: run an index-freshness check comparing the document store's last-modified timestamps against the index's last-build timestamp — if the index predates the last policy update, that's the root cause without needing to touch the model or the code at all. This is also why "the model" is usually the wrong first suspect in RAG systems: it answered correctly *given what it was handed*.

**4. The routing default matters.**
Case for routing to the monetary-capable specialist by default: it maximizes helpfulness — most ambiguous messages are legitimate account questions, and you don't want to under-serve the common case. Case for routing to the specialist with no monetary tool: an ambiguous routing decision means the model was already uncertain, and uncertainty paired with monetary capability is exactly the combination you want to avoid — the cost of wrongly restricting a legitimate billing question (the user asks again, or gets redirected) is far lower than the cost of a monetary tool becoming reachable through a routing failure. Ship the second one. The "wrong" answer (favor helpfulness) looks better in a demo and worse in an incident report — optimizing for the demo case here is the actual mistake.

**5. The canary that wasn't.**
Yes, this is an incident, even though the automated system did the right thing — a real, if narrow, exposure window still executed against production traffic. To answer "how many and how do you know": trace-based, not guess-based — pull the traffic-split window's exact start/end timestamps from the canary script's own log, then query request volume against the broken revision specifically in that window from Application Insights (revision is a taggable dimension). The honest follow-up for the stakeholder is naming the structural gap (a declarative traffic rule can't express "0% until externally verified") and the concrete next step to close it, not just "it self-healed."

**6. Budget just got cut in half.**
First cut: stop running every environment continuously — provision staging/production only around demos/releases (`azd up` before, `azd down --purge` after) rather than leaving them warm; this alone can cut idle Container Apps/Search/Content Safety cost dramatically since model billing itself is consumption-based with no idle cost. Second: consolidate SKUs further where the free tier is available (only one Search Free tier per subscription — make sure it's actually assigned to the environment that benefits most, usually dev). Third: reduce `containerAppMinReplicas` to 0 everywhere except the environment actively being presented. What I'd refuse to cut: the release gate and the guardrail/audit layers — those are integrity controls, not idle infrastructure, and their cost is model-call-based (near zero when nothing's running), not standing infrastructure — cutting them doesn't even save meaningful money, it just removes the safety net.

**7. The metric that stopped meaning anything.**
Explanation A: the eval dataset is stale — it was representative of real traffic three months ago, but the traffic mix has shifted (new question types, new edge cases) that the fixed dataset never tests, so the score is accurately measuring an increasingly unrepresentative slice of reality. Explanation B: the metric itself has a blind spot — groundedness measures "is the answer supported by the retrieved text," which stays high even if the *retrieved text itself* is wrong (stale/incorrect source), because the model is being faithfully grounded to bad information. To tell them apart: check whether the escalation tickets map to questions inside or outside the eval dataset's coverage (points to A) versus whether they map to a specific, identifiable source document going stale (points to B, and is the same failure mode as scenario 3).

**8. A tool call that shouldn't have been possible.**
"How do you know this is the only time" — pull every audit log entry over a defined window matching the same denial pattern (tool name + specialist identity), not just today's single event; a fail-safe audit trail exists specifically so this question has a real answer instead of "we assume so." "How do you know the block is reliable and not lucky" — point to the layered design: the block isn't a single classifier's judgment, it's a deterministic intersection check (specialist's fixed tool set ∩ policy-approved tools) that has no code path allowing an out-of-scope tool through regardless of what the model outputs — and cite the safety-regression eval dataset that specifically exercises this boundary on every release, not just this one incident.

**9. The vendor changed something and didn't tell you.**
Process: first confirm no code, prompt, or dataset changed on your side in the drift window (git log against the eval-score timeline) — if truly nothing changed and the model deployment reference is identical, that isolates it to the provider side. Second, check the provider's own status/changelog for the exact date range. Third, if unconfirmable externally, treat it as evidence-based even without vendor confirmation — the eval trend itself is the proof, logged and dated. Going forward: this is the entire argument for nightly (not just PR-triggered) evaluation — it's the one mechanism that catches a regression with zero corresponding commit, and it should open a single deduplicated issue rather than fail silently where no one sees it.

**10. Two teams, one release gate.**
Start by separating "the threshold is measuring the wrong thing" from "the threshold is measuring the right thing but is inconvenient right now" — only the first is a legitimate reason to change it. Ask what evidence supports loosening it: a recalibration against real production outcomes, not launch-date pressure. What would change my mind: data showing the threshold is miscalibrated (e.g., it's rejecting cases that are actually fine, verified by human review of the false positives). What wouldn't: "we need to ship by Friday" — that's a case for a documented, time-boxed exception with an owner and a follow-up date, not a permanent threshold change, and the distinction between those two responses is itself worth stating explicitly in the conversation.

**11. Explain it to a VP in two minutes, no jargon.**
"A normal web service does the same thing every time you give it the same input — if it passes your tests once, it keeps passing. This system doesn't work that way: it can give a different answer to the same question depending on what's changed in the world around it, even if we haven't touched a single line of code. That means testing it once before launch isn't enough — we have to keep re-checking it on a schedule, the same way you'd re-audit a policy that depends on facts changing over time, not just re-test software that depends on code changing."

---

## Part 2 — Coding Answers

**1. Enforce a least-privilege tool boundary.**
```python
def scope_tools(specialist_tools: set[str], offered_tools: list[str]) -> set[str]:
    """Intersection only -- neither set is trusted alone."""
    allowed = specialist_tools.intersection(offered_tools)
    return allowed  # may legitimately be empty

def choose_tool_mode(allowed: set[str]) -> str:
    """Never force a tool choice when nothing survived scoping."""
    return "auto" if allowed else "none"
```
The key behavior an interviewer is checking for: an empty intersection must degrade to `tool_choice="none"`, never silently widen back to the full tool set as a "helpful" fallback.

**2. A safe-default router.**
```python
VALID_ROUTES = {"delegate_to_billing": "billing", "delegate_to_account": "account"}
DEFAULT_ROUTE = "account"  # the specialist with no monetary-capable tool

def route(response) -> str:
    tool_calls = getattr(response.choices[0].message, "tool_calls", None)
    if not tool_calls:
        return DEFAULT_ROUTE
    name = tool_calls[0].function.name
    return VALID_ROUTES.get(name, DEFAULT_ROUTE)  # explicit whitelist, not a bare .get on trust
```
What makes this "safe" specifically: `VALID_ROUTES.get(name, DEFAULT_ROUTE)` means an unrecognized function name — a hallucinated one, say — falls to the default exactly the same as a missing one. There's no code path where an unexpected string reaches the caller.

**3. Estimate cost without a real tokenizer.**
```python
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # OpenAI's own rule-of-thumb ratio for English

def estimate_cost(input_text: str, output_text: str, price_per_1k: dict) -> float:
    in_tok = estimate_tokens(input_text)
    out_tok = estimate_tokens(output_text)
    return (in_tok / 1000) * price_per_1k["input"] + (out_tok / 1000) * price_per_1k["output"]
```
Where this diverges from a real invoice, said out loud: real tokenizers split on subword units, not characters — punctuation-heavy or non-English text can be off by 30%+ from the 4-chars/token assumption; it also can't see system-prompt overhead the provider bills for unless you include it in `input_text`; and it has zero knowledge of provider-side price changes since it's a static table, not a live rate card.

**4. Apply a release policy from a YAML file.**
```python
def evaluate(policy: dict, metrics: dict) -> dict:
    breaches = []
    for name, rule in policy["thresholds"].items():
        if name not in metrics:
            continue  # skip, don't fail, on a metric with no result
        value = metrics[name]
        bound_type = "min" if "min" in rule else "max"
        bound = rule[bound_type]
        breached = (bound_type == "min" and value < bound) or (bound_type == "max" and value > bound)
        if breached:
            breaches.append({"metric": name, "value": value, "on_breach": rule.get("on_breach", "hold")})

    if not breaches:
        return {"decision": "promote", "breaches": []}
    if any(b["on_breach"] == "reject" for b in breaches):
        return {"decision": "reject", "breaches": breaches}
    return {"decision": "hold", "breaches": breaches}
```
The two things a reviewer is watching for: `continue` (skip) rather than `raise` on a missing metric, and the decision resolving to the *worst* breach across the whole list rather than stopping at the first one found.

**5. A stateless rate limiter, sort of.**
```python
import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 20
_calls: dict[str, deque] = defaultdict(deque)

def allow(key: str, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    q = _calls[key]
    while q and now - q[0] > _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= _MAX_REQUESTS:
        return False
    q.append(now)
    return True
```
What you lose versus a real distributed limiter, said explicitly: this dict lives in one process's memory — with more than one replica (which `maxReplicas: 3` in this system already implies), each replica enforces its own independent cap, so the *effective* system-wide limit is `_MAX_REQUESTS × replica_count`, not `_MAX_REQUESTS`. A real fix needs shared state (Redis, or a database-backed counter) — naming that gap out loud is the actual point of the exercise.

**6. Diff two prompt files and predict the blast radius.**
```python
import difflib

def removed_instructions(old: str, new: str, threshold: float = 0.6) -> list[str]:
    old_sentences = [s.strip() for s in old.split(".") if s.strip()]
    new_sentences = [s.strip() for s in new.split(".") if s.strip()]
    flagged = []
    for s in old_sentences:
        best = max(
            (difflib.SequenceMatcher(None, s, n).ratio() for n in new_sentences),
            default=0.0,
        )
        if best < threshold:
            flagged.append(s)
    return flagged
```
This is deliberately similarity-based, not exact-match — wording legitimately changes between prompt versions, but a sentence with no reasonably similar counterpart anywhere in the new version is a real signal that an instruction, not just a phrasing, disappeared.

**7. A readiness check that can't become a cost problem.**
```python
REQUIRED_ENV = ["LLM_GATEWAY_ENDPOINT", "LLM_GATEWAY_API_KEY", "AZURE_SEARCH_ENDPOINT", "AZURE_CONTENT_SAFETY_ENDPOINT"]

@app.get("/readyz")
def readyz():
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        return JSONResponse(status_code=503, content={"status": "not_ready", "missing": missing})
    return {"status": "ready"}
```
Trade-off, said out loud: this proves configuration is *present*, not that the three downstream services are actually *reachable right now* — a real outage in Search wouldn't flip this to unready. That's an intentional trade: a readiness probe hitting three live APIs on every poll interval (every few seconds, times every replica) becomes its own rate-limit/cost source, so this trades a small blind spot for not turning your own health check into load.

**8. Weighted traffic shift with a rollback path.**
```python
def canary_deploy(app_name, new_revision, old_revision, traffic_pct, soak_seconds):
    set_traffic_weight(app_name, {new_revision: traffic_pct, old_revision: 100 - traffic_pct})
    new_fqdn = get_revision_fqdn(app_name, new_revision)  # the NEW revision directly, not the shared endpoint
    time.sleep(soak_seconds)
    result = run_smoke_test(new_fqdn)  # verify_deployment.py equivalent

    if result.get("all_passed") is True:
        set_traffic_weight(app_name, {new_revision: 100})
        deactivate_revision(app_name, old_revision)
        return "promoted"
    else:
        set_traffic_weight(app_name, {old_revision: 100})
        return "rolled_back"  # ANY non-True result -- error, timeout, ambiguous -- rolls back, never promotes
```
The line that matters most: `result.get("all_passed") is True`, not `!= False`. An exception, a timeout, or a malformed result all fail this check and roll back — "unsure" and "known-broken" are treated identically, which is the actual design goal.

**9. A fail-safe audit write.**
```python
def record_audit_event(event: dict) -> None:
    try:
        table_client.create_entity(event)
    except Exception:
        logger.error("audit_write_failed", extra={"event_type": event.get("type")}, exc_info=True)
        # deliberately no re-raise -- the calling request must still complete
```
The try/except scope is the whole point: it wraps *only* the write, not the request handler that calls it, so a Table Storage outage degrades to "this event wasn't durably recorded, and we logged that fact loudly" rather than "the customer's chat request failed because our audit system had a bad day."

**10. Deduplicate a recurring alert.**
```python
def handle_nightly_failure(gh_client, repo, failure_summary: str, dedupe_label="nightly-drift"):
    open_issues = gh_client.search_issues(repo=repo, labels=[dedupe_label], state="open")
    if open_issues:
        gh_client.comment(open_issues[0], f"Still failing as of tonight:\n\n{failure_summary}")
    else:
        gh_client.create_issue(
            repo=repo, title="Nightly drift check failing",
            body=failure_summary, labels=[dedupe_label],
        )
    # closing is a separate, explicit path -- only when a run PASSES with a prior open issue present
```
The design decision worth stating out loud: search-before-create keyed on a stable label, and closing is never automatic on "the job ran" — only on an explicit pass signal, so a flaky job that alternates pass/fail doesn't spam open/close noise on every run.

---

*Same note as before: say these out loud in an actual interview, don't read them verbatim — the value here is knowing the reasoning cold, not memorizing the wording.*
