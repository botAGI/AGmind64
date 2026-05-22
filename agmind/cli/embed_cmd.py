"""`agmind embed` — quick embedding CLI."""

from __future__ import annotations

import json
import os
import sys


def cmd_embed(
    texts: list[str],
    *,
    server_url: str | None = None,
    model: str = "",
    as_json: bool = True,
) -> int:
    """Embed list of texts. Read from stdin if no args."""
    from agmind.compute.clients import LlamaServerClient

    if not texts:
        # Read from stdin
        for line in sys.stdin:
            line = line.rstrip("\n")
            if line:
                texts.append(line)
    if not texts:
        print("ERROR: no input texts (args or stdin)", file=sys.stderr)
        return 1

    url = server_url or os.environ.get("AGMIND_LLAMA_SERVER_URL", "http://localhost:8081")
    client = LlamaServerClient(url)
    try:
        vectors = client.embed(texts, model=model)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: embed failed: {exc}", file=sys.stderr)
        return 2

    if as_json:
        out = [
            {"text": t, "embedding": v, "dim": len(v)} for t, v in zip(texts, vectors, strict=False)
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for t, v in zip(texts, vectors, strict=False):
            print(f"# {t[:60]}...")
            print(" ".join(f"{x:.4f}" for x in v[:8]) + "  ...")
    return 0


def cmd_rerank(
    query: str,
    documents: list[str],
    *,
    server_url: str | None = None,
    top_n: int | None = None,
) -> int:
    """Rerank documents by relevance to query."""
    from agmind.compute.clients import LlamaServerClient

    url = server_url or os.environ.get("AGMIND_LLAMA_SERVER_URL", "http://localhost:8082")
    client = LlamaServerClient(url)
    try:
        scores = client.rerank(query, documents, top_n=top_n)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: rerank failed: {exc}", file=sys.stderr)
        return 2

    # Pretty: sort desc, print rank/score/doc
    ranked = sorted(
        zip(documents, scores, strict=False),
        key=lambda t: t[1],
        reverse=True,
    )
    for rank, (doc, score) in enumerate(ranked, start=1):
        print(f"{rank:3d}  {score:6.3f}  {doc[:80]}")
    return 0
