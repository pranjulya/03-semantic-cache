#!/usr/bin/env bash
# One-command setup: Python deps + cached embedder + local LLM. Needs network once.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${SCACHE_LLM_TAG:-qwen2.5:7b}"

command -v uv >/dev/null || { echo "error: uv not found. Install: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v ollama >/dev/null || { echo "error: ollama not found. Install: https://ollama.com/download" >&2; exit 1; }

uv sync
curl -sf http://localhost:11434/api/tags >/dev/null \
  || { echo "error: Ollama is not running. Start it with: ollama serve" >&2; exit 1; }
ollama pull "$MODEL"
uv run python scripts/download_models.py
uv run pytest -q
echo "ready."
