"""
Shared prompt-file loader for every agent role (manager, billing,
account). Extracted out of the old single-agent `support_agent.py` so
all three roles parse the same front-matter format the same way, instead
of three copies of the same regex drifting apart.

Prompts live as versioned Markdown files in app/prompts/, not as Python
string constants -- `load_prompt(role, version)` reads and parses
`system_prompt_<file_stub>.md`'s front matter + body. This is the same
prompt-as-config pattern a real deployment would use (prompts reviewed
and versioned independently of application code), and it's what
tests/validate_prompt_templates.py checks the shape of.
"""
from __future__ import annotations

import re
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def load_prompt(file_stub: str) -> str:
    """Reads app/prompts/system_prompt_<file_stub>.md, validates its front
    matter has the required fields, and returns the body text (the actual
    system instruction). Raises FileNotFoundError / ValueError with a
    message pointing back at the file on any problem.

    `file_stub` is the part of the filename after `system_prompt_` and
    before `.md` -- e.g. "manager", "billing_baseline", "account".
    """
    path = PROMPT_DIR / f"system_prompt_{file_stub}.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt file at {path}")
    raw = path.read_text()
    m = FRONT_MATTER_RE.match(raw)
    if not m:
        raise ValueError(f"{path} is missing required YAML-style front matter")
    return m.group(2).strip()
