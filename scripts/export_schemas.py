"""Export Pydantic schemas → JSON Schema artifacts (Phase H'.A).

Запуск:
    python -m scripts.export_schemas
    # → templates/schemas/service.json

В pre-commit hook (Phase H'.B) запускается автоматически когда меняется
`agmind/schemas/`. Generated файлы коммитятся в git для VSCode convenience —
не считаются "сгенерированными в build time".

См. ADR-0005.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agmind.schemas import ServiceDescriptor

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "templates" / "schemas"


def export_service_descriptor_schema() -> Path:
    """Write Pydantic JSON Schema → templates/schemas/service.json."""
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    out = SCHEMAS_DIR / "service.json"
    schema = ServiceDescriptor.model_json_schema()
    # Добавляем $id для VSCode YAML extension recognition
    schema["$id"] = "https://github.com/botAGI/AGmindx86/schemas/service.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "AGmind Service Descriptor"
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main() -> int:
    written: list[Path] = [export_service_descriptor_schema()]
    for p in written:
        rel = p.relative_to(REPO_ROOT)
        print(f"✓ wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
