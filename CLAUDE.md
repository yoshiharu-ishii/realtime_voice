# realtime_voice 引き継ぎ書(Claude Code用)

Push-to-Talkのリアルタイム音声通話アプリ。ブラウザ ⇄ FastAPI(WebSocket中継) ⇄ OpenAI Realtime API。
2026-07-11に1日で構築。開発記: https://pocraft.net/?p=122

**仕組みの図解: `docs/architecture.md`(Mermaid)。ユーザーはコードを読まず図で把握するスタイルなので、実装を変えるPRでは必ず該当する図も更新すること。**

## 構成

- `backend/` — FastAPI。モジュール分割済み: `main.py`(組み立て+ルーティングのみ) / `config.py`(環境変数) / `auth.py`(Cognito) / `relay.py`(WebSocket中継の本体) / `webrtc.py`(WebRTC用一時キー発行) / `personas.py` / `history.py`(SQLite) / `search.py`(web_searchツール)。ペルソナ定義(personas/*.md)、uv管理(pyproject.toml)、`.env` と `chat_history.db` もここ(gitignore済み)
- 回線は2方式: WebSocket中継(`relay.py`+`app.js`)とWebRTC直結(`webrtc.py`+`frontend/webrtc.js`)。UIの「回線」で切替。WebRTCではfunction callingと履歴保存を**ブラウザ側**が `/api/search` `/api/history/log` 経由で行う(docs/architecture.md §7)
- `frontend/` — index.html / app.js / pcm-worklet.js / login.html。URLパスは `/static/...` のまま配信元だけこのディレクトリ
- `infra/` — Terraform(Cognito一式)。stateはローカル(gitignore済み)

## 起動と検証の約束事

- 起動: `cd backend && uv run uvicorn main:app --port 8000`、またはコンテナで `docker compose up --build`(同じく8000。履歴は名前付きボリューム chat-history の /data に永続化、DBパスは環境変数 `DB_PATH` で制御)
- **ポート8000はユーザーが自分のターミナルで起動する。Claudeの検証は8001を使い、終わったら必ず止める**。コンテナ検証は `docker run -p 8001:8000 --env-file backend/.env <image>`
- **検証前に `lsof -nP -i :8001` で幽霊プロセスがいないか確認する**(過去に、殺し損ねた旧コードのuvicornが8001に居座り、コンテナ検証のつもりが幽霊に接続していて結果が全部嘘だった事故がある。コンテナ検証では `docker logs` に接続ログが出ていることも合わせて確認する)
- 認証付きE2Eには**専用テストユーザー claude-e2e@pocraft.net を使う**(admin-set-user-passwordで再設定してよいのはこのユーザーだけ。本人アカウントのパスワードを触るとログイン不能になる事故が起きた)
- 検証は必ずエンドツーエンドで: 合成音声は `say -v Kyoko -o x.aiff "…" && afconvert -f WAVE -d LEI16@24000 -c 1 x.aiff x.wav`、WebSocketテストクライアントで append→commit→response.create を流す
- 認証付きの検証: `aws cognito-idp admin-initiate-auth --auth-flow ADMIN_USER_PASSWORD_AUTH` でIDトークンを発行し、HTTPは `Authorization: Bearer`、ブラウザは `id_token` Cookieに注入。テストユーザーのパスワードが不明なら `admin-set-user-password --permanent` で再設定
- ブラウザペインはマイク権限がないため、実マイクの録音テストはユーザーに依頼する。ただし**getUserMediaを差し替えれば偽マイクでE2E可能**: `AudioContext`+`createMediaStreamDestination()` のstreamを返すよう`navigator.mediaDevices.getUserMedia`を上書きし、sayで作ったWAVを`AudioBufferSourceNode`でdestへ再生するとWebRTC経由でも文字起こしまで検証できる(テスト用WAVは一時的にfrontend/へ置いて配信し、終わったら削除)。**罠2つ**: (1) getUserMediaは呼ばれるたびに新しいdestを返すこと(同じstreamを返すと再接続時にstop済みの死んだトラックを配ってしまう) (2) destには**ゲイン0の発振器を常時接続しておく**こと(ソースが繋がっていない間はフレームが生成されず、WebRTCのRTPパケットが止まりserver_vadが凍る)
- プレビューランチャー(launch.json)はサンドボックスがvenvを読めず使えない。Bashバックグラウンド起動+ブラウザで確認

## アーキテクチャの要点(ハマりどころ)

- **中継サーバーが全イベントを見る**設計。ツール実行(web_search)・履歴保存・認証はここに差し込む。クライアントから転送するイベントはホワイトリスト制
- 会話モードは2つ(UIの「モード」、両回線と直交): **PTT**=turn_detection null+手動commit(最低100ms)+response.create / **VAD(ハンズフリー)**=server_vadが自動検知・自動応答・自動バージイン。VADは常時送信=無音も課金。ボタンはVADではミュートトグルになる。検索中は自動ミュート
- 会話の文脈はOpenAIセッション内にあり、**再接続(ペルソナ/回線/モード切替・自動再接続)のたびにリセット**される。履歴DBから conversation.item.create でテキスト注入すれば文脈復元が可能(未実装・バックログ)
- **音声は24kHz PCM16**。録音はネイティブレートのAudioContext+ワークレットの面積平均リサンプラで24kHz化する。**24kHz強制のAudioContextは使わない**(レート強制はOSのデバイス設定に介入し、iPhone連携マイク等の仮想デバイスで音声が壊れ、文字起こしが崩壊した)。線形補間だけに戻すのもエイリアシングで認識が壊れるため不可
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
- **ECS Fargate + Terraformでのサービス化**(ローカルコンテナ化は完了)。目標は「terraform applyしたらサービスが立つ」。論点: マイク(getUserMedia)はhttps必須なのでACM証明書+ドメイン、CognitoリダイレクトURI追加、履歴DBの置き場(EFSかDynamoDB)、ALBはidle_timeout 300s以上でuvicornのws pingがそれより短いこと
