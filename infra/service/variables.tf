variable "region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "zone_name" {
  description = "Route53の既存ホストゾーン名"
  type        = string
  default     = "pocraft.net"
}

variable "service_domain" {
  description = "サービスの公開ドメイン(zone_nameのサブドメイン)"
  type        = string
  default     = "voice.pocraft.net"
}

variable "openai_api_key" {
  description = "OpenAI APIキー(SSM SecureStringに格納。secrets.auto.tfvarsで渡す)"
  type        = string
  sensitive   = true
}
