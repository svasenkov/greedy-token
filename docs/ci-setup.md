# CI / headless setup

greedy-token in Jenkins / GitLab / self-hosted runner **without Cursor MCP** ([#18](https://github.com/svasenkov/greedy-token/issues/18)).

## Env contract

| Variable | Required | Description |
|----------|:--------:|-------------|
| `GREEDY_TOKEN_ROOT` | yes | Consumer repo root (automator or monorepo) |
| `CHEAP_LLM_URL` / `OLLAMA_URL` | yes* | Ollama or openai_compat base URL |
| `GREEDY_TOKEN_LOG` | no | Telemetry path (default `~/.greedy-token/usage.jsonl`) |
| `GREEDY_EXPENSIVE_LLM` | no | `1` to opt in to paid expensive models |
| `YANDEX_GPT_API_KEY` | no | YandexGPT when expensive enabled |
| `YANDEX_FOLDER_ID` | no | Yandex Cloud folder for native API |

\* Or configure via `.greedy-token.yaml` / `~/.greedy-token/config.yaml`.

## Install

```bash
python -m pip install "greedy-token[mcp]==0.5.9"
# MCP optional in CI — CLI only:
python -m pip install greedy-token==0.5.9
```

## Smoke (runner)

```bash
export GREEDY_TOKEN_ROOT=/path/to/repo
export OLLAMA_URL=http://ollama.internal:11434

greedy-token llm list
greedy-token route "sync testops layer"
greedy-token pipeline "pipeline: tms-classify path=fixtures/case.json" --execute
greedy-token report --since 24h
```

## Jenkins snippet

```groovy
stage('LLM classify') {
  steps {
    sh '''
      export GREEDY_TOKEN_ROOT="${WORKSPACE}"
      export OLLAMA_URL=http://10.0.0.5:11434
      greedy-token llm invoke \
        --profile tms-classify \
        --system-file prompts/classify.txt \
        --user-file input/case.json \
        --tags project=tms-automator,step=classify \
        --json > build/classify.json
    '''
  }
}
```

## GitHub Actions (self-hosted)

```yaml
- name: greedy-token pipeline
  env:
    GREEDY_TOKEN_ROOT: ${{ github.workspace }}
    OLLAMA_URL: http://127.0.0.1:11434
  run: |
    greedy-token pipeline "pipeline: meta-audit configurator-boolean" --execute
```

## Task classes

| Class | Executor |
|-------|----------|
| find / grep | `greedy-token run` → rg (0 LLM) |
| sync / resolve scripts | python tier |
| classify / generate | `llm invoke --profile tms-*` |
| architecture / wiring | Cursor agent (out of CI scope) |

See [tms-automator-contract.md](tms-automator-contract.md) for TMS-specific profiles.
