output "instance_id" { value = oci_core_instance.yuki.id }
output "public_ip" {
  value = oci_core_instance.yuki.public_ip
  description = "Public IPv4 address. Configure DNS and TLS before exposing the gateway."
}
output "vcn_id" { value = oci_core_vcn.yuki.id }
output "free_tier_shape" { value = "VM.Standard.A1.Flex: 2 OCPUs, 12 GB RAM, 50 GB boot volume" }
