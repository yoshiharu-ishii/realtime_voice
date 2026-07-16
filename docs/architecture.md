# アーキテクチャ図解

コードを読まなくても仕組みが分かるための図集。実装を変えたら、そのPRでこの図も更新すること。

## 1. 全体像(登場人物と通信路)

```mermaid
flowchart LR
    subgraph Browser["ブラウザ (frontend/)"]
        MIC["🎤 マイク<br/>AudioWorklet<br/>24kHz PCM16化"]
        SPK["🔊 スピーカー<br/>Web Audio再生"]
        UI["UI<br/>PTTボタン / ペルソナ / 履歴"]
    end

    subgraph Relay["FastAPI 中継サーバー (backend/ ※relay.py=中継, auth.py=認証, search.py=検索, history.py=履歴)"]
        WS["/ws WebSocket中継<br/>イベントのホワイトリスト転送"]
        AUTH["認証ゲート<br/>Cookie の IDトークンを検証"]
        DB[("SQLite<br/>chat_history.db")]
        TOOL["web_search 実行係"]
    end

    subgraph External["外部サービス"]
        OAI["OpenAI Realtime API<br/>(gpt-realtime)"]
        RESP["OpenAI Responses API<br/>(Web検索)"]
        COG["Amazon Cognito<br/>(Hosted UI / JWKS)"]
    end

    MIC -- "音声 (base64 PCM16)" --> WS
    WS -- "音声delta / 文字起こし" --> SPK
    WS <--> OAI
    TOOL --> RESP
    WS --> DB
    UI -- "ログイン(PKCE)" --> COG
    AUTH -. "署名検証(JWKS)" .-> COG

    style Relay fill:#f5f5f5,stroke:#999,color:#333
```

APIキーは中継サーバーの `.env` にのみ存在し、ブラウザには一切渡らない。

**注: この図はWebSocket(中継)回線の全体像。既定の回線はWebRTC(直結)で、その場合は音声と
イベントがブラウザ⇄OpenAI直結になり、サーバーの役割は一時キー発行・検索代行・履歴受信に
縮む(§7参照)。§2〜§6はWebSocket回線の仕組み、§7以降が回線・モード・コンテナの話。**

## 2. 中継サーバーの本質: 両耳に受話器を持つ通訳者

WebSocketは「リクエスト→レスポンス」ではなく**電話**。どちらからでも、いつでも喋れる。
中継サーバーは2本の電話を同時に持ち、2つのループを並走させる。

```mermaid
flowchart TB
    subgraph relay["relay() — 1接続ごとに1人の通訳者"]
        direction LR
        L1["browser_to_openai<br/>ブラウザの声を聞き続けるループ<br/>(ホワイトリスト検査して転送)"]
        L2["openai_to_browser<br/>OpenAIの声を聞き続けるループ<br/>(転送+履歴保存+ツール検知)"]
    end
    B["ブラウザ"] --> L1 --> O["OpenAI"]
    O --> L2 --> B
    NOTE["どちらかの電話が切れたら<br/>asyncio.wait(FIRST_COMPLETED)が検知し<br/>もう片方も必ず切る(後片付け)"]
    relay -.- NOTE
```

**不変条件: ブラウザ側も電話は常に1本だけ。** 再接続時は旧ソケットのハンドラを外して閉じてから新規接続する(これを破ると別セッションの音声・字幕が混線する)。

## 3. Push-to-Talkの1ターン(シーケンス)

```mermaid
sequenceDiagram
    participant U as ユーザー
    participant B as ブラウザ
    participant R as 中継サーバー
    participant O as OpenAI Realtime

    U->>B: ボタンを押す
    loop 押している間
        B->>R: input_audio_buffer.append (音声)
        R->>O: (そのまま転送)
    end
    U->>B: ボタンを離す
    B->>R: input_audio_buffer.commit + response.create
    R->>O: (転送)
    O-->>R: 文字起こし完了(あなたの発話)
    R-->>B: (転送・画面に表示)
    R->>R: SQLiteに保存
    loop 応答生成中
        O-->>R: response.output_audio.delta (音声)
        R-->>B: (転送・スピーカー再生)
    end
    O-->>R: response.done
    Note over B,O: サーバーVADは無効。区切りはPTTが決める。<br/>commitには最低100msの音声が必要
```

## 4. Function Calling(Web検索)の仕組み

モデルは関数を**実行できない**。「呼びたい」という構造化データを出すだけで、実行するのは中継サーバー。

```mermaid
sequenceDiagram
    participant B as ブラウザ
    participant R as 中継サーバー
    box rgb(230, 240, 255) OpenAI
    participant O as Realtime API<br/>(音声。自分では検索できない)
    participant S as Responses API<br/>(ホスト型Web検索を持つ)
    end

    Note over O: セッション開始時に「求人票」を渡してある<br/>tools: [web_search], tool_choice: auto
    B->>R: (音声)「ミジオロウスキーについて教えて」
    R->>O: (転送)
    O-->>R: response.done の中に function_call<br/>{name: web_search, arguments: {query}, call_id}
    R-->>B: proxy.search(🔍表示・PTTロック)
    R->>S: 検索APIへ橋渡し(HTTPを1本投げるだけ)
    Note over S: 実際のWeb検索と要約は<br/>OpenAI側のインフラが行う
    S-->>R: 検索結果テキスト
    R->>O: conversation.item.create<br/>function_call_output {call_id, 結果}
    R->>O: response.create(続きをどうぞ)
    O-->>R: 検索結果を踏まえた音声応答
    R-->>B: (転送・再生・PTTロック解除)
```

`call_id` が「どの依頼への答えか」を紐付ける伝票番号。中継サーバーは検索エンジンを持っておらず、「検索できない音声モデル」と「検索できるテキストAPI」の橋渡しをしているだけ。`web_search` の中身(橋渡し先)を差し替えれば、社内DB検索でもメール送信でも同じ仕組みで動く。

## 5. 認証(Cognito + 門番ページ)

未認証者にはアプリのHTMLを1バイトも返さない。`GET /` の時点でサーバーが判定する。

```mermaid
flowchart TD
    A["GET / (ブラウザ)"] --> B{"CookieのIDトークンは<br/>署名検証を通るか?"}
    B -- "有効" --> C["アプリ本体 (index.html) を返す"]
    B -- "無効/なし" --> D["門番ページ (login.html) を返す<br/>※アプリのUIを含まない白画面"]
    D --> E["PKCEを準備して<br/>Cognito Hosted UI へリダイレクト"]
    E --> F["ログイン成功 → /?code=xxx に戻る"]
    F --> G["門番ページが認可コードを<br/>トークンに交換 → Cookieへ保存"]
    G --> A
    C --> H["WebSocket接続後、最初のメッセージ<br/>proxy.auth {token} でも再検証"]

    style D fill:#fff3e0,stroke:#e65100,color:#333
```

**罠**: `/` は認証状態で返す内容が変わるため `Cache-Control: no-store` が必須。
キャッシュされた門番ページが使い回されると、有効なCookieがあってもCognitoへ飛び続ける無限ループになる(実際に起きた)。安全網としてログイン画面への往復4回で中断するガードも入っている。

## 6. 切断と再接続(切断は正常系)

```mermaid
stateDiagram-v2
    [*] --> 接続中: connect()
    接続中 --> 通話可: proxy.ready
    通話可 --> 再接続待ち: 切断(OpenAIのアイドル切断・ネットワーク断)
    再接続待ち --> 接続中: 3秒後に自動再接続
    通話可 --> 接続中: ペルソナ/回線/モード切替(意図的に張り直し)
    通話可 --> ログインへ: 認証エラー(トークン期限切れ)
    ログインへ --> [*]: Cookie破棄+リロード→門番ページ

    note right of 再接続待ち
        connect()は必ず旧ソケットを
        始末してから新規接続する
        (電話は常に1本)
    end note
```

OpenAIはアイドルセッションを自発的に閉じる。PTT設計では切断は発話の合間にしか起きないため、失うものがない。「切られない努力」より「切られても平気な設計」。

## 7. 回線の二方式: WebSocket(中継) と WebRTC(直結)

UIの「回線」リストボックスで切り替えられる。設計初日に比較した2アーキテクチャの両実装。

```mermaid
flowchart TB
    subgraph WS["WebSocket(中継) — 全てがサーバーを通る"]
        B1[ブラウザ] <-->|音声+イベント| R1[中継サーバー]
        R1 <--> O1[OpenAI Realtime]
    end
    subgraph RTC["WebRTC(直結) — 音声とイベントはブラウザ⇄OpenAI直"]
        B2[ブラウザ] <-->|"音声=メディアトラック<br/>イベント=データチャネル oai-events"| O2[OpenAI Realtime]
        B2 -.->|"①一時キー取得 ②検索の実行代行<br/>③履歴の送信"| R2[中継サーバー]
    end
```

| 観点 | WebSocket(中継) | WebRTC(直結) |
|---|---|---|
| 音声の経路 | サーバー経由(生PCM16) | ブラウザ⇄OpenAI直(Opus/RTP、ジッター耐性あり) |
| 遅延・回線の悪条件 | やや不利 | 有利 |
| サーバーから見えるもの | 全て(履歴・監査・介入が自然) | 何も見えない(履歴はブラウザからの自己申告) |
| function calling | サーバーが検知・実行 | ブラウザが検知し `/api/search` へ実行を代行依頼 |
| 履歴保存 | サーバーが自動保存 | ブラウザが `/api/history/log` へ送信 |
| APIキー | サーバーのみ | サーバーのみ(ブラウザには数分で失効する一時キー `ek_` だけ) |
| 企業プロキシ/FW | 強い(wss/443の1本) | SDP/UDP経路に依存(片通話の故障モードがある) |

接続手順(WebRTC): サーバーが `/v1/realtime/client_secrets` でペルソナ設定入りの一時キーを発行
→ ブラウザが SDP offer を `/v1/realtime/calls` へPOST → 応答音声はリモートトラック、
イベントはデータチャネルで送受信。PTTはマイクトラックの enabled 切替+clear/commitで実現。

## 8. 会話モード: PTT と ハンズフリー通話(VAD)

「モード」リストボックスで切り替え。どちらの回線(WS/WebRTC)とも組み合わせ可能。

| | 押して話す(PTT) | ハンズフリー通話(VAD) |
|---|---|---|
| 発話の区切り | ボタン(手動commit) | **サーバーVADが自動検知**(speech_started/stopped) |
| 応答の開始 | クライアントが response.create | OpenAIが自動生成 |
| バージイン | ボタン押下で response.cancel | 話し始めると自動で割り込み |
| ボタンの役割 | 押している間だけ録音 | **ミュートトグル**(クリック/スペース) |
| 音声の送信 | 押している間のみ | **常時**(ミュート・検索中を除く) |
| コスト | 話した分だけ | **無音の間も課金**(通話1時間で数ドル規模) |

VADモードの追加挙動: 検索中は自動ミュート(検索結果と新規発話の混線防止)、
WSモードでは speech_started 受信時にローカル再生を停止(バージイン)。
PTTは無線機、VADは電話——用途で使い分ける。

誤検知対策(2段構え): ブラウザ側は getUserMedia の noiseSuppression / echoCancellation /
autoGainControl、OpenAI側はセッション設定の `noise_reduction: near_field`(VADと文字起こしの
手前で入力を掃除する)。机を叩くなどの衝撃音を発話と誤認するのを抑える。両回線共通
(`build_session_config` で埋め込むため、WS中継でもWebRTC直結でも効く)。

## 9. コンテナ構成(ローカル)

「terraform applyしたらサービスが立つ」への段階1。イメージには**コードだけ**を焼き、秘密(.env)と状態(履歴DB)は外から与える。

```mermaid
flowchart LR
    subgraph Image["Dockerイメージ (uvベース Python 3.12)"]
        BE["/app/backend<br/>(uv syncで固定した依存+コード)"]
        FE["/app/frontend<br/>(静的ファイル)"]
    end
    ENV[".env<br/>(env_fileで注入・焼き込まない)"] --> BE
    VOL[("named volume chat-history<br/>→ /data/chat_history.db")] <--> BE
    U["ブラウザ :8000"] --> BE
```

- 起動: `docker compose up --build`。ネイティブ起動(`uv run uvicorn`)と同じポート8000・同じ使い勝手
- DBパスは環境変数 `DB_PATH` で外から差し替え可能(コンテナでは /data、ECS移行時はここをEFS等に付け替えるだけ)
- コンテナ内のディレクトリ配置はリポジトリと同じ(backend/ が ../frontend を参照する相対関係を維持)
- 段階2(ECS Fargate + Terraform)の論点はCLAUDE.mdのバックログ参照
