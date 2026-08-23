# gateway/Dockerfile -- packages LiteLLM Proxy (MIT license,
# github.com/BerriAI/litellm) with this project's config baked in. Built
# and pushed by `azd up`/`azd deploy` exactly like the main
# Dockerfile at the repo root -- see azure.yaml's `gateway` service and
# docs/adr/0002-llm-gateway-and-observability.md.
#
# Pinned to a versioned tag, not `:latest` or the deprecated
# `:main-stable` convention (discontinued by BerriAI 2026-09-01) --
# bump this deliberately, don't float it.
FROM ghcr.io/berriai/litellm:1.84.0

COPY litellm_config.yaml /app/config.yaml

EXPOSE 4000

# The base image's own ENTRYPOINT is expected to be `litellm` (per
# BerriAI's published docker-quick-start run examples); this CMD only
# supplies arguments to it, so it composes with that entrypoint rather
# than replacing it. If the gateway container fails to start after
# `azd deploy`, check `az containerapp logs show` first -- the most
# likely cause is a base-image entrypoint mismatch, which this project
# has not independently verified against every tag.
CMD ["--config", "/app/config.yaml", "--port", "4000"]
