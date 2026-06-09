"""AGmind agent-core (Agno) — an AgentOS agent platform behind Authelia.

A FastAPI service built on Agno's AgentOS runtime, running an agent against the stack's
self-hosted OpenAI-compatible llama.cpp endpoint, with session/memory state persisted to
the dedicated agent-db (Postgres + pgvector), traces shipped to the in-stack Arize Phoenix
collector via OpenInference, and Agno's anonymous telemetry fully DISABLED (no phone-home).

Config is 100% environment-driven (boots bare, no install-time materialization).

    AGENT_LLM_BASE_URL   OpenAI-compatible base (default http://llama-llm:8080/v1)
    AGENT_LLM_MODEL      model id the server accepts (default "llama")
    AGENT_LLM_API_KEY    dummy key — llama.cpp ignores it (default sk-no-key-required)
    AGENT_SYSTEM_PROMPT  agent instructions (has a sensible default)
    AGENT_DB_URL         Postgres URL for Agno session/memory (default → agent-db)
    AGENT_OTEL_ENDPOINT  OTLP/HTTP traces endpoint (default http://phoenix:6006/v1/traces)
    AGENT_OTEL_SERVICE   OTel service.name (default agmind-agent-agno)
    AGNO_TELEMETRY       forced "false" below regardless of inbound value (air-gap)
    AGMIND_OFFLINE       "1" → air-gap: never configure the OTLP exporter
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

# Kill Agno's anonymous telemetry before importing agno (belt: env + per-instance flag).
os.environ["AGNO_TELEMETRY"] = "false"

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agmind.agent.agno")

LLM_BASE_URL = os.environ.get("AGENT_LLM_BASE_URL", "http://llama-llm:8080/v1")
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "llama")
LLM_API_KEY = os.environ.get("AGENT_LLM_API_KEY", "sk-no-key-required")
SYSTEM_PROMPT = os.environ.get(
    "AGENT_SYSTEM_PROMPT",
    "You are AGmind, a concise, helpful self-hosted assistant. Answer directly and "
    "factually. If you are unsure, say so rather than inventing details.",
)
DB_URL = os.environ.get("AGENT_DB_URL", "postgresql+psycopg://agent:agent@agent-db:5432/agents")
OTEL_ENDPOINT = os.environ.get("AGENT_OTEL_ENDPOINT", "http://phoenix:6006/v1/traces")
OTEL_SERVICE = os.environ.get("AGENT_OTEL_SERVICE", "agmind-agent-agno")
OFFLINE = os.environ.get("AGMIND_OFFLINE", "0") == "1"
# Model id this agent advertises on the OpenAI-compatible surface (so a chat UI lists it).
MODEL_ID = os.environ.get("AGENT_MODEL_ID", "agmind-agno")

_APP_VERSION = "0.1.0"


def _configure_tracing() -> bool:
    """Ship Agno traces to the in-stack Phoenix OTLP/HTTP collector via OpenInference.

    Instrumentor-only route (no heavy arize-phoenix client in the image). Best-effort:
    a tracing failure must never take the agent down. Skipped in air-gap mode.
    """
    if OFFLINE or not OTEL_ENDPOINT:
        log.info("tracing disabled (offline=%s endpoint=%r)", OFFLINE, OTEL_ENDPOINT)
        return False
    try:
        from openinference.instrumentation.agno import AgnoInstrumentor
        from opentelemetry import trace as trace_api
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": OTEL_SERVICE}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT)))
        trace_api.set_tracer_provider(provider)
        AgnoInstrumentor().instrument()
        log.info("tracing → %s (service=%s)", OTEL_ENDPOINT, OTEL_SERVICE)
        return True
    except Exception:  # noqa: BLE001 — tracing must never break the service
        log.exception("tracing setup failed — continuing without traces")
        return False


def _run_text(run: Any) -> str:
    """Extract the assistant text from an Agno run result across 2.x shapes."""
    for attr in ("content", "output", "text"):
        val = getattr(run, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(run)


_TRACING_ON = _configure_tracing()

# Build the agent + AgentOS app.
from agno.agent import Agent  # noqa: E402
from agno.models.openai.like import OpenAILike  # noqa: E402
from agno.os import AgentOS  # noqa: E402

_db: Any = None
try:
    from agno.db.postgres import PostgresDb

    _db = PostgresDb(db_url=DB_URL)
except Exception:  # noqa: BLE001 — degrade to stateless if the DB lib/conn is unavailable
    log.exception("PostgresDb unavailable — running agent stateless (no session/memory)")

_agent = Agent(
    name="agmind-agno",
    model=OpenAILike(id=LLM_MODEL, api_key=LLM_API_KEY, base_url=LLM_BASE_URL),
    db=_db,
    instructions=SYSTEM_PROMPT,
    telemetry=False,
)
_agent_os = AgentOS(agents=[_agent], telemetry=False)
app: FastAPI = _agent_os.get_app()  # AgentOS IS a FastAPI app (built-in /health + run routes)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="User message for the agent")


class ChatResponse(BaseModel):
    reply: str
    model: str


@app.get("/agmind/info")
def info() -> dict[str, Any]:
    return {
        "service": "agmind-agent-agno",
        "version": _APP_VERSION,
        "framework": "agno",
        "model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "tracing": _TRACING_ON,
        "db": bool(_db),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Fixed-contract chat route (AgentOS also mounts its own run routes).

    Failures (LLM endpoint unreachable, DB error, upstream timeout) surface as a clean 503,
    never a raw 500 with a stack trace.
    """
    try:
        run = await _agent.arun(req.message)
    except Exception as exc:  # noqa: BLE001 — clean 503, no raw 500/stack leak to the client
        log.warning("agent run failed: %s", exc)
        raise HTTPException(status_code=503, detail="agent upstream unavailable") from exc
    return ChatResponse(reply=_run_text(run), model=LLM_MODEL)


# --- OpenAI-compatible surface ------------------------------------------------------
# Lets any OpenAI-compatible chat UI/client (e.g. self-hosted Open WebUI) use this agent as a
# selectable "model" — no vendor UI needed. Single-turn per call; streaming sends one SSE chunk.


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    """OpenAI-style model list — advertises this agent as one selectable model."""
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "agmind"}],
    }


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


async def _run_reply(prompt: str) -> str:
    try:
        run = await _agent.arun(prompt)
    except Exception as exc:  # noqa: BLE001 — clean 503, no raw 500/stack leak
        log.warning("agent run failed: %s", exc)
        raise HTTPException(status_code=503, detail="agent upstream unavailable") from exc
    return _run_text(run)


@app.post("/v1/chat/completions")
async def chat_completions(req: dict[str, Any]) -> Any:
    """OpenAI chat-completions shim — run the agent on the latest user message."""
    prompt = _last_user_message(req.get("messages") or [])
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="no user message")
    reply = await _run_reply(prompt)
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex}"

    if req.get("stream"):

        async def _sse() -> AsyncIterator[str]:
            base = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
            }
            yield (
                "data: "
                + json.dumps(
                    {
                        **base,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": reply},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
                + "\n\n"
            )
            yield (
                "data: "
                + json.dumps(
                    {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(_sse(), media_type="text/event-stream")

    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="info")  # noqa: S104 — container-internal


if __name__ == "__main__":
    main()
