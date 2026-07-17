# 実行基盤(サービス層)のエントリポイント。
# 認証基盤(../auth)とはstateを分離しており、ここでのterraform destroyは
# User Pool(ユーザー登録)に一切届かない。何度でも壊して作り直せる層。

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

# 認証基盤の出力(pool id / client id / Hosted UIドメイン)を参照する
data "terraform_remote_state" "auth" {
  backend = "local"
  config = {
    path = "${path.module}/../auth/terraform.tfstate"
  }
}
