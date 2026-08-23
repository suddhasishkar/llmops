# Scenario-Based vs. Coding Questions — LLMOps / AgentOps

Same source material as the first question set, split by *what kind of thinking* each question tests. Scenario questions test judgment under ambiguity — there's rarely one right answer, what matters is the diagnostic process. Coding questions test whether you can actually implement the guardrail, not just describe it — each includes a hint at the expected shape of the answer so you can self-check.

---

## Part 1 — Scenario-Based Questions

*Format: a situation, then "what do you do." Answer out loud, in order: first hypothesis, what you'd check to confirm or rule it out, then the fix.*

**1. The silent regression.**
A teammate's PR shortens the support agent's system prompt "to make responses snappier." Every deterministic test passes. Code review approves it — it's a one-line prompt edit. Two days after merge, someone notices the agent has started approving billing tool calls in situations it used to defer on. Where in your pipeline should this have been caught, and why didn't a code reviewer catch it just by reading the diff?

**2. The 3am page.**
Your error-rate alert fires at 3am. `requests/failed` is elevated but the restart-count alert hasn't fired — the container isn't crash-looping. Walk me through your first five minutes: what do you check, in what order, and what would each result rule in or out?

**3. Customers are getting confidently wrong answers.**
No deploy happened in the last two weeks. No code changed. Support tickets show the agent citing a refund-policy document that was superseded ten days ago. What's your hypothesis, and what's the fastest way to confirm it without guessing?

**4. The routing default matters.**
Your Manager agent's routing call comes back malformed on a specific class of ambiguous message. You have two choices for the default: route to the specialist with a monetary tool, or route to the one without. Argue both sides, then tell me which you'd actually ship and why the "wrong" answer here is worse than it looks.

**5. The canary that wasn't.**
You deploy a new revision behind a canary rollout. The canary script smoke-tests it, sees a failure, and rolls back — but a handful of real user requests hit the broken revision during the few seconds before rollback. Is this an incident? What would you tell a stakeholder who asks "how many customers were affected and how do you know?"

**6. Budget just got cut in half.**
You're running three environments and told to cut infrastructure cost by 50% without dropping any environment entirely. Walk me through what you'd change first, second, third — and what you'd explicitly refuse to cut no matter the budget pressure.

**7. The metric that stopped meaning anything.**
Your groundedness score has been steady at 0.91 for three months. This week it's still 0.91, but support escalations for "wrong answer" have tripled. What are two different explanations that both produce "stable metric, degraded reality," and how would you tell them apart?

**8. A tool call that shouldn't have been possible.**
An audit log shows a specialist agent attempted to call a tool outside its declared scope. It was blocked — the system worked. But leadership asks "how do you know this is the only time, and how do you know the block is actually reliable and not just lucky?" What's your answer?

**9. The vendor changed something and didn't tell you.**
Your evaluation scores drift downward starting on a specific date, with no corresponding commit in your repo. What's your process for confirming (or ruling out) "the model provider changed something upstream," and what would you do differently going forward so this is caught faster next time?

**10. Two teams, one release gate.**
A platform team owns the release-policy thresholds; a product team wants a threshold loosened because it's blocking a launch they consider high-priority. How do you handle this conversation — what information would change your mind, and what wouldn't?

**11. Explain it to someone who'll cut your budget if they don't get it.**
In two minutes, explain to a non-technical VP why an AI system needs a different release process than a normal web service — without using the words "model," "token," or "prompt."

---

## Part 2 — Coding Questions

*Format: describe the task; the "expected shape" line tells you what a strong answer demonstrates, not a full solution — write the real thing yourself first.*

**1. Enforce a least-privilege tool boundary.**
Write a function that takes a specialist's fixed tool set, a list of tools that survived an upstream policy check, and returns only the intersection — plus raises/logs if the intersection is empty and the caller tries to force a tool choice anyway.
*Expected shape: two-set intersection, explicit handling of the empty-set case, no silent fallback to "allow everything."*

**2. A safe-default router.**
Write a routing function that calls a model with a forced tool choice between exactly two options, and returns a default value if the response is missing, malformed, or anything other than one of the two known function names — with the default going to the *less capable* path on purpose.
*Expected shape: an explicit whitelist check (`response in KNOWN_VALUES`), not a bare `.get()` with silent coercion; the default should be argued for, not incidental.*

**3. Estimate cost without a real tokenizer.**
Write a rough token-count estimator for English text with no external library, then use it to estimate a dollar cost given a per-1K-token price table for input and output separately. Then explain, out loud, exactly where this estimate would diverge from a real invoice.
*Expected shape: character-count heuristic (~4 chars/token), separate input/output pricing, and a clear verbal caveat about what "estimate" means here.*

**4. Apply a release policy from a YAML file.**
Given a policy with named thresholds (some `min`, some `max`), each with an `on_breach` action (`hold` or `reject`), and a dict of observed metric values, write the function that produces a promote/hold/reject decision. Handle: a metric the policy doesn't mention, a metric the results don't contain, and more than one breach at different severities simultaneously.
*Expected shape: skip-if-missing (not fail), a decision that's `reject` if ANY breach is `reject`-severity regardless of how many `hold`s there are, and a returned list of every breach — not just the first one found.*

**5. A stateless rate limiter, sort of.**
Your LLM gateway runs with no database. Design (in code or pseudocode) the simplest possible per-key request cap you could add without introducing a new stateful backing service — and explain what you'd lose compared to a real distributed rate limiter.
*Expected shape: an in-memory sliding window or token bucket scoped to a single process, explicit acknowledgment that this doesn't work correctly with more than one replica.*

**6. Diff two prompt files and predict the blast radius.**
Given two versions of a system prompt as strings, write something that flags removed instructions (not just changed wording) — specifically, sentences present in the old version with no reasonably similar sentence in the new one.
*Expected shape: sentence-level diffing, not a raw string diff; a similarity threshold rather than exact match (wording legitimately changes; instructions disappearing is the actual risk).*

**7. A readiness check that can't become a cost problem.**
Write a `/readyz` endpoint for a service that depends on three external APIs, such that it never makes a live network call on every poll, but still meaningfully reflects whether the service can actually serve traffic.
*Expected shape: checks required configuration/credentials are present and well-formed, does NOT ping the three APIs directly; discuss the trade-off out loud.*

**8. Weighted traffic shift with a rollback path.**
Pseudocode (or real code against any cloud CLI/SDK you know) a canary deploy: shift X% of traffic to a new revision, run a smoke test against that revision specifically (not the shared endpoint), then either promote to 100% or restore the previous revision's weight — and make the function fail loudly, not silently, on an ambiguous smoke-test result.
*Expected shape: smoke test targets the new revision directly; an ambiguous/errored smoke test result routes to rollback, not to "assume success."*

**9. A fail-safe audit write.**
Write a function that records an audit event (e.g., "tool call denied") to a persistent store, such that if the write itself fails, the calling request still completes successfully — but the failure is not silently swallowed either.
*Expected shape: try/except around the write only, log the failure with enough context to alert on it later, never let the audit write raise into the request path.*

**10. Deduplicate a recurring alert.**
You have a nightly job that, on failure, should open a GitHub issue — but never open a second issue for the same still-open problem. Write the logic that decides "open a new issue" vs. "comment on the existing one" vs. "do nothing."
*Expected shape: search for an open issue matching a stable identifying label/title before creating; comment-and-update rather than duplicate; only auto-close (or leave open) based on an explicit resolution signal, not just "the job passed once."*

---

*Same rule as the first set: don't rehearse a memorized answer — walk the actual reasoning out loud. On the scenario questions especially, interviewers are listening for your process more than your conclusion.*
