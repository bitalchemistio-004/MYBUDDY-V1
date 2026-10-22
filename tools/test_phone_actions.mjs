// node tools/test_phone_actions.mjs : checks the phone link builders, the parser and the model advice.
import assert from "node:assert/strict";
import { buildAction, parseQuick, parseWhen, recommend, intent, isAndroid, GALLERY_PACKAGE } from "../shell/ui/js/phone-actions.js";

const now = new Date(2026, 9, 9, 10, 0, 0);                         // Friday 9 Oct 2026, 10:00
let a = buildAction("alarm", { hour: 7, minute: 5, label: "Gym" });
assert.equal(a.href, "intent:#Intent;action=android.intent.action.SET_ALARM;i.android.intent.extra.alarm.HOUR=7;i.android.intent.extra.alarm.MINUTES=5;S.android.intent.extra.alarm.MESSAGE=Gym;B.android.intent.extra.alarm.SKIP_UI=true;end");
a = buildAction("timer", { seconds: 600 });
assert.ok(a.href.includes("SET_TIMER") && a.href.includes("alarm.LENGTH=600") && a.label === "Timer 10 min");
a = buildAction("calendar", { title: "Dentist; room 2", start: new Date(2026, 9, 10, 15, 30).toISOString() });
assert.ok(a.href.startsWith("intent://com.android.calendar/events#Intent;scheme=content;action=android.intent.action.INSERT;S.title=Dentist%3B%20room%202;") && a.alt.length === 2);
assert.ok(a.alt[0].href.includes("dates=20261010T153000/20261010T163000") && a.alt[1].file.text.includes("SUMMARY:Dentist\\; room 2"));
assert.equal(buildAction("call", { number: "+212600111222" }).href, "tel:+212600111222");
assert.equal(buildAction("whatsapp", { number: "+212 600", body: "hi there" }).href, "https://wa.me/212600?text=hi%20there");
assert.ok(buildAction("open_gallery").href.includes(`package=${GALLERY_PACKAGE}`) && buildAction("open_gallery").href.includes("browser_fallback_url=https%3A%2F%2Fplay.google.com"));
assert.equal(buildAction("nope"), null);

// what people type
assert.deepEqual(parseQuick("set a 10 minute timer", now), [{ kind: "timer", params: { seconds: 600 } }]);
assert.deepEqual(parseQuick("timer for 45 seconds", now), [{ kind: "timer", params: { seconds: 45 } }]);
assert.deepEqual(parseQuick("set an alarm for 7:30 am", now)[0].params, { hour: 7, minute: 30, label: "" });
assert.deepEqual(parseQuick("wake me up at 6pm", now)[0].params.hour, 18);
const r = parseQuick("remind me to call the dentist tomorrow at 9", now);
assert.equal(r[0].kind, "task"); assert.equal(r[0].params.title, "call the dentist"); assert.equal(r[1].kind, "calendar");
assert.equal(new Date(r[0].params.when).getDate(), 10); assert.equal(new Date(r[0].params.when).getHours(), 9);
assert.deepEqual(parseQuick("call +212 600 111 222", now), [{ kind: "call", params: { number: "+212600111222" } }]);
assert.equal(parseQuick("call mum", now).length, 0);                // no contacts from a web page: it says so instead of guessing
assert.equal(parseQuick("take me to Central Station", now)[0].params.place, "Central Station");
assert.equal(parseQuick("search pasta recipe", now)[0].kind, "search");
assert.equal(parseQuick("tell me a joke", now).length, 0);
assert.equal(parseWhen("in 2 hours", now).getHours(), 12);
assert.equal(parseWhen("friday at 3pm", now).getDate(), 16);        // today is Friday: the next one
assert.equal(parseWhen("hello", now), null);

// which model
assert.equal(recommend({ android: true, ram: 8 }).model.name, "Gemma 4 E4B");
assert.equal(recommend({ android: true, ram: 6 }).model.name, "Gemma 4 E2B");
assert.equal(recommend({ android: true, ram: 4 }).alt, null);
assert.equal(recommend({ android: true, ram: 2 }).model, null);
assert.equal(recommend({ android: true }).model.name, "Gemma 4 E2B");
assert.equal(recommend({ android: false, ram: 8 }).model, null);
assert.ok(isAndroid("Mozilla/5.0 (Linux; Android 14; Pixel 8)") && !isAndroid("iPhone"));
console.log("phone actions: all checks passed");

// a time that comes first, then "to" and the thing; and a calendar line
{
  const at = new Date(2026, 9, 9, 13, 45, 0);
  const r = parseQuick("remind me in 20 minutes to stretch", at);
  assert.equal(r[0].params.title, "stretch");
  assert.equal(new Date(r[0].params.when).getTime(), at.getTime() + 20 * 60000);
  assert.equal(parseQuick("remind me tomorrow at 9 to call Sara", at)[0].params.title, "call Sara");
  assert.equal(parseQuick("remind me to call Sara tomorrow at 9", at)[0].params.title, "call Sara");
  const c = parseQuick("add meeting with Ali on friday at 3pm to my calendar", at);
  assert.equal(c.length, 1); assert.equal(c[0].kind, "calendar"); assert.equal(c[0].params.title, "meeting with Ali");
  const d = new Date(c[0].params.start); assert.equal(d.getDay(), 5); assert.equal(d.getHours(), 15);
  assert.equal(parseQuick("schedule dentist tomorrow at 10", at)[0].params.title, "dentist");
  assert.deepEqual(parseQuick("what is the weather like", at), []);
}
console.log("phone actions: line checks passed");
