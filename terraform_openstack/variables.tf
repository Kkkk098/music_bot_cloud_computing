variable "flavor_name" {
  type    = string
  default = "m1.small"
}

variable "network_name" {
  type    = string
  default = "sutdents-net"
}

variable "key_pair" {
  type    = string
  default = "kkk2"
}

variable "volume_size" {
  default = 20
}
