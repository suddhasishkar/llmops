"""
Input-layer content-safety guardrail for the Nimbus Support Agent —
always calls the real Azure AI Content Safety API. No offline stub.

This is one of three real-Azure clients in this rebuild (the others:
app/retrieval/retrieval.py, app/agent/azure_openai_client.py) that used
to have a free/offline default and an opt-in real backend. That split is
gone deliberately: every earlier version of this program left "cloud"
as something you had to remember to turn on, which is exactly backwards
for a program whose whole point is teaching how these services are
actually used in production. There is nothing to default to here now —
`check_content()` always calls Azure.

This module can FLAG a turn; only app/agent/tool_policy.py's
`enforce_tool_execution_boundary()` can actually BLOCK a tool call. That
separation — a probabilistic content classifier vs. a deterministic
authorization boundary — is the single most important idea in this
program's guardrail design, and it does not change just because the
classifier itself is now always real. See app/agent/agent.py's
`run_turn()`: a flagged turn gets every tool withheld before the model
is ever asked, but that's still an upstream decision the deterministic
boundary doesn't depend on to do its own job correctly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ContentSafetyResult:
    flagged: bool
    categories: list[str] = field(default_factory=list)
    reason: str = ""


def check_content(text: str) -> ContentSafetyResult:
    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions
    except ImportError as e:
        raise ImportError(
            "The 'azure-ai-contentsafety' package is required. Install it with: "
            "pip install azure-ai-contentsafety azure-identity"
        ) from e

    endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "AZURE_CONTENT_SAFETY_ENDPOINT is not set. `azd up` writes this "
            "automatically -- see Day1_Lab_Guide.md Part 0."
        )

    api_key = os.getenv("AZURE_CONTENT_SAFETY_API_KEY")
    if api_key:
        from azure.core.credentials import AzureKeyCredential
        credential = AzureKeyCredential(api_key)
    else:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential(managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"))

    client = ContentSafetyClient(endpoint, credential)
    response = client.analyze_text(AnalyzeTextOptions(text=text))
    # Severity is 0-6 per category; 2 ("medium") is Microsoft's own
    # documented default action threshold -- see Azure AI Content Safety
    # "severity levels" docs before changing this in a real deployment.
    flagged_categories = [
        str(item.category) for item in response.categories_analysis if (item.severity or 0) >= 2
    ]
    return ContentSafetyResult(
        flagged=bool(flagged_categories),
        categories=flagged_categories,
        reason=("Azure AI Content Safety analyze_text flagged category severity >= 2"
                if flagged_categories else "no category above severity threshold"),
    )
