# LLMOps / AgentOps Interview Questions

Built from the actual Nimbus Support Copilot platform, so every question below has a real, concrete answer sitting in your own repo — not a textbook definition. Use this either way: as an interviewer probing a candidate's depth, or as your own prep sheet, since each question includes a one-line pointer to the real answer you already built. A short "questions to ask them" section sits at the end in case you're the one being interviewed.

---

## 1. LLMOps fundamentals

1. **What's the difference between MLOps and LLMOps — what does LLMOps add that a classic ML pipeline doesn't have to think about?**
   *Your answer anchor: prompts, model deployment, retrieval, eval datasets, and safety config are all things that change independently of code — and each needs the same release discipline as a code change, or you get a silent bypass.*

2. **What's the difference between LLMOps and AgentOps?**
   *Anchor: AgentOps adds tools, permissions, memory/state, policy, trajectory, and approval on top of everything LLMOps already tracks — it's about a system that *acts*, not just *answers*.*

3. **Why can't you evaluate an LLM system with the same unit-test mindset you'd use for a deterministic API?**
   *Anchor: the deterministic/probabilistic split — Layers 1–2 (unit tests, schema validation, dependency scans) are pass/fail gates; Layers 3–4 (groundedness, citation coverage, tool-selection accuracy) are threshold judgments against real model output, not assertions.*

4. **How do you decide what belongs in a hard release gate versus what stays informational-only?**
   *Anchor: your own LLM-as-judge decision — a new signal (judge-scored groundedness/helpfulness) stays informational until it has a calibration history against known good/bad cases; gating on an uncalibrated metric is worse than not measuring it at all.*

5. **What's in your "release unit" for an AI system, and why isn't it just the container image?**
   *Anchor: code, but also prompts, tool schemas, the release policy itself, gateway config, the model deployment reference, and dataset versions — all versioned together under one release SHA, so rollback means rolling back all of it, not just the binary.*

---

## 2. AgentOps / multi-agent architecture

6. **Walk me through why you'd split a single agent into a Manager + Specialist architecture instead of one agent with all tools.**
   *Anchor: least-privilege by construction — the Manager makes one routing call restricted to two functions and never sees the real tool schemas at all, so it structurally cannot call a monetary tool even if the model wanted to.*

7. **If your routing model returns something ambiguous or malformed, what should happen — and why does the *direction* of the default matter?**
   *Anchor: your ManagerAgent defaults to the specialist with no monetary-capable tool ("fail toward the least-capable path"), not to a guess — a bad default should always fail safe, not fail permissive.*

8. **How many real model calls does one user turn cost in a multi-agent system, and why does that matter operationally?**
   *Anchor: 2 minimum (route + specialist decision), 3 if a tool executes — this directly changed your `max_agent_steps` release-policy threshold and is a real cost/latency trade-off worth naming out loud, not hiding.*

9. **How do you enforce that a specialist agent can only ever call the tools it's supposed to have?**
   *Anchor: double narrowing — the specialist's fixed tool set intersected with whatever `offered_tools` survived the policy/content-safety layer one level up. Neither set is trusted alone.*

10. **Where's the actual authorization boundary in your system — is it the prompt, the model's tool choice, or something else?**
    *Anchor: neither. `agent_policy_layer()` decides what's *offered*; `enforce_tool_execution_boundary()` decides what's *allowed to execute*, as a separate deterministic step after the model decides. A prompt-injection classifier is not a substitute for this.*

---

## 3. Model platform & gateway decisions

11. **Why put an LLM gateway between your application and the model provider instead of calling the provider directly?**
    *Anchor: centralizes routing, auth, logging, and observability wiring in one place instead of instrumenting every agent module separately — and it's the single point where you could later add virtual keys, budgets, or provider failover without touching agent code.*

12. **How does your gateway authenticate to the model provider, and why does that matter for a security review?**
    *Anchor: managed identity, never a static API key — the gateway is the only thing that holds Azure credentials at all; nothing upstream of it does.*

13. **You had two constraints that pulled in different directions when picking an observability tool — what were they, and how did you resolve the conflict?**
    *Anchor: "must be MIT-licensed" vs. "minimum budget across three environments." Self-hosting the MIT tool needed four backing services (real ongoing cost); the same MIT tool's free hosted tier needed zero new infrastructure. You picked the free tier and named the trade-off (usage cap, retention limit) explicitly instead of hiding it.*

14. **Why would you evaluate and reject two other well-known tools before landing on your final choice?**
    *Anchor: two strong candidates were ruled out on a hard license constraint (Apache-2.0 and Elastic License 2.0, not MIT) — a good interview signal is "I checked the actual license file, I didn't assume."*

15. **What do you lose by running your gateway stateless (no database)?**
    *Anchor: per-caller virtual keys and spend tracking — acceptable for a shared demo key, not acceptable for real multi-tenant production. Naming what you *don't* get from a simplification is as important as naming what you do.*

---

## 4. Evaluation & release engineering

16. **Read me a release-policy threshold and tell me what happens on breach, and why the response differs by threshold.**
    *Anchor: some thresholds `hold` (human review before re-attempt), some `reject` outright with zero tolerance (e.g. any unauthorized monetary action) — the response should match the blast radius of the failure, not be uniform.*

17. **Your cost-per-turn threshold is built on an estimate, not billed usage. Is that a flaw? How would you defend it?**
    *Anchor: it's honestly documented as an estimate (character-count heuristic) good enough to catch a regression trend, explicitly not good enough to reconcile against an invoice — the real fix (reading the actual `usage` object from the model response) is a named, scoped next step, not a hidden gap.*

18. **What's the difference between your PR-triggered evaluation and your nightly evaluation, and why do you need both?**
    *Anchor: PR eval catches regressions a code change introduces; nightly eval catches regressions nothing in your repo caused at all — an upstream model behavior shift, or index staleness with zero commits.*

19. **Why does a code-scanning stage that's explicitly a placeholder still belong in a pipeline you'd show a client?**
    *Anchor: it demonstrates the pipeline *shape* honestly, with every real-tool swap-in named (which SAST tool, which secrets scanner, which CVE scanner) — the alternative (silently omitting the stage) hides more than a labeled mock does.*

20. **How do you keep three environments' infrastructure from drifting apart over time?**
    *Anchor: one Bicep template, parametrized per environment (SKU, capacity, replica count) — never three forked templates. Divergence gets caught at the parameter level, not by comparing whole files.*

---

## 5. Deployment reliability

21. **What's actually wrong with `activeRevisionsMode: Single` for a system you care about rolling back?**
    *Anchor: a bad deploy overwrites the only serving revision immediately — there's no previous revision left to fall back to, by construction, not by bad luck.*

22. **Design a canary rollout for me — what's the smallest version that's still meaningfully safer than "deploy and hope"?**
    *Anchor: shift a small traffic percentage to the new revision, smoke-test that revision directly (not through the shared endpoint), then promote to 100% or roll back based on the result — production gets a smaller slice and a longer soak than dev/staging.*

23. **Tell me about a gap in your own reliability design that you didn't fully close, and why you left it open.**
    *Anchor (this is a strong answer to have ready): a declarative traffic rule means a freshly deployed revision gets 100% of traffic the instant it's healthy, before your canary script can shift it down — you narrowed the exposure window as much as possible but didn't eliminate it, and documented exactly why the stricter fix wasn't shipped without full verification.*

24. **What's the difference between a liveness probe and a readiness probe, and what's a common mistake teams make with either?**
    *Anchor: liveness = restart if dead; readiness = don't route traffic until actually ready. Common mistake: a readiness probe that makes a live downstream call on every poll interval becomes its own cost/rate-limit problem — probes should be cheap and dependency-free.*

25. **How do you decide an alert threshold isn't just a guessed number?**
    *Anchor: trace it back to a written SLO target, and prefer GA (generally available) metrics you can verify the exact name and unit of over Preview metrics whose dimension values you can't confirm — a metric alert that silently never fires because you guessed a filter value wrong is worse than no alert.*

---

## 6. Governance, audit & compliance-adjacent

26. **What's the difference between your audit trail and your application's business data — why do they need to be two different things?**
    *Anchor: the audit trail is a governance record of *what happened* (fail-safe, write failures never block a request); business data is the mock state the demo operates on. Conflating "make the audit trail durable" with "make the whole demo stateful" is a design smell to avoid.*

27. **You built a human-approval workflow with no authentication in front of it. How would you defend that in a security review — and what would you actually fix first?**
    *Anchor: name it as a scoped, presentation-grade boundary consistent with everything else in the system, not an oversight — and the fix (real identity check in front of the approval surface) is the concrete, correctly-prioritized next step, not a vague "we'll add security later."*

28. **How would you explain the difference between monitoring and observability to a non-technical stakeholder?**
    *Anchor: monitoring answers "is the system healthy overall" (aggregate: volume, errors, latency); observability answers "what happened for this one specific request" (a trace). You need both — one tells you something's wrong, the other tells you what.*

29. **What's a drift category that a standard software monitoring stack would never catch?**
    *Anchor: knowledge/retrieval drift — the code and index are both technically "working," but the underlying policy document changed and nothing re-indexed it. Nothing crashed; the system just started being confidently wrong.*

---

## 7. Judgment / trade-off questions (the ones that separate depth from memorization)

30. **Tell me about a decision where you had a "best" option and a "cheapest" option, and you picked the cheap one. How did you make sure that wasn't just cutting a corner?**
31. **When is a placeholder or mock component the *right* engineering choice instead of technical debt?**
32. **Describe a time your system's default behavior mattered more than its happy-path behavior.**
33. **How do you decide something is "good enough to demo to a client" versus "good enough for production"? Where's that line in your own system, concretely?**
34. **What would you do differently if this had to serve real production traffic tomorrow — rank the top three changes by priority, not by ease.**

---

## If you're the one being interviewed: questions worth asking them

- "What does your organization currently use for LLM evaluation — is it a hard release gate, or informational like a lot of teams start with?"
- "How do you handle the fact that a model provider can silently change behavior with no deploy on your side — do you have anything like scheduled regression evaluation?"
- "Where does your team draw the line between what a guardrail should catch deterministically versus what an LLM-based classifier is trusted to catch?"
- "How mature is your rollback story for AI-specific releases — does 'rollback' mean the container, or the whole release unit (prompts, tool schemas, index version) together?"
- "What's the biggest named gap in your current LLMOps/AgentOps maturity that you're actively working to close?" (Shows you think in terms of maturity roadmaps, not finished/unfinished binaries.)

---

*Built from your own ADRs (0001–0004), release-policy.yaml, and the actual CI/CD pipeline — every "anchor" above is something you can speak to with a real file, a real trade-off, and a real reason, not a rehearsed definition.*
