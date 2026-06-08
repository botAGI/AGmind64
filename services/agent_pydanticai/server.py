"""AGmind agent-core (PydanticAI) — a typed agent server behind Authelia.

A minimal, hardened FastAPI service that runs a PydanticAI agent against the stack's
self-hosted OpenAI-compatible llama.cpp endpoint, emits OpenTelemetry traces to the
in-stack Arize Phoenix collector, and (optionally) checkpoints runs durably to Postgres
via DBOS. No vendor telemetry leaves the host — the only egress is the LLM endpoint and
the OTLP collector you configure, both in-cluster.

Config is 100% environment-driven (no config file → boots bare, no install-time
materialization). Every var has a safe default so the container also runs standalone.

    AGENT_LLM_BASE_URL   OpenAI-compatible base (default http://llama-llm:8080/v1)
    AGENT_LLM_MODEL      model id the server accepts (default "llama")
    AGENT_LLM_API_KEY    dummy key — llama.cpp ignores it (default sk-no-key-required)
    AGENT_SYSTEM_PROMPT  agent system prompt (has a sensible default)
    AGENT_OTEL_ENDPOINT  OTLP/HTTP traces endpoint (default http://phoenix:6006/v1/traces)
    AGENT_OTEL_SERVICE   OTel service.name (default agmind-agent-pydanticai)
    AGENT_DURABLE        "1" → wrap the agent in DBOS durable execution
    AGENT_DB_URL         Postgres URL for DBOS checkpoints (required iff AGENT_DURABLE=1)
    AGMIND_OFFLINE       "1" → air-gap: never configure the OTLP exporter
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agmind.agent.pydanticai")

# --- config (env-driven, every var defaulted) ---------------------------------------
LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "http://llama-llm:8080/v1")
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "llama")
LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "sk-no-key-required")
SYSTEM_PROMPT = os.environ.get(
    "AGENT_SYSTEM_PROMPT",
    "You are AGmind, a concise, helpful self-hosted assistant. Answer directly and "
    "factually. If you are unsure, say so rather than inventing details.",
)
OTEL_ENDPOINT = os.environ.get("AGENT_OTEL_ENDPOINT", "http://phoenix:6006/v1/traces")
OTEL_SERVICE = os.environ.get("AGENT_OTEL_SERVICE", "agmind-agent-pydanticai")
DURABLE = os.environ.get("AGENT_DURABLE", "0") == "1"
DB_URL = os.environ.get("AGENT_DB_URL", "")
OFFLINE = os.environ.get("AGMIND_OFFLINE", "0") == "1"

_APP_VERSION = "0.1.0"


def _configure_tracing() -> bool:
    """Wire PydanticAI's native OTel spans to the in-stack Phoenix OTLP/HTTP collector.

    Uses the raw OpenTelemetry SDK (no Logfire dependency → nothing can phone home to a
    SaaS). Skipped entirely in air-gap mode or when no endpoint is set. Best-effort: a
    tracing failure must never take the agent down.
    """
    if OFFLINE or not OTEL_ENDPOINT:
        log.info("tracing disabled (offline=%s endpoint=%r)", OFFLINE, OTEL_ENDPOINT)
        return False
    try:
        from opentelemetry import trace as trace_api
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from pydantic_ai import Agent as _Agent

        provider = TracerProvider(resource=Resource.create({"service.name": OTEL_SERVICE}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT)))
        trace_api.set_tracer_provider(provider)
        _Agent.instrument_all()  # turn on PydanticAI's OTel emission for every agent
        log.info("tracing → %s (service=%s)", OTEL_ENDPOINT, OTEL_SERVICE)
        return True
    except Exception:  # noqa: BLE001 — tracing must never break the service
        log.exception("tracing setup failed — continuing without traces")
        return False


def _build_agent() -> Any:
    """Construct the PydanticAI agent bound to the OpenAI-compatible llama endpoint.

    Returns the plain ``Agent`` or, when ``AGENT_DURABLE=1``, a DBOS-wrapped durable
    agent — both expose the same ``.run()`` surface so the handler is identical.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    model = OpenAIChatModel(
        LLM_MODEL,
        provider=OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY),
    )
    agent = Agent(model, name="agmind-pydanticai", system_prompt=SYSTEM_PROMPT)

    if not DURABLE:
        return agent

    # Optional durable execution: checkpoint each run to Postgres via DBOS (in-process,
    # no external workflow engine). Gated so the default path has zero DB dependency.
    if not DB_URL:
        raise RuntimeError("AGENT_DURABLE=1 requires AGENT_DB_URL (Postgres for DBOS checkpoints)")
    from dbos import DBOS, DBOSConfig
    from pydantic_ai.durable_exec.dbos import DBOSAgent

    DBOS(config=DBOSConfig(name="agmind_pydanticai", system_database_url=DB_URL))
    durable = DBOSAgent(agent)
    DBOS.launch()
    log.info("durable execution ON (DBOS → Postgres)")
    return durable


_TRACING_ON = _configure_tracing()
_AGENT = _build_agent()

app = FastAPI(title="AGmind Agent Core (PydanticAI)", version=_APP_VERSION)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User message for the agent")


class ChatResponse(BaseModel):
    reply: str
    model: str
    durable: bool


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — 200 as soon as the app object is constructed."""
    return {"status": "ok"}


@app.get("/")
def info() -> dict[str, Any]:
    return {
        "service": "agmind-agent-pydanticai",
        "version": _APP_VERSION,
        "framework": "pydantic-ai",
        "model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "tracing": _TRACING_ON,
        "durable": DURABLE,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run the agent on a single message and return its text reply."""
    result = await _AGENT.run(req.message)
    return ChatResponse(reply=result.output, model=LLM_MODEL, durable=DURABLE)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="info")  # noqa: S104 — container-internal


if __name__ == "__main__":
    main()
