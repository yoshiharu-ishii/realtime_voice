output "region" {
  value = var.region
}

output "service_url" {
  value = "https://${var.service_domain}"
}

output "ecr_repo_url" {
  value = aws_ecr_repository.app.repository_url
}
