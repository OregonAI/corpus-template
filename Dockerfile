# MCP server for the {{CORPUS_ID}} corpus (HTTP transport).
#
#   docker build -t {{CORPUS_ID}}-mcp .
#   docker run -p 8000:8000 {{CORPUS_ID}}-mcp
#
# The mirrored corpus is baked in at build time; rebuild the image to pick up new commits.
#
# BUILD FROM A SHALLOW CLONE, not your working tree. `.git` cannot be excluded — it is a
# RUNTIME dependency, because the FTS cache key is `git rev-parse HEAD` plus a hash of
# `git status --porcelain`, and corpus_overview() shells out to `git log -1`. A depth-1
# clone keeps git working and the image small:
#
#   git clone --depth 1 --branch main https://github.com/OregonAI/{{CORPUS_ID}} build/
#   docker build -t {{CORPUS_ID}}-mcp build/
#
# HYBRID/API ARCHETYPES need NETWORK EGRESS at runtime for their live half. Keep the
# degradation HONEST: an upstream outage must report upstream_status: "unavailable",
# never render as a zero (see oregon-budget's Dockerfile and tests/test_hybrid.py for
# the precedent and the reasoning). A document corpus can delete this paragraph.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /repo
# Deps BEFORE content: a content-only change must not re-run pip. With these two steps
# the other way round — how the live corpora read until 2026-07-30 — every edited
# document invalidated the COPY layer and forced a full reinstall.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Pre-build the FTS index so the first request is instant, and fail the BUILD if content
# is missing rather than shipping an image that starts fine and answers nothing.
# ...AND smoke-test what the container actually RUNS: the CMD runs `server` via the
# console script, so the build imports that module and resolves that entry point, not
# just the framework (platform-deploy#2 — an image once built green and crash-looped
# because mcp 2.0.0 broke `server` while `framework` imported fine).
RUN python3 -c "\
from corpus_toolkit import config as config_mod; \
from corpus_toolkit.mcp.framework import CorpusFramework; \
CorpusFramework(config_mod.load('_meta/corpus.yml')).ensure_index()" \
 && python3 -c "import corpus_toolkit.mcp.server" \
 && corpus-mcp-serve --help >/dev/null
EXPOSE 8000

# --path and --public-hostname both matter behind the tunnel and are easy to omit:
#   * A Cloudflare Tunnel matches on path but does NOT strip it. Routing /{{CORPUS_ID}}
#     here forwards the whole path, so the server must mount at that same prefix or
#     every request 404s.
#   * Without --public-hostname the SDK's DNS-rebinding guard rejects the forwarded Host
#     header with 421 Invalid Host header.
# Override either at `docker run` for a different hostname or a dedicated-host
# deployment (in which case pass --path /mcp).
CMD ["corpus-mcp-serve", "--config", "_meta/corpus.yml", "--http", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--path", "/{{CORPUS_ID}}/mcp", \
     "--public-hostname", "oregonai.morficflux.com"]
