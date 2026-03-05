# нам нужен айдишник для block_device для создания отдельного диска (volume), который будет удален при удалении ВМ
data "openstack_images_image_v2" "ubuntu" {
  name = "ununtu-22.04"
}

# Создаем volume из образа
resource "openstack_blockstorage_volume_v3" "vm_volume" {
  name        = "tf-volume_mutovkina"
  size        = var.volume_size
  image_id    = data.openstack_images_image_v2.ubuntu.id
  availability_zone = "nova"
}

resource "openstack_compute_instance_v2" "vm" {
  name              = "tf-vm_mutovkina"
  flavor_name       = var.flavor_name
  key_pair          = var.key_pair
  availability_zone = "nova"

  security_groups = [
    "default",
    "students-general"
  ]

  network {
    name = var.network_name
  }

  block_device {
    uuid                  = openstack_blockstorage_volume_v3.vm_volume.id
    source_type           = "volume"
    destination_type      = "volume"
    boot_index            = 0
    delete_on_termination = true
  }
}

output "vm_id" {
  value = openstack_compute_instance_v2.vm.id
}

output "vm_ip" {
  value = openstack_compute_instance_v2.vm.network[0].fixed_ip_v4
}
