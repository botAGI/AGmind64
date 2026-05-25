locals {
  cloud_init_user_data = {
    for name, node in var.nodes : name => join("\n", [
      "#cloud-config",
      yamlencode({
        hostname         = name
        manage_etc_hosts = true
        package_update   = true
        packages = [
          "ca-certificates",
          "curl",
          "python3",
          "python3-apt",
          "qemu-guest-agent",
        ]
        runcmd = [
          ["systemctl", "enable", "--now", "qemu-guest-agent"],
        ]
        timezone = var.timezone
        users = [
          "default",
          {
            name                = node.admin_user
            groups              = ["sudo"]
            shell               = "/bin/bash"
            ssh_authorized_keys = node.ssh_public_keys
            sudo                = "ALL=(ALL) NOPASSWD:ALL"
          },
        ]
        write_files = [
          {
            content     = node.role
            path        = "/etc/agmind-node-role"
            permissions = "0644"
          },
        ]
      }),
    ])
  }
}

resource "proxmox_virtual_environment_file" "cloud_init" {
  for_each = var.nodes

  content_type = "snippets"
  datastore_id = each.value.cloud_init_datastore_id
  node_name    = each.value.node_name

  source_raw {
    data      = local.cloud_init_user_data[each.key]
    file_name = "${each.key}-user-data.yaml"
  }
}

resource "proxmox_virtual_environment_vm" "agmind" {
  for_each = var.nodes

  name      = each.key
  node_name = each.value.node_name
  vm_id     = each.value.vm_id
  tags      = distinct(concat(var.tags, [each.value.role]))

  stop_on_destroy = true

  agent {
    enabled = true
  }

  clone {
    vm_id = each.value.template_vm_id
    full  = true
  }

  cpu {
    cores = each.value.cores
    type  = var.cpu_type
  }

  memory {
    dedicated = each.value.memory_mb
  }

  disk {
    datastore_id = each.value.datastore_id
    interface    = "scsi0"
    size         = each.value.disk_size
  }

  initialization {
    datastore_id       = each.value.cloud_init_datastore_id
    user_data_file_id  = proxmox_virtual_environment_file.cloud_init[each.key].id

    ip_config {
      ipv4 {
        address = each.value.ipv4_address
        gateway = each.value.ipv4_gateway
      }
    }
  }

  network_device {
    bridge = each.value.network_bridge
    model  = "virtio"
  }

  operating_system {
    type = "l26"
  }

  serial_device {}
}
