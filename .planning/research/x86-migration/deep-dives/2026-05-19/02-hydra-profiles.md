# Hydra-core для AGmindx86: deep research

## 0. TL;DR
**Hydra-core 1.3.2** (последний stable, февраль 2023; в развитии 1.4 для Py 3.10-3.14) — это инструмент **именно того класса**, который описан в задаче: composition через config groups + defaults list + multirun + CLI override. Но для AGmindx86 размером ~5-10k LOC и одной точкой `_select_auto()` это **избыточно**. Прагматичный путь — **OmegaConf standalone + pydantic v2 + typer**, без `@hydra.main`. Ниже — почему и как.

---

## 1. Hydra-core 1.3.x — реальные возможности

- **Defaults list** в `config.yaml`: список вида `- task: rag`, `- model_tier: l`, `- _self_`. Hydra мерджит файлы из `task/rag.yaml`, `model_tier/l.yaml` поверх друг друга в порядке списка. Позиция `_self_` определяет, когда родитель перекрывает группы. См. [Defaults List docs](https://github.com/facebookresearch/hydra/blob/main/website/docs/advanced/defaults_list.md).
- **Config groups** = подпапки. Один файл из группы выбирается за раз; `--multirun task=rag,chat model_tier=m,l` запускает 4 комбинации ([tutorial](https://towardsdatascience.com/complete-tutorial-on-how-to-use-hydra-in-machine-learning-projects-1c00efcc5b9b/)).
- **OmegaConf interpolation**: `${task.model_tier}`, `${oc.env:AGMIND_BACKEND,auto}` (с default-значением), кастомные резолверы через `OmegaConf.register_new_resolver("detect_gpu", lambda: ...)` ([Resolvers](https://omegaconf.readthedocs.io/en/latest/custom_resolvers.html)).
- **Override syntax**: `+key=val` (add), `++key=val` (force), `~key` (delete), `task=rag` (group select).
- **ConfigStore** для structured configs из `@dataclass` (НЕ pydantic напрямую — см. §3).

## 2. POC композиции для AGmind

Реальная структура (выложил бы в `conf/`):

```
conf/
  config.yaml
  task/{rag,embed_batch,chat,cluster}.yaml
  backend/{auto,vulkan,rocm,cpu}.yaml
  model_tier/{s,m,l,xl}.yaml
  proxy/{traefik,nginx}.yaml
  services/{core,rag,observability}.yaml
```

`conf/config.yaml`:
```yaml
defaults:
  - _self_
  - task: chat
  - backend: auto
  - model_tier: m
  - proxy: traefik
  - override hydra/job_logging: disabled

run_id: ${oc.env:AGMIND_RUN_ID,${now:%Y%m%d-%H%M%S}}
data_dir: ${oc.env:AGMIND_DATA,/var/lib/agmind}
ram_gb: ${detect_ram:}              # custom resolver
gpu: ${detect_gpu:}                 # custom resolver -> 'rocm'|'vulkan'|'cpu'
```

`conf/task/rag.yaml`:
```yaml
# @package _global_
task:
  name: rag
  services: [llama_server, infinity, qdrant, ragflow]
  routing: rag_pipeline
  model_tier: l                      # рекомендация, перекрываемая CLI
  embed_model: BAAI/bge-m3
defaults:
  - /model_tier@_here_: l            # вытащить рекомендацию в группу
```

`conf/task/embed_batch.yaml`:
```yaml
# @package _global_
task:
  name: embed_batch
  services: [infinity]
  routing: embed_batch
  batch_size: 256
  endpoint: /embeddings/batch
backend: rocm                        # принудительно ROCm для throughput
```

`conf/backend/auto.yaml`:
```yaml
# @package _global_
backend:
  selected: ${gpu}                   # резолвер вычисляет в runtime
  llama_args: []
```

`conf/model_tier/l.yaml`:
```yaml
# @package _global_
model_tier:
  name: l
  ram_min_gb: 64
  llama_quant: Q5_K_M
  context: 16384
  parallel_slots: 4
```

Команда:
```
agmind start task=rag model_tier=xl backend=rocm proxy=nginx
```
Hydra мерджит: `config.yaml` → `task/rag` → `backend/rocm` → `model_tier/xl` → `proxy/nginx`. Результат — единый `DictConfig` ([Compose API](https://hydra.cc/docs/advanced/compose_api/)).

## 3. Hydra + pydantic v2

**Прямой ConfigStore с BaseModel НЕ работает** — Hydra ожидает dataclass-совместимые объекты, OmegaConf v2.1+ имеет лишь частичную поддержку BaseModel, interpolation резолвится до валидации ([w3tutorials](https://www.w3tutorials.net/blog/is-it-possible-to-use-pydantic-instead-of-dataclasses-in-structured-configs-in-hydra-core-python-package/)).

Канонический паттерн **compose-then-validate** ([Towards Data Science](https://towardsdatascience.com/configuration-management-for-model-training-experiments-using-pydantic-and-hydra-d14a6ae84c13/)):
```python
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf
from agmind.config_schema import AgmindConfig  # pydantic BaseModel

with initialize_config_dir(config_dir=str(CONF_DIR), version_base="1.3"):
    raw = compose(config_name="config", overrides=["task=rag", "model_tier=l"])
cfg = AgmindConfig.model_validate(OmegaConf.to_container(raw, resolve=True))
```
Это лучший вариант: Hydra = композиция, pydantic = типы/инварианты (`Tier`, `Backend` enum). Альтернатива `hydra-zen` существует, но добавляет ещё один слой и сам перешёл в режим medium-activity.

## 4. Замена `_select_auto()`

Текущая матрица в `agmind/compute/_registry.py:73-91` (Profile → Backend) переезжает так:

```yaml
# conf/profile/tg.yaml  (token-generation)
profile:
  name: tg
  preferred_backends: [vulkan, rocm, cpu]
```
```yaml
# conf/profile/embed_batch.yaml
profile:
  name: embed_batch
  preferred_backends: [rocm, vulkan, cpu]
```

Runtime-detection (Vulkan/ROCm доступность) делается **custom resolver-ом**, регистрируемым ДО `compose()`:
```python
OmegaConf.register_new_resolver("detect_gpu", lambda: _probe())
OmegaConf.register_new_resolver("first_available",
    lambda *cands: next((b for b in cands if _backend_ok(b)), "cpu"))
```
В yaml: `selected: ${first_available:${profile.preferred_backends}}`.

Для случаев, когда нужно дописать поле runtime-ом (не объявленное в schema), используется `OmegaConf.set_struct(cfg, False)` + `open_dict()` контекст ([usage](https://omegaconf.readthedocs.io/en/2.3_branch/usage.html)). Дублирование `_TIER_RAM_THRESHOLDS_GB` (Python dict) и `templates/models.yaml` устраняется: один источник — `conf/model_tier/*.yaml`, pydantic-схема его валидирует.

## 5. CLI ergonomics (typer + Hydra)

`@hydra.main` **конфликтует с typer/click** — оба читают `sys.argv` ([blog](https://blog.abhilashbabuj.com/posts/hydra_typer/), [issue #1964](https://github.com/facebookresearch/hydra/issues/1964)). Решение: **не использовать `@hydra.main`**, остаться на typer как entry-point, вызывать `compose()` руками:

```python
@app.command()
def start(
    overrides: list[str] = typer.Argument(None),   # task=rag model_tier=l
    dry_run: bool = typer.Option(False),
):
    with initialize_config_dir(str(CONF_DIR), version_base="1.3"):
        cfg = compose("config", overrides=overrides or [])
    ...
```
Бонус: `agmind start --help` остаётся typer-ным, не перехватывается Hydra.

## 6. Альтернативы (короткое сравнение)

| Tool | Composition | Overlays/groups | CLI override | Validation | Когда брать |
|---|---|---|---|---|---|
| **Hydra 1.3.2** | ✅ defaults list | ✅ группы | ✅ родной | через pydantic post-compose | сложные эксперименты, multirun |
| **OmegaConf standalone** | ✅ `merge()` | вручную | ❌ | ❌ | минимализм, ты сам строишь merge-порядок |
| **Dynaconf 3.x** | priority sources (env > toml > yaml) | layers per env | через env | pydantic-плагин | многосредовый деплой, **2.3× быстрее Hydra** при загрузке ([benchmark](https://johal.in/dynaconf-nested-configs-multi-env-settings-for-ml-deployments-2025-2/)) |
| **pydantic-settings 2.x** | только env + .env + secrets | ❌ | ❌ | ✅ native | сервисный config, 12-factor |
| **ConfZ** | multi-source | базово | ❌ | ✅ | редкий выбор, малое сообщество |

**Для AGmindx86 task-driven сценария** Hydra реально превосходит остальных по матрице `task × backend × tier × proxy`. Dynaconf не даёт group-выбор. pydantic-settings вообще не про композицию.

## 7. Real-world ML usage 2025

- **lightning-hydra-template** (10k★) — де-факто стандарт обучения ([repo](https://github.com/ashleve/lightning-hydra-template)).
- **vLLM, transformers** — НЕ используют Hydra (у них CLI на argparse/dataclass-based `TrainingArguments`); серверные продукты тяготеют к pydantic-settings ([vLLM blog 2025-04](https://blog.vllm.ai/2025/04/11/transformers-backend.html)).
- Тренд 2025: «**Hydra жив**, но не для serving». Composition-фишки нужны research-pipelines; для deploy народ идёт на pydantic-settings/Dynaconf ([safjan blog](https://safjan.com/python-configuration-management/), [MarkTechPost 2025-11](https://www.marktechpost.com/2025/11/04/how-can-we-build-scalable-and-reproducible-machine-learning-experiment-pipelines-using-meta-research-hydra/)). Нет статей «Hydra is dead»; есть «overkill for small projects».

## 8. Cost of adoption

- Зависимости: `hydra-core>=1.3,<1.4` + `omegaconf` (транзитивная). ~+8 MB wheel.
- Файлы: ~12-20 yaml (`conf/`), 1 pydantic schema (`agmind/config_schema.py`, ~80 строк), 1 loader (`agmind/config.py`, ~30 строк).
- Тронуть: `agmind/compute/_registry.py` (вынести матрицу), `agmind/models.py` (убрать `_TIER_RAM_THRESHOLDS_GB`), `agmind/cli/*` (вызов `compose()`).
- **Постепенная миграция**: feature-флаг `AGMIND_USE_HYDRA=1`. Старый env-слой остаётся как fallback в loader.
- **Тестирование**: `with initialize_config_dir(...)` идеально работает в pytest как context manager ([Hydra unit tests](https://hydra.cc/docs/advanced/unit_testing/)); глобальная `initialize()` вызывается один раз — отсюда anti-pattern в parametrize-тестах, используй context-форму.

## 9. Анти-паттерны

- **`_target_` instantiate hell**: yaml с `_target_: agmind.routing.RagPipeline` + `_partial_: true` превращает config в скрытый Python — не делать для бизнес-логики; OK только для plugins (storage backends).
- **Глубокая вложенность 3+**: `cfg.task.routing.embed.batch.size` нечитаемо; держать 2 уровня max.
- **Override-цепочки в `defaults:`**: `- override /model/encoder: bge` — мощно, но 5+ override строк → распутать невозможно. Лучше плоский config + явный pydantic-resolve.
- **Структура конфига = структура кода**: завязывает рефакторинг кода на пересборку yaml. Разделяй: yaml описывает **намерение** (task), код решает **как** (handlers).

---

## Вердикт для AGmindx86

Hydra решает **именно ту задачу** (`task × backend × tier × proxy` overlay), которую ты сформулировал. Но порог входа — десяток yaml + переучивание команды на defaults-list синтаксис. **Рекомендуемый промежуточный путь**:

1. **Phase A**: OmegaConf standalone + pydantic v2. Один `conf/config.yaml` + ручной `OmegaConf.merge(base, task, tier)` в loader. ~150 строк, без новой зависимости top-level (omegaconf уже транзитивен от многих). Решает 70% задачи.
2. **Phase B** (если реально пойдут multirun-эксперименты или появится >3 осей композиции): добавить Hydra поверх — group-структура `conf/` уже будет готова, нужно лишь `defaults:` блок и `compose()` вызов.

Для проекта твоего размера и одной точки `_select_auto()` чистый Hydra сегодня = overengineering. Но **архитектуру `conf/` готовь под Hydra-совместимый layout сразу** — переход будет дешёвым.

## Источники
- [facebookresearch/hydra](https://github.com/facebookresearch/hydra) — Hydra 1.3.2 stable, 1.4 dev, Py 3.10-3.14
- [Defaults List | Hydra docs](https://github.com/facebookresearch/hydra/blob/main/website/docs/advanced/defaults_list.md)
- [Compose API](https://hydra.cc/docs/advanced/compose_api/)
- [Hydra in Unit Tests](https://hydra.cc/docs/advanced/unit_testing/)
- [Structured Config schema](https://hydra.cc/docs/tutorials/structured_config/schema/)
- [Configuration management with Pydantic and Hydra (Towards Data Science)](https://towardsdatascience.com/configuration-management-for-model-training-experiments-using-pydantic-and-hydra-d14a6ae84c13/)
- [Pydantic в structured configs — ограничения (w3tutorials)](https://www.w3tutorials.net/blog/is-it-possible-to-use-pydantic-instead-of-dataclasses-in-structured-configs-in-hydra-core-python-package/)
- [Using Hydra and Typer together (Abhilash Babu)](https://blog.abhilashbabuj.com/posts/hydra_typer/)
- [GH issue #1964: hydra + click conflict](https://github.com/facebookresearch/hydra/issues/1964)
- [GH issue #2177: hydra hijacks command line](https://github.com/facebookresearch/hydra/issues/2177)
- [OmegaConf Resolvers](https://omegaconf.readthedocs.io/en/latest/custom_resolvers.html)
- [OmegaConf set_struct/open_dict usage](https://omegaconf.readthedocs.io/en/2.3_branch/usage.html)
- [lightning-hydra-template](https://github.com/ashleve/lightning-hydra-template)
- [Dynaconf vs Hydra benchmark 2025](https://johal.in/dynaconf-nested-configs-multi-env-settings-for-ml-deployments-2025-2/)
- [Python Config Management overview (safjan)](https://safjan.com/python-configuration-management/)
- [Hydra ML pipelines 2025 (MarkTechPost)](https://www.marktechpost.com/2025/11/04/how-can-we-build-scalable-and-reproducible-machine-learning-experiment-pipelines-using-meta-research-hydra/)
- [vLLM transformers backend blog 2025-04](https://blog.vllm.ai/2025/04/11/transformers-backend.html)
