"""
tools/browser_mic_test.py - record the laptop mic through Chrome's audio path.

On this laptop the raw Windows capture (PortAudio, Sound Recorder) sounds
robotic and quiet, while browser mic tests sound clean: Chrome applies its
own WebRTC gain control, noise suppression and echo cancellation. This serves
a local page that records with exactly those constraints and posts the take
back, so Whisper can be tested on the audio Chrome actually produces.

Usage:
    .venv/Scripts/python.exe tools/browser_mic_test.py
then open http://127.0.0.1:8765 in Chrome (localhost counts as a secure
origin, so the microphone is allowed). Takes are saved to
test_lab/stt_bench/browser/take_N.wav at the browser's sample rate.
"""
from __future__ import annotations

import http.server
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test_lab" / "stt_bench" / "browser"
PORT = 8765
_counter = itertools.count(1)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>MBUDDY browser mic test</title>
<style>body{font:18px system-ui;max-width:640px;margin:40px auto;padding:0 16px}
button{font-size:20px;padding:12px 24px}#s{margin-top:16px;font-weight:600}</style></head><body>
<h2>MBUDDY browser mic test</h2>
<p>Microphone: <select id="dev"></select></p>
<p>Press Record, then say:<br><b>"Can you open Visual Studio Code and my project folder?"</b></p>
<button id="b">Record 5 s</button><div id="s"></div>
<script>
const s = document.getElementById('s'), sel = document.getElementById('dev');
async function listDevices() {
  await navigator.mediaDevices.getUserMedia({audio: true}).then(st => st.getTracks().forEach(t => t.stop()));
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
  sel.innerHTML = devs.map(d => `<option value="${d.deviceId}">${d.label}</option>`).join('');
  const arr = devs.find(d => d.label.includes('Microphone Array')); if (arr) sel.value = arr.deviceId;
}
listDevices();
function wav(samples, rate) {
  const b = new ArrayBuffer(44 + samples.length * 2), v = new DataView(b);
  const w = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, samples.length * 2, true);
  samples.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32767, true));
  return b;
}
document.getElementById('b').onclick = async () => {
  const stream = await navigator.mediaDevices.getUserMedia({audio: {
    deviceId: {exact: sel.value}, echoCancellation: true, noiseSuppression: true,
    autoGainControl: true, channelCount: 1}});
  const settings = stream.getAudioTracks()[0].getSettings();
  const ctx = new AudioContext(); const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1); const chunks = [];
  proc.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc); proc.connect(ctx.destination);
  s.textContent = 'RECORDING... speak now';
  await new Promise(r => setTimeout(r, 5000));
  proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop());
  const all = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let o = 0; chunks.forEach(c => { all.set(c, o); o += c.length; });
  const r = await fetch('/upload', {method: 'POST', body: wav(all, ctx.sampleRate)});
  s.textContent = 'Saved ' + (await r.text()) + '  (AGC=' + settings.autoGainControl +
    ', NS=' + settings.noiseSuppression + ', AEC=' + settings.echoCancellation + ', ' + ctx.sampleRate + ' Hz)';
  ctx.close();
};
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/upload":
            self.send_error(404)
            return
        data = self.rfile.read(int(self.headers["Content-Length"]))
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"take_{next(_counter)}.wav"
        path.write_bytes(data)
        print(f"saved {path} ({len(data)} bytes)", flush=True)
        reply = path.name.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


if __name__ == "__main__":
    print(f"open http://127.0.0.1:{PORT} in Chrome", flush=True)
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
