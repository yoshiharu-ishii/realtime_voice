// WebRTC直結モード。
// WebSocket中継と違い、音声もイベントもブラウザ⇄OpenAI直結。
// サーバーは一時キー発行・検索代行(/api/search)・履歴受け取り(/api/history/log)のみ。
// このファイルは app.js より先に読み込まれ、app.js のグローバル
// (setStatus, handleServerEvent, authHeaders, personaSel など)を実行時に参照する。

const transportSel = document.getElementById('transport');
const modeSel = document.getElementById('mode');
const remoteAudioEl = document.getElementById('remoteAudio');

let pc = null; // RTCPeerConnection
let dc = null; // データチャネル "oai-events"
let rtcStream = null; // マイクのMediaStream
let rtcReady = false;
let rtcSessionId = '';
let rtcMeterCtx = null;

function isWebRTC() {
  return transportSel.value === 'webrtc';
}

function isHandsFree() {
  return modeSel.value === 'vad';
}

function webrtcReady() {
  return rtcReady;
}

// PTT: 押している間だけマイクトラックを有効化(無効中は無音が流れる。
// 押した瞬間に input_audio_buffer.clear するので無音は捨てられる)
function webrtcSetMic(on) {
  const track = rtcStream?.getAudioTracks()[0];
  if (track) track.enabled = on;
}

function sendEventRTC(obj) {
  if (dc && dc.readyState === 'open') dc.send(JSON.stringify(obj));
}

async function connectWebRTC() {
  // 世代ガード: connect()のたびにconnectSeqが増える。await明けに世代が
  // 変わっていたら、自分は追い越された古い接続要求なので、自分が作った
  // 資源だけ片付けて黙って退場する(グローバルには触らない)。
  // これがないと、素早い切替時に古い処理が新しい接続のpc/dcを上書き破壊する
  const seq = connectSeq;
  const stale = () => seq !== connectSeq;

  // 1) サーバーからペルソナ設定入りの一時キーをもらう(APIキーは受け取らない)
  setStatus('一時キーを取得中…');
  let secret;
  try {
    const persona = encodeURIComponent(personaSel.value || 'default');
    const mode = encodeURIComponent(modeSel.value || 'ptt');
    const res = await fetch(`/api/webrtc/secret?persona=${persona}&mode=${mode}`, {
      headers: authHeaders(),
    });
    if (stale()) return;
    if (res.status === 401) { requireLogin(); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    secret = await res.json();
    if (stale()) return;
  } catch (err) {
    if (stale()) return;
    setStatus(`一時キーの取得に失敗: ${err.message}`, true);
    scheduleRtcReconnect();
    return;
  }

  // 2) マイク取得(WSモードと同じ制約: 選択デバイス・voiceIsolation等)
  let stream;
  try {
    stream = await getMicStream();
  } catch (err) {
    if (stale()) return;
    setStatus(`マイクを使用できません: ${err.message}`, true);
    return;
  }
  if (stale()) { // 追い越された: 取ったマイクは自分で返す
    stream.getTracks().forEach((t) => t.stop());
    return;
  }
  rtcStream = stream;
  webrtcSetMic(false); // PTT: 押すまで送らない
  initMics(); // 権限取得後はデバイス名が読めるので一覧更新

  // 3) PeerConnection: 送り=マイクトラック、受け=リモート音声トラック
  const myPc = new RTCPeerConnection();
  pc = myPc;
  myPc.ontrack = (e) => { if (!stale()) remoteAudioEl.srcObject = e.streams[0]; };
  myPc.addTrack(stream.getAudioTracks()[0], stream);
  attachRtcMeter(stream);

  // 4) イベントはデータチャネル "oai-events" でJSONそのまま
  const myDc = myPc.createDataChannel('oai-events');
  dc = myDc;
  myDc.onmessage = (e) => {
    if (stale()) return; // 古いセッションのイベントは表示に混ぜない
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    maybeHandleFunctionCalls(ev); // function callingはブラウザが自前処理
    handleServerEvent(ev); // 表示系はWSモードと共通ハンドラ
  };
  myDc.onopen = () => {
    if (stale()) return;
    rtcReady = true;
    setStatus(`接続完了 (model: ${secret.model} / ペルソナ: ${secret.persona} / WebRTC直結)`);
    pttBtn.disabled = false;
    // ハンズフリー通話モードなら即マイクON(ミュート中を除く)。
    // バージイン(応答中の割り込み)はserver_vadがOpenAI側で処理する
    if (isHandsFree()) webrtcSetMic(!vadMuted);
    updatePttUi();
  };
  myPc.onconnectionstatechange = () => {
    if (stale()) return;
    if (['failed', 'disconnected', 'closed'].includes(myPc.connectionState) && rtcReady) {
      rtcReady = false;
      pttBtn.disabled = true;
      scheduleRtcReconnect();
    }
  };

  // 5) SDP交換(セキュアブラウザ環境で片通話になり得るのはこの経路)
  const offer = await myPc.createOffer();
  if (stale()) { try { myPc.close(); } catch (_) {} return; }
  await myPc.setLocalDescription(offer);
  if (stale()) { try { myPc.close(); } catch (_) {} return; }
  setStatus('OpenAIへ接続中…(WebRTC)');
  let sdpRes;
  try {
    sdpRes = await fetch(
      `https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(secret.model)}`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${secret.value}`, 'Content-Type': 'application/sdp' },
        body: offer.sdp,
      }
    );
  } catch (err) {
    if (stale()) { try { myPc.close(); } catch (_) {} return; }
    setStatus(`WebRTC接続に失敗: ${err.message}`, true);
    scheduleRtcReconnect();
    return;
  }
  if (stale()) { try { myPc.close(); } catch (_) {} return; }
  if (!sdpRes.ok) {
    setStatus(`WebRTC接続に失敗: HTTP ${sdpRes.status}`, true);
    scheduleRtcReconnect();
    return;
  }
  const answerSdp = await sdpRes.text();
  if (stale()) { try { myPc.close(); } catch (_) {} return; }
  await myPc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

  // 履歴用のセッションID(WebRTCではサーバーが会話を見ないため、ブラウザが採番して送る)
  rtcSessionId =
    new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15) +
    '-rtc' + Math.random().toString(36).slice(2, 6);
}

function scheduleRtcReconnect() {
  if (fatalError) return;
  setStatus('切断されました。3秒後に再接続します…', true);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 3000);
}

function teardownWebRTC() {
  rtcReady = false;
  if (dc) { try { dc.close(); } catch (_) {} dc = null; }
  if (pc) {
    pc.onconnectionstatechange = null;
    try { pc.close(); } catch (_) {}
    pc = null;
  }
  if (rtcStream) {
    rtcStream.getTracks().forEach((t) => t.stop());
    rtcStream = null;
  }
  if (rtcMeterCtx) { rtcMeterCtx.close().catch(() => {}); rtcMeterCtx = null; }
  if (remoteAudioEl) remoteAudioEl.srcObject = null;
}

// レベルインジケーター(WSモードのワークレットの代わりにAnalyserNodeで)
function attachRtcMeter(stream) {
  rtcMeterCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = rtcMeterCtx.createMediaStreamSource(stream);
  const analyser = rtcMeterCtx.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const loop = () => {
    if (!rtcMeterCtx || rtcMeterCtx.state === 'closed') return;
    analyser.getFloatTimeDomainData(buf);
    updateMeter(buf);
    requestAnimationFrame(loop);
  };
  loop();
}

// function calling: WebRTCモードではイベントがブラウザに直接届くので、
// 検索の実行だけサーバー(/api/search)へ代行依頼し、結果をデータチャネルで返す
async function maybeHandleFunctionCalls(ev) {
  if (ev.type !== 'response.done') return;
  const calls = (ev.response?.output || []).filter(
    (i) => i.type === 'function_call' && i.name === 'web_search'
  );
  if (!calls.length) return;
  for (const call of calls) {
    let query = '';
    try { query = JSON.parse(call.arguments || '{}').query || ''; } catch (_) {}
    setStatus(`🔍 Web検索中: ${query}`);
    appendTurn('🔍 検索').textContent = query;
    setSearchLock(true);
    logHistory('search', query);
    let result = '検索に失敗しました';
    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query }),
      });
      if (res.ok) result = (await res.json()).result;
    } catch (_) { /* resultは失敗文言のまま */ }
    sendEventRTC({
      type: 'conversation.item.create',
      item: { type: 'function_call_output', call_id: call.call_id, output: result },
    });
  }
  sendEventRTC({ type: 'response.create' }); // 検索結果を踏まえた応答を再開
}

// 履歴: WebRTCモードでは中継サーバーが会話を見ないため、ブラウザから送る
function logHistory(role, text) {
  if (!text || !isWebRTC()) return;
  fetch('/api/history/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      session_id: rtcSessionId,
      role,
      text,
      persona: personaSel.selectedOptions[0]?.textContent || '',
    }),
  }).catch(() => { /* 履歴は補助機能なので失敗しても会話は止めない */ });
}
