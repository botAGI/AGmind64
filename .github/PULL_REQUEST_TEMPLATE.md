<!-- Thanks for contributing to AGmind. Keep the diff minimal and focused. -->

## Summary

<!-- What does this change and why? One or two sentences. -->

## Type of change

- [ ] Bug fix (`fix:`)
- [ ] Feature (`feat:`)
- [ ] Docs (`docs:`)
- [ ] CI / build / chore (`ci:` / `build:` / `chore:`)
- [ ] Service descriptor add or version bump (`templates/services/*.yaml`)

## Checklist

- [ ] Conventional commit title (e.g. `fix(install): …`, `feat(services): …`)
- [ ] `make lint` passes
- [ ] `make test-fast` passes — new behaviour is covered by a test
- [ ] `make audit` and `make schema-validate` pass
- [ ] `agmind governance validate` passes
- [ ] `pre-commit run --all-files` passes
- [ ] New test files carry a backend marker (`backend_any` / `cpu` / `vulkan` / `rocm`)
- [ ] Service descriptor change: re-rendered Compose (`agmind render compose …`) and checked the result
- [ ] README change: `README.md` and `README.ru.md` kept in sync

See [CONTRIBUTING.md](../CONTRIBUTING.md) for dev setup and the full workflow.
