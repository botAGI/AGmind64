# AGmind Proxmox VM Compose Target

This root module is the first OpenTofu skeleton for the
`proxmox-vm-compose` deployment target.

It provisions Ubuntu VM shells on Proxmox VE and leaves the AGmind runtime
unchanged: Ansible still configures the host, and Docker Compose remains the
service runtime.

## Boundary

- OpenTofu creates VM infrastructure, cloud-init user data, disks, and network
  attachment.
- Ansible configures Ubuntu after the VM is reachable over SSH.
- AGmind renders and applies Docker Compose after host configuration.

## Prerequisites

- Proxmox VE API endpoint and API token.
- A prepared Ubuntu cloud-init template VM on the target Proxmox node.
- Snippets enabled on the configured cloud-init datastore.
- An SSH public key for the operator/admin user.

## Local Use

```bash
cd infra/proxmox/vm-compose
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
tofu init
tofu plan
```

Keep real `terraform.tfvars`, state files, and plan files local. They are
ignored by this module's `.gitignore`.

After apply, generate an Ansible inventory for the existing AGmind install
playbook:

```bash
tofu output -json > /tmp/agmind-proxmox-output.json
python ../../../scripts/ops/proxmox_inventory.py \
  --input /tmp/agmind-proxmox-output.json \
  --output ../../../ansible/inventory/proxmox.generated.yml
```

## Outputs

- `agmind_hosts`: VM metadata keyed by inventory hostname.
- `ansible_inventory`: inventory-shaped object consumed by
  `scripts/ops/proxmox_inventory.py`.

## References

- OpenTofu provider requirements:
  https://opentofu.org/docs/language/providers/requirements/
- bpg/proxmox provider:
  https://search.opentofu.org/provider/bpg/proxmox/v0.93.0
- bpg/proxmox cloud-init guide:
  https://search.opentofu.org/provider/bpg/proxmox/v0.93.0/docs/guides/cloud-init
