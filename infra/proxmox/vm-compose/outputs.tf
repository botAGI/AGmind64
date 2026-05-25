locals {
  agmind_hosts = {
    for name, node in var.nodes : name => {
      ansible_host = coalesce(
        node.ansible_host,
        try(proxmox_virtual_environment_vm.agmind[name].ipv4_addresses[1][0], null),
        try(proxmox_virtual_environment_vm.agmind[name].ipv4_addresses[0][0], null),
        name,
      )
      ansible_user = node.admin_user
      node_name    = node.node_name
      role         = node.role
      vm_id        = node.vm_id
      vm_name      = proxmox_virtual_environment_vm.agmind[name].name
    }
  }
}

output "agmind_hosts" {
  description = "Provisioned AGmind VM metadata keyed by inventory hostname."
  value       = local.agmind_hosts
}

output "ansible_inventory" {
  description = "Inventory-shaped object for the M7.B.4 AGmind inventory bridge."
  value = {
    all = {
      children = {
        agmind_nodes = {
          children = {
            agmind_master  = {}
            agmind_workers = {}
          }
        }
        agmind_master = {
          hosts = {
            for name, host in local.agmind_hosts : name => {
              ansible_host = host.ansible_host
              ansible_user = host.ansible_user
              node_name    = host.node_name
              vm_id        = host.vm_id
            } if host.role == "master"
          }
        }
        agmind_workers = {
          hosts = {
            for name, host in local.agmind_hosts : name => {
              ansible_host = host.ansible_host
              ansible_user = host.ansible_user
              node_name    = host.node_name
              vm_id        = host.vm_id
            } if host.role == "worker"
          }
        }
      }
    }
  }
}
