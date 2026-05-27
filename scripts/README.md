# AGmind Scripts Layout

`scripts/` is split by operational purpose:

- `checks/` — repository, governance, deploy-target, Kubernetes render, and
  version validation entrypoints used by CI and pre-commit.
- `proof/` — non-destructive proof/smoke harnesses that produce operator
  evidence bundles or exercise root-owned flows.
- `ops/` — host/operator helper scripts that are copied or run outside the
  Python package.
- `dev/` — maintainer-only generators and repository maintenance helpers.

Keep production library code in `agmind/`; scripts should stay thin entrypoints
or standalone operator tools.
