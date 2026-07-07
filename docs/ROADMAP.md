# Roadmap

**Русская версия:** [ROADMAP-RU.md](ROADMAP-RU.md)

greedy-token today is optimized for **Cursor + Ollama** workspaces. CLI and MCP are IDE-agnostic; paid APIs and alternate local runtimes are on the roadmap below.

Legend: ✅ supported · ❌ not yet · 🔜 planned

Track progress: [GitHub issues labeled `roadmap`](https://github.com/svasenkov/greedy-token/issues?q=is%3Aissue+label%3Aroadmap).

## v0.5 themes

| Theme | Goal | Tracking |
|-------|------|----------|
| **local_llm** | `provider: ollama \| openai_compat` — one config for Ollama and OpenAI-compatible servers | [#2](https://github.com/svasenkov/greedy-token/issues/2) |
| **cloud_llm** | Optional cheap cloud executor for bulk classify / audit (off agent chat) | [#3](https://github.com/svasenkov/greedy-token/issues/3) |
| **mcp_hosts** | Document and test MCP beyond Cursor | [#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15) |

## Paid / cloud

| Provider | Role | CLI | MCP | Cheap executor | Status | Issue |
|----------|------|:---:|:---:|:--------------:|:------:|-------|
| **Cursor** (Agent / Composer) | Agent host, escalation, token baseline | ✅ | ✅ | — | ✅ | — |
| **Anthropic** (Claude API) | Bulk classify / audit off agent | ✅ route | — | ❌ | ❌ 🔜 | [#9](https://github.com/svasenkov/greedy-token/issues/9) |
| **OpenAI** (GPT / Codex API) | Same | ✅ route | — | ❌ | ❌ 🔜 | [#10](https://github.com/svasenkov/greedy-token/issues/10) |
| **Google** (Gemini API) | Same | ✅ route | — | ❌ | ❌ 🔜 | [#11](https://github.com/svasenkov/greedy-token/issues/11) |
| **Mistral** (Codestral API) | Same | ✅ route | — | ❌ | ❌ 🔜 | [#12](https://github.com/svasenkov/greedy-token/issues/12) |
| **Groq / Together / Fireworks** | Fast cloud open-weights (Ollama-tier substitute) | ✅ route | — | ❌ | ❌ 🔜 | [#13](https://github.com/svasenkov/greedy-token/issues/13) |
| **GitHub Copilot** | IDE agent integration | — | — | — | ❌ | [#16](https://github.com/svasenkov/greedy-token/issues/16) |
| **Windsurf / Codeium** | IDE agent integration | — | — | — | ❌ | [#17](https://github.com/svasenkov/greedy-token/issues/17) |

`route` / `estimate` can recommend escalation to a paid agent; greedy-token does **not** call paid APIs as an executor today.

### cloud_llm executor — acceptance criteria

- Config: `cloud_llm.provider`, `api_key` env, `model`, optional `base_url`
- Tier `cloud_llm` between `ollama` and `cursor` in `routes.yaml`
- Scripts `audit-skill`, `classify-file`, `batch-inventory` can target cloud when Ollama unavailable
- Token footer: real API token estimate + billing note (not Cursor baseline only)
- Opt-in only — no silent cloud calls

## Free / local

| Runtime / model | API | CLI tier | Pipeline / scripts | Status | Issue |
|-----------------|-----|:--------:|:------------------:|:------:|-------|
| **Ollama** (localhost) | `/api/chat`, `/api/tags` | ✅ | ✅ | ✅ | — |
| **Ollama** (remote `OLLAMA_URL`) | same | ✅ | ✅ | ✅ | — |
| **Open models via Ollama** (Qwen, Llama, Mistral, …) | via Ollama | ✅ | ✅ | ✅ | — |
| **LM Studio** | OpenAI `/v1/chat/completions` | — | — | ❌ 🔜 | [#4](https://github.com/svasenkov/greedy-token/issues/4) |
| **llama.cpp server** | OpenAI-compatible | — | — | ❌ 🔜 | [#5](https://github.com/svasenkov/greedy-token/issues/5) |
| **vLLM / TGI** | OpenAI-compatible | — | — | ❌ 🔜 | [#6](https://github.com/svasenkov/greedy-token/issues/6) |
| **MLX** (Apple Silicon) | native / via Ollama | partial | partial | ❌ 🔜 | [#7](https://github.com/svasenkov/greedy-token/issues/7) |
| **GPT4All / Jan** | own local API | — | — | ❌ | [#8](https://github.com/svasenkov/greedy-token/issues/8) |

### local_llm abstraction — acceptance criteria

```yaml
# ~/.greedy-token/config.yaml (proposed)
local_llm:
  provider: ollama          # ollama | openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
  # openai_compat only:
  # api_key: optional
  # base_url: http://localhost:1234/v1
```

- `get_local_llm_settings()` replaces `get_ollama_settings()` (alias kept for compat)
- Health check: `/api/tags` (ollama) or `GET /v1/models` (openai_compat)
- Chat: `/api/chat` or `POST /v1/chat/completions`
- `scripts/ollama/_common.sh` → generic `scripts/local-llm/_common.sh` or env-driven backend
- Existing `OLLAMA_*` env vars remain aliases

## IDE / MCP host

| Host | MCP tools | Token-economy rule | Status | Issue |
|------|:---------:|:------------------:|:------:|-------|
| **Cursor** | ✅ | ✅ | ✅ | — |
| **Claude Desktop** (MCP) | likely ✅ | — | ❌ 🔜 | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | — | — | ❌ | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **CLI only** (no IDE) | — | — | ✅ | — |

### MCP hosts — acceptance criteria

- `docs/mcp-setup.md` with host-specific config snippets
- Smoke checklist: 5 tools visible, `greedy_token_search` + `greedy_token_route` work
- Optional: example rule file for non-Cursor agents (Continue custom instructions)

## Out of scope (for now)

- Replacing Cursor/Claude as primary coding agent
- Hosted greedy-token SaaS
- Fine-tuning or training models

## Changelog

| Version | Focus |
|---------|-------|
| **v0.4.2** | Security hardening, MCP dry-run default, CI pytest, log rotation, settings module |
| **v0.4** | MCP pipeline, Ollama config, token economy footer |
| **v0.5** | `local_llm` + `cloud_llm` providers (this roadmap) |
| **v0.6** | IDE integrations beyond Cursor |
