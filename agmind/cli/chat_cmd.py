"""`agmind chat` — interactive REPL поверх llama-server.

Uses LlamaServerClient напрямую (быстрее чем backend.load_llm).
"""

from __future__ import annotations

import os
import sys

from agmind.core.logging import logger

log = logger(__name__)


def cmd_chat(
    *,
    server_url: str | None = None,
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1024,
    stream: bool = True,
) -> int:
    """Interactive chat REPL.

    Args:
        server_url: override AGMIND_LLAMA_SERVER_URL (default http://localhost:8080).
        system: optional system prompt.
        temperature: sampling temperature.
        max_tokens: per-turn token limit.
        stream: stream output as tokens arrive (recommended).
    """
    from agmind.compute.clients import LlamaServerClient, SamplingParams

    url = server_url or os.environ.get("AGMIND_LLAMA_SERVER_URL", "http://localhost:8080")
    client = LlamaServerClient(url)

    if not client.is_alive():
        print(
            f"ERROR: llama-server not reachable at {url}.\n"
            "Start it: docker compose up -d llama-llm",
            file=sys.stderr,
        )
        return 2

    props = client.props()
    print(f"agmind chat — server: {url}")
    if props:
        model = props.get("default_generation_settings", {}).get("model") or props.get("model")
        if model:
            print(f"model:  {model}")
    print("(type 'exit' or Ctrl-D to quit; '/clear' to reset history)")
    print()

    history: list[dict[str, str]] = []
    if system:
        history.append({"role": "system", "content": system})

    sampling = SamplingParams(temperature=temperature)

    while True:
        try:
            user = input("user> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user:
            continue
        if user in ("exit", "quit", "/exit"):
            return 0
        if user == "/clear":
            history.clear()
            if system:
                history.append({"role": "system", "content": system})
            print("(history cleared)")
            continue

        history.append({"role": "user", "content": user})

        print("asst> ", end="", flush=True)
        reply_parts: list[str] = []
        try:
            if stream:
                for chunk in client.chat_stream(
                    history,
                    max_tokens=max_tokens,
                    sampling=sampling,
                ):
                    print(chunk, end="", flush=True)
                    reply_parts.append(chunk)
                print()
            else:
                full = client.chat(history, max_tokens=max_tokens, sampling=sampling)
                print(full)
                reply_parts.append(full)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[error: {exc}]")
            history.pop()  # remove failed user message
            continue

        history.append({"role": "assistant", "content": "".join(reply_parts)})
