# CLIで作成済みの既存リソースをTerraform管理下へ取り込むためのimport宣言。
# 初回 apply でstateに取り込まれる(取り込み後は残しておいても無害)。
# ゼロから新環境を作る場合はこのファイルを削除すること。

import {
  to = aws_cognito_user_pool.this
  id = "ap-northeast-1_7sgawXxmL"
}

import {
  to = aws_cognito_user_pool_client.web
  id = "ap-northeast-1_7sgawXxmL/2benl3jq7gpievj8p328j8bs6d"
}

import {
  to = aws_cognito_user_pool_domain.this
  id = "rtv-auth-051961177429"
}
