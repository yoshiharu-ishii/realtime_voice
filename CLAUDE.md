# realtime_voice 引き継ぎ書(Claude Code用)

Push-to-Talkのリアルタイム音声通話アプリ。ブラウザ ⇄ FastAPI(WebSocket中継) ⇄ OpenAI Realtime API。
2026-07-11に1日で構築。開発記: https://pocraft.net/?p=122

## 構成

- `backend/` — FastAPI (main.py)、ペルソナ定義(personas/*.md)、uv管理(pyproject.toml)。`.env` と `chat_history.db` もここ(gitignore済み)
- `frontend/` — index.html / app.js / pcm-worklet.js / login.html。URLパスは `/static/...` のまま配信元だけこのディレクトリ
- `infra/` — Terraform(Cognito一式)。stateはローカル(gitignore済み)

## 起動と検証の約束事

- 起動: `cd backend && uv run uvicorn main:app --port 8000`
- **ポート8000はユーザーが自分のターミナルで起動する。Claudeの検証は8001を使い、終わったら必ず止める**
- 検証は必ずエンドツーエンドで: 合成音声は `say -v Kyoko -o x.aiff "…" && afconvert -f WAVE -d LEI16@24000 -c 1 x.aiff x.wav`、WebSocketテストクライアントで append→commit→response.create を流す
- 認証付きの検証: `aws cognito-idp admin-initiate-auth --auth-flow ADMIN_USER_PASSWORD_AUTH` でIDトークンを発行し、HTTPは `Authorization: Bearer`、ブラウザは `id_token` Cookieに注入。テストユーザーのパスワードが不明なら `admin-set-user-password --permanent` で再設定
- ブラウザペインはマイク権限がないため、実マイクの録音テストはユーザーに依頼する
- プレビューランチャー(launch.json)はサンドボックスがvenvを読めず使えない。Bashバックグラウンド起動+ブラウザで確認

## アーキテクチャの要点(ハマりどころ)

- **中継サーバーが全イベントを見る**設計。ツール実行(web_search)・履歴保存・認証はここに差し込む。クライアントから転送するイベントはホワイトリスト制
- **PTTなのでサーバーVADは無効**(turn_detection: null)。手動commit+response.create。commitには最低100ms必要
- **音声は24kHz PCM16**。録音は24kHz AudioContext(ブラウザ内蔵リサンプラ)が主、ワークレットの面積平均リサンプラがフォールバック。線形補間だけに戻すとエイリアシングで認識が壊れる
- **voiceは音声出力後に変更不可**。ペルソナ切替はWebSocket再接続(新セッション)で実現
- **認証**: IDトークンはCookie。`GET /` はサーバー側で検証し、未認証には門番ページ(login.html)のみ返す。**`/` にCache-Control: no-storeは必須**(消すとログイン無限ループが再発する)。WebSocketは接続後最初の `proxy.auth` メッセージで認証(URLにトークンを載せない)
- **再接続は常に1本**: connect()が旧ソケットのハンドラを外して閉じる。この不変条件を壊すと会話が混線する
- OpenAIはアイドルセッションを勝手に閉じる。切断は正常系として扱う(自動再接続あり)
- Terraform: `generate_secret` は書かない(書くとimport時にクライアント置換を強制される)。清書後は plan で No changes を確認してからapply

## 運用ループ(グローバルCLAUDE.mdにも記載)

作る → PR単位で回す(検証結果をPR本文に) → マージはユーザーの指示 → 節目でブログ化を提案(pocraft.netへ下書き投稿、公開はGO待ち) → リポジトリ公開系の操作はユーザー自身が実行

## バックログ(次のループ候補)

- 会話履歴DB(chat_history.db)を検索ツール化して「昨日何話したっけ?」に答える会話メモリRAG
- insufficient_quota(クレジット切れ)をUIで明示する
- 沈黙検知でAIから話しかける(クライアントタイマーで20秒無操作→促しイベント)
- 銀行等向けのロールプレイ研修モード(顧客役ペルソナ+承認済みペルソナのホワイトリスト化)
- 次の新規アプリは初日からコンテナ化(uvベースDockerfile→compose→ECS Fargate+Terraform。ALBはidle_timeout 300s以上、uvicornのws pingがそれより短いこと)
