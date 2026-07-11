output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_env" {
  description = "アプリの .env にそのまま貼れる形式"
  value       = <<-EOT
    COGNITO_REGION=${var.region}
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.this.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.web.id}
    COGNITO_DOMAIN=https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com
  EOT
}
