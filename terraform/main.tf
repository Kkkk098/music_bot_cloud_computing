variable "image_name" {}
variable "flavor_name" {}
variable "network_name" {}
variable "keypair" {}

resource "openstack_compute_instance_v2" "music_server" {
  name            = "music-api-server"
  image_name      = var.image_name
  flavor_name     = var.flavor_name
  key_pair        = var.keypair

  network {
    name = var.network_name
  }
}

output "vm_ip" {
  value = openstack_compute_instance_v2.music_server.access_ip_v4
}