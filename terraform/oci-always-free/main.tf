data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu_a1" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_vcn" "yuki" {
  compartment_id = var.compartment_ocid
  display_name   = "yuki-free-vcn"
  cidr_blocks    = ["10.42.0.0/16"]
  dns_label      = "yukivcn"
}

resource "oci_core_internet_gateway" "yuki" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.yuki.id
  display_name   = "yuki-free-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.yuki.id
  display_name   = "yuki-free-public-routes"
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.yuki.id
  }
}

resource "oci_core_security_list" "gateway" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.yuki.id
  display_name   = "yuki-gateway-ingress"
  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    description = "HTTPS gateway only"
    tcp_options {
      min = 443
      max = 443
    }
  }
  dynamic "ingress_security_rules" {
    for_each = var.ssh_cidr == "" ? [] : [var.ssh_cidr]
    content {
      protocol    = "6"
      source      = ingress_security_rules.value
      description = "SSH from explicitly trusted administrator CIDR"
      tcp_options {
        min = 22
        max = 22
      }
    }
  }
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    description = "Outbound updates and model-provider connections"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.yuki.id
  cidr_block                 = "10.42.1.0/24"
  display_name               = "yuki-free-public-subnet"
  dns_label                  = "yukipub"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.gateway.id]
  prohibit_public_ip_on_vnic = false
}

resource "oci_core_instance" "yuki" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_number].name
  display_name        = var.instance_name
  shape               = "VM.Standard.A1.Flex"
  shape_config {
    ocpus         = 2
    memory_in_gbs = 12
  }
  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu_a1.images[0].id
    boot_volume_size_in_gbs = 50
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    hostname_label   = "yukicloud"
  }
  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data          = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      instance_name = var.instance_name
      ssh_cidr      = var.ssh_cidr
    }))
  }
  freeform_tags = {
    project      = "Yuki"
    cost-profile = "always-free"
    managed-by   = "terraform"
  }
}
