# Agent core

> **⚠️ Experimental / opt-in.** This is for embedding agents **in your own code** (PydanticAI's
> typed structured output; Agno's code-first multi-agent + memory). If you just want to **configure
> and run agents through a UI** (prompts, tools, RAG, workflows, chat) — use **Dify** (already in the
> stack, `dify` profile), no code. The agent core only earns its place when you write software that
> embeds an agent as a library/service. Off by default; never part of the core stack.

AGmind ships a reference **agent core** as two opt-in installer profiles. Tick one (or both) in
`agmind setup` like any other profile; each stands up a self-hosted agent server wired to the
stack's local LLM, tracing, and a dedicated database — no external services, vendor telemetry off.

## Two profiles — pick your framework

| Profile | Service | Framework | Best for |
|---------|---------|-----------|----------|
| `agents-pydantic` | `agent-pydanticai` | [PydanticAI](https://ai.pydantic.dev) | typed, library-first, lean; optional DBOS durable execution on Postgres |
| `agents-agno` | `agent-agno` | [Agno AgentOS](https://docs.agno.com) | batteries-included platform; first-class session/memory |

Both share a dedicated **`agent-db`** (Postgres 17 + pgvector) for agent state/memory and DBOS
checkpoints, caged on the internal `data-net`.

## Web morda — Agno Agent UI, self-hosted

The `agents-agno` profile also stands up **`agent-ui`** — **Agno's own official Agent UI**
(`agno-agi/agent-ui`), **self-hosted** on your host (built on-host from pinned source), **not** the
`os.agno.com` hosted control plane. Behind Authelia at `https://agents.<domain>`. Open it, pick the
`agmind-agno` agent, chat — zero config.

The image bakes a same-origin reverse-proxy (`/os-api/* → agent-agno:8800`), so the browser reaches
the AgentOS through the UI's own origin: **no CORS, no per-install domain baked in, no separate
AgentOS exposure.**

Why `agents-agno` only: Agno's Agent UI speaks the **AgentOS** API, which is an Agno construct — it
can't drive the PydanticAI agent. The **PydanticAI** agent instead exposes an **OpenAI-compatible**
surface (`/v1/models`, `/v1/chat/completions`, streaming) at `https://agent.<domain>`, so any
OpenAI-compatible client/UI (or `curl`) can use it. (Both agents expose that OpenAI surface.)

> Needs a DNS record for `agents.<domain>` (wildcard `*.<domain> → server IP` recommended) — like
> every other service, no DNS = browser NXDOMAIN. Note: Agno's *rich* dashboard (configure agents,
> sessions, memory, knowledge, traces) is **only** available on the hosted `os.agno.com`; the
> self-hostable `agent-ui` is chat. A fully self-hosted management dashboard would be a custom build.

## Architecture

```
  Authelia (edge auth)  ──►  agent-pydanticai / agent-agno  ──►  llama-llm   (llm_inference)
                                     │       │                 ──►  phoenix     (llm_tracing, OTLP)
                                     │       └───────────────► agent-db     (Postgres+pgvector)
                                     └─ FastAPI: GET /health · POST /chat {"message": "..."}
```

- **LLM** — consumes `llm_inference`; talks to the in-stack `llama-llm` OpenAI-compatible endpoint
  (`http://llama-llm:8080/v1`). Override per-deploy with `AGENT_LLM_BASE_URL` / `AGENT_LLM_MODEL`
  (e.g. point at a peer node's model).
- **Tracing** — consumes `llm_tracing`; ships OpenTelemetry/OTLP-HTTP spans to the in-stack Phoenix
  (`http://phoenix:6006/v1/traces`). Vendor telemetry is OFF (PydanticAI: no Logfire cloud; Agno:
  `AGNO_TELEMETRY=false`). `AGMIND_OFFLINE=1` disables the exporter entirely (air-gap).
- **Edge** — behind Authelia forward-auth (`chain-internal` for PydanticAI, `chain-llm`+SSE for Agno's
  streaming run routes). Reachable at `https://agent.<domain>` / `https://agno.<domain>`.

## Custom images, no registry (build-on-host)

The agent images are **AGmind-authored** — built on the operator's host from shipped source via a
compose-native `build:` block, not pulled from a registry. This is the first use of the descriptor
`build:` field: such a service is built locally (`docker compose up --build`) and is exempt from the
digest-pin gate. Nothing is published; air-gap installs pre-build or `docker save/load`.

Dockerfiles: `docker/Dockerfile.agent-pydanticai`, `docker/Dockerfile.agent-agno` (slim
`python:3.12-slim`, non-root, ~280 MB). App source: `services/agent_pydanticai/`,
`services/agent_agno/`.

## Use it

```bash
curl -s https://agent.<domain>/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is the capital of France?"}'
# → {"reply":"Paris","model":"...","durable":false}
```

## Env knobs (descriptor `env:` / runtime `.env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `AGENT_LLM_BASE_URL` | `http://llama-llm:8080/v1` | OpenAI-compatible LLM endpoint |
| `AGENT_LLM_MODEL` | `llama` | model id the server accepts |
| `AGENT_OTEL_ENDPOINT` | `http://phoenix:6006/v1/traces` | OTLP/HTTP traces target |
| `AGENT_DURABLE` (pydantic) | `0` | `1` → DBOS durable execution on `agent-db` |
| `AGENT_DB_URL` | `…@agent-db:5432/agents` | Postgres (Agno state / DBOS checkpoints) |
| `AGMIND_OFFLINE` | `0` | `1` → never configure the OTLP exporter (air-gap) |

## Status

Both cores are built, deployed, and **proven live** against the cluster's Qwen3.6 model + Phoenix
(real `/chat` answers, traces shipping, telemetry off). See `.planning/AGENT-CORE/RESULTS.md`.
