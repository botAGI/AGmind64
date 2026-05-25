# R21 — k3s storage and secrets addon candidates

- **Date:** 2026-05-24
- **Status:** research-backed catalog update
- **Scope:** candidate records only; no Kubernetes addon rendering yet

## Why this slice

The M7 universal deploy ladder already references `longhorn` storage and
`external-secrets` secrets profiles from enterprise candidates such as Harbor,
Keycloak, Vault, and Infisical. Those profiles were dangling: the project knew
about tools that depend on them, but the addon tools themselves were not in the
candidate catalog.

This slice adds the missing records as `deploy-target-addon` candidates for the
`k3s` deployment target. They are not AGmind runtime services and should not get
Docker Compose `ServiceDescriptor` files.

## Longhorn

- **Recommended version:** `1.11.2`
- **Source:** https://github.com/longhorn/longhorn/releases/tag/v1.11.2
- **Docs:** https://longhorn.io/docs/1.11.2/deploy/install/install-with-helm/

Longhorn is the homelab k3s storage candidate because it gives a Kubernetes
storage plane with snapshots, backups, and recovery semantics. The 1.11.2
release is marked latest in Longhorn docs and GitHub release notes. The release
requires Kubernetes `v1.25+`, so any future k3s promotion must check cluster
version before accepting Longhorn evidence.

AGmind admission stance:

- scope: `deploy-target-addon`
- runtime: `kubernetes`
- contracts: `provision`, `configure`, `operate`, `recover`
- service descriptor required: false
- component contract required: false

Next proof before renderer work:

- Helm values contract for single-node and multi-node k3s.
- Node disk prerequisite check.
- StorageClass policy and default/not-default decision.
- Snapshot/backup/restore proof using a throwaway PVC.

## External Secrets Operator

- **Recommended version:** `2.4.1`
- **Source:** https://github.com/external-secrets/external-secrets/releases/tag/v2.4.1
- **Docs:** https://external-secrets.io/latest/introduction/overview/

External Secrets Operator is the k3s secrets addon candidate because it maps
external secret stores into Kubernetes Secrets through CRDs such as
`SecretStore`, `ExternalSecret`, and `ClusterSecretStore`. The official overview
calls out access-control risk: the operator runs with elevated privileges and
can create/read/update Secrets, so AGmind must require least-privilege stores
and avoid broad shared stores by default.

AGmind admission stance:

- scope: `deploy-target-addon`
- runtime: `kubernetes`
- contracts: `configure`, `operate`, `secure`
- service descriptor required: false
- component contract required: false

Next proof before renderer work:

- SecretStore/ClusterSecretStore policy for homelab versus enterprise.
- Bootstrap path from SOPS/age or external Vault/Infisical without committing
  secret values.
- Render check that secret material never appears in manifests, logs, or
  dry-run artifacts.

## Decision

Add both tools to `templates/tool-candidates/` now, with recommended versions
and source URLs. Keep them as candidates until the k3s target has real addon
contracts and server-side dry-run evidence.
