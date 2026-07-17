# realtime_voice のサービス基盤 (ECS Fargate + ALB + ACM + Route53 + EFS)
#
# 目標:「terraform apply したらサービスが立つ」。
# 前提: Route53 に var.zone_name のホストゾーンが既にあること。
# イメージの build/push は初回のECR作成後に ../deploy.sh で行う
# (以後のデプロイも deploy.sh 一発: build → push → サービス再起動)。

# --- 参照: DNSゾーンとデフォルトVPC ---
# ネットワークはデフォルトVPCを使う(デモ用途・追加コスト最小。
# サブネットはパブリックなので、タスクにはパブリックIPを付与しNATを不要にする)

data "aws_route53_zone" "main" {
  name = var.zone_name
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# --- ECR (コンテナイメージ置き場) ---

resource "aws_ecr_repository" "app" {
  name         = "realtime-voice"
  force_delete = true # デモ用途: destroy時にイメージごと消せるように
}

# --- ACM証明書 (DNS検証。マイク=getUserMediaはhttps必須) ---

resource "aws_acm_certificate" "app" {
  domain_name       = var.service_domain
  validation_method = "DNS"
  tags              = { Name = "realtime-voice" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.app.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }
  zone_id = data.aws_route53_zone.main.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# --- セキュリティグループ (ALB→タスク→EFS の一方向) ---

resource "aws_security_group" "alb" {
  name        = "realtime-voice-alb"
  description = "ALB: internet to 80 and 443"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name        = "realtime-voice-task"
  description = "ECS task: ALB to 8000"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "efs" {
  name        = "realtime-voice-efs"
  description = "EFS: task to 2049"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }
}

# --- ALB (WebSocket対応。idle_timeoutは約束事の300s以上) ---

resource "aws_lb" "app" {
  name               = "realtime-voice"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
  # uvicornのws ping(既定20s)がこれより十分短いこと。
  # これより短いとVADモードの長い無音や検索待ちでwsが切られる
  idle_timeout = 400
}

resource "aws_lb_target_group" "app" {
  name        = "realtime-voice"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip" # Fargateはipターゲット
  vpc_id      = data.aws_vpc.default.id

  health_check {
    path    = "/api/auth/config" # 認証不要で常に200を返す軽いJSON
    matcher = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  # httpはhttpsへ301(マイク許可はhttpsでしか出ない)
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.app.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.service_domain
  type    = "A"

  alias {
    name                   = aws_lb.app.dns_name
    zone_id                = aws_lb.app.zone_id
    evaluate_target_health = false
  }
}

# --- EFS (chat_history.db の永続化。DB_PATH=/data/... をここに向ける) ---

resource "aws_efs_file_system" "history" {
  creation_token = "realtime-voice-history"
  encrypted      = true
  tags = { Name = "realtime-voice-history" } # コンソールで無名表示にならないように
}

resource "aws_efs_mount_target" "history" {
  for_each        = toset(data.aws_subnets.default.ids)
  file_system_id  = aws_efs_file_system.history.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

# コンテナはroot実行のため、uid/gid 0 のアクセスポイントで /history を切る
resource "aws_efs_access_point" "history" {
  file_system_id = aws_efs_file_system.history.id
  tags           = { Name = "realtime-voice-history" }

  posix_user {
    uid = 0
    gid = 0
  }
  root_directory {
    path = "/history"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

# --- 秘密 (OPENAI_API_KEYのみ。COGNITO_*は公開情報なので平文envで渡す) ---

resource "aws_ssm_parameter" "openai_api_key" {
  name  = "/realtime-voice/openai-api-key"
  type  = "SecureString"
  value = var.openai_api_key
}

# --- IAM ---

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# 実行ロール: イメージpull・ログ出力・SSMからの秘密取得(ECS基盤側が使う)
resource "aws_iam_role" "execution" {
  name               = "realtime-voice-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_base" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_ssm" {
  name = "read-openai-key"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = [aws_ssm_parameter.openai_api_key.arn]
    }]
  })
}

# タスクロール: アプリ自体はAWS APIを呼ばないので空のロール
resource "aws_iam_role" "task" {
  name               = "realtime-voice-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# --- CloudWatch Logs ---

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/realtime-voice"
  retention_in_days = 30
}

# --- ECS (Fargate / ARM64。Apple Silicon Macのdocker buildをそのままpushできる) ---

resource "aws_ecs_cluster" "app" {
  name = "realtime-voice"
}

resource "aws_ecs_task_definition" "app" {
  family                   = "realtime-voice"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  volume {
    name = "history"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.history.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.history.id
        iam             = "DISABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "app"
    image     = "${aws_ecr_repository.app.repository_url}:latest"
    essential = true
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    mountPoints = [{
      sourceVolume  = "history"
      containerPath = "/data"
    }]
    environment = [
      { name = "DB_PATH", value = "/data/chat_history.db" },
      { name = "COGNITO_REGION", value = var.region },
      { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.this.id },
      { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.web.id },
      { name = "COGNITO_DOMAIN", value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com" },
    ]
    secrets = [
      { name = "OPENAI_API_KEY", valueFrom = aws_ssm_parameter.openai_api_key.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "app"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = "realtime-voice"
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true # デフォルトVPC(パブリックサブネット)でNATなしにECR/OpenAIへ出るため
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 60

  depends_on = [aws_lb_listener.https]
}
