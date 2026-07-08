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

## v0.6 themes

| Theme | Goal | Tracking |
|-------|------|----------|
| **mcp_hosts** (cont.) | Continue — full smoke | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **ci_headless** | greedy-token in CI: route/pipeline to self-hosted Ollama instead of always-Claude jobs | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

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
| **Claude Desktop** (MCP) | likely ✅ | ✅ example | ✅ docs | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | — | — | ❌ | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **CLI only** (no IDE) | — | — | ✅ | — |

### MCP hosts — acceptance criteria

- ✅ `docs/mcp-setup.md` with host-specific config snippets — [mcp-setup.md](mcp-setup.md); Claude: [claude-desktop-setup.md](claude-desktop-setup.md)
- ✅ Smoke checklist: 5 tools visible, `greedy_token_search` + `greedy_token_route` work — [mcp-setup.md#smoke-checklist](mcp-setup.md#smoke-checklist); automated stdio: `tests/test_mcp_stdio.py`
- ✅ Optional: example rule file for non-Cursor agents — [examples/claude/instructions.md](../examples/claude/instructions.md)
- 🔜 Continue / VS Code — [#15](https://github.com/svasenkov/greedy-token/issues/15)

## CI / headless

Scenario: a company already burns Claude/Cursor in pipelines; they run Ollama (or openai_compat) on internal GPUs — greedy-token in the job routes bulk AI work to the local LLM instead of always-Claude.

```text
CI job → greedy-token CLI → rg | python | Ollama (internal) | RAG | cloud_llm (opt-in)
```

This is **not MCP inside Actions** — headless CLI (`route`, `pipeline --execute`, `report`). Remote `OLLAMA_URL` already works; missing pieces are docs, example workflows, and an explicit runner env contract.

| CI host | Role | Status | Issue |
|---------|------|:------:|-------|
| **Self-hosted / VPN runner** + in-network Ollama | Primary target pattern | ❌ 🔜 | [#18](https://github.com/svasenkov/greedy-token/issues/18) |
| **GitHub-hosted ephemeral** with no path to private Ollama | rg/python/rag or cloud_llm only | out of focus | — |
| **Jenkins / GitLab CI** | Same CLI contract | ❌ 🔜 (examples) | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

### ci_headless — acceptance criteria

- `docs/ci-setup.md`: env (`OLLAMA_URL` / `local_llm`, `GREEDY_TOKEN_ROOT`, telemetry), no Cursor MCP
- Example workflow: GitHub Actions (self-hosted) + optional Jenkins snippet
- Smoke: `route` + `pipeline … --execute` against remote Ollama from a clean runner image
- Guidance: which task classes stay local vs escalate to `cloud_llm` / agent
- Optional: `greedy-token report` in job summary / artifact

Related: [#2](https://github.com/svasenkov/greedy-token/issues/2) (`local_llm`), [#3](https://github.com/svasenkov/greedy-token/issues/3) (`cloud_llm` as paid fallback).

## Out of scope (for now)

- Replacing Cursor/Claude as primary coding agent
- Hosted greedy-token SaaS
- Fine-tuning or training models
- Ephemeral public runners with no network path to corporate Ollama (without VPN/self-hosted)

## Changelog

| Version | Focus |
|---------|-------|
| **v0.4.4** | Cursor-first README, mascot, shorter MCP instructions, CI/headless roadmap (#18) |
| **v0.4.3** | Cursor starter kit (`examples/cursor/`) + setup docs for PyPI users |
| **v0.4.2** | Security hardening, MCP dry-run default, CI pytest, log rotation, settings module |
| **v0.4** | MCP pipeline, Ollama config, token economy footer |
| **v0.5** | `local_llm` + `cloud_llm` providers (this roadmap) |
| **v0.6** | IDE integrations beyond Cursor + **CI / headless** docs & examples ([#18](https://github.com/svasenkov/greedy-token/issues/18)) |
