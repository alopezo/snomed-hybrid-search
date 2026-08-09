#!/usr/bin/env bash
# Start a small, local, OpenAI-compatible LLM for the translation + rerank step, using Ollama.
# Ollama is Metal-accelerated on Apple Silicon and needs no extra scaffolding.
#
# Usage:
#   scripts/serve-llm.sh                 # uses GEMMA_MODEL from .env, or the default below
#   scripts/serve-llm.sh gemma3:1b       # override the model tag
#
# See docs/llm-setup.md for alternatives (MLX, llama.cpp) and model options.
set -euo pipefail

# Load GEMMA_MODEL from .env if present (without exporting the whole file).
if [ -f .env ]; then
  ENV_MODEL="$(grep -E '^GEMMA_MODEL=' .env | tail -1 | cut -d= -f2- || true)"
fi
MODEL="${1:-${ENV_MODEL:-gemma3:4b}}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed. Install it, then re-run this script:"
  echo "  brew install ollama          # macOS (Homebrew)"
  echo "  # or download from https://ollama.com/download"
  exit 1
fi

# Keep the model resident so queries don't pay a cold-load penalty (default: never unload).
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"

# Ollama serves at http://localhost:11434 (OpenAI-compatible under /v1). Start it if not reachable.
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Starting 'ollama serve' in the background (logs: /tmp/ollama.log; OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE)…"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  for _ in $(seq 1 30); do
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

echo "Pulling model: $MODEL (first time downloads it)…"
ollama pull "$MODEL"

echo
echo "LLM ready. Ensure these are set in your .env:"
echo "  GEMMA_URL=http://localhost:11434/v1"
echo "  GEMMA_MODEL=$MODEL"
echo
echo "Quick test:"
echo "  curl -s http://localhost:11434/v1/chat/completions -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Translate to English: azucar alta\"}]}'"
