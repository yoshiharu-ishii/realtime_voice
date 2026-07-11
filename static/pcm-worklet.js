// マイク入力を 24kHz mono Float32 でメインスレッドへ送る AudioWorklet。
// コンテキストが 24kHz ならそのまま転送し、それ以外は面積平均(簡易ローパス)
// 付きでリサンプリングする。単純な線形補間だけではエイリアシングで音声が濁り、
// 音声認識の精度が大きく落ちるため。
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 24000;
    this.ratio = sampleRate / this.targetRate; // sampleRate は worklet のグローバル
    this.win = Math.max(1, Math.round(this.ratio)); // 平均化窓 = デシメーション比
    this.readPos = 0; // 入力ストリーム全体に対する読み取り位置(小数)
    this.leftover = new Float32Array(0); // 前フレームの未消費サンプル
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) return true;
    const chunk = input[0];

    // コンテキストが既に 24kHz ならコピーして送るだけ
    if (this.ratio === 1) {
      const out = new Float32Array(chunk);
      this.port.postMessage(out, [out.buffer]);
      return true;
    }

    // 前回の残りと今回の入力を連結
    const buf = new Float32Array(this.leftover.length + chunk.length);
    buf.set(this.leftover, 0);
    buf.set(chunk, this.leftover.length);

    // 窓平均ぶん先読みするため、末尾 win サンプルは次回に回す
    const outLen = Math.floor((buf.length - this.win - this.readPos) / this.ratio);
    if (outLen <= 0) {
      this.leftover = buf;
      return true;
    }

    const out = new Float32Array(outLen);
    let pos = this.readPos;
    for (let i = 0; i < outLen; i++) {
      const idx = Math.floor(pos);
      let sum = 0;
      for (let j = 0; j < this.win; j++) sum += buf[idx + j];
      out[i] = sum / this.win;
      pos += this.ratio;
    }

    // 消費済みサンプルを捨て、端数位置を持ち越す
    const consumed = Math.floor(pos);
    this.readPos = pos - consumed;
    this.leftover = buf.slice(consumed);

    this.port.postMessage(out, [out.buffer]);
    return true;
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);
