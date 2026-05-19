---
recon: R11 — RAGFlow альтернативы для Strix Halo amd64 без CUDA
date: 2026-05-19
status: completed
source_agent: general-purpose (~25 sources)
related: R3, R4, R5, R10
---

# R11: RAGFlow alternatives на Strix Halo

## TL;DR — REPLACE RAGFlow lean stack

**Primary recommendation:** заменить RAGFlow на lean stack:
- **Dify** (existing) для workflow + KB orchestration
- **Qdrant** как vector store (или Weaviate — оба нативные amd64)
- **llama-server** с bge-m3 (embed) + bge-reranker-v2-m3 (rerank) ROCm/Vulkan build
- **Docling** container для default parsing PDF/Office/HTML/tables
- **MinerU** sidecar с `vlm-http-client` для hard PDFs/scans
- **Open WebUI** как chat frontend с attached KB

**Fallback:** если нужны RAGFlow specific template chunkers (Resume/Manual/
Paper/Laws), оставить `infiniflow/ragflow:v0.25.4` (amd64) с
`DEVICE=cpu` + `MINERU_BACKEND=vlm-http-client` указывает на llama-server.

## RAGFlow upstream status

- **v0.25.4** последний stable (выпущен ~2026-05-14)
- Docker Hub `infiniflow/ragflow:v0.25.4` — **linux/amd64 only**, slim edition
- v0.26 запланирован 2026-05-11 — RESTful API standardization
- **Никакого ROCm support** в коде RAGFlow — только x86 CPU и NVIDIA GPU
- **GPU optional** — все features работают на CPU, just slower (10-50× на больших PDFs)
- `ar2r223/ragflow-spark:v0.24.1-spark` = NVIDIA arm64 fork, **НЕ usable** на Strix Halo amd64

## Что теряем без RAGFlow

- **TitleChunker / TokenChunker** — heuristic-based chunking
- **7 template chunkers** (General/Q&A/Resume/Manual/Table/Paper/Laws) — tuned пайплайны per doc type
- **Multilingual OCR pipelines** (DeepDoc + MinerU) — заменимо Docling + dots.ocr
- **GraphRAG construction at scale** — нет нативного аналога в Dify
- **Agentic eval loop** — RAGFlow имеет built-in chunk/rerank tuning UI
- **Citations & answer grounding UX** — RAGFlow's strong suit

## Альтернативы (полные RAG frameworks)

| Project | License | CUDA dep | Docling-like parsing | RU support | Active 2026 |
|---------|---------|----------|----------------------|------------|-------------|
| **R2R** (SciPhi-AI) | MIT | optional | Unstructured + custom; multimodal | via embed | ✅ updates 2026 |
| **Cognita** (TrueFoundry) | Apache-2.0 | none core | Modular parsers | via embed | ✅ |
| **Verba** (Weaviate) | BSD-3 | none | Basic PDF/text | via embed | ⚠️ slow, no 2026 release |
| **Quivr** | Apache-2.0 | none | Megaparse (their own) | via LLM | ✅ Feb 2026, ~39k stars |
| **Open WebUI KB** | MIT | none | Basic + Apache Tika; hybrid BM25+vector + reranker | via embed | ✅ very active 2026 |
| **Langflow** (DataStax) | MIT | none core | LangChain loaders | via embed | ✅ v1.8 Mar 2026 |
| **Flowise** | Apache-2.0 | none core | LangChain loaders | via embed | ✅ v3.1 Mar 2026 |
| Embedchain / Mem0 | Apache-2.0 | none | Basic | via embed | ❌ maintenance only |

**R2R** — closest functional analog к RAGFlow для "retrieval + parsing +
agent". CPU-friendly. Не CUDA-dependent.

**Open WebUI KB** — самый дешёвый replacement если уже используем Open
WebUI как chat frontend. Hybrid search + reranker + function-calling KB
browse.

## Doc parsing alternatives

| Tool | License | CPU performance | Multilingual | Tables/Layout |
|------|---------|-----------------|--------------|---------------|
| **Docling** (IBM) | Apache-2.0 | 10-100 docs/min on CPU | Yes (Granite-Docling 258M params) | Strong |
| **MinerU** (OpenDataLab) | Apache-2.0 | Slower without GPU | Yes | Very strong |
| **dots.ocr** (rednote-hilab) | Apache-2.0 | Better with GPU | 100+ langs incl. Russian Cyrillic | Layout+OCR VLM |
| **Unstructured** | Apache-2.0 | Variable | Yes | Weak on tables |
| **PyMuPDF + Tesseract** | AGPL/Apache | Fast CPU | Yes via tesseract lang packs | Weak |
| **GROBID** | Apache-2.0 | CPU OK | Limited | Strong for academic PDFs |

**Recommendation** (R7 ещё в полёте, но R11 уже описал):
- **Docling primary** для PDF/Office/HTML — multilingual built-in
- **MinerU sidecar** с `vlm-http-client` к llama-server VLM для hard PDFs/scans
- **dots.ocr** опционально для scans/multilingual

## Lean stack proposal (для Strix Halo)

```yaml
# docker-compose proposal
services:
  qdrant:                # vector store
    image: qdrant/qdrant:v1.x
    # ROCm не нужен, чистый CPU
    ports: ["6333:6333"]

  embed:                 # bge-m3 embeddings
    image: agmind/vulkan:latest  # llama.cpp Vulkan build
    command: |
      llama-server -m /models/bge-m3-Q8_0.gguf
      --embeddings --pooling cls
      --port 8081 -ngl 99 --no-mmap
    ports: ["8081:8081"]
    # devices: /dev/dri, group-add video/render

  rerank:                # bge-reranker-v2-m3
    image: agmind/vulkan:latest
    command: |
      llama-server -m /models/bge-reranker-v2-m3-Q8_0.gguf
      --reranking --port 8082 -ngl 99 --no-mmap

  docling:               # CPU document parser
    image: docling-serve:cpu  # need to verify exact tag, R7 will tell
    # CPU only

  mineru:                # sidecar для hard PDFs
    image: opendatalab/mineru:latest
    environment:
      MINERU_BACKEND: vlm-http-client
      VLM_ENDPOINT: http://llm-vision:8083/v1
    # ROCm для VLM через llm-vision сервис

  llm-vision:            # Vision LLM для MinerU
    image: agmind/vulkan:latest
    command: |
      llama-server -m /models/Qwen2.5-VL-7B-Q4_K_M.gguf
      --port 8083 -ngl 99 --no-mmap

  llm-chat:              # Main LLM (Qwen3-Coder 30B etc.)
    image: agmind/vulkan:latest
    command: |
      llama-server -m /models/Qwen3-Coder-30B-Q4_K_XL.gguf
      --port 8080 -ngl 99 --no-mmap
      --flash-attn 1 --ubatch-size 256 --batch-size 512
      -np 8

  dify:                  # Workflow + KB orchestration
    image: langgenius/dify-api:latest  # need to verify Dify amd64 status
    # No GPU needed

  openwebui:             # Chat frontend with KB
    image: ghcr.io/open-webui/open-webui:latest
    # No GPU needed
```

## Update в AGMIND_MIGRATION_SPEC.md (proposed)

Part 1.3 «Запреты»:
```
| RAGFlow forks | ar2r223/ragflow-spark (NVIDIA arm64); old infiniflow
arm64 builds (dropped 2024-09-29); 0xgkd/ragflow-arm64 (Apple Silicon focus) |
```

Part 1.3 «Разрешено» (новая категория):
```
| RAG stack | RAGFlow infiniflow/ragflow:v0.25.4 (amd64) если нужны
template chunkers; иначе lean stack (Dify+Qdrant+llama-server+Docling+MinerU) |
| Vector store | Qdrant (rec), Weaviate, Milvus (billion-scale only) |
| Doc parsing | Docling primary, MinerU sidecar, dots.ocr для scans |
```

## Sources

- RAGFlow GitHub releases + Docker Hub tags
- RAGFlow README + FAQ (https://ragflow.io/docs)
- NVIDIA forum: RAGFlow v0.24.0 on DGX Spark
- R2R: github.com/SciPhi-AI/R2R
- Cognita: github.com/truefoundry/cognita
- Verba: github.com/weaviate/Verba
- Quivr: github.com/QuivrHQ/quivr
- Open WebUI Knowledge docs
- Langflow + Flowise репo
- Dify Knowledge Pipeline blog
- Docling IBM Granite-Docling announcement
- MinerU + dots.ocr GitHub
- TEI Issue #108
- llama.cpp server README + reranking
- AMD ROCm 7.2 Phoronix
- Embedchain → Mem0 status (DeepWiki)
- Vector DB benchmarks 2026 (CallSphere, Firecrawl)
- Dify vs RAGFlow vs Coze 2026 comparison (Jimmy Song)
