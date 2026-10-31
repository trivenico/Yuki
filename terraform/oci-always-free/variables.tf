variable "tenancy_ocid" {
  description = "OCI tenancy OCID."
  type        = string
}
variable "user_ocid" {
  description = "OCI user OCID used by Terraform API-key authentication."
  type        = string
}
variable "fingerprint" {
  description = "OCI API signing key fingerprint."
  type        = string
}
variable "private_key_path" {
  description = "Path to OCI API signing private key; never commit it."
  type        = string
  sensitive   = true
}
variable "region" {
  description = "OCI tenancy home region."
  type        = string
}
variable "compartment_ocid" {
  description = "Compartment OCID for Yuki resources."
  type        = string
}
variable "availability_domain_number" {
  description = "Zero-based availability-domain index; change if A1 capacity is unavailable."
  type        = number
  default     = 0
}
variable "ssh_cidr" {
  description = "Optional trusted IPv4 CIDR for SSH. Empty disables public SSH ingress."
  type        = string
  default     = ""
}
variable "instance_name" {
  type    = string
  default = "yuki-cloud-free"
}
variable "ssh_public_key" {
  description = "SSH public key for emergency administration."
  type        = string
  sensitive   = true
}
