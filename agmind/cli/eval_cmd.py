"""``agmind eval`` — measure retrieval quality against a frozen golden set.

Thin CLI by design (mirrors ``loadtest_cmd``): parsing, output formatting and exit codes only.
Every decision lives in ``agmind/eval/`` so it is unit-testable without a terminal, a corpus or
a live service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer


def _repo_root() -> Path:
    from agmind.core.paths import data_root

    return data_root()


def _golden_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return _repo_root() / "templates" / "eval" / "rag" / "golden.jsonl"


def cmd_corpus(as_json: bool) -> int:
    """Show the frozen evaluation corpus."""
    from agmind.eval.corpus import CorpusError, build_manifest, verify_manifest

    root = _repo_root()
    try:
        manifest = build_manifest(root)
    except CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    drift = verify_manifest(root, manifest)
    if as_json:
        print(json.dumps({**manifest.to_dict(), "drift": drift}, indent=2, ensure_ascii=False))
        return 0

    print(f"corpus_ref   {manifest.corpus_ref}")
    print(f"fingerprint  {manifest.fingerprint()}")
    print(f"documents    {len(manifest.docs)}")
    print(f"bytes        {manifest.total_bytes}")
    if drift:
        print("\ndrift vs working tree:")
        for item in drift:
            print(f"  - {item}")
    return 0


def cmd_cases(as_json: bool, golden: Path | None) -> int:
    """Show the golden set composition."""
    from agmind.eval.cases import EvalCaseError, iter_class_counts, load_cases

    try:
        cases = load_cases(_golden_path(golden))
    except (EvalCaseError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    counts = dict(iter_class_counts(cases))
    if as_json:
        print(json.dumps({"total": len(cases), "by_class": counts}, indent=2, ensure_ascii=False))
        return 0

    print(f"golden set: {len(cases)} cases")
    for name, count in sorted(counts.items()):
        print(f"  {name:<12} {count}")
    languages: dict[str, int] = {}
    for case in cases:
        languages[case.question_lang] = languages.get(case.question_lang, 0) + 1
    print("  languages:  " + ", ".join(f"{k}={v}" for k, v in sorted(languages.items())))
    return 0


def cmd_run(  # noqa: C901 - one branch per retriever; splitting hides the flow
    retriever: str,
    k: int,
    as_json: bool,
    golden: Path | None,
    embed_url: str,
    allow_lan: bool,
    write_baseline: bool = False,
    ragflow_url: str = "",
    ragflow_dataset: tuple[str, ...] = (),
    api_key_file: Path | None = None,
) -> int:
    """Run the evaluation and print a report."""
    from agmind.eval.cases import EvalCase, EvalCaseError, load_cases
    from agmind.eval.report import RunScope, build_report, format_report_json, format_report_text
    from agmind.eval.runner import (
        DEFAULT_ABSTAIN_THRESHOLD,
        RetrievedChunk,
        load_corpus_chunks,
        run_cases,
    )

    root = _repo_root()
    try:
        cases = load_cases(_golden_path(golden))
    except (EvalCaseError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    manifest, chunks = load_corpus_chunks(root)
    extra: dict[str, object] = {}

    if retriever == "lexical":
        from agmind.eval.reference import ReferenceRetriever

        lexical_engine = ReferenceRetriever(chunks)

        def search(case: EvalCase) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(h.chunk_id, h.text, h.score, h.doc_key)
                for h in lexical_engine.search(case.question, top_k=k)
            ]

        extra["scoring"] = "bm25(k1=1.2,b=0.75)"

    elif retriever == "dense":
        from agmind.eval.clients.embeddings import EmbeddingClient, EmbeddingError
        from agmind.eval.clients.resolver import resolve_host
        from agmind.eval.dense import DenseRetriever, EmbeddingCache
        from agmind.eval.endpoints import classify_endpoint

        verdict = classify_endpoint(embed_url, resolve=resolve_host, allow_lan=allow_lan)
        if not verdict.allowed:
            print(
                f"ERROR: refusing embedding endpoint {embed_url}: {verdict.reason}.\n"
                "Zero-egress requires loopback (or --allow-lan for an on-premises host).",
                file=sys.stderr,
            )
            return 2
        if verdict.lan_opt_in:
            print(f"NOTE: {verdict.residual_risk_note()}", file=sys.stderr)

        try:
            client = EmbeddingClient(verdict, model="bge-m3")
            cache = EmbeddingCache(
                Path.home() / ".local" / "share" / "agmind" / "eval" / "cache",
                manifest.fingerprint() + f"-c{len(chunks)}",
                "bge-m3",
            )
            cached = cache.load()
            if cached is None:
                vectors = {
                    c.chunk_id: tuple(v)
                    for c, v in zip(
                        chunks,
                        client.embed_all([c.searchable_text for c in chunks], batch_size=4),
                        strict=True,
                    )
                }
                cache.store({key: list(value) for key, value in vectors.items()})
            else:
                vectors = {key: tuple(value) for key, value in cached.items()}

            dense_engine = DenseRetriever(chunks, dict(vectors))
            query_vectors = dict(
                zip(
                    [c.case_id for c in cases],
                    client.embed_all([c.question for c in cases], batch_size=4),
                    strict=True,
                )
            )
        except EmbeddingError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        def search(case: EvalCase) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(h.chunk_id, h.text, h.score, h.doc_key)
                for h in dense_engine.search(query_vectors[case.case_id], top_k=k)
            ]

        extra["scoring"] = "cosine(bge-m3)"
        extra["embed_endpoint"] = verdict.url

    elif retriever == "ragflow":
        from agmind.eval.clients.ragflow import (
            RagflowError,
            RagflowRetrievalClient,
            filename_to_corpus_key,
            load_api_key,
        )
        from agmind.eval.clients.resolver import resolve_host
        from agmind.eval.endpoints import classify_endpoint

        if not ragflow_dataset:
            print(
                "ERROR: --ragflow-dataset is required: the score is about ONE indexed dataset, "
                "and guessing which would make the number unattributable.",
                file=sys.stderr,
            )
            return 2

        verdict = classify_endpoint(ragflow_url, resolve=resolve_host, allow_lan=allow_lan)
        if not verdict.allowed:
            print(
                f"ERROR: refusing RAGFlow endpoint {ragflow_url}: {verdict.reason}.\n"
                "Zero-egress requires loopback (or --allow-lan for an on-premises host).",
                file=sys.stderr,
            )
            return 2
        if verdict.lan_opt_in:
            print(f"NOTE: {verdict.residual_risk_note()}", file=sys.stderr)

        try:
            ragflow_client = RagflowRetrievalClient(
                verdict,
                load_api_key(key_file=api_key_file),
                dataset_ids=list(ragflow_dataset),
            )
        except RagflowError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        known_docs = frozenset(manifest.doc_keys)

        def search(case: EvalCase) -> list[RetrievedChunk]:
            return [
                RetrievedChunk(
                    hit.chunk_id,
                    hit.content,
                    hit.similarity,
                    filename_to_corpus_key(hit.document_name, known_docs),
                )
                for hit in ragflow_client.retrieve(case.question, top_k=k)
            ]

        # RAGFlow chunks the corpus ITSELF, so `corpus_chunks` in the scope block describes the
        # harness's chunking and not what was actually searched. Say so rather than let the two
        # numbers be read as one.
        extra["scoring"] = "ragflow hybrid (vector_similarity_weight=0.3)"
        extra["ragflow_endpoint"] = verdict.url
        extra["ragflow_datasets"] = len(ragflow_dataset)
        extra["chunking"] = "ragflow-side (corpus_chunks below describes the harness's own)"

    else:  # pragma: no cover - typer restricts the choices
        print(f"ERROR: unknown retriever {retriever!r}", file=sys.stderr)
        return 2

    threshold = DEFAULT_ABSTAIN_THRESHOLD.get(retriever)
    extra["abstain_threshold"] = threshold
    outcome = run_cases(cases, chunks, search, k=k, abstain_threshold=threshold)

    scope = RunScope(
        retriever=retriever,
        corpus_ref=manifest.corpus_ref,
        corpus_fingerprint=manifest.fingerprint(),
        corpus_docs=len(manifest.docs),
        corpus_chunks=len(chunks),
        golden_set_cases=len(cases),
        k=k,
        extra=extra,
    )
    report = build_report(
        scope,
        outcome.scores,
        aggregate_score=outcome.aggregate,
        latency_ms_p50=outcome.latency_p50,
    )
    if write_baseline:
        from datetime import UTC, datetime

        from agmind.core.files import write_text_atomic
        from agmind.eval.gate import baseline_from_report, default_baseline_path

        baseline = baseline_from_report(
            json.loads(format_report_json(report)),
            recorded_at=datetime.now(UTC).isoformat(),
        )
        target = default_baseline_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, json.dumps(baseline.to_dict(), indent=2, ensure_ascii=False))
        print(f"baseline written: {target}", file=sys.stderr)

    print(format_report_json(report) if as_json else format_report_text(report), end="")
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``eval`` command group to ``app``."""
    eval_app = typer.Typer(
        name="eval",
        help="Measure retrieval quality against a frozen, repo-versioned golden set.",
        no_args_is_help=True,
    )
    app.add_typer(eval_app)

    @eval_app.command("corpus")
    def corpus_cmd(
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    ) -> None:
        """Show the frozen evaluation corpus and any drift from the working tree."""
        raise typer.Exit(code=cmd_corpus(as_json))

    @eval_app.command("cases")
    def cases_cmd(
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
        golden: Path | None = typer.Option(None, "--golden", help="Golden-set JSONL override."),
    ) -> None:
        """Show the golden-set composition."""
        raise typer.Exit(code=cmd_cases(as_json, golden))

    @eval_app.command("run")
    def run_cmd(
        retriever: str = typer.Option(
            "lexical",
            "--retriever",
            help="lexical (BM25 floor), dense (stack's bge-m3), or ragflow (deployed RAG).",
        ),
        k: int = typer.Option(5, "--k", help="Retrieval cutoff."),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
        golden: Path | None = typer.Option(None, "--golden", help="Golden-set JSONL override."),
        embed_url: str = typer.Option(
            "http://127.0.0.1:8081/v1/embeddings",
            "--embed-url",
            envvar="AGMIND_EVAL_EMBED_URL",
            help="OpenAI-compatible embeddings endpoint (dense retriever only).",
        ),
        allow_lan: bool = typer.Option(
            False,
            "--allow-lan",
            help="Permit an on-premises LAN endpoint. Loopback needs no opt-in.",
        ),
        write_baseline: bool = typer.Option(
            False,
            "--write-baseline",
            help="Record this run as the regression baseline for future comparisons.",
        ),
        ragflow_url: str = typer.Option(
            "http://127.0.0.1:9380/api/v1/retrieval",
            "--ragflow-url",
            envvar="AGMIND_EVAL_RAGFLOW_URL",
            help="RAGFlow chunk-level retrieval endpoint (ragflow retriever only).",
        ),
        ragflow_dataset: list[str] = typer.Option(  # noqa: B006 - typer builds the list
            [],
            "--ragflow-dataset",
            help="Dataset id to search. Repeatable; required for --retriever ragflow.",
        ),
        api_key_file: Path | None = typer.Option(
            None,
            "--api-key-file",
            help="File holding the RAGFlow API key (mode 0600). Never passed on the command line.",
        ),
    ) -> None:
        """Score the golden set and print a report with intervals and scope."""
        raise typer.Exit(
            code=cmd_run(
                retriever,
                k,
                as_json,
                golden,
                embed_url,
                allow_lan,
                write_baseline,
                ragflow_url,
                tuple(ragflow_dataset),
                api_key_file,
            )
        )


__all__ = ["cmd_cases", "cmd_corpus", "cmd_run", "register"]
