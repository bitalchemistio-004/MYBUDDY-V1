// node tools/test_papp_brain.mjs : the phone app's brain picks a brain in the owner's order, runs the model's tools, and keeps working
// with simple commands when no brain can be reached. The phone's own functions (Native) are stand-ins here.
import assert from "node:assert/strict";

const mem = {};
globalThis.window = globalThis;
globalThis.location = new URL("https://appassets.androidplatform.net/web/phoneapp.html");
globalThis.localStorage = { getItem: (k) => mem[k] ?? null, setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
const acts = [];
const state = { cloudKey: false, paired: false, phoneModel: false, cloudAnswer: null, phoneAnswer: null, cloudStatus: 200, spoken: [], agent: [], screen: true };
globalThis.Native = {
  cloudKeyPresent: () => state.cloudKey, paired: () => state.paired,
  localModelState: () => JSON.stringify({ active: state.phoneModel ? "gemma-4-e2b" : "" }),
  act: (kind, params) => { acts.push([kind, JSON.parse(params)]); return '{"ok":true}'; },
  speak: (t) => state.spoken.push(t), agentStatus: () => JSON.stringify({ enabled: state.screen, connected: state.screen }),
  agentStart: (goal) => { state.agent.push(goal); return ""; },
  setPlanner() {}, setCloudModel() {},
};
globalThis.fetch = async (url, init) => {
  if (url === "/cloud/chat") {
    if (state.cloudStatus !== 200) return new Response("{}", { status: state.cloudStatus });
    return new Response(JSON.stringify({ message: { content: state.cloudAnswer } }), { status: 200 });
  }
  if (url === "/native/local-chat") return new Response(JSON.stringify({ ok: true, text: state.phoneAnswer, ms: 1 }), { status: 200 });
  if (String(url).includes("/control/chat")) return new Response(JSON.stringify({ ok: true, reply: "from the pc" }), { status: 200 });
  return new Response("{}", { status: 404 });
};

const store = await import("../shell/ui/js/papp/store.js");
const brain = await import("../shell/ui/js/papp/brain.js");
brain.init({ setStatus() {} });
const reset = () => { acts.length = 0; state.spoken.length = 0; state.agent.length = 0; for (const k of Object.keys(mem)) delete mem[k]; };

// 1. the cloud answers: its words are said and its tools are run
reset(); Object.assign(state, { cloudKey: true, cloudStatus: 200, cloudAnswer: '{"say":"Ten minutes, set.","do":[{"tool":"timer","seconds":600},{"tool":"task","title":"Call Sara","when":"2026-10-12T09:00"}]}' });
let r = await brain.ask("set a ten minute timer and remind me to call Sara monday at 9");
assert.equal(r.source, "cloud"); assert.equal(r.say, "Ten minutes, set.");
assert.deepEqual(acts[0], ["timer", { seconds: 600, label: "" }]);
assert.equal(store.tasks.list()[0].title, "Call Sara");
assert.equal(state.spoken.at(-1), "Ten minutes, set.");

// 2. a request that needs another app goes to screen control, with the owner's words
reset(); state.cloudAnswer = '{"say":"On it.","do":[{"tool":"phone","goal":"Send Sara on WhatsApp that I am late"}]}';
r = await brain.ask("tell Sara on whatsapp I'm late");
assert.deepEqual(state.agent, ["Send Sara on WhatsApp that I am late"]);

// 3. the cloud cannot be reached: the next brain answers (the model on the phone), still in the same shape
reset(); Object.assign(state, { cloudKey: true, cloudStatus: 503, phoneModel: true, phoneAnswer: '{"say":"Offline, but here.","do":[{"tool":"note","text":"milk"}]}' });
r = await brain.ask("note milk");
assert.equal(r.source, "phone"); assert.equal(r.say, "Offline, but here."); assert.equal(store.notes.list()[0].text, "milk");

// 4. "always this phone" ignores a working cloud
reset(); store.settings.set({ planner: "phone" }); Object.assign(state, { cloudStatus: 200, cloudAnswer: '{"say":"cloud"}', phoneAnswer: '{"say":"phone"}' });
{ const x = await brain.ask("hi"); assert.equal(x.source, "phone", JSON.stringify(x) + " state=" + JSON.stringify({ planner: store.settings.get().planner, phone: state.phoneModel })); }

// 5. the PC is the next brain when there is no cloud key and the owner chose auto
reset(); Object.assign(state, { cloudKey: false, paired: true, phoneModel: false });
assert.deepEqual([(await brain.ask("hello")).source], ["pc"]);

// 6. no brain at all: a simple command still works from the rules; a question says what to do
reset(); Object.assign(state, { cloudKey: false, paired: false, phoneModel: false });
r = await brain.ask("set a 5 minute timer");
assert.equal(r.source, "rules"); assert.deepEqual(acts[0], ["timer", { seconds: 300, label: "" }]);
r = await brain.ask("what is the weather");
assert.equal(r.source, "error"); assert.match(r.say, /brain/);

// 7. a model that answers with plain words instead of JSON is still heard
reset(); Object.assign(state, { cloudKey: true, cloudAnswer: "Just some words." });
r = await brain.ask("tell me something");
assert.equal(r.say, "Just some words."); assert.deepEqual(r.did, []);

// 8. tools with bad values do nothing
reset(); state.cloudAnswer = '{"say":"ok","do":[{"tool":"timer","seconds":-5},{"tool":"alarm","hour":99},{"tool":"call"},{"tool":"unknown"}]}';
r = await brain.ask("do silly things"); assert.deepEqual(r.did, []); assert.equal(acts.length, 0);


// 9. a model that SAYS it did something without asking for the tool is not believed: the rules do it if they can, else it says nothing was done
reset(); Object.assign(state, { cloudKey: true, cloudStatus: 200, cloudAnswer: '{"do":[],"say":"Okay, I have set a reminder for you."}' });
r = await brain.ask("remind me to buy milk tomorrow at 9am");
assert.equal(r.source, "cloud+rules"); assert.equal(store.tasks.list()[0].title, "buy milk"); assert.equal(acts.length, 0);
reset(); state.screen = false; state.cloudAnswer = '{"do":[],"say":"I have sent the message to Sara."}';
r = await brain.ask("tell Sara I am late");
state.screen = true;
assert.deepEqual(r.did, []); assert.match(r.say, /nothing was done/); assert.doesNotMatch(r.say, /sent the message/);
// ...and with screen control on, a request that needs another app is handed to it, in the owner's own words
reset(); state.cloudAnswer = '{"do":[],"say":"I have sent the message to Sara."}';
r = await brain.ask("tell Sara on whatsapp I am late");
assert.deepEqual(state.agent, ["tell Sara on whatsapp I am late"]); assert.equal(r.did.length, 1);
reset(); state.cloudAnswer = '{"do":[],"say":"12 times 12 is 144."}';
r = await brain.ask("what is 12 times 12"); assert.equal(r.say, "12 times 12 is 144.");

console.log("papp brain: all checks passed");
