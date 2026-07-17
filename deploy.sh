#!/bin/sh
# デプロイ一発スクリプト: イメージbuild → ECRへpush → ECSサービス再起動。
# 前提: infra/service で terraform apply 済み(ECR等が存在すること)。
# Apple SiliconのビルドはARM64ネイティブ = Fargate(ARM64)にそのまま載る。
set -eu
cd "$(dirname "$0")"

REGION=$(terraform -chdir=infra/service output -raw region)
REPO=$(terraform -chdir=infra/service output -raw ecr_repo_url)
REGISTRY=${REPO%%/*}

echo "== build =="
docker build -t "$REPO:latest" .

echo "== push =="
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker push "$REPO:latest"

echo "== deploy (force new deployment) =="
aws ecs update-service --region "$REGION" \
  --cluster realtime-voice --service realtime-voice \
  --force-new-deployment --query 'service.deployments[0].status' --output text

echo "== done: $(terraform -chdir=infra/service output -raw service_url) =="
