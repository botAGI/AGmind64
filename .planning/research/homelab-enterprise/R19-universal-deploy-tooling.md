# R19 — Universal deploy tooling: homelab → enterprise

- **Date:** 2026-05-23
- **Status:** research + architecture direction, not implementation
- **Driver:** move AGmind from "clean Ubuntu + Docker Compose" toward universal
  deploy targets for homelab and enterprise: Proxmox, Kubernetes/k3s/RKE2,
  GitOps, backup, secrets, storage, and optional AI apps such as ComfyUI.

## Current codebase reality

AGmind currently has a strong single-host core:

- Python `ServiceDescriptor` catalog in `templates/services/*.yaml`
- one renderer: `agmind render compose`
- one host mutation lane: Ansible
- one runtime lane: Docker Compose
- one cluster model: master/full stack + worker inference nodes discovered over
  LAN/mDNS
- observability/security profiles already exist: Prometheus, Grafana, Loki,
  Alloy, cAdvisor, Portainer, Authelia

That is coherent for v0.6/v1.0. It is not yet a universal deploy model.

## Inconsistencies / logic gaps

1. **Docs say cluster, implementation means inference workers.**
   `agmind cluster` is not an orchestrator. It routes inference and generates
   Ansible inventory; it does not schedule arbitrary services, reschedule
   failed workloads, or provide persistent volume semantics.

2. **Service catalog is runtime-specific.**
   `ServiceDescriptor` currently maps directly to Docker Compose fields:
   `ports`, `volumes`, `devices`, `group_add`, `health`, `routing`. This is
   excellent for Compose, but there is no `deploy_target`, storage class,
   secret backend, ingress class, or GPU/device abstraction for Kubernetes,
   Nomad, Proxmox LXC, or enterprise clusters.

3. **Provisioning is missing as a first-class layer.**
   Ansible mutates an already-existing Ubuntu host. There is no IaC layer for
   creating VMs/LXCs on Proxmox, attaching cloud-init, selecting storage, or
   generating inventory from provisioned nodes.

4. **Backup story is still single-host config backup.**
   R14 already says Docker volumes, models, encryption, and multi-node backup
   are gaps. Homelab/enterprise needs a separate DR lane: PBS/ZFS/restic/Kopia
   for Compose/VMs, Velero/CSI snapshots for Kubernetes.

5. **Security tier is homelab-light.**
   Authelia profile exists, but Keycloak/OIDC, SOPS/age, Vault/Infisical,
   registry policy/scanning, and multi-operator RBAC are not represented.

6. **The roadmap still points only at GA hardening.**
   M6.C/M6.D are correct next gates, but "universal deploy" should become an
   M7/M8 architecture track, not be smuggled into M6.

7. **Some planning state is stale.**
   Latest green CI is now `26333245295` on `d21294f`, not the older
   `26297545718` on `33a2050`.

## Researched tooling by layer

| Layer | Recommended default | Optional / enterprise | Why |
|---|---|---|---|
| Host provisioning | **OpenTofu + bpg/proxmox provider** | Packer for golden VM images | Proxmox API + cloud-init can create repeatable VMs; OpenTofu keeps state and modules. |
| Host configuration | **Existing Ansible roles** | AWX/AAP later | Keep current boundary: Ansible mutates OS/Docker/firewall after provisioning. |
| Local runtime | **Docker Compose** | Portainer as UI | Keep v1.0 path simple; Compose is still best for 1 node / small LAN. |
| Homelab cluster | **k3s** | Talos when ready to go immutable | k3s is lightweight and explicitly targets homelab/edge. Talos is cleaner but requires a Kubernetes-first operating model. |
| Enterprise Kubernetes | **RKE2** | upstream kubeadm / Rancher | RKE2 is security/compliance-focused and closer to enterprise defaults. |
| Alternative scheduler | Nomad | Nomad + Consul + Vault | Good for mixed workloads, but would be a second renderer and second ops model; defer. |
| Bare-metal K8s LB | MetalLB | kube-vip for API VIP + service LB | Needed because homelab clusters do not get cloud LoadBalancer services. |
| GitOps | Argo CD | Flux | Use once Kubernetes manifests exist; do not add before a K8s renderer exists. |
| Storage | ZFS/TrueNAS, MinIO | Ceph, Longhorn | Pick per target: ZFS/PBS for Proxmox/Compose, Longhorn for k3s, Ceph for serious multi-node storage. |
| Backup/DR | Proxmox Backup Server, restic/Kopia | Velero for K8s | Different runtime targets need different backup primitives. |
| Secrets | SOPS + age | Vault or Infisical | SOPS is low-ops and GitOps-friendly; Vault/Infisical are enterprise/team secrets planes. |
| IAM | Authelia | Keycloak | Authelia remains lightweight reverse-proxy auth; Keycloak is the OIDC/SSO enterprise path. |
| Registry/supply chain | Existing pinned image audit | Harbor + Trivy/SBOM/signing | Harbor is useful once AGmind builds/publishes its own images at scale. |
| Remote access | WireGuard/Tailscale-style | Headscale/NetBird | Needed for multi-site homelabs and enterprise edge nodes without public ports. |
| AI app catalog | Open WebUI, Dify, RAGFlow | **ComfyUI**, n8n, Home Assistant bridge | ComfyUI should be an optional app/sidecar, not part of core deploy. |

## Tool admission rule

Do not add tools because they are popular in homelabs. Add them only if they map
to one of these contracts:

1. **provision** infrastructure before Ansible runs,
2. **configure** hosts or clusters,
3. **render** AGmind services to a target runtime,
4. **operate** deployed services day 2,
5. **recover** state after failure,
6. **secure** users, secrets, network, or images.

## Recommended architecture direction

Add an explicit deploy target ladder instead of replacing Compose:

```text
Target 0: ubuntu-compose
  current path: clean Ubuntu -> Ansible -> Docker Compose

Target 1: proxmox-vm-compose
  OpenTofu provisions Ubuntu VM(s) on Proxmox
  cloud-init creates SSH/user/network
  Ansible runs current AGmind install
  Docker Compose remains runtime

Target 2: k3s-homelab
  OpenTofu/Ansible provisions nodes
  k3s installed
  new renderer emits Helm/Kustomize or plain manifests
  MetalLB/kube-vip + Longhorn/MinIO optional packs

Target 3: rke2-enterprise
  RKE2/Talos/Rancher-ready path
  Keycloak/Vault or Infisical/Harbor/GitOps/Velero profiles
  stronger RBAC, policy, backup, registry, and audit assumptions
```

This keeps v1.0 stable while opening a real universal-deploy track.

## First implementation slice

Recommended M7.A:

1. Add an ADR: **Deploy targets and provisioning boundary**.
2. Add a `DeploymentTarget` schema:
   - `name`: `ubuntu-compose`, `proxmox-vm-compose`, `k3s`, `rke2`, `nomad`
   - `runtime`: `compose | kubernetes | nomad`
   - `provisioner`: `none | opentofu-proxmox | external`
   - `configurator`: `ansible | talosctl | kubectl`
   - `storage_profile`: `local-zfs | pbs | longhorn | ceph | external`
   - `secrets_profile`: `files | sops-age | vault | infisical`
3. Add a Proxmox module skeleton under `infra/proxmox/` using OpenTofu.
4. Generate Ansible inventory from OpenTofu outputs.
5. Keep service rendering as Compose for this slice.

This gives immediate homelab value without forcing Kubernetes into the v1.0
release path.

## Service catalog candidates

Add only as opt-in descriptors/profiles:

| Service | Profile | Notes |
|---|---|---|
| `comfyui` | `creative-ai` | Optional image/workflow UI. Needs GPU/ROCm/Vulkan reality check on Strix Halo. |
| `n8n` | `automation` | Useful for local agent/workflow automation; license and telemetry defaults need review. |
| `home-assistant` | `homelab` | Only if AGmind wants smart-home/IoT bridge; otherwise too broad. |
| `keycloak` | `enterprise-security` | OIDC/SSO for enterprise target. |
| `vault` or `infisical` | `enterprise-secrets` | Pick one as enterprise path; SOPS+age for low-ops baseline. |
| `harbor` | `enterprise-registry` | Registry, policy, scanning; useful once image publishing matures. |
| `restic` / `kopia` runner | `backup` | Compose/host backup jobs. |
| `proxmox-exporter` | `proxmox` | Observability for PVE hosts. |

## Defer / avoid for now

- Do not replace Compose with Kubernetes before v1.0 E2E is recorded.
- Do not add Nomad until a Kubernetes renderer decision is made; Nomad creates
  a parallel ecosystem and would split effort.
- Do not make ComfyUI core; it is workload/app catalog, not orchestration.
- Do not put Proxmox credentials in normal `.env`; use SOPS/age, environment,
  or a local ignored tfvars file.
- Do not treat Portainer as an orchestrator. It is a management UI over
  Docker/Kubernetes environments.

## Sources

- Proxmox VE docs/API: https://pve.proxmox.com/pve-docs/index.html,
  https://pve.proxmox.com/wiki/Proxmox_VE_API
- Proxmox Backup Server docs: https://pbs.proxmox.com/docs/
- bpg Proxmox OpenTofu/Terraform provider:
  https://bpg.sh/docs/, https://bpg.sh/docs/guides/cloud-init/
- OpenTofu docs: https://opentofu.org/, https://opentofu.org/docs/
- Ansible docs: https://docs.ansible.com/projects/ansible/latest/getting_started/index.html
- Docker Compose docs: https://docs.docker.com/compose/
- Kubernetes docs: https://kubernetes.io/docs/concepts/overview/
- k3s docs: https://docs.k3s.io/
- RKE2 docs: https://docs.rke2.io/
- Talos docs: https://docs.siderolabs.com/talos/
- Nomad docs: https://developer.hashicorp.com/nomad/docs
- MetalLB docs: https://metallb.io/
- kube-vip docs: https://kube-vip.io/docs/
- Longhorn docs: https://longhorn.io/docs/latest/
- Ceph docs: https://docs.ceph.com/en/latest/architecture/
- TrueNAS docs: https://www.truenas.com/docs/scale/26/storage/
- OpenZFS docs: https://openzfs.github.io/openzfs-docs/
- Argo CD docs: https://argo-cd.readthedocs.io/
- Flux docs: https://fluxcd.io/flux/
- Harbor docs: https://goharbor.io/
- SOPS docs: https://getsops.io/
- Vault docs: https://developer.hashicorp.com/vault/docs
- Authelia docs: https://www.authelia.com/
- Keycloak docs: https://www.keycloak.org/documentation
- ComfyUI docs: https://docs.comfy.org/
- Portainer docs: https://docs.portainer.io/
- NetBird docs: https://docs.netbird.io/about-netbird/how-netbird-works
- Tailscale docs: https://tailscale.com/docs/concepts/what-is-tailscale
- Headscale docs: https://docs.headscale.org/
