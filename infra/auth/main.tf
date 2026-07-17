# 認証基盤 (Amazon Cognito) — 実行基盤(../service)とはstateを分離している。
#
# 分離の理由: User Poolは「ユーザー登録」というデータを持つ層であり、
# 実行基盤の作り直し(terraform destroy)に巻き込まれてはならない。
# 実際に一度、ターゲットなしのdestroyでユーザーごと消す事故が起きた。
# こちらのディレクトリでは原則destroyしないこと。

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70"
    }
  }
}

provider "aws" {
  region = var.region
}

resource "aws_cognito_user_pool" "this" {
  name                     = "realtime-voice"
  user_pool_tier           = "ESSENTIALS"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE" # 事故対策: destroyやコンソール操作での削除を拒否する
  mfa_configuration        = "OFF"

  # セルフサインアップ禁止(ユーザー作成は管理者のみ)
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 8
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "web"
  user_pool_id = aws_cognito_user_pool.this.id
  # generate_secret は未指定 = シークレットなしの公開クライアント(PKCEで保護)。
  # 明示的に false を書くと既存クライアントのimport時に置換強制されるため書かない

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  # ローカル開発用URL + 本番サービスURL(完全一致で検証される)
  callback_urls = concat(var.app_urls, ["https://${var.service_domain}/"])
  logout_urls   = concat(var.app_urls, ["https://${var.service_domain}/"])
  supported_identity_providers         = ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH", # 自動テストでのトークン発行用
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  enable_token_revocation = true
  auth_session_validity   = 3
  refresh_token_validity  = 30
}

resource "aws_cognito_user_pool_domain" "this" {
  domain                = var.domain_prefix
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = 1 # クラシックHosted UI
}
