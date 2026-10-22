// usage: node cdp.mjs "<js expression>"   -> evaluates in the app's WebView page, prints the JSON result
const targets = await (await fetch("http://127.0.0.1:9222/json")).json();
const t = targets.find((x) => x.type === "page" && x.url.includes("phoneapp")) || targets.find((x) => x.type === "page" && x.url.includes("appassets")) || targets.find((x) => x.type === "page");
if (!t) { console.log("NO PAGE", JSON.stringify(targets)); process.exit(2); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression: process.argv[2].startsWith("@") ? (await import("node:fs")).readFileSync(process.argv[2].slice(1), "utf8") : process.argv[2], awaitPromise: true, returnByValue: true, userGesture: true } }));
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id === 1) { console.log(JSON.stringify(d.result?.result?.value ?? d.result ?? d.error)); ws.close(); }
};
setTimeout(() => process.exit(0), 170000);
