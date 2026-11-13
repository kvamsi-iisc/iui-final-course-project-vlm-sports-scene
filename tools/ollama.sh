#!/usr/bin/env bash
# Run this project's Ollama installation and keep model weights out of source control.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ollama_bin="$project_dir/.local/ollama-0.24.0/ollama"
# The older downloader also fetches Llama's template/parameter layers.
if [[ ! -x "$ollama_bin" ]]; then
    ollama_bin="$project_dir/.local/ollama/ollama"
fi
if [[ ! -x "$ollama_bin" ]]; then
    ollama_bin="$(command -v ollama || true)"
fi
if [[ -z "$ollama_bin" ]]; then
    printf '%s\n' 'Install Ollama from https://ollama.com/download first.' >&2
    exit 1
fi

export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_MODELS="$project_dir/.local/ollama-models"
export OLLAMA_NO_CLOUD=1
export OLLAMA_MAX_LOADED_MODELS=1

if [[ "${1:-}" == "pull-defaults" ]]; then
    "$ollama_bin" pull qwen3-vl:8b-instruct
    "$ollama_bin" pull llama3.2-vision:11b
    exec "$ollama_bin" list
fi
exec "$ollama_bin" "$@"
