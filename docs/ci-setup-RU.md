# CI / headless-настройка

greedy-token в Jenkins / GitLab / self-hosted runner **без Cursor MCP** ([#18](https://github.com/svasenkov/greedy-token/issues/18)).

**English:** [ci-setup.md](ci-setup.md)

## Контракт окружения

| Переменная | Обязательна | Описание |
|----------|:--------:|-------------|
| `GREEDY_TOKEN_ROOT` | да | Корень consumer-репозитория (automator или монорепо) |
| `CHEAP_LLM_URL` / `OLLAMA_URL` | да* | Базовый URL Ollama или openai_compat |
| `GREEDY_TOKEN_LOG` | нет | Путь телеметрии (по умолчанию `~/.greedy-token/usage.jsonl`) |
| `GREEDY_EXPENSIVE_LLM` | нет | `1` — включить платные expensive-модели |

\* Или задать через `.greedy-token.yaml` / `~/.greedy-token/config.yaml`.

## Установка

```bash
# пиньте версию, которую проверили (текущая: 0.10.0)
python -m pip install "greedy-token[mcp]==0.10.0"
# MCP в CI опционален — только CLI:
python -m pip install greedy-token==0.10.0
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

## Jenkins-сниппет

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

## Классы задач

| Класс | Executor |
|-------|----------|
| find / grep | `greedy-token run` → rg (0 LLM) |
| синхронизация / resolve скриптов | python-tier |
| classify / generate | `llm invoke --profile tms-*` |
| архитектура / wiring | Cursor-агент (вне scope CI) |

TMS-специфичные профили — см. [tms-automator-contract.md](tms-automator-contract.md).
