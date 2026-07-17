# ユーザーアカウントもコード管理する(terraform applyでアカウントまで生える)。
# セルフサインアップ無効のため、ここに書くことが唯一の登録経路。
#
# パスワードだけは意図的にコード外: tfvarsに書けばstateに平文で残ってしまう。
# apply後に1回だけ手動で設定する:
#   aws cognito-idp admin-set-user-password --user-pool-id <pool> \
#     --username <email> --password '<パスワード>' --permanent

locals {
  users = [
    "yoshiharu.ishii@pocraft.net", # 管理者本人
    "claude-e2e@pocraft.net",      # E2E検証専用(自動化がパスワード再設定してよい唯一のユーザー)
  ]
}

resource "aws_cognito_user" "this" {
  for_each     = toset(local.users)
  user_pool_id = aws_cognito_user_pool.this.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = "true"
  }

  message_action = "SUPPRESS" # 招待メールは送らない

  lifecycle {
    # パスワードはTerraformの管理外(CLIで設定した値を上書きしない)
    ignore_changes = [password, temporary_password]
  }
}
