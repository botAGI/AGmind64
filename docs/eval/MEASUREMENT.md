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
| `false abstention` | on ANSWERABLE questions: share wrongly declined. Printed with `abstention`, never without it |

Only `anchor_ndcg@k` gates. The other five print under `exploratory`. The reason is arithmetic,
not taste: nine of eleven answerable cases carry exactly one anchor, and at `|A| = 1` recall and
hit are the same number while nDCG and MRR carry nearly the same information. Gating on six
correlated metrics inflates the family-wise error rate to the point where a green run means
little.

`abstention` is reported **separately and never blended** into a retrieval score. A system can be
excellent at finding passages and terrible at knowing when there is nothing to find; a single
composite would hide precisely that failure.

It is also **never printed without its false-abstention counterpart**. A decline rate alone is
half an ROC point and cannot be falsified in the over-refusing direction: a retriever that
declined *everything* would post a perfect 1.00. The pair is what carries meaning — and on this
installation it is exactly what the pair revealed (see the table below).

An abstention that cannot be measured — no threshold configured, or a retriever that reports no
scores — is reported as **unmeasured**, never as success. Defaulting it to "declined correctly"
awarded a perfect score for returning nothing measurable.

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

## Measuring the deployment itself

`--retriever ragflow` is the only one of the three that scores the system an operator actually
queries. `lexical` and `dense` re-chunk the corpus under the harness's own assumptions, which
measures retrieval quality but not this installation; RAGFlow indexed the same documents with its
own chunker, its own hybrid scoring and its own index. Both readings are useful and they are not
interchangeable, which is why the report's scope block names which chunking produced the number.

It talks to `/api/v1/retrieval` and **refuses** `/api/v1/dify/retrieval`. The Dify-shaped endpoint
drops the chunk id, flattens the three similarity numbers into one and hard-codes the vector
weight, so chunk-level ground truth cannot be expressed through it at all.

The abstention threshold for this retriever is `0.2` because that is RAGFlow's own default for
this endpoint, not a value chosen to make the table look good. The client asks the server for
`0.0` so the harness sees the true ranking and makes the abstention decision itself — at the
threshold the product would have used.

## Current measurement on the reference installation

Corpus: 30 tracked `docs/**/*.md` (this directory is excluded — see below), 643 chunks.
Golden set: 15 cases. k=5. Corpus fingerprint `59bb825e4cce`, all three rows measured against it.
The fingerprint hashes document content only — a commit that leaves the corpus alone leaves it
unchanged, which is what lets the regression gate compare two runs at all.

| retriever | `anchor_ndcg@5` | `anchor_recall@5` | abstention | false abstention |
|---|---|---|---|---|
| lexical (BM25) | 0.000 [0.000–0.000] n=11 | 0.000 [0.000–0.000] n=11 | 0.00 [0.00–0.49] n=4 | 0.00 [0.00–0.26] n=11 |
| dense (bge-m3) | 0.277 [0.056–0.518] n=11 | 0.318 [0.091–0.591] n=11 | 0.75 [0.30–0.95] n=4 | 0.36 [0.15–0.65] n=11 |
| ragflow (deployed) | 0.200 [0.000–0.421] n=11 | 0.273 [0.000–0.545] n=11 | 0.00 [0.00–0.49] n=4 | 0.00 [0.00–0.26] n=11 |

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
* **The abstention pair is the clearest lesson in the table.** Read alone, `abstention 0.75`
  says the system knows when to stay quiet. Read with its counterpart, it says the threshold is
  simply high: the same setting also declines 36% of questions the corpus *can* answer. Neither
  number is wrong; quoting the first without the second would be.
* **The deployed stack declines nothing.** All four unanswerable questions came back with chunks
  above RAGFlow's own confidence floor. It pays nothing for that in false abstention — it never
  wrongly declines an answerable question either — but a RAG that always answers cannot tell an
  operator when it does not know, and that is a property worth knowing before trusting it.
* n=11 is small. These intervals are wide on purpose. Do not quote the point estimate alone.

## What made these numbers possible at all

The corpus could not be indexed when this retriever was first pointed at the live stack: 26 of 30
documents failed, because RAGFlow's chunker emits 545–574 token chunks and `llama-embed` rejected
every input above 512 tokens with HTTP 500. Pooled embedding is non-causal, so llama.cpp needs the
whole sequence in one *physical* batch (`-ub`), which defaults to 512 — while the service
advertised 2048 tokens per slot. The descriptors now pass `-b`/`-ub` derived from
`--ctx-size / --parallel`, and the same defect in `llama-rerank` was fixed with it.

It is worth stating plainly why no gate caught this: every test embedded short strings. The
capability was never exercised at the length real documents produce. Measuring the deployed
system, rather than a reconstruction of it, is what surfaced it.

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

## Why not an off-the-shelf evaluation framework

A fair question, especially since this stack already deploys Arize Phoenix. The answer differs per
candidate, and none of it is "not invented here".

**Phoenix** is a *tracing* product, and it is kept for that. It records what a request did; it
does not hold a versioned golden set, and its per-chunk annotation API needs chunk-level
attributes that Dify does not emit — Dify puts the retrieved chunks in `output.value` as an opaque
JSON string. That was confirmed by probe, not assumed: a structured span annotates with 200, a
Dify-shaped one is rejected with 422, and spans are immutable, so it can only be fixed where the
span is emitted. Phoenix answers "what happened on this request". This answers "is retrieval good,
with what confidence, and has it got worse" — a different question that wants a frozen corpus, a
frozen question set and an interval.

**`arize-phoenix-evals`** is licensed Elastic-2.0. That is the disqualifier for a self-hosted
product that ships to operators. An earlier draft of this section claimed it also pulled in 17
transitive packages; that was checked and it is 9. The wrong reason is removed rather than left
standing, because a correct decision resting on a false premise gets reversed by the next reviewer.

**RAGAS and similar frameworks** score with an LLM judge, which is the layer deliberately not
shipped yet — see the section above. Adopting one would import the judge decision along with the
metrics, and the judge is the part the research argued against for this deployment.

What is actually implemented here is roughly 600 lines of metric and statistics code with no
runtime dependency outside the standard library. The metrics themselves are textbook — the value
is not in inventing them but in binding them to a frozen corpus, an anchor set that cannot quote
its own answer, and a report that has no code path capable of printing a bare point estimate.
