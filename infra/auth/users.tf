# ユーザーアカウントもコード管理する(terraform applyでアカウントまで生える)。
# セルフサインアップ無効のため、ここに書くことが唯一の登録経路。
#
# 人間のユーザー(invite = true):
#   applyするとCognitoが一時パスワード入りの招待メールを送る(既定送信元
#   no-reply@verificationemail.com、無料枠50通/日)。初回ログイン時に
#   Hosted UIが本パスワードの設定を強制する(FORCE_CHANGE_PASSWORDフロー)。
#   管理者は最終パスワードを一切知らない。一時パスワードの有効期限は7日。
#
# 自動化ユーザー(invite = false):
#   メールは送らない(実在しない宛先へのバウンス防止)。パスワードは
#   admin-set-user-password --permanent で自動化側が設定する。

locals {
  users = {
    "yoshiharu.ishii@pocraft.net" = { invite = true }  # 管理者本人
    "claude-e2e@pocraft.net"      = { invite = false } # E2E検証専用
  }
}

resource "aws_cognito_user" "this" {
  for_each     = local.users
  user_pool_id = aws_cognito_user_pool.this.id
  username     = each.key

  attributes = {
    email          = each.key
    email_verified = "true"
  }

  # invite=true は既定動作(招待メール送信)に任せるため null
  message_action           = each.value.invite ? null : "SUPPRESS"
  desired_delivery_mediums = each.value.invite ? ["EMAIL"] : null

  lifecycle {
    # パスワードはTerraformの管理外(初回ログイン/CLIで設定した値を上書きしない)
    ignore_changes = [password, temporary_password]
  }
}
