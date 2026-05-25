#!/usr/bin/env python3
"""
audit_forbidden.py — поиск платформо-зависимых хардкодов и упоминаний
старого стека (GB10 / CUDA / aarch64) во всём репозитории.

Запуск:
    python scripts/audit_forbidden.py              # отчёт по всему репо
    python scripts/audit_forbidden.py path/to/dir  # по подкаталогу
    python scripts/audit_forbidden.py --fail       # exit 1 при находках (для CI)
    python scripts/audit_forbidden.py --json out.json

Категории = текущие x86-only guardrails и repository policy checks.
Папки legacy/ и agent-local notes исключены из аудита.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "legacy",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".planning",
    "superpowers",
}

# DEF-AUDIT-GITIGNORE: generated artifacts которые .gitignore исключает,
# но audit видит и шумит false-findings. Используем fnmatch glob — name-part
# match (не full path). Пример: any dir named "agmind.egg-info" → skip.
EXCLUDED_DIR_PATTERNS = (
    "*.egg-info",
    "*.dist-info",
    ".tox",
)

# Meta-файлы которые опт-аутятся целиком — они описывают запреты как
# rules / decision context / inventory. Это per-file опт-аут поверх
# per-line `# audit: allow`. Список конкретный,
# не паттерн — чтобы не получить false negatives через wildcard.
EXCLUDED_PATHS = {
    # Meta docs that intentionally discuss platform support boundaries.
    "README.md",
    "CLAUDE.md",
    "docs/HARDWARE.md",
    "docs/BENCHMARKS.md",
    "docs/QUICKSTART.md",
    "docs/TROUBLESHOOTING.md",
    # Сам audit_forbidden.py: module docstring упоминает что ищем.
    # Self-reference; RULES strings уже опт-аутены через `# audit: allow`.
    "scripts/audit_forbidden.py",
    # test_audit_script.py намеренно содержит запрещённые паттерны как
    # fixtures для positive-detection тестов. По семантике = полный
    # `# audit: allow` на файл.
    "tests/test_audit_script.py",
}

# Каталоги опт-аутенные как «рабочие notes» или historical decisions.
EXCLUDED_PREFIXES = (
    "docs/adr/",  # ADR описывают историю решений
    ".claude/",  # IDE/agent config
)

TEXT_SUFFIXES = {
    ".py",
    ".pyx",
    ".pyi",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".rst",
    ".txt",
    ".sh",
    ".bash",
    ".zsh",
    ".dockerfile",
    ".cmake",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".cu",
    ".cuh",
    ".rs",
    ".go",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    "",
}

BARE_NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt", ".gitignore", ".env"}

# Каждый regex ниже описывает запрещённый паттерн. Сами по себе строки
# regex'ов содержат запрещённые слова — это rule-self-reference, которое
# нужно опт-аутить через `# audit: allow`. Эта же логика применяется к
# legacy migration archive, который перечисляет запреты как rules.
RULES: list[tuple[str, str, re.Pattern]] = [
    (
        "cuda_runtime",
        "Прямые упоминания CUDA runtime / API",
        re.compile(
            r"\b(cudaMalloc|cudaMemcpy|cudaFree|cudaStream|cublas\w*|"  # audit: allow rule-self-reference
            r"cudnn\w*|nvinfer\d*|nvrtc|nccl|cuFFT|curand)\b"  # audit: allow rule-self-reference
        ),
    ),
    (
        "cuda_python",
        "CUDA в Python-импортах и атрибутах",
        # Method-call patterns (typed-identifier.cuda() / .to('cuda')) и
        # `torch.cuda.foo` идут БЕЗ lookbehind — слева от точки всегда стоит
        # идентификатор (буква), который старый lookbehind ошибочно блокировал.
        # Import / `device=` строки сохраняют lookbehind чтобы не ловить
        # `from mypycuda` etc.
        re.compile(
            r"\.cuda\(\)"  # audit: allow rule-self-reference
            r"|\.to\(['\"]cuda(?::\d+)?['\"]\)"  # audit: allow rule-self-reference
            r"|torch\.cuda\."  # audit: allow rule-self-reference
            r"|(?<![A-Za-z_])("
            r"import\s+(?:pycuda|cupy|tensorrt|onnxruntime_gpu)"  # audit: allow rule-self-reference
            r"|from\s+(?:pycuda|cupy|tensorrt)\b"  # audit: allow rule-self-reference
            r"|device\s*=\s*['\"]cuda(?::\d+)?['\"]"  # audit: allow rule-self-reference
            r")"
        ),
    ),
    (
        "cuda_paths",
        "Хардкод путей CUDA / NVIDIA",
        re.compile(
            r"(/usr/local/cuda[\w\-./]*|/opt/nvidia[\w\-./]*|"  # audit: allow rule-self-reference
            r"nvcr\.io/[\w\-./:]+|nvidia/cuda:[\w\-.]+)"  # audit: allow rule-self-reference
        ),
    ),
    (
        "arm_aarch64",
        "Упоминания ARM/aarch64 архитектуры",  # audit: allow rule-self-reference
        re.compile(
            r"\b(aarch64|arm64|armv[78]|--platform=linux/arm64|"  # audit: allow rule-self-reference
            r"platform_machine\s*==\s*['\"]aarch64['\"])\b",  # audit: allow rule-self-reference
            re.IGNORECASE,
        ),
    ),
    (
        "nvidia_hw",
        "Имена NVIDIA-железа и продуктов",
        re.compile(
            r"\b(GB10|GB200|Grace|Blackwell|Hopper|H100|H200|A100|"  # audit: allow rule-self-reference
            r"Jetson|Orin|DGX|Xavier|Tegra|TensorRT[\-_]LLM|Triton[\-_]Inference)\b"  # audit: allow rule-self-reference
        ),
    ),
    (
        "cuda_arch_flags",
        "CUDA build flags (CMake/setup)",
        re.compile(
            r"(CUDA_ARCHITECTURES|CMAKE_CUDA_\w+|nvcc\b|--gpu-architecture|"  # audit: allow rule-self-reference
            r"compute_\d{2}|sm_\d{2})"  # audit: allow rule-self-reference
        ),
    ),
    (
        "native_march",
        "-march=native в shippable артефактах",  # audit: allow rule-self-reference
        re.compile(r"-march=native"),  # audit: allow rule-self-reference
    ),
]


@dataclass
class Finding:
    rule: str
    description: str
    file: str
    line: int
    snippet: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0

    @property
    def by_rule(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.rule, []).append(f)
        return out


def is_text_file(p: Path) -> bool:
    if p.name in BARE_NAMES:
        return True
    return p.suffix.lower() in TEXT_SUFFIXES


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _excluded_part(part: str) -> bool:
    if part in EXCLUDED_DIRS:
        return True
    return any(fnmatch.fnmatch(part, pat) for pat in EXCLUDED_DIR_PATTERNS)


def iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(_excluded_part(part) for part in p.parts):
            continue
        if not is_text_file(p):
            continue
        # EXCLUDED_PATHS / EXCLUDED_PREFIXES — это конкретные репо-файлы
        # ("README.md", "docs/HARDWARE.md", ...). Резолвим относительно
        # REPO_ROOT, а не scan_root — иначе при сканировании произвольной
        # subdir любой файл `README.md` внутри неё ошибочно opt-out'нется.
        try:
            rel_repo = p.resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            rel_repo = ""  # вне репо (например, tmp_path в тестах) — opt-out не применяем
        if rel_repo and rel_repo in EXCLUDED_PATHS:
            continue
        if rel_repo and any(rel_repo.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        yield p


def scan_file(p: Path, report: Report) -> None:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    report.scanned_files += 1
    for i, line in enumerate(text.splitlines(), start=1):
        if "# audit: allow" in line or "// audit: allow" in line:
            continue
        for rule_id, desc, pat in RULES:
            if pat.search(line):
                report.findings.append(
                    Finding(
                        rule=rule_id,
                        description=desc,
                        file=str(p),
                        line=i,
                        snippet=line.strip()[:200],
                    )
                )


def print_report(report: Report) -> None:
    grouped = report.by_rule
    print("\n=== AGmind audit ===")
    print(f"Файлов проверено: {report.scanned_files}")
    print(f"Находок:          {len(report.findings)}")
    if not report.findings:
        print("✅ Запрещённых паттернов не найдено.")
        return
    print()
    for rule_id, items in sorted(grouped.items()):
        desc = items[0].description
        print(f"[{rule_id}] {desc} — {len(items)} находок")
        for f in items[:50]:
            print(f"  {f.file}:{f.line}: {f.snippet}")
        if len(items) > 50:
            print(f"  ... и ещё {len(items) - 50}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=".", type=Path)
    ap.add_argument("--fail", action="store_true", help="exit 1 если есть находки (для CI)")
    ap.add_argument("--json", type=Path, default=None, help="дополнительно записать JSON-отчёт")
    args = ap.parse_args()

    root = args.path.resolve()
    if not root.exists():
        print(f"Путь не существует: {root}", file=sys.stderr)
        return 2

    report = Report()
    for p in iter_files(root):
        scan_file(p, report)
    print_report(report)

    if args.json:
        payload = {
            "scanned_files": report.scanned_files,
            "findings": [asdict(f) for f in report.findings],
        }
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nJSON отчёт: {args.json}")

    if args.fail and report.findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
