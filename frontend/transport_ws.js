// WebSocket回線(中継)のクライアント実装。
// 接続(connectWS)・録音チェーン(ワークレットで24kHz PCM16化して送信)・
// 応答PCM(24kHz)の再生を持つ。UIと共通イベント処理は app.js。
// このファイルは app.js より先に読み込まれ、app.js のグローバル
// (setStatus, handleServerEvent, authCfg など)を実行時に参照する。

let ws = null;

function wsReady() {
  return !!ws && ws.readyState === WebSocket.OPEN;
}

function sendEventWS(obj) {
  if (wsReady()) ws.send(JSON.stringify(obj));
}

// 旧ソケットはハンドラを外してから閉じる(残った旧セッションの応答が
// 画面や音声に混ざる事故を防ぐ。電話は常に1本)
function teardownWS() {
  if (ws) {
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(); } catch (_) {}
    ws = null;
  }
}

// ---- 再生側 ----
const playCtx = new (window.AudioContext || window.webkitAudioContext)();
let playHead = 0; // 次のチャンクを再生する時刻
let activeSources = [];

function playPcm16Base64(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer);
  if (pcm.length === 0) return;

  const buf = playCtx.createBuffer(1, pcm.length, 24000);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;

  const src = playCtx.createBufferSource();
  src.buffer = buf;
  src.connect(playCtx.destination);
  const now = playCtx.currentTime;
  if (playHead < now) playHead = now + 0.02;
  src.start(playHead);
  playHead += buf.duration;
  activeSources.push(src);
  src.onended = () => { activeSources = activeSources.filter(s => s !== src); };
}

function stopPlayback() {
  for (const src of activeSources) { try { src.stop(); } catch (_) {} }
  activeSources = [];
  playHead = 0;
}

// ---- 録音チェーン(WS回線のみ。WebRTCはマイクトラックを直接送る) ----
let captureReady = false;
let captureStream = null;
let captureCtx = null;
function teardownCapture() {
  if (captureStream) captureStream.getTracks().forEach((t) => t.stop());
  if (captureCtx) captureCtx.close().catch(() => {});
  captureStream = null;
  captureCtx = null;
  captureReady = false;
}


async function setupCapture() {
  const stream = await getMicStream();
  captureStream = stream;
  // コンテキストは必ずネイティブレートで開き、24kHz化はワークレットの
  // 面積平均リサンプラに任せる。以前は24kHz指定のコンテキストを優先していたが、
  // レート強制はOS側のデバイス設定に介入するため、iPhone連携マイク等の
  // 仮想デバイスで「実レートとラベルがずれた音声」になり、文字起こしが
  // 完全に崩壊する(こもった声・無関係な文への作話)。WebRTC回線が無事なのは
  // Chrome内蔵のWebRTCスタック(ネイティブレート)を使いこの経路を通らないため
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  console.log('capture sample rate:', ctx.sampleRate, '→ 24000 (worklet resample)');
  await ctx.audioWorklet.addModule('/static/pcm-worklet.js');
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-capture');
  node.port.onmessage = (e) => {
    const f32 = e.data;
    updateMeter(f32); // 録音中は常にレベルインジケーターを更新
    // 送信条件: PTTモード=押している間 / VADモード=ミュート解除中は常時(検索中を除く)
    const streaming = talking || (isHandsFree() && !vadMuted && !searching && !isWebRTC());
    if (!streaming || !ws || ws.readyState !== WebSocket.OPEN) return;
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 32768 : s * 32767;
    }
    sentSamples += i16.length;
    const bytes = new Uint8Array(i16.buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 8192) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: btoa(bin) }));
  };
  source.connect(node);
  captureCtx = ctx;
  captureReady = true;
  // 権限取得後はデバイスのラベルが読めるようになるので一覧を更新
  initMics();
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const persona = encodeURIComponent(personaSel.value || 'default');
  const mode = encodeURIComponent(modeSel.value || 'ptt');
  ws = new WebSocket(`${proto}://${location.host}/ws?persona=${persona}&mode=${mode}`);

  ws.onopen = () => {
    // 認証有効時は最初のメッセージでトークンを提示(サーバーが検証するまで
    // 他のイベントは受け付けられない)
    if (authCfg.enabled) {
      ws.send(JSON.stringify({ type: 'proxy.auth', token: idToken() }));
    }
    setStatus('サーバー接続OK。OpenAIへ接続中…');
  };

  ws.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    handleServerEvent(ev);
  };

  ws.onclose = () => {
    pttBtn.disabled = true;
    responseActive = false;
    if (fatalError) return; // 設定エラー時はメッセージを残し再接続しない
    setStatus('切断されました。3秒後に再接続します…', true);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 3000);
  };
  ws.onerror = () => { if (!fatalError) setStatus('WebSocketエラー', true); };
}
