# Roadmap

**Русская версия:** [ROADMAP-RU.md](ROADMAP-RU.md)

As of **v0.10.0**, greedy-token runs in any MCP-capable agent host (`agent_host: cursor | claude | continue`, Cursor by default) with Ollama or any OpenAI-compatible runtime as the cheap tier. CLI and MCP are IDE-agnostic; metered bulk APIs are supported opt-in ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)). Remaining gaps are tracked below.

Legend: ✅ supported · ❌ not yet · 🔜 planned

Track progress: [GitHub issues labeled `roadmap`](https://github.com/svasenkov/greedy-token/issues?q=is%3Aissue+label%3Aroadmap).

## Themes

| Theme | Goal | Status |
|-------|------|--------|
| **cheap_llm** | `provider: ollama \| openai_compat` — one config for Ollama and OpenAI-compatible servers | ✅ v0.5.0 ([#2](https://github.com/svasenkov/greedy-token/issues/2)) |
| **expensive_llm** | Metered agent / API path (Cursor today; optional paid agent APIs) — not bulk classify | ✅ MVP v0.5.9; metered bulk path v0.10.0 ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)) ([#3](https://github.com/svasenkov/greedy-token/issues/3)) |
| **mcp_hosts** | Document and test MCP beyond Cursor | ✅ v0.10.0 config + docs (`agent_host`); 🔜 live smoke ([#14](https://github.com/svasenkov/greedy-token/issues/14), [#15](https://github.com/svasenkov/greedy-token/issues/15)) |
| **ci_headless** | greedy-token in CI: route/pipeline to self-hosted Ollama instead of always-Claude jobs | ✅ docs + examples ([ci-setup.md](ci-setup.md)); 🔜 live smoke ([#18](https://github.com/svasenkov/greedy-token/issues/18)) |

## Paid / cloud

| Provider | Role | CLI | MCP | Cheap executor | Status | Issue |
|----------|------|:---:|:---:|:--------------:|:------:|-------|
| **Cursor** (Agent / Composer) | Agent host, escalation, token baseline | ✅ | ✅ | — | ✅ | — |
| **Anthropic** (Claude API) | Bulk classify / audit off agent | ✅ route | — | ✅ via `openai_compat` + ADR-0002 | ✅ | [#9](https://github.com/svasenkov/greedy-token/issues/9) |
| **OpenAI** (GPT / Codex API) | Same | ✅ route | — | ✅ via `openai_compat` + ADR-0002 | ✅ | [#10](https://github.com/svasenkov/greedy-token/issues/10) |
| **Google** (Gemini API) | Same | ✅ route | — | ✅ via `openai_compat` + ADR-0002 | ✅ | [#11](https://github.com/svasenkov/greedy-token/issues/11) |
| **Mistral** (Codestral API) | Same | ✅ route | — | ✅ via `openai_compat` + ADR-0002 | ✅ | [#12](https://github.com/svasenkov/greedy-token/issues/12) |
| **Groq / Together / Fireworks** | Fast cloud open-weights (Ollama-tier substitute) | ✅ route | — | ✅ via `openai_compat` + ADR-0002 | ✅ | [#13](https://github.com/svasenkov/greedy-token/issues/13) |
| **GitHub Copilot** | IDE agent integration | — | — | — | ❌ | [#16](https://github.com/svasenkov/greedy-token/issues/16) |
| **Windsurf / Codeium** | IDE agent integration | — | — | — | ❌ | [#17](https://github.com/svasenkov/greedy-token/issues/17) |

Metered bulk APIs serve the cheap executor tier via `llm.models[]` (`billing: metered`, derived tier cheap) — opt-in `llm.metered.opt_in` + spend guard, see [ADR-0002](adr/0002-metered-bulk-cheap-tier.md). Escalation to a paid *agent* remains recommend-only.

### expensive_llm path — acceptance criteria

- Label in footers/docs: **expensive LLM** = full agent chat (Cursor) or metered agent API
- Config (optional, v0.5+): `expensive_llm.provider`, `api_key` env, `model` for non-Cursor agent hosts
- Opt-in only for any paid API calls — no silent spend

## Free / local

| Runtime / model | API | CLI tier | Pipeline / scripts | Status | Issue |
|-----------------|-----|:--------:|:------------------:|:------:|-------|
| **Ollama** (localhost) | `/api/chat`, `/api/tags` | ✅ | ✅ | ✅ | — |
| **Ollama** (remote `OLLAMA_URL`) | same | ✅ | ✅ | ✅ | — |
| **Open models via Ollama** (Qwen, Llama, Mistral, …) | via Ollama | ✅ | ✅ | ✅ | — |
| **LM Studio** | OpenAI `/v1/chat/completions` | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#4](https://github.com/svasenkov/greedy-token/issues/4) |
| **llama.cpp server** | OpenAI-compatible | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#5](https://github.com/svasenkov/greedy-token/issues/5) |
| **vLLM / TGI** | OpenAI-compatible | ✅ via `openai_compat` | ✅ | ✅ adapter · 🔜 docs | [#6](https://github.com/svasenkov/greedy-token/issues/6) |
| **MLX** (Apple Silicon) | native / via Ollama | partial | partial | ❌ 🔜 | [#7](https://github.com/svasenkov/greedy-token/issues/7) |
| **GPT4All / Jan** | own local API | — | — | ❌ | [#8](https://github.com/svasenkov/greedy-token/issues/8) |

### cheap_llm abstraction — acceptance criteria

**Shipped in v0.5.0** ([#2](https://github.com/svasenkov/greedy-token/issues/2)). Tier route id stays `ollama`.

```yaml
# ~/.greedy-token/config.yaml
cheap_llm:
  provider: ollama          # ollama | openai_compat
  url: http://localhost:11434
  model: qwen2.5-coder:7b-instruct-q4_K_M
  # openai_compat example:
  # provider: openai_compat
  # url: http://localhost:1234   # /v1 appended if missing
```

- ✅ `get_cheap_llm_settings()` replaces `get_ollama_settings()` (alias kept for compat)
- ✅ Health check: `/api/tags` (ollama) or `GET /v1/models` (openai_compat)
- ✅ Chat: `/api/chat` or `POST /v1/chat/completions`
- ✅ `OLLAMA_*` / `ollama:` config remain aliases; `CHEAP_LLM_*` preferred
- 🔜 Workspace `scripts/ollama/_common.sh` → env-driven / `scripts/cheap-llm/` (consumer scripts; not package)

Multi-model registry (one `llm.models[]` pool, derived tier) and ready-made presets: [ADR-0001](adr/0001-unified-model-spec-derived-tier.md) · [examples/presets/README.md](../examples/presets/README.md).

## IDE / MCP host

| Host | MCP tools | Token-economy rule | Status | Issue |
|------|:---------:|:------------------:|:------:|-------|
| **Cursor** | ✅ | ✅ | ✅ | — |
| **Claude Desktop** (MCP) | ✅ | ✅ `examples/claude/CLAUDE.md` | ✅ config + docs (`agent_host: claude`) | [#14](https://github.com/svasenkov/greedy-token/issues/14) |
| **VS Code + Continue** | ✅ | ✅ `examples/continue/continuerules.md` | ✅ config + docs (`agent_host: continue`) | [#15](https://github.com/svasenkov/greedy-token/issues/15) |
| **CLI only** (no IDE) | — | — | ✅ | — |

### MCP hosts — acceptance criteria

- ✅ Host setup guides with config snippets: `docs/claude-setup.md`, `docs/continue-setup.md` (+RU)
- ✅ `agent_host: cursor|claude|continue` config — `audit-context` and the naive-chat baseline count the host's always-on rules (`CLAUDE.md`, `.continuerules`)
- ✅ Rule templates for non-Cursor agents: `examples/claude/CLAUDE.md`, `examples/continue/continuerules.md`
- 🔜 Live smoke on real hosts: tools visible, `greedy_token_search` + `greedy_token_route` work (manual checklist)

## CI / headless

Scenario: a company already burns Claude/Cursor in pipelines; they run Ollama (or openai_compat) on internal GPUs — greedy-token in the job routes bulk AI work to the cheap LLM instead of always-Claude.

```text
CI job → greedy-token CLI → rg | python | cheap_llm (Ollama/internal) | RAG | expensive_llm agent (opt-in)
```

This is **not MCP inside Actions** — headless CLI (`route`, `pipeline --execute`, `report`). Remote `OLLAMA_URL` works; docs, example workflows, and the runner env contract live in [ci-setup.md](ci-setup.md).

| CI host | Role | Status | Issue |
|---------|------|:------:|-------|
| **Self-hosted / VPN runner** + in-network Ollama | Primary target pattern | ✅ docs + env contract · 🔜 live smoke | [#18](https://github.com/svasenkov/greedy-token/issues/18) |
| **GitHub-hosted ephemeral** with no path to private Ollama | rg/python/rag or expensive_llm agent only | out of focus | — |
| **Jenkins / GitLab CI** | Same CLI contract | ✅ snippets in [ci-setup.md](ci-setup.md) | [#18](https://github.com/svasenkov/greedy-token/issues/18) |

### ci_headless — acceptance criteria

- `docs/ci-setup.md`: env (`OLLAMA_URL` / `cheap_llm`, `GREEDY_TOKEN_ROOT`, telemetry), no Cursor MCP
- Example workflow: GitHub Actions (self-hosted) + optional Jenkins snippet
- Smoke: `route` + `pipeline … --execute` against remote Ollama from a clean runner image
- Guidance: which task classes stay on cheap_llm vs escalate to expensive_llm / agent
- Optional: `greedy-token report` in job summary / artifact

Related: [#2](https://github.com/svasenkov/greedy-token/issues/2) (`cheap_llm`), [#3](https://github.com/svasenkov/greedy-token/issues/3) (`expensive_llm`).

## Out of scope (for now)

- Replacing Cursor/Claude as primary coding agent
- Hosted greedy-token SaaS
- Fine-tuning or training models
- Ephemeral public runners with no network path to corporate Ollama (without VPN/self-hosted)

## Changelog

Per-release detail: `CUT-v*.md` checklists in the repo root.

| Version | Focus |
|---------|-------|
| **v0.10.0** | Beyond-Cursor: `agent_host: cursor \| claude \| continue`, metered bulk APIs under spend guard ([ADR-0002](adr/0002-metered-bulk-cheap-tier.md)), calibration nudges, team route presets (`init --preset`) |
| **v0.9.0** | Unified model registry ([ADR-0001](adr/0001-unified-model-spec-derived-tier.md), derived cheap/expensive tier), mutation-equivalents registry with drift guard, MCP `greedy_token_crystallize` |
| **v0.8.0** | Crystallization L3 safe mode (`draft` → shadow → `promote` / `reject`), portable routes, `calibrate` (baseline provenance), telemetry-calibrated confidence |
| **v0.7.x** | Route quality: `explain_route`, override attribution, `init --profile`, hub metrics; v0.7.2 — mutation testing, secret masking, doc-drift guard |
| **v0.6.x** | Crystallize L2 (`override`, `scripts lint`, shadow routes), `hub serve`, `doctor`, split budget, usage.jsonl v2 `billing` |
| **v0.5.9** | Multi-model registry, profiles, `llm invoke`, escalation, model presets (`config --init --preset`) |
| **v0.5.0** | `cheap_llm` provider (`ollama` \| `openai_compat`); tier id `ollama` unchanged; `OLLAMA_*` compat |
| **v0.4.x** | MCP pipeline, token economy footer, security hardening, Cursor starter kit + setup docs |
