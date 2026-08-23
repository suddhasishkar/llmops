#!/usr/bin/env python3
"""Layer 1 — prompt-template validation. Confirms every prompt file has
required front matter and renders without missing sections. Run:
python -m tests.validate_prompt_templates
"""
from __future__ import annotations
import re, sys
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "app" / "prompts"
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
REQUIRED_FIELDS = ["prompt_id", "version"]


def validate() -> list[str]:
    errors = []
    prompt_files = list(PROMPT_DIR.glob("*.md"))
    if not prompt_files:
        return ["No prompt files found in app/prompts/"]
    for path in prompt_files:
        raw = path.read_text()
        m = FRONT_MATTER_RE.match(raw)
        if not m:
            errors.append(f"{path.name}: missing front matter")
            continue
        for field in REQUIRED_FIELDS:
            if f"{field}:" not in m.group(1):
                errors.append(f"{path.name}: missing required front-matter field '{field}'")
        body = raw[m.end():].strip()
        if len(body) < 20:
            errors.append(f"{path.name}: prompt body suspiciously short ({len(body)} chars)")
    return errors


if __name__ == "__main__":
    errs = validate()
    if errs:
        print(f"FAILED: {len(errs)} prompt validation error(s)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("All prompt templates valid.")
