variable "region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "app_urls" {
  description = "OAuthのコールバック/ログアウト先URL(ローカル開発用)"
  type        = list(string)
  default = [
    "http://127.0.0.1:8000/",
    "http://127.0.0.1:8001/",
    "http://localhost:8000/",
    "http://localhost:8001/",
  ]
}

variable "domain_prefix" {
  description = "Hosted UIのドメインプレフィックス(グローバルで一意)"
  type        = string
  default     = "rtv-auth-051961177429"
}

variable "service_domain" {
  description = "本番サービスのドメイン(コールバックURLに追加される)"
  type        = string
  default     = "voice.pocraft.net"
}
