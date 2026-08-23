#!/usr/bin/env bash
# One-command LOCAL setup: installs dependencies and runs every check that
# does NOT require a live Azure subscription -- dataset/prompt/tool-schema
# validation and the unit test suite. This is deliberately everything this
# project can verify for free, before you spend any cloud money.
#
# It does NOT build a retrieval index and does NOT start the agent -- there
# is no local/offline path for either one left in this project (see
# Agent_End_to_End_Architecture.md Section 2, "No local fallback, anywhere").
# The next command after this script finishes successfully is always
# `azd up`, which is what actually provisions Azure OpenAI, Azure AI
# Search, and Azure AI Content Safety and builds the real index.
#
# Usage: ./scripts/seed_lab.sh
set -euo pipefail

echo "== Installing dependencies =="
pip install -r requirements.txt --break-system-packages --quiet

echo "== Validating datasets, prompts, and tool schemas (free, local, no cloud calls) =="
python -m eval.validate_datasets
python -m tests.validate_prompt_templates
python -m tests.validate_tool_schemas

echo "== Running unit tests (free, local, no cloud calls) =="
pytest tests/unit -q

echo ""
echo "All local checks passed. Nothing further can be verified without a"
echo "real Azure subscription -- there is no stub or offline mode to fall"
echo "back to. Next step:"
echo "  azd auth login"
echo "  azd up"
echo ""
echo "See Day1_Lab_Guide.md Part 0 for what azd up actually does and how"
echo "long each stage takes."
