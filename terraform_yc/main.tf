resource "yandex_vpc_network" "network" {
  name = "tf-network"
}

resource "yandex_vpc_subnet" "subnet" {
  name           = "tf-subnet"
  zone           = "ru-central1-a"
  network_id     = yandex_vpc_network.network.id
  v4_cidr_blocks = ["10.1.0.0/24"]
}

resource "yandex_compute_instance" "vm" {
  name = "tf-vm-mutovkina"

  resources {
    cores  = 2
    memory = 2
  }

  boot_disk {
    initialize_params {
      image_id = "fd8j0a8f6n1s0l3h3v2m" # Ubuntu 22.04
      size     = 20
    }
  }

  network_interface {
    subnet_id = yandex_vpc_subnet.subnet.id
    nat       = true
  }

  metadata = {
    ssh-keys = "ubuntu:${file("~/.ssh/id_ed25519.pub")}"
  }
}

output "vm_ip" {
  value = yandex_compute_instance.vm.network_interface.0.nat_ip_address
}
