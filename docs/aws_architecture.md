# AWS構成

本番環境の構成。すべて Terraform (`infra/`) で管理しており、`terraform apply` 一発で下記の全体が立ち上がり、ターゲット指定の `terraform destroy` で片付く。再現性は検証済み: サービス層全体を実際に破壊→ゼロから再構築し、手作業なしで復旧することを確認している。

稼働URL: **https://voice.pocraft.net**

## 全体図

```mermaid
flowchart TB
    subgraph Internet["インターネット"]
        U["ブラウザ<br/>(マイクはHTTPS必須)"]
    end

    subgraph AWS["AWS (ap-northeast-1)"]
        R53["Route53<br/>Aレコード(alias): voice.pocraft.net"]
        ACM["ACM証明書<br/>(DNS検証・自動更新)"]

        subgraph VPC["デフォルトVPC (パブリックサブネット)"]
            ALB["Application Load Balancer<br/>:443 HTTPS / :80は301<br/>idle_timeout = 400s"]
            TASK["ECS Fargateタスク (ARM64)<br/>0.25 vCPU / 512 MB<br/>uvicorn :8000"]
            EFS[("EFS<br/>/data/chat_history.db")]
        end

        ECR["ECR<br/>realtime-voice:latest"]
        SSM["SSM Parameter Store<br/>SecureString: OPENAI_API_KEY"]
        CW["CloudWatch Logs<br/>/ecs/realtime-voice (30日)"]
        COG["Cognito User Pool<br/>Hosted UI + PKCE"]
    end

    OAI["OpenAI Realtime API"]

    U -->|"DNS"| R53 --> ALB
    ACM -.->|"TLS証明書"| ALB
    ALB -->|"HTTP/WebSocket :8000"| TASK
    TASK <--> EFS
    TASK -.->|"イメージpull(起動時)"| ECR
    TASK -.->|"秘密の注入(起動時)"| SSM
    TASK --> CW
    TASK <-->|"WebSocket回線(中継)"| OAI
    U <-.->|"WebRTC回線: 音声/イベント直結"| OAI
    U -.->|"ログイン(PKCE)"| COG
    TASK -.->|"JWT検証(JWKS)"| COG
```

## コンポーネントと設計判断

| コンポーネント | 選択 | 理由 |
|---|---|---|
| コンピュート | ECS Fargate、ARM64、0.25vCPU/512MB | サーバー管理ゼロ。ARM64はApple Siliconの`docker build`がそのまま載り(クロスビルド不要)、料金も安い |
| ネットワーク | デフォルトVPC、パブリックサブネット、タスクにパブリックIP | NATゲートウェイ代ゼロ(デモ用途では十分)。タスクへはALBのSG経由でしか届かない |
| TLS/ドメイン | ACM証明書 + Route53 alias | `getUserMedia`(マイク)はセキュア文脈でしか動かない——HTTPSは飾りではなく**必須要件** |
| ロードバランサ | ALB、`idle_timeout = 400s` | uvicornのWebSocket ping間隔(既定20s)より必ず長くする。逆転するとハンズフリー(VAD)モードの長い無音でソケットが切られる |
| 履歴DB | EFS上のSQLite(`/data`にマウント) | アプリはローカル開発と無変更——`DB_PATH`の向き先が変わるだけ。タスクの再起動・再デプロイをまたいで残る |
| 秘密 | SSM SecureString → タスク起動時にECSが注入 | [deployment.md](deployment.md#シークレット) 参照 |
| 認証 | 既存のCognito User Pool(Terraformの別レイヤー) | サービス層のteardownがユーザーアカウントに触れない分離。アプリクライアントのコールバックURLに `https://voice.pocraft.net/` を追加済み |
| ログ | CloudWatch Logs、保持30日 | Fargateにおける`docker logs`相当 |

## セキュリティグループ(一方向の連鎖)

```mermaid
flowchart LR
    NET["0.0.0.0/0"] -->|":80, :443"| SGALB["sg: realtime-voice-alb"]
    SGALB -->|":8000"| SGTASK["sg: realtime-voice-task"]
    SGTASK -->|":2049 (NFS)"| SGEFS["sg: realtime-voice-efs"]
```

各ホップは1つ前のセキュリティグループからの通信しか受けない。タスクとファイルシステムにはインターネットから直接届かない。

## データの通信経路

### 音声の経路は回線で全く違う

**WebRTC回線(既定) — 音声はAWSを通らない。** ALBを通るのは制御系のHTTPSだけ。

```mermaid
flowchart LR
    B["ブラウザ"] <==>|"音声: Opus / DTLS-SRTP (UDP)<br/>イベント: データチャネル"| OAI["OpenAI Realtime"]
    B -.->|"HTTPS: ①一時キー取得<br/>②検索の実行代行 ③履歴送信"| ALB["ALB :443"]
    ALB -.-> TASK["Fargateタスク"]
    TASK -.->|"履歴を書き込み"| EFS[("EFS")]
```

**WebSocket回線 — すべてがAWSを通る。** `idle_timeout` のルールが守っているのはこちら。

```mermaid
flowchart LR
    B["ブラウザ"] <-->|"WSS :443<br/>音声=base64 PCM16"| ALB["ALB<br/>(TLS終端)"]
    ALB <-->|"HTTP/WS :8000<br/>(VPC内・平文)"| TASK["Fargateタスク<br/>(中継+履歴+検索)"]
    TASK <-->|"WSS :443"| OAI["OpenAI Realtime"]
    TASK -->|"NFS :2049<br/>(TLS: 転送時暗号化)"| EFS[("EFS")]
```

### 経路とプロトコルの一覧

| データ | 経路 | プロトコル / ポート | 暗号化 |
|---|---|---|---|
| 音声(WebRTC回線) | ブラウザ ⇄ OpenAI 直結 | Opus over SRTP (UDP)、SDP交換はHTTPS | DTLS-SRTP |
| イベント(WebRTC回線) | ブラウザ ⇄ OpenAI 直結 | WebRTCデータチャネル `oai-events` | DTLS |
| 音声・イベント(WS回線) | ブラウザ → ALB → タスク → OpenAI | WSS:443 → HTTP/WS:8000 → WSS:443 | ALBでTLS終端。**ALB→タスク間はVPC内の平文**(SGでALBからのみ許可) |
| ログイン | ブラウザ ⇄ Cognito Hosted UI | HTTPS(認可コード+PKCE) | TLS。トークンはCookie(ブラウザ)のみ |
| JWT検証 | タスク → Cognito | HTTPS(JWKS取得、キャッシュあり) | TLS |
| Web検索(function calling) | タスク → OpenAI Responses API | HTTPS | TLS |
| 履歴(WS回線) | タスクがEFSへ直接書き込み | NFS:2049 | EFS転送時暗号化(TLS)+保存時暗号化 |
| 履歴(WebRTC回線) | ブラウザ → ALB → タスク → EFS | HTTPS `/api/history/log` → NFS | TLS → EFS暗号化 |
| OpenAI APIキー | SSM → タスク(起動時のみ) | HTTPS | TLS+KMS。ブラウザには一時キー`ek_`のみ(数分で失効) |
| イメージ | ECR → タスク(起動時のみ) | HTTPS | TLS |

補足: ALB→タスク間を平文HTTPにしているのは意図的な割り切り(VPC内・SGでALB以外から到達不可)。end-to-endのTLSが要件になったらタスク側に証明書を持たせるかService Connectを検討する。

## IAMロール

- **実行ロール**: ECRからのpull、CloudWatch Logsへの書き込み、SSMパラメータ**1個だけ**(OpenAIキー)の読み取り。使うのはECS基盤側で、アプリではない
- **タスクロール**: 空。アプリは実行時にAWSのAPIを一切呼ばない

## コスト(目安)

ALB 約$20/月 + Fargate(1タスク、0.25vCPU ARM64) 約$9/月 + EFS/ログ/Route53 数ドル → **月$30前後**。コンピュートだけ止めるならサービスのdesired countを0に(ALB代は残る)。完全撤収は [deployment.md](deployment.md#teardown完全削除) 参照。
