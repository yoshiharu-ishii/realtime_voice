variable "region" {
  description = "AWSリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "app_urls" {
  description = "OAuthのコールバック/ログアウト先URL(完全一致で検証される)"
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
