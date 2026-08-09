# Local LLM setup (the ② component)

The hybrid search needs an **OpenAI-compatible chat endpoint** for two lightweight tasks:
1. **Translate/expand** the query to English (e.g. `azucar alta` → `high blood sugar hyperglycemia`).
2. **Optional rerank** of the top results by faithfulness to the original query.

This is *not* the embedding model (that's BioLORD, handled by `embed/`). It is a small chat model.
It is **optional**: without it, English queries and cross-lingual embeddings still work; you lose
reliable handling of short non-English lay phrases and the rerank.

> **Model size vs quality (measured).** A very small model (e.g. `gemma3:4b`) is fastest (~0.6–1 s)
> but terse/inconsistent — it can drop the key term or invent details, which hurts retrieval.
> `gemma3:12b` (~1–2 s) recovers quality close to a large 26B-class model and is the recommended
> default. Go bigger (`gemma3:27b`) only if you need the last bit of quality and have the RAM.

The app reads two variables from `.env`:

```
GEMMA_URL=http://localhost:11434/v1
GEMMA_MODEL=gemma3:12b
```

---

## Option A — Ollama (recommended, easiest)

Metal-accelerated on Apple Silicon, one-line install, no extra scaffolding.

```bash
brew install ollama                 # or download from https://ollama.com/download
scripts/serve-llm.sh                # starts ollama + pulls the model (reads GEMMA_MODEL from .env)
```

`scripts/serve-llm.sh` starts `ollama serve` (at `http://localhost:11434`, OpenAI-compatible under
`/v1`) and pulls the model. Pass a tag to override: `scripts/serve-llm.sh gemma3:1b`.

Set in `.env`:
```
GEMMA_URL=http://localhost:11434/v1
GEMMA_MODEL=gemma3:12b       # recommended; gemma3:4b faster/lighter, gemma3:27b highest quality
```

Model tags: see https://ollama.com/library/gemma3 (and `gemma2`). `ollama list` shows what you have.

---

## Option B — MLX (`mlx-lm`), Apple-native, fastest on Apple Silicon

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/gemma-3-4b-it-4bit --port 8080
```
Set in `.env`:
```
GEMMA_URL=http://localhost:8080/v1
GEMMA_MODEL=mlx-community/gemma-3-4b-it-4bit
```
Pick any gemma from https://huggingface.co/mlx-community (the `--model` value is the `GEMMA_MODEL`).

---

## Option C — llama.cpp (`llama-server`), Metal, portable

```bash
brew install llama.cpp
llama-server -hf ggml-org/gemma-3-4b-it-GGUF --port 8080   # downloads the GGUF on first run
```
Set in `.env`:
```
GEMMA_URL=http://localhost:8080/v1
GEMMA_MODEL=gemma-3-4b-it            # any non-empty name; llama-server serves the loaded model
```

---

## Verify

```bash
curl -s "$GEMMA_URL/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"'"$GEMMA_MODEL"'","messages":[{"role":"user","content":"Translate to English: azucar alta"}]}'
```
You should get something like `high blood sugar / hyperglycemia`. If the endpoint is down, the app
falls back to the raw query (8 s timeout, non-blocking).
