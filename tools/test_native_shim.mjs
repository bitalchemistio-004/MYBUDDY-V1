// node tools/test_native_shim.mjs : the page's calls to the PC go through the Android app when window.Native exists.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const calls = [];
const store = {};
globalThis.window = globalThis;
globalThis.location = new URL("https://appassets.androidplatform.net/web/phone.html");
globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
globalThis.Native = {
  paired: () => true,
  cloudChatAsync: (id, body) => { calls.push({ cloud: "chat", body }); setTimeout(() => window.__nativeHttp(id, 200, JSON.stringify({ message: { content: "hi" } })), 5); },
  saveCloudKeyAsync: (id, key) => { calls.push({ cloud: "key", key }); setTimeout(() => window.__nativeHttp(id, 200, JSON.stringify({ ok: true })), 5); },
  pairAsync: (id, address, code, name) => { calls.push({ pair: [address, code, name] }); setTimeout(() => window.__nativeHttp(id, 400, JSON.stringify({ ok: false, error: "no" })), 5); },
  httpAsync: (id, method, path, body) => { calls.push({ method, path, body }); setTimeout(() => window.__nativeHttp(id, 200, JSON.stringify({ ok: true, path })), 5); },
};
let realFetchCalls = 0;
globalThis.fetch = async () => { realFetchCalls++; return new Response("net"); };
new Function(readFileSync(new URL("../shell/ui/js/native-shim.js", import.meta.url), "utf8"))();

const r = await fetch("https://appassets.androidplatform.net/remote/ui/tasks", { method: "POST", body: JSON.stringify({ title: "x" }) });
assert.equal(r.status, 200);
assert.deepEqual(await r.json(), { ok: true, path: "/remote/ui/tasks" });
assert.deepEqual(calls[0], { method: "POST", path: "/remote/ui/tasks", body: '{"title":"x"}' });
assert.equal((await (await fetch("/health")).json()).path, "/health");
assert.equal((await (await fetch("https://appassets.androidplatform.net/mcp/abc")).json()).path, "/mcp/abc");
assert.equal(realFetchCalls, 0, "calls to the PC never go to the network from the page");
const chat = await fetch("/cloud/chat", { method: "POST", body: JSON.stringify({ model: "m", messages: [] }) });
assert.equal((await chat.json()).message.content, "hi");
assert.deepEqual(calls.at(-1), { cloud: "chat", body: '{"model":"m","messages":[]}' });
assert.equal((await (await fetch("/cloud/key", { method: "POST", body: JSON.stringify({ key: "k" }) })).json()).ok, true);
assert.deepEqual(calls.at(-1), { cloud: "key", key: "k" });
const pr = await fetch("/native/pair", { method: "POST", body: JSON.stringify({ address: "http://a", code: "123456", name: "p" }) });
assert.equal(pr.status, 400);
assert.deepEqual(calls.at(-1), { pair: ["http://a", "123456", "p"] });
assert.equal(realFetchCalls, 0, "the cloud key and pairing never go through the page's network");
await (await fetch("https://example.com/other")).text();
assert.equal(realFetchCalls, 1, "anything else is left alone");
assert.equal(store.mbuddy_phone_token, "in-the-app");                 // the page sees "paired", never the token
console.log("native shim: all checks passed");
