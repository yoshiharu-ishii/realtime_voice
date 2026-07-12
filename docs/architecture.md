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

    subgraph Relay["FastAPI 中継サーバー (backend/main.py)"]
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
    participant O as OpenAI Realtime
    participant S as Responses API (Web検索)

    Note over O: セッション開始時に「求人票」を渡してある<br/>tools: [web_search], tool_choice: auto
    B->>R: (音声)「ミジオロウスキーについて教えて」
    R->>O: (転送)
    O-->>R: response.done の中に function_call<br/>{name: web_search, arguments: {query}, call_id}
    R-->>B: proxy.search(🔍表示・PTTロック)
    R->>S: 検索を実行(ここは普通のPython)
    S-->>R: 検索結果テキスト
    R->>O: conversation.item.create<br/>function_call_output {call_id, 結果}
    R->>O: response.create(続きをどうぞ)
    O-->>R: 検索結果を踏まえた音声応答
    R-->>B: (転送・再生・PTTロック解除)
```

`call_id` が「どの依頼への答えか」を紐付ける伝票番号。`web_search` の中身を差し替えれば、社内DB検索でもメール送信でも同じ仕組みで動く。

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
    通話可 --> 接続中: ペルソナ切替(意図的に張り直し)
    通話可 --> ログインへ: 認証エラー(トークン期限切れ)
    ログインへ --> [*]: Cookie破棄+リロード→門番ページ

    note right of 再接続待ち
        connect()は必ず旧ソケットを
        始末してから新規接続する
        (電話は常に1本)
    end note
```

OpenAIはアイドルセッションを自発的に閉じる。PTT設計では切断は発話の合間にしか起きないため、失うものがない。「切られない努力」より「切られても平気な設計」。
