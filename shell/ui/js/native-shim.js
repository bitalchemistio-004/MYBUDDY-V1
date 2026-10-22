// Inside the Android app (window.Native exists) the page's calls to the PC go through the app: the app talks to the PC, with
// the pairing token it keeps, and hands the answer back. In a browser (no window.Native) this file does nothing.
(function () {
  if (!window.Native) return;
  const pending = new Map();
  let n = 0;
  window.__nativeHttp = function (id, status, body) {
    const resolve = pending.get(id);
    if (!resolve) return;
    pending.delete(id);
    resolve(new Response(body, { status: status, headers: { "Content-Type": "application/json" } }));
  };
  const OWN = { "/cloud/chat": "cloudChatAsync", "/cloud/models": "cloudModelsAsync", "/cloud/key": "saveCloudKeyAsync", "/native/pair": "pairAsync", "/native/listen": "listenAsync", "/native/local-chat": "localChatAsync", "/native/web": "webAnswerAsync", "/native/brains-test": "brainsTestAsync" };
  const ARGS = {
    cloudChatAsync: function (b, raw) { return [raw]; },
    cloudModelsAsync: function () { return []; },
    saveCloudKeyAsync: function (b) { return [String(b.key || "")]; },
    listenAsync: function () { return []; },
    localChatAsync: function (b, raw) { return [raw]; },
    webAnswerAsync: function (b, raw) { return [raw]; },
    brainsTestAsync: function () { return []; },
    pairAsync: function (b) { return [String(b.address || ""), String(b.code || ""), String(b.name || "")]; },
  };
  const original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init || {};
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const path = url.pathname + url.search;
    // The brain from the phone itself (no PC): the app talks to the cloud with the key it keeps; the page never holds the key.
    const own = url.origin === location.origin && OWN[url.pathname];
    if (own && window.Native[own]) {
      return new Promise(function (resolve) {
        const id = String(++n);
        pending.set(id, resolve);
        let args = [id];
        try { const b = init.body ? JSON.parse(String(init.body)) : {}; args = args.concat(ARGS[own](b, init.body ? String(init.body) : "")); } catch (e) { args = [id]; }
        window.Native[own].apply(window.Native, args);
      });
    }
    if (url.origin === location.origin && (path.indexOf("/remote/") === 0 || path === "/health" || path === "/pair" || path.indexOf("/mcp/") === 0)) {
      return new Promise(function (resolve) {
        const id = String(++n);
        pending.set(id, resolve);
        window.Native.httpAsync(id, init.method || "GET", path, init.body ? String(init.body) : "");
      });
    }
    return original(input, init);
  };
  // The page decides "paired or not" from the token it can see; the real token stays in the app.
  try { if (window.Native.paired()) localStorage.setItem("mbuddy_phone_token", "in-the-app"); else localStorage.removeItem("mbuddy_phone_token"); } catch (e) { /* private mode */ }
})();
