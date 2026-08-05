# RAG retrieval measurement — what the numbers mean

`agmind eval` answers one question: **is retrieval on this installation working, and has it got
worse?** It is deliberately narrower than "is our RAG good", because that question has no
measurable form.

Everything below runs on-box. No cloud service is contacted, and the endpoints the harness may
talk to are checked against a repo-owned allow-list before a socket is opened.

## The two layers

**Layer 1 — deterministic, no LLM.** A frozen set of questions is replayed against a retriever;
a retrieved chunk counts as relevant when it contains a verbatim *anchor* from the golden set.
This is the layer that gates. It costs nothing, reproduces exactly, and — critically — does not
drift when a judge model is swapped or requantised.

**Layer 2 — judged (not yet shipped).** Grounding and answer quality need a model. The design
work is done and deliberately deferred; see "Why there is no LLM judge yet" below.

## What is measured

| metric | meaning |
|---|---|
| `anchor_ndcg@k` | **headline, gated.** Graded gain (a chunk's gain = how many distinct anchors it covers), discounted by rank, normalised against the exactly-computed ideal ranking over the whole frozen corpus. Measures *finding* and *ordering* together. |
| `anchor_recall@k` | share of the case's anchors covered by the top-k |
| `anchor_precision@k` | share of returned chunks that carry an anchor, denominator `min(k, returned)` |
| `anchor_hit@k` | did anything relevant appear at all |
| `anchor_mrr@k` | reciprocal rank of the first relevant chunk |
| `abstention` | on deliberately unanswerable questions: share where nothing cleared the retriever's confidence threshold |

Only `anchor_ndcg@k` gates. The other five print under `exploratory`. The reason is arithmetic,
not taste: nine of eleven answerable cases carry exactly one anchor, and at `|A| = 1` recall and
hit are the same number while nDCG and MRR carry nearly the same information. Gating on six
correlated metrics inflates the family-wise error rate to the point where a green run means
little.

`abstention` is reported **separately and never blended** into a retrieval score. A system can be
excellent at finding passages and terrible at knowing when there is nothing to find; a single
composite would hide precisely that failure.

## Two deliberate divergences from RAGFlow's own metrics

RAGFlow ships `precision/recall/f1/hit_rate/mrr` in `evaluation_service.py`. Ours differ in two
declared ways, so the numbers must never be compared across the two:

1. **Explicit `@k`.** RAGFlow's precision divides by the entire returned set, so its value moves
   when `top_k` changes even though ranking quality did not.
2. **Ranked dedup instead of `set()`.** Duplicates are removed preserving first occurrence and
   *before* truncation, so a retriever that repeats a chunk cannot consume two of the k slots.

## Anchors: what they are and what they are not

An anchor is a short verbatim string from the corpus (`mem_info_gtt_total`, `AGMIND_OFFLINE=1`).
Relevance is "does this chunk contain the anchor", after NFKC + casefold + whitespace collapse.

Chosen over chunk-id ground truth because ids are destroyed by reindexing, re-chunking, a backend
swap or a wipe — binding human judgement to identifiers that die on the first reindex is
self-defeating. Anchors survive all of it.

**Known limitation, stated rather than buried.** Answer-string containment is a long-standing QA
proxy with failure modes both ways: a chunk can contain the string incidentally, or express the
same fact in other words. Mitigations actually in force:

* anchors are distinctive identifiers, not common words;
* a CI gate proves every anchor exists verbatim in the document it names, so the set cannot
  accumulate hallucinated ground truth;
* a second gate proves no question contains its own anchor, so a case cannot degenerate into
  string matching;
* the false-negative direction is accepted on purpose — this layer asks "did the retriever
  surface the passage an operator would need", which is narrower and more honest than "is the
  answer semantically present somewhere".

## Reading a report honestly

* **Every number carries `n` and an interval.** There is no code path that prints a bare point
  estimate. At n≈11 the intervals are wide, and that is the finding, not a defect of the report.
* **The `scope` block is the number's boundary.** A score is about one corpus fingerprint, one
  retriever configuration, one golden set, one `k`. "AGmind's RAG quality" is not expressible by
  this tool by construction.
* **Overlapping intervals do not mean "no difference"**, and differing means do not mean "there
  is a difference". For A/B, compare paired per-case deltas, not two independent intervals.
* **Any errored case invalidates the run.** The averages become a biased subsample of whatever
  happened to succeed, and the report says so.

## The lexical floor

`--retriever lexical` runs Okapi BM25 over the same corpus and questions. It is not a fallback
retriever; it is the **floor**. A dense score is only interpretable next to it, because a neural
retriever failing to beat a bag-of-words baseline is a documented and common outcome, not an
exotic one. Reporting a dense number alone hides that possibility.

## Current measurement on the reference installation

Corpus: 30 tracked `docs/**/*.md` (this directory is excluded — see below), 660 chunks.
Golden set: 15 cases. k=5.

| retriever | `anchor_ndcg@5` | `anchor_recall@5` | abstention |
|---|---|---|---|
| lexical (BM25) | 0.000 [0.000–0.000] n=11 | 0.000 [0.000–0.000] n=11 | 0.00 [0.00–0.49] n=4 |
| dense (bge-m3) | 0.277 [0.056–0.518] n=11 | 0.318 [0.091–0.591] n=11 | 0.75 [0.30–0.95] n=4 |

> **`docs/eval/` is excluded from the corpus on purpose.** This very file quotes golden-set
> anchors verbatim as examples, so leaving it in would score the evaluation's own documentation
> as maximally-relevant ground truth. Self-referential ground truth is the quietest way for a
> benchmark to end up measuring itself.
>
> **An anchor only counts in the document its case names.** Four cases use an anchor that also
> appears elsewhere (`mem_info_gtt_total` is in four files); without document scoping a retriever
> earned full credit for surfacing a passage from a document the case never claimed answers the
> question, which silently gutted the distractor class.

What this does and does not say:

* The set **discriminates** — a 0.36 recall spread between a lexical and a semantic retriever,
  against an acceptance criterion of ≥0.15. An oracle pass separately confirms all 13 anchors are
  reachable in some chunk, so the lexical zero is a real result rather than an unwinnable set.
* The questions were written so as not to reuse the anchors' wording. That is why BM25 scores
  zero, and it is deliberate: a question that quotes its own answer measures string matching.
* Dense retrieval **fails all three distractor cases** — the class built so that the obvious
  lexical match sits in the wrong document. That is the most actionable line in the table.
* n=11 is small. These intervals are wide on purpose. Do not quote the point estimate alone.

## Why there is no LLM judge yet

The original plan was the "RAG triad" scored by a local general LLM. Research into current
practice killed both halves for this deployment:

* The strongest local general model available here serves 4096 tokens per slot, which a triad
  prompt carrying a retrieved chunk set does not fit into.
* Published measurements put general-purpose LLM judges near or below the majority-class baseline
  on hallucination detection for this exact retrieval stack and language mix, while small
  purpose-built fact-verification models match far larger ones.
* An uncalibrated judge in a gate produces a number that moves when the model is requantised.

So layer 1 gates, and the judged layer waits for a purpose-built verifier rather than shipping a
number nobody should trust. This is recorded so the decision is revisited on evidence, not
forgotten.
