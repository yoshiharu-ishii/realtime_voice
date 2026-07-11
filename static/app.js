// Push-to-Talk クライアント
// 押している間: マイク音声を PCM16(24kHz) で WebSocket に送信
// 離した時: input_audio_buffer.commit + response.create で応答を要求
// 応答音声: PCM16(24kHz) の delta を Web Audio で順次再生

const statusEl = document.getElementById('status');
const pttBtn = document.getElementById('ptt');
const transcriptEl = document.getElementById('transcript');

let ws = null;
let talking = false;
let fatalError = false; // APIキー未設定など、再接続しても直らないエラー
let responseActive = false; // AIの応答が進行中か(response.cancel の送信判断に使う)
let personaChanging = false; // ペルソナ切替による意図的な再接続中か
const personaSel = document.getElementById('persona');
let sentSamples = 0; // commit には最低 100ms(2400サンプル)必要

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

// ---- 録音側 ----
let captureReady = false;
let captureStream = null;
let captureCtx = null;
const micSel = document.getElementById('mic');

function teardownCapture() {
  if (captureStream) captureStream.getTracks().forEach((t) => t.stop());
  if (captureCtx) captureCtx.close().catch(() => {});
  captureStream = null;
  captureCtx = null;
  captureReady = false;
}

async function setupCapture() {
  const constraints = {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  if (micSel.value) constraints.deviceId = { exact: micSel.value };
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
  } catch (err) {
    if (!micSel.value) throw err;
    // 選択したマイクが外された等で使えない場合は既定のマイクにフォールバック
    delete constraints.deviceId;
    micSel.value = '';
    localStorage.removeItem('micId');
    stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    setStatus('選択したマイクが使えないため、既定のマイクに切り替えました');
  }
  captureStream = stream;
  // 24kHz指定でコンテキストを作れれば、ブラウザ内蔵の高品質リサンプラが
  // マイク入力を変換してくれる(ワークレット側の簡易リサンプラより高精度)
  let ctx;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
  } catch (_) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
  }
  console.log('capture sample rate:', ctx.sampleRate);
  await ctx.audioWorklet.addModule('/static/pcm-worklet.js');
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-capture');
  node.port.onmessage = (e) => {
    if (!talking || !ws || ws.readyState !== WebSocket.OPEN) return;
    const f32 = e.data;
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

// ---- マイク選択 ----
async function initMics() {
  let devices;
  try {
    devices = await navigator.mediaDevices.enumerateDevices();
  } catch (_) {
    return; // 列挙できない環境では「既定のマイク」のみ
  }
  const mics = devices.filter((d) => d.kind === 'audioinput');
  const current = micSel.value;
  micSel.innerHTML = '<option value="">既定のマイク</option>';
  mics.forEach((d, i) => {
    if (!d.deviceId || d.deviceId === 'default') return;
    const opt = document.createElement('option');
    opt.value = d.deviceId;
    opt.textContent = d.label || `マイク ${i + 1}`;
    micSel.appendChild(opt);
  });
  const saved = current || localStorage.getItem('micId') || '';
  if ([...micSel.options].some((o) => o.value === saved)) micSel.value = saved;
}

micSel.addEventListener('change', () => {
  if (micSel.value) localStorage.setItem('micId', micSel.value);
  else localStorage.removeItem('micId');
  // 次にボタンを押したとき、選択したマイクで録音チェーンを作り直す
  teardownCapture();
  const label = micSel.selectedOptions[0]?.textContent || '既定のマイク';
  setStatus(`マイクを「${label}」に切り替えました`);
});

if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener('devicechange', initMics);
}
initMics();

// ---- 文字起こし表示 ----
let currentAiTurn = null;
let currentUserTurn = null;

function appendTurn(who, cls) {
  const div = document.createElement('div');
  div.className = 'turn';
  div.innerHTML = `<span class="who">${who}</span> <span class="text"></span>`;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return div.querySelector('.text');
}

// ---- WebSocket ----
function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.className = isError ? 'error' : '';
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const persona = encodeURIComponent(personaSel.value || 'default');
  ws = new WebSocket(`${proto}://${location.host}/ws?persona=${persona}`);

  ws.onopen = () => setStatus('サーバー接続OK。OpenAIへ接続中…');

  ws.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }

    switch (ev.type) {
      case 'proxy.ready':
        setStatus(`接続完了 (model: ${ev.model} / ペルソナ: ${ev.persona || '標準'})`);
        pttBtn.disabled = false;
        break;
      case 'proxy.search': {
        setStatus(`🔍 Web検索中: ${ev.query}`);
        appendTurn('🔍 検索').textContent = ev.query;
        break;
      }
      case 'proxy.error':
        fatalError = true;
        setStatus(`${ev.message} (修正後にページを再読み込みしてください)`, true);
        pttBtn.disabled = true;
        break;
      case 'error':
        console.error('OpenAI error:', ev);
        // キャンセル対象なしはタイミング起因で起こり得る無害なエラーなので表示しない
        if (!`${ev.error?.message}`.includes('no active response')) {
          setStatus(`エラー: ${ev.error?.message || JSON.stringify(ev)}`, true);
        }
        break;
      // 応答音声 (GA / beta 両対応)
      case 'response.output_audio.delta':
      case 'response.audio.delta':
        playPcm16Base64(ev.delta);
        break;
      // 自分の発話の文字起こし
      case 'conversation.item.input_audio_transcription.delta':
        if (!currentUserTurn) currentUserTurn = appendTurn('あなた');
        if (currentUserTurn.dataset.pending) {
          currentUserTurn.textContent = '';
          delete currentUserTurn.dataset.pending;
        }
        currentUserTurn.textContent += ev.delta;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
        break;
      case 'conversation.item.input_audio_transcription.completed':
        if (!currentUserTurn) currentUserTurn = appendTurn('あなた');
        currentUserTurn.textContent = ev.transcript;
        delete currentUserTurn.dataset.pending;
        currentUserTurn = null;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
        break;
      case 'conversation.item.input_audio_transcription.failed':
        if (currentUserTurn) currentUserTurn.textContent = '(文字起こしに失敗しました)';
        currentUserTurn = null;
        break;
      // 応答の文字起こし
      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta':
        if (!currentAiTurn) currentAiTurn = appendTurn('AI');
        currentAiTurn.textContent += ev.delta;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
        break;
      case 'response.done':
        responseActive = false;
        currentAiTurn = null;
        setStatus('待機中(ボタンを押して話してください)');
        break;
      case 'response.created':
        responseActive = true;
        setStatus('AIが応答中…');
        break;
    }
  };

  ws.onclose = () => {
    pttBtn.disabled = true;
    responseActive = false;
    if (personaChanging) { // ペルソナ切替時は即座に繋ぎ直す
      personaChanging = false;
      connect();
      return;
    }
    if (fatalError) return; // 設定エラー時はメッセージを残し再接続しない
    setStatus('切断されました。3秒後に再接続します…', true);
    setTimeout(connect, 3000);
  };
  ws.onerror = () => { if (!fatalError) setStatus('WebSocketエラー', true); };
}

// ---- Push-to-Talk 操作 ----
async function startTalking() {
  if (talking || pttBtn.disabled || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (playCtx.state === 'suspended') await playCtx.resume();
  if (!captureReady) {
    setStatus('マイク準備中…');
    try {
      await setupCapture();
    } catch (err) {
      setStatus(`マイクを使用できません: ${err.message}`, true);
      return;
    }
  }
  // 割り込み: 再生を止め、応答が進行中の場合のみキャンセルを送る
  stopPlayback();
  if (responseActive) {
    ws.send(JSON.stringify({ type: 'response.cancel' }));
    responseActive = false;
  }
  ws.send(JSON.stringify({ type: 'input_audio_buffer.clear' }));
  sentSamples = 0;
  talking = true;
  pttBtn.classList.add('talking');
  pttBtn.textContent = '🔴 録音中… 離すと送信';
  setStatus('録音中…');
}

function stopTalking() {
  if (!talking) return;
  talking = false;
  pttBtn.classList.remove('talking');
  pttBtn.textContent = '押している間だけ話す(またはスペースキー長押し)';
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (sentSamples < 2400) { // 100ms 未満は commit がエラーになるため破棄
    ws.send(JSON.stringify({ type: 'input_audio_buffer.clear' }));
    setStatus('短すぎました。もう一度押して話してください');
    return;
  }
  ws.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
  ws.send(JSON.stringify({ type: 'response.create' }));
  currentUserTurn = appendTurn('あなた');
  currentUserTurn.textContent = '(文字起こし中…)';
  currentUserTurn.dataset.pending = '1';
  setStatus('送信しました。応答を待っています…');
}

// ---- タブ切り替えと履歴表示 ----
async function loadHistory() {
  const listEl = document.getElementById('historyList');
  listEl.textContent = '読み込み中…';
  let sessions;
  try {
    const res = await fetch('/api/history');
    sessions = await res.json();
  } catch (err) {
    listEl.textContent = `履歴の取得に失敗しました: ${err.message}`;
    return;
  }
  listEl.innerHTML = '';
  if (!sessions.length) {
    listEl.textContent = 'まだ履歴がありません。通話するとここに保存されます。';
    return;
  }
  for (const s of sessions) {
    const sec = document.createElement('section');
    sec.className = 'hist-session';
    const h = document.createElement('h3');
    h.textContent = `${s.started_at.replace('T', ' ')} — ${s.messages.length}件`;
    sec.appendChild(h);
    for (const m of s.messages) {
      const div = document.createElement('div');
      div.className = 'turn';
      const who = document.createElement('span');
      who.className = 'who';
      who.textContent = { user: 'あなた', assistant: 'AI', search: '🔍 検索' }[m.role] || m.role;
      const text = document.createElement('span');
      text.textContent = ' ' + m.text;
      div.append(who, text);
      sec.appendChild(div);
    }
    listEl.appendChild(sec);
  }
}

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b === btn));
    const tab = btn.dataset.tab;
    document.getElementById('view-call').hidden = tab !== 'call';
    document.getElementById('view-history').hidden = tab !== 'history';
    if (tab === 'history') loadHistory();
  });
});
document.getElementById('reloadHistory').addEventListener('click', loadHistory);

pttBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); startTalking(); });
pttBtn.addEventListener('pointerup', stopTalking);
pttBtn.addEventListener('pointerleave', stopTalking);
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !e.repeat && document.activeElement !== pttBtn) {
    e.preventDefault();
    startTalking();
  }
});
window.addEventListener('keyup', (e) => {
  if (e.code === 'Space') { e.preventDefault(); stopTalking(); }
});

// ---- ペルソナ ----
async function initPersonas() {
  try {
    const res = await fetch('/api/personas');
    const list = await res.json();
    personaSel.innerHTML = '';
    for (const p of list) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      personaSel.appendChild(opt);
    }
    const saved = localStorage.getItem('persona');
    if (saved && list.some((p) => p.id === saved)) personaSel.value = saved;
  } catch (_) {
    // 一覧が取れなくても default で接続する
  }
}

personaSel.addEventListener('change', () => {
  localStorage.setItem('persona', personaSel.value);
  const name = personaSel.selectedOptions[0]?.textContent || personaSel.value;
  appendTurn('⚙️').textContent = `ペルソナを「${name}」に切り替え(新しいセッションを開始)`;
  stopPlayback();
  if (ws && ws.readyState === WebSocket.OPEN) {
    personaChanging = true;
    ws.close(); // onclose が即座に新ペルソナで繋ぎ直す
  } else {
    connect();
  }
});

initPersonas().then(connect);
