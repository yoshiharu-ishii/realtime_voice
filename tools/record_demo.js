// 使用感デモの自動収録スクリプト(疑似E2Eを兼ねる)。
//
// 仕組み: ヘッドレスChromeで実アプリに接続し、偽マイクから合成音声を流して
// 本物の通話を行い、映像(タブ録画)と音声(偽マイク+AI再生音をWeb Audioで
// タップしてページ内MediaRecorder)を収録する。OSの画面収録権限は不要。
//
// 事前準備:
//   1. npm install puppeteer-core  (このディレクトリで)
//   2. ユーザー役の合成音声を作って frontend/ に置く:
//        say -v Kyoko -o u1.aiff "こんにちは。あなたは何ができますか？"
//        afconvert -f WAVE -d LEI16@24000 -c 1 u1.aiff frontend/u1.wav  (u2も同様)
//   3. サーバー起動(認証有効ならID_TOKEN環境変数にCognitoのIDトークン)
// 実行:
//   APP_URL=http://localhost:8000/ ID_TOKEN=eyJ... node tools/record_demo.js
// 仕上げ(mp4とGIF):
//   ffmpeg -i video.webm -i audio.webm -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest demo.mp4
//   ffmpeg -i video.webm -vf "setpts=PTS/1.75,fps=8,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer" demo.gif
//
const OUT = process.env.OUT_DIR || '.';
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const TOKEN = process.env.ID_TOKEN || ''; // 認証有効時はCognitoのIDトークンを渡す
const URL = process.env.APP_URL || 'http://localhost:8000/';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new',
    args: [
      '--autoplay-policy=no-user-gesture-required',
      '--window-size=860,760',
      '--lang=ja',
    ],
    defaultViewport: { width: 860, height: 760 },
    protocolTimeout: 300000,
  });
  const page = await browser.newPage();
  if (TOKEN) await page.setCookie({ name: 'id_token', value: TOKEN, url: URL });

  // アプリのスクリプトより先に仕込む: 偽マイク + AI再生音のタップ
  await page.evaluateOnNewDocument(() => {
    window.__taps = []; // 録音対象のMediaStream群
    // (1) 偽マイク: 呼ばれるたび新しいdest、ゲイン0発振器で常時フレーム生成
    const fakeCtx = new AudioContext();
    window.__fakeCtx = fakeCtx;
    navigator.mediaDevices.getUserMedia = async () => {
      const dest = fakeCtx.createMediaStreamDestination();
      const osc = fakeCtx.createOscillator();
      const g = fakeCtx.createGain();
      g.gain.value = 0;
      osc.connect(g).connect(dest);
      osc.start();
      window.__dest = dest;
      window.__taps.push(dest.stream); // ユーザー役の声も録音対象
      return dest.stream;
    };
    // (2) AI再生のタップ: 再生ソースがdestinationに繋がる瞬間、同じ音を録音用destにも分岐
    const origConnect = AudioBufferSourceNode.prototype.connect;
    AudioBufferSourceNode.prototype.connect = function (dst, ...rest) {
      try {
        if (dst && dst instanceof AudioDestinationNode) {
          const ctx = this.context;
          if (!ctx.__tap) {
            ctx.__tap = ctx.createMediaStreamDestination();
            window.__taps.push(ctx.__tap.stream);
          }
          origConnect.call(this, ctx.__tap);
        }
      } catch (_) {}
      return origConnect.call(this, dst, ...rest);
    };
    // (3) 合成音声の再生
    window.__play = async (url) => {
      const buf = await fakeCtx.decodeAudioData(await (await fetch(url)).arrayBuffer());
      const src = fakeCtx.createBufferSource();
      src.buffer = buf;
      src.connect(window.__dest);
      src.start();
      return buf.duration;
    };
    // (4) 録音: tapsをミキサーに集めてMediaRecorderへ
    window.__startRec = () => {
      const mix = new AudioContext();
      window.__mixCtx = mix;
      const out = mix.createMediaStreamDestination();
      const wire = (s) => mix.createMediaStreamSource(s).connect(out);
      window.__taps.forEach(wire);
      // 録音開始後に増えたタップ(再接続等)も配線する
      window.__taps.push = function (s) { wire(s); return Array.prototype.push.call(this, s); };
      window.__chunks = [];
      window.__rec = new MediaRecorder(out.stream, { mimeType: 'audio/webm' });
      window.__rec.ondataavailable = (e) => window.__chunks.push(e.data);
      window.__rec.start(250);
    };
    window.__stopRec = () =>
      new Promise((resolve) => {
        window.__rec.onstop = () => {
          const blob = new Blob(window.__chunks, { type: 'audio/webm' });
          const fr = new FileReader();
          fr.onload = () => resolve(fr.result.split(',')[1]); // data:...;base64, を剥がす
          fr.readAsDataURL(blob); // 巨大配列のspreadはRangeErrorで死ぬのでFileReaderで
        };
        window.__rec.stop();
      });
  });

  await page.goto(URL, { waitUntil: 'networkidle2' });

  // WS回線 + ハンズフリー(VAD)に切り替え(AI音声がAudioBufferSource=タップ対象を通る回線)
  await page.evaluate(() => {
    transportSel.value = 'ws';
    modeSel.value = 'vad';
    transportSel.dispatchEvent(new Event('change'));
  });
  await page.waitForFunction(
    () => document.getElementById('status').textContent.includes('接続完了'),
    { timeout: 20000 }
  );
  console.log('connected');

  // 録音・録画スタート
  await page.evaluate(() => __startRec());
  const recorder = await page.screencast({ path: OUT + '/video.webm' });
  await new Promise((r) => setTimeout(r, 1200));

  const waitIdle = async (timeout) => {
    // 応答生成が終わり、再生キューも空になるまで待つ
    await page.waitForFunction(
      () => !responseActive && activeSources.length === 0 && !searching,
      { timeout }
    );
  };

  // ターン1: 挨拶
  console.log('turn1');
  await page.evaluate(() => __play('/static/u1.wav'));
  await new Promise((r) => setTimeout(r, 6000)); // 発話+検知待ち
  await waitIdle(60000);
  await page.screenshot({ path: OUT + '/shot_talk.png' });
  await new Promise((r) => setTimeout(r, 800));

  // ターン2: Web検索を誘発
  console.log('turn2');
  await page.evaluate(() => __play('/static/u2.wav'));
  // 検索ロックの瞬間を撮る
  try {
    await page.waitForFunction(() => searching === true, { timeout: 30000 });
    await page.screenshot({ path: OUT + '/shot_search.png' });
  } catch (_) {
    console.log('(検索は発動せず)');
  }
  await waitIdle(90000);
  await new Promise((r) => setTimeout(r, 1000));
  await page.screenshot({ path: OUT + '/shot_final.png' });

  // 停止・保存
  await recorder.stop();
  const audioB64 = await page.evaluate(() => __stopRec());
  fs.writeFileSync(OUT + '/audio.webm', Buffer.from(audioB64, 'base64'));

  // おまけ: 履歴タブのスクリーンショット
  await page.click('button[data-tab="history"]');
  await new Promise((r) => setTimeout(r, 1500));
  await page.screenshot({ path: OUT + '/shot_history.png' });

  await browser.close();
  console.log('done');
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
