variable "proxmox_endpoint" {
  description = "Proxmox VE API endpoint, for example https://pve.example.com:8006/."
  type        = string

  validation {
    condition     = can(regex("^https://", var.proxmox_endpoint))
    error_message = "proxmox_endpoint must be an HTTPS URL."
  }
}

variable "proxmox_api_token" {
  description = "Proxmox API token in the format user@realm!token-id=secret."
  type        = string
  sensitive   = true
  nullable    = false
}

variable "proxmox_insecure" {
  description = "Allow insecure TLS for homelab Proxmox endpoints with local certificates."
  type        = bool
  default     = false
}

variable "timezone" {
  description = "Timezone written into cloud-init user data."
  type        = string
  default     = "UTC"
}

variable "tags" {
  description = "Base tags applied to all AGmind VMs."
  type        = list(string)
  default     = ["agmind", "compose"]
}

variable "cpu_type" {
  description = "CPU type exposed to provisioned VMs."
  type        = string
  default     = "host"
}

variable "nodes" {
  description = "AGmind VM definitions keyed by inventory hostname."
  type = map(object({
    role                    = optional(string, "worker")
    node_name               = string
    vm_id                   = number
    template_vm_id          = number
    datastore_id            = string
    cloud_init_datastore_id = optional(string, "local")
    network_bridge          = optional(string, "vmbr0")
    ipv4_address            = optional(string, "dhcp")
    ipv4_gateway            = optional(string)
    ansible_host            = optional(string)
    admin_user              = optional(string, "agmind")
    ssh_public_keys         = list(string)
    cores                   = optional(number, 8)
    memory_mb               = optional(number, 32768)
    disk_size               = optional(number, 128)
  }))

  validation {
    condition     = alltrue([for node in values(var.nodes) : contains(["master", "worker"], node.role)])
    error_message = "Each node role must be either master or worker."
  }

  validation {
    condition     = length([for node in values(var.nodes) : node if node.role == "master"]) == 1
    error_message = "Exactly one node must have role master."
  }

  validation {
    condition     = alltrue([for node in values(var.nodes) : length(node.ssh_public_keys) > 0])
    error_message = "Each node must define at least one SSH public key."
  }
}
