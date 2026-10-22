package app.mbuddy.mobile

import android.os.Build
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Toast
import org.json.JSONObject

/**
 * What the page (the phone tools, bundled in the app) may ask the app to do. It is exposed as `window.Native`, only used by pages
 * the app itself serves, and every method checks the page is one of those: a page from anywhere else gets nothing.
 *
 * The page is the same one the PC serves to a browser (shell/ui/phone.html). Inside the app its network calls go through
 * [httpAsync] (js/native-shim.js): the app, not the page, talks to the PC, so there is no cross-site trouble and the pairing token
 * never reaches the page.
 */
class Bridge(private val activity: MainActivity, private val web: WebView) {

    private fun trusted(): Boolean = activity.currentUrl.startsWith(MainActivity.ASSET_ORIGIN)

    /** A call to the PC. The answer comes back by window.__nativeHttp(id, status, body). Only the paths the web page uses. */
    @JavascriptInterface
    fun httpAsync(id: String, method: String, path: String, body: String) {
        val allowed = path.startsWith("/remote/") || path == "/health" || path == "/pair" || path.startsWith("/mcp/")
        if (!trusted() || !allowed) return
        Thread {
            val r = Pc.request(activity, method.uppercase(), path, body)
            val status = if (r.status == 0) 503 else r.status
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, $status, ${JSONObject.quote(r.body)})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    @JavascriptInterface fun paired(): Boolean = trusted() && Store.paired(activity)

    /** The address to give Google AI Edge Gallery (it carries the token, so it is only handed to the bundled page). */
    @JavascriptInterface fun mcpAddress(): String = if (trusted() && Store.paired(activity)) Store.pcBase(activity) + "/mcp/" + Store.token(activity) else ""

    @JavascriptInterface fun deviceInfo(): String = if (trusted()) DeviceInfo.json(activity) else "{}"

    /** Do one action now (the page's buttons call this; the app is on screen). Returns {"ok":true} at once. */
    @JavascriptInterface
    fun act(kind: String, paramsJson: String): String {
        if (!trusted()) return """{"ok":false}"""
        val p = runCatching { JSONObject(paramsJson) }.getOrNull() ?: return """{"ok":false,"error":"bad details"}"""
        if (Privacy.on(activity) && kind !in setOf("timer", "alarm", "flashlight", "calendar")) return JSONObject().put("ok", false).put("error", Privacy.REFUSAL).toString()
        activity.runOnUiThread {
            val go = { toast(Actions.execute(activity, kind, p, onScreen = true)) }
            if (kind == "calendar" && !Actions.calendarAllowed(activity)) {
                activity.askCalendar { granted ->
                    if (granted) go() else {
                        toast("Without calendar access I'll open the calendar for you to save it.")
                        Actions.intentFor(activity, kind, p)?.let { runCatching { activity.startActivity(it) } }
                    }
                }
            } else go()
        }
        return """{"ok":true}"""
    }

    @JavascriptInterface
    fun openFullApp() {
        if (trusted()) activity.runOnUiThread { activity.openFullApp() }
    }

    @JavascriptInterface
    fun unpair() {
        if (!trusted()) return
        Store.clear(activity)
        activity.runOnUiThread { toast("This phone is no longer paired. Remove it on the PC too (Away, Remove).") }
    }

    /** The PC says it no longer knows this phone (it was removed there): forget the pairing so the app does not pretend. */
    @JavascriptInterface
    fun pairingLost() {
        if (!trusted() || !Store.paired(activity)) return
        Store.clear(activity)
        activity.runOnUiThread { toast("Your PC removed this phone. Pair again to share tasks and use its brain.") }
    }

    // ---- screen control: the buddy does a task in any app (Agent.kt) ----

    /** What the page needs to show the screen-control card: is it on, is a task running, can it speak and hear. */
    @JavascriptInterface
    fun agentStatus(): String = if (!trusted()) "{}" else JSONObject()
        .put("enabled", ControlService.enabled(activity)).put("connected", ControlService.instance != null)
        .put("running", Agent.running).put("voice", Store.voice(activity)).put("hear", Voice.canHear(activity))
        .put("character", Store.character(activity)).put("paired", Store.paired(activity)).put("wasOn", Store.controlWasOn(activity)).toString()

    /** Start a task. Returns "" when it began, or why it could not (said to the owner as it is). */
    @JavascriptInterface
    fun agentStart(goal: String): String {
        if (!trusted()) return "Not allowed."
        if (Store.voice(activity) && !Voice.canHear(activity)) activity.runOnUiThread { activity.askMic() }
        return Agent.start(goal) ?: ""
    }

    @JavascriptInterface fun agentStop() { if (trusted()) Agent.stop("stopped from the app") }

    /** Opens Android's Accessibility settings, where the owner switches MBUDDY screen control on. */
    @JavascriptInterface
    fun openControlSettings() {
        if (!trusted()) return
        activity.runOnUiThread {
            // Android 12 and later can open this one service's own page (its switch is right there); the general list is the fallback.
            val own = android.content.Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS")
                .putExtra(android.content.Intent.EXTRA_COMPONENT_NAME, android.content.ComponentName(activity, ControlService::class.java).flattenToString())
            val general = android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)
            if (runCatching { activity.startActivity(own) }.isFailure) runCatching { activity.startActivity(general) }
        }
    }

    @JavascriptInterface fun setCharacter(name: String) {
        if (!trusted()) return
        Store.setCharacter(activity, name)
        ControlService.instance?.handle?.refresh()          // the edge handle shows the buddy that was chosen
    }
    @JavascriptInterface fun setVoice(on: Boolean) { if (trusted()) Store.setVoice(activity, on) }
    @JavascriptInterface fun overlayStyle(): String = if (trusted()) Store.overlayStyle(activity) else "bar"
    @JavascriptInterface fun brainsOrder(): String = if (trusted()) org.json.JSONArray(Brains.order(activity)).toString() else "[]"
    @JavascriptInterface fun brainsState(): String = if (trusted()) Brains.state(activity).toString() else "{}"
    @JavascriptInterface fun noteBrain(name: String, ok: Boolean, ms: Long, why: String) { if (trusted() && name in Brains.ALL) Brains.note(name, ok, ms, why) }
    /** Ask each brain one tiny question; answers {"cloud":{ready,ok,ms,why}, "pc":â€¦, "phone":â€¦} through window.__nativeHttp. */
    @JavascriptInterface
    fun brainsTestAsync(id: String) {
        if (!trusted()) return
        Thread {
            val out = runCatching { Brains.test(activity.applicationContext) }.getOrElse { JSONObject().put("error", it.message ?: "") }
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote(out.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }
    @JavascriptInterface fun setOverlayStyle(v: String) { if (trusted()) Store.setOverlayStyle(activity, v) }
    @JavascriptInterface fun showSteps(): Boolean = trusted() && Store.showSteps(activity)
    @JavascriptInterface fun setShowSteps(on: Boolean) { if (trusted()) Store.setShowSteps(activity, on) }
    @JavascriptInterface fun colorfulLight(): Boolean = !trusted() || Store.colorfulLight(activity)
    @JavascriptInterface fun setColorfulLight(on: Boolean) { if (trusted()) Store.setColorfulLight(activity, on) }
    /** Show the chosen style for a few seconds over this screen. Returns "" or why it cannot (screen control is off). */
    @JavascriptInterface
    fun previewOverlay(): String {
        if (!trusted()) return "Not allowed."
        val ov = ControlService.instance?.overlay ?: return "Turn on screen control first (Android settings, Accessibility, MBUDDY)."
        if (Agent.running) return "I'm busy with a task right now."
        ov.preview(); return ""
    }

    // ---- the brain, from the phone itself ----

    /** Whether a cloud key is saved. The key itself never leaves the app. */
    @JavascriptInterface fun cloudKeyPresent(): Boolean = trusted() && Cloud.hasKey(activity)

    /** A chat request to the cloud; the answer comes back by window.__nativeHttp(id, status, body). */
    @JavascriptInterface
    fun cloudChatAsync(id: String, body: String) {
        if (!trusted()) return
        Thread {
            // the model chosen here is the one that works with this key: when the plan refuses it, another is found and kept
            var r = Cloud.chat(activity, runCatching { JSONObject(body).put("model", Store.cloudModel(activity)).toString() }.getOrDefault(body))
            if (r.status == 402 || r.status == 403) {
                val m = Cloud.findModel(activity)
                if (m != null) r = Cloud.chat(activity, runCatching { JSONObject(body).put("model", m).toString() }.getOrDefault(body))
            }
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, ${if (r.status == 0) 503 else r.status}, ${JSONObject.quote(r.body)})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    @JavascriptInterface
    fun cloudModelsAsync(id: String) {
        if (!trusted()) return
        Thread {
            val r = Cloud.models(activity)
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, ${if (r.status == 0) 503 else r.status}, ${JSONObject.quote(r.body)})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    /** Save a cloud key after the cloud accepts it. Answers by window.__nativeHttp as {"ok":true} or {"ok":false,"error":"..."}. */
    @JavascriptInterface
    fun saveCloudKeyAsync(id: String, key: String) {
        if (!trusted()) return
        Thread {
            val err = Cloud.saveKey(activity, key)
            val body = if (err == null) JSONObject().put("ok", true) else JSONObject().put("ok", false).put("error", err)
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, ${if (err == null) 200 else 400}, ${JSONObject.quote(body.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    @JavascriptInterface fun removeCloudKey() { if (trusted()) Store.setCloudKey(activity, "") }

    /** Pair with the PC (optional: it adds sync and the PC's own brain). Answers {"ok":true} or {"ok":false,"error":"..."}. */
    @JavascriptInterface
    fun pairAsync(id: String, address: String, code: String, name: String) {
        if (!trusted()) return
        Thread {
            val err = Pc.pair(activity, address, code, name)
            val body = if (err == null) JSONObject().put("ok", true) else JSONObject().put("ok", false).put("error", err)
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, ${if (err == null) 200 else 400}, ${JSONObject.quote(body.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    // ---- voice: the buddy talks and listens (the phone's own speech engines) ----

    @JavascriptInterface fun speak(text: String) { if (trusted() && Store.voice(activity)) Voice.say(activity, text.take(600)) }
    @JavascriptInterface fun stopSpeaking() { if (trusted()) Voice.shutUp() }

    /** Listen once. The answer comes by window.__nativeHttp(id, 200, {"text":"...","error":0}); error 9 means no microphone permission. */
    @JavascriptInterface
    fun listenAsync(id: String) {
        if (!trusted()) return
        if (!Voice.canHear(activity)) {
            activity.runOnUiThread { activity.askMic() }
            web.post { web.evaluateJavascript("window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote("{\"text\":\"\",\"error\":9}")})", null) }
            return
        }
        Voice.listen(activity, { activity.runOnUiThread { web.evaluateJavascript("window.dispatchEvent(new Event('mbuddy-listening'))", null) } }) { text, err ->
            val body = JSONObject().put("text", text).put("error", err).toString()
            activity.runOnUiThread { web.evaluateJavascript("window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote(body)})", null) }
        }
    }

    /** Debug builds only: run a spoken command as if it had been heard, so the voice path can be tested without speaking. The result goes to the log (tag MBuddyDebug). */
    @JavascriptInterface
    fun debugRespond(text: String) {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        Agent.testMode = true
        Thread {
            val r = Command.handle(activity.applicationContext, text)
            val body = JSONObject().put("say", r.say).put("did", org.json.JSONArray(r.did)).put("source", r.source).toString()
            android.util.Log.i("MBuddyDebug", body)
        }.start()
    }

    /** Debug builds only: listen for one request and write what the model on the phone heard to the log (tag MBuddyDebug). */
    @JavascriptInterface
    fun debugTranscribe() {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        Thread {
            val t0 = System.currentTimeMillis()
            val wav = Recorder.utterance()
            android.util.Log.i("MBuddyDebug", "recorded ${wav?.size ?: 0} bytes in ${System.currentTimeMillis() - t0} ms")
            if (wav != null) {
                val text = try { LocalModel.transcribe(activity, wav, Voice.appNames(activity)) } catch (e: Throwable) { "ERROR " + e.message }
                Dbg.i("MBuddyDebug", "model heard: $text")
            }
        }.start()
    }

    /** Debug builds only: how a sentence would be read as a message (who, what, which app), without sending or opening anything. Goes to the log (MBuddyDebug). */
    @JavascriptInterface
    fun debugParse(text: String) {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        val m = QuickRules.parseMessage(activity, text)
        Dbg.i("MBuddyDebug", "parse '$text' -> " + (if (m == null) "not a message" else "to='${m.person.name}' text='${m.body}' app=${m.app}"))
    }

    /** Debug builds only: run a task through the agent in test mode (see Agent.testMode). The log (MBuddyAgent, MBuddyTest) shows how it went. */
    @JavascriptInterface
    fun debugTask(goal: String): String {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return "not allowed"
        Agent.testMode = true
        return Agent.start(goal) ?: ""
    }

    /** Debug builds only: the screen as the planner sees it now (app, the short list for the model on the phone, the full list), so prompts can be tried on real screens. */
    @JavascriptInterface
    fun debugScreen(): String {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return "{}"
        val snap = ControlService.instance?.snapshot() ?: return "{}"
        return JSONObject().put("app", snap.pkg).put("compact", Planner.compactScreen(snap)).put("full", snap.lines).toString()
    }

    /** Debug builds only: show the buddy's bar and edge light in a given state (to check how they look), or hide them with mode "hide". */
    @JavascriptInterface
    fun debugCard(json: String) {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        val ov = ControlService.instance?.overlay ?: return
        val j = JSONObject(json)
        if (j.optString("mode") == "hide") { ov.hide(); return }
        // try another phone's cutout: "cutout":[left,top,right,bottom] in pixels, [] for none, "real" for this phone's own
        if (j.has("cutout")) {
            val c = j.opt("cutout")
            if (c == "real") { Cutout.overrideSet = false } else {
                val a = j.optJSONArray("cutout")
                Cutout.override = if (a != null && a.length() == 4) android.graphics.Rect(a.getInt(0), a.getInt(1), a.getInt(2), a.getInt(3)) else android.graphics.Rect()
                Cutout.overrideSet = true
            }
        }
        if (j.has("style")) Store.setOverlayStyle(activity, j.optString("style"))
        val b = j.optJSONArray("buttons")
        val buttons = (0 until (b?.length() ?: 0)).map { b!!.getJSONObject(it).let { o -> o.optString("id") to o.optString("label") } }
        ov.show(j.optString("mode", "working"), j.optString("text"), j.optString("step"), buttons, j.optBoolean("input"))
    }

    // ---- contacts: so "message Baba" can find Baba ----

    @JavascriptInterface fun contactsAllowed(): Boolean = trusted() && Contacts.allowed(activity)
    @JavascriptInterface fun askContacts() { if (trusted()) activity.runOnUiThread { activity.askContactsNow() } }

    /** Message a contact: opens the chat with the text typed, then the agent taps Send after reading it back. Returns "" or why not. */
    @JavascriptInterface
    fun messageContact(app: String, to: String, text: String): String = if (!trusted()) "Not allowed." else (Messaging.send(activity, app, to, text) ?: "")

    // ---- "hey buddy" and the things done while the screens were closed ----

    @JavascriptInterface fun wakeState(): String = if (trusted()) WakeWord.state(activity).toString() else "{}"
    @JavascriptInterface fun wakeModelDownload(): String = if (trusted()) (WakeWord.download(activity) ?: "") else "Not allowed."
    @JavascriptInterface fun wakeModelDelete() { if (trusted()) WakeWord.delete(activity) }

    /** Turn "hey buddy" on or off. Returns "" when done, or what the owner still has to do. */
    @JavascriptInterface
    fun wakeEnable(on: Boolean): String {
        if (!trusted()) return "Not allowed."
        if (!on) { Store.setWake(activity, false); WakeService.stop(activity); return "" }
        if (!WakeWord.installed(activity)) return "Download the wake-word model first."
        if (!Voice.canHear(activity)) { activity.runOnUiThread { activity.askMic() }; return "Allow the microphone, then switch it on again." }
        if (Build.VERSION.SDK_INT >= 33) activity.runOnUiThread { activity.askNotificationsNow() }
        Store.setWake(activity, true)
        WakeService.start(activity)
        return ""
    }

    // ---- the edge handle and the smart cards ----
    @JavascriptInterface fun handleState(): String = if (!trusted()) "{}" else JSONObject().put("on", Store.handle(activity)).put("side", Store.handleSide(activity)).put("panelListen", Store.panelListen(activity))
        .put("screenControl", ControlService.instance != null).toString()
    @JavascriptInterface fun setHandle(on: Boolean, side: String) {
        if (!trusted()) return
        Store.setHandle(activity, on); Store.setHandleSide(activity, side)
        ControlService.instance?.handle?.refresh()
    }
    @JavascriptInterface fun setFrequent(json: String) { if (trusted()) Store.setFrequent(activity, json) }
    @JavascriptInterface fun setAgenda(json: String) { if (trusted()) { Store.setAgenda(activity, json); Smart.reschedule(activity); Widgets.refreshAll(activity) } }
    @JavascriptInterface fun smartState(): String = if (!trusted()) "{}" else JSONObject().put("events", Store.smartEvents(activity)).put("tasks", Store.smartTasks(activity))
        .put("brief", Store.smartBrief(activity)).put("hour", Store.briefHour(activity)).toString()
    @JavascriptInterface fun setSmart(key: String, on: Boolean, hour: Int) {
        if (!trusted()) return
        Store.setSmart(activity, key, on); if (hour in 5..11) Store.setBriefHour(activity, hour)
        Smart.reschedule(activity)
    }
    @JavascriptInterface fun triggersState(): String = if (!trusted()) "{}" else JSONObject().put("msgCards", Store.msgCards(activity))
        .put("access", notificationAccessGranted(activity)).put("car", Store.carName(activity)).toString()
    @JavascriptInterface fun setMsgCards(on: Boolean) { if (trusted()) Store.setMsgCards(activity, on) }
    @JavascriptInterface fun openNotificationAccess() {
        if (!trusted()) return
        activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    @JavascriptInterface fun setCar(name: String) {
        if (!trusted()) return
        Store.setCarName(activity, name)
        if (name.isNotBlank() && android.os.Build.VERSION.SDK_INT >= 31 &&
            androidx.core.content.ContextCompat.checkSelfPermission(activity, android.Manifest.permission.BLUETOOTH_CONNECT) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            activity.requestPermissions(arrayOf(android.Manifest.permission.BLUETOOTH_CONNECT), 61)
    }
    // ---- permissions: what is allowed, and the way to allow it ----
    @JavascriptInterface
    fun permissionsState(): String {
        if (!trusted()) return "[]"
        fun has(p: String) = androidx.core.content.ContextCompat.checkSelfPermission(activity, p) == android.content.pm.PackageManager.PERMISSION_GRANTED
        val sdk = android.os.Build.VERSION.SDK_INT
        val items = listOf(
            Triple("mic", has(android.Manifest.permission.RECORD_AUDIO), false),
            Triple("notifications", sdk < 33 || has(android.Manifest.permission.POST_NOTIFICATIONS), false),
            Triple("contacts", Contacts.allowed(activity), false),
            Triple("calendar", Actions.calendarAllowed(activity), false),
            Triple("screen", ControlService.instance != null, false),
            Triple("overlay", android.provider.Settings.canDrawOverlays(activity), false),
            Triple("assistant", sdk >= 29 && runCatching { activity.getSystemService(android.app.role.RoleManager::class.java).isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT) }.getOrDefault(false), true),
            Triple("notifaccess", notificationAccessGranted(activity), true),
            Triple("bluetooth", sdk < 31 || has(android.Manifest.permission.BLUETOOTH_CONNECT), true),
        )
        val a = org.json.JSONArray()
        items.forEach { (id, ok, optional) -> a.put(JSONObject().put("id", id).put("granted", ok).put("optional", optional)) }
        return a.toString()
    }

    @JavascriptInterface
    fun requestPermission(id: String) {
        if (!trusted()) return
        activity.runOnUiThread {
            fun open(i: android.content.Intent) = runCatching { activity.startActivity(i.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) }
            when (id) {
                "mic" -> activity.askMic()
                "notifications" -> activity.askNotificationsNow()
                "contacts" -> activity.askContactsNow()
                "calendar" -> activity.askCalendar { }
                "screen" -> open(android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS))
                "overlay" -> open(android.content.Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:" + activity.packageName)))
                "notifaccess" -> open(android.content.Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                "assistant" -> openAssistantSettings()
                "bluetooth" -> if (android.os.Build.VERSION.SDK_INT >= 31) activity.requestPermissions(arrayOf(android.Manifest.permission.BLUETOOTH_CONNECT), 61)
                "app" -> open(android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.parse("package:" + activity.packageName)))
            }
        }
    }

    /** Save a text file to the Downloads folder (no permission needed on Android 10 and later). Returns where, or "" when it could not. */
    @JavascriptInterface
    fun saveToDownloads(name: String, text: String): String {
        if (!trusted()) return ""
        return runCatching {
            val safe = name.replace(Regex("[^A-Za-z0-9._-]"), "_").take(60)
            val cv = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Downloads.DISPLAY_NAME, safe); put(android.provider.MediaStore.Downloads.MIME_TYPE, "application/json")
                if (android.os.Build.VERSION.SDK_INT >= 29) put(android.provider.MediaStore.Downloads.RELATIVE_PATH, android.os.Environment.DIRECTORY_DOWNLOADS)
            }
            val uri = activity.contentResolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv) ?: return ""
            activity.contentResolver.openOutputStream(uri)?.use { it.write(text.toByteArray()) }
            "Downloads/$safe"
        }.getOrDefault("")
    }

    /** Forget everything this app keeps: its settings, the pairing, the cloud key, the inbox. Downloaded models stay. The page then reloads. */
    @JavascriptInterface
    fun wipeAll() {
        if (!trusted()) return
        Store.clear(activity)
        activity.getSharedPreferences("mbuddy_plain", android.content.Context.MODE_PRIVATE).edit().clear().apply()
        activity.getSharedPreferences("mbuddy_inbox", android.content.Context.MODE_PRIVATE).edit().clear().apply()
        activity.getSharedPreferences("mbuddy_usage", android.content.Context.MODE_PRIVATE).edit().clear().apply()
        runCatching { Store.setCloudKey(activity, "") }
        Smart.reschedule(activity)
        ControlService.instance?.handle?.hide()
    }

    // ---- the voice of the chosen buddy ----
    @JavascriptInterface
    fun voicesState(): String {
        if (!trusted()) return "{}"
        Voice.say(activity, "")             // starts the speech engine so its voices can be listed
        val a = org.json.JSONArray()
        val seen = HashMap<String, Int>()
        Voice.englishVoices().forEach {
            val country = it.locale.displayCountry.ifBlank { it.locale.language }
            val k = (seen[country] ?: 0) + 1; seen[country] = k
            a.put(JSONObject().put("name", it.name).put("label", "$country, voice $k" + (if (it.quality >= android.speech.tts.Voice.QUALITY_HIGH) " · high quality" else "")))
        }
        return JSONObject().put("voices", a).put("chosen", Store.voiceName(activity, Store.character(activity))).put("character", Store.character(activity)).toString()
    }
    @JavascriptInterface fun setBuddyVoice(name: String) { if (trusted()) Store.setVoiceName(activity, Store.character(activity), name) }
    @JavascriptInterface fun previewVoice() { if (trusted()) Voice.preview(activity) }
    @JavascriptInterface fun auditionVoice(name: String) { if (trusted()) Voice.auditionVoice(activity, name) }
    /** The phone's own way to add voices: the speech engine's voice-data page, or its text-to-speech settings. */
    @JavascriptInterface
    fun getMoreVoices() {
        if (!trusted()) return
        activity.runOnUiThread {
            val flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
            runCatching { activity.startActivity(android.content.Intent(android.speech.tts.TextToSpeech.Engine.ACTION_INSTALL_TTS_DATA).addFlags(flags)) }
                .onFailure { runCatching { activity.startActivity(android.content.Intent("com.android.settings.TTS_SETTINGS").addFlags(flags)) } }
        }
    }
    @JavascriptInterface fun voiceEngine(): String = if (trusted()) Store.voiceEngine(activity) else "auto"
    @JavascriptInterface fun setVoiceEngine(v: String) { if (trusted()) Store.setVoiceEngine(activity, v) }
    /** Privacy mode: {"on":bool,"until":ms or 0}. */
    @JavascriptInterface fun privacyState(): String = if (!trusted()) "{}" else JSONObject().put("on", Privacy.on(activity)).put("until", Store.privacyUntil(activity)).toString()
    /** Words about privacy mode ("turn privacy mode off"): applied, and what to say back; "" when it was not about privacy mode. */
    @JavascriptInterface fun privacyCommand(text: String): String = if (trusted()) (Privacy.voiceCommand(activity, text) ?: "") else ""
    @JavascriptInterface fun setPrivacy(on: Boolean, minutes: Int) { if (trusted()) Privacy.set(activity, on, minutes) }
    @JavascriptInterface fun panelListenOn(): Boolean = trusted() && Store.panelListen(activity)
    @JavascriptInterface fun setPanelListen(on: Boolean) { if (trusted()) Store.setPanelListen(activity, on) }
    @JavascriptInterface fun chordOn(): Boolean = trusted() && Store.chord(activity)
    @JavascriptInterface fun setChord(on: Boolean) { if (trusted()) Store.setChord(activity, on) }

    /** Whether MBUDDY is the phone's assistant (holding the power button, or the assistant gesture, calls the buddy). */
    @JavascriptInterface
    fun isAssistant(): Boolean = trusted() && Build.VERSION.SDK_INT >= 29 &&
        runCatching { activity.getSystemService(android.app.role.RoleManager::class.java).isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT) }.getOrDefault(false)

    /** Android's page for choosing the assistant app (an app cannot make itself the assistant; the owner picks it there). */
    @JavascriptInterface
    fun openAssistantSettings() {
        if (!trusted()) return
        activity.runOnUiThread {
            runCatching { activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)) }
                .onFailure { runCatching { activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS)) } }
        }
    }

    /** Needed so a voice command can open an app while the screen is off or another app is in front. */
    @JavascriptInterface
    fun openOverlaySettings() {
        if (trusted()) activity.runOnUiThread { runCatching { activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:" + activity.packageName))) } }
    }

    /** So Android does not stop the listening to save battery. */
    @JavascriptInterface
    fun openBatterySettings() {
        if (trusted()) activity.runOnUiThread { runCatching { activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.parse("package:" + activity.packageName))) } }
    }

    /** The screens give the app the buddy's instructions (for voice commands heard while they are closed), and take back what was done. */
    @JavascriptInterface fun setSystemPromptCloud(text: String) { if (trusted()) Store.setSystemTemplateCloud(activity, text) }
    @JavascriptInterface fun setSystemPrompt(text: String) { if (trusted()) Store.setSystemTemplate(activity, text) }
    @JavascriptInterface fun setRecent(json: String) { if (trusted()) Store.setRecent(activity, json) }
    /** The short copy the home-screen widgets draw from (open tasks, level, streak, quest floor). */
    @JavascriptInterface fun setWidget(json: String) { if (trusted()) { Store.setWidgetData(activity, json); Widgets.refreshAll(activity) } }
    @JavascriptInterface fun lastCrash(): String = if (trusted()) Store.lastCrash(activity) else ""
    @JavascriptInterface fun clearCrash() { if (trusted()) Store.setLastCrash(activity, "") }

    /** Opens the system file picker; the chosen file's text comes back as window.__nativePicked(text) (null when cancelled). */
    @JavascriptInterface fun pickFile() { if (trusted()) activity.runOnUiThread { activity.pickFileNow() } }

    /**
     * Debug builds only: a beep, then [seconds] of the microphone from several sources AT THE SAME TIME, with the loudest moment of each
     * second for each (0 to 32767). Speaking during it shows which source hears the voice and which one is attenuated.
     * The answer comes by window.__nativeHttp.
     */
    @JavascriptInterface
    fun micTest(id: String, seconds: Int) {
        if (!trusted() || activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        Thread {
            runCatching { android.media.ToneGenerator(android.media.AudioManager.STREAM_MUSIC, 90).startTone(android.media.ToneGenerator.TONE_PROP_BEEP2, 300) }
            Thread.sleep(500)
            val sources = listOf("voice_recognition" to android.media.MediaRecorder.AudioSource.VOICE_RECOGNITION, "mic" to android.media.MediaRecorder.AudioSource.MIC,
                "camcorder" to android.media.MediaRecorder.AudioSource.CAMCORDER, "voice_communication" to android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION)
            val results = java.util.concurrent.ConcurrentHashMap<String, JSONObject>()
            val end = System.currentTimeMillis() + seconds * 1000L
            val threads = sources.map { (name, src) ->
                Thread {
                    val r = JSONObject()
                    try {
                        val min = android.media.AudioRecord.getMinBufferSize(16000, android.media.AudioFormat.CHANNEL_IN_MONO, android.media.AudioFormat.ENCODING_PCM_16BIT)
                        val rec = android.media.AudioRecord(src, 16000, android.media.AudioFormat.CHANNEL_IN_MONO, android.media.AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 16384))
                        if (rec.state != android.media.AudioRecord.STATE_INITIALIZED) { r.put("error", "not initialized"); results[name] = r; return@Thread }
                        rec.startRecording()
                        val buf = ShortArray(1600)
                        val per = ArrayList<Int>(); var secPeak = 0; var secStart = System.currentTimeMillis(); var peak = 0
                        while (System.currentTimeMillis() < end) {
                            val k = rec.read(buf, 0, buf.size); if (k <= 0) continue
                            for (q in 0 until k) { val v = kotlin.math.abs(buf[q].toInt()); if (v > secPeak) secPeak = v; if (v > peak) peak = v }
                            if (System.currentTimeMillis() - secStart >= 1000) { per.add(secPeak); secPeak = 0; secStart = System.currentTimeMillis() }
                        }
                        rec.stop(); rec.release()
                        r.put("peak", peak).put("perSecondPeaks", org.json.JSONArray(per))
                    } catch (e: Exception) { r.put("error", e.javaClass.simpleName + ": " + (e.message ?: "")) }
                    results[name] = r
                }.also { it.start() }
            }
            threads.forEach { it.join() }
            val out = JSONObject(); sources.forEach { (n, _) -> out.put(n, results[n] ?: JSONObject().put("error", "no result")) }
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote(out.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    /** Debug builds only. */
    @JavascriptInterface fun widgetSelfTest(): String = if (trusted() && Dbg.debuggable) Widgets.selfTest(activity) else "release build"
    @JavascriptInterface fun drainInbox(): String = if (trusted()) Inbox.drain(activity) else "[]"

    // ---- the model on the phone ----

    @JavascriptInterface fun localModelState(): String = if (trusted()) LocalModel.state(activity).toString() else "{}"
    @JavascriptInterface fun localModelDownload(id: String): String = if (trusted()) (LocalModel.download(activity, id) ?: "") else "Not allowed."
    @JavascriptInterface fun localModelCancel() { if (trusted()) LocalModel.cancel() }
    @JavascriptInterface fun localModelDelete(id: String) { if (trusted()) LocalModel.delete(activity, id) }
    @JavascriptInterface fun setLocalBackend(name: String) { if (trusted()) { Store.setLocalBackend(activity, name); LocalModel.reload() } }
    @JavascriptInterface fun localModelUse(id: String) { if (trusted() && LocalModel.spec(id)?.tool == false) Store.setLocalModel(activity, id) }

    /** Ask the model on the phone once. Body: {"system":"...","user":"..."}. Answers {"ok":true,"text":"...","ms":N} or {"ok":false,"error":"..."}. */
    @JavascriptInterface
    fun localChatAsync(id: String, bodyJson: String) {
        if (!trusted()) return
        Thread {
            val out = try {
                val b = JSONObject(bodyJson)
                val schema = b.optJSONObject("schema")?.let { jsonToMap(it) }
                val text = LocalModel.chat(activity, b.optString("system"), b.optString("user"), schema)
                JSONObject().put("ok", true).put("text", text).put("ms", LocalModel.lastMs)
            } catch (e: Throwable) { JSONObject().put("ok", false).put("error", e.message ?: "The model couldn't answer.") }
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote(out.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    /**
     * Open a page from an answer's sources inside MBUDDY: a full-screen viewer with a Close button, so the owner never leaves the app.
     * Only https pages, no file access, and links inside it stay inside it.
     */
    @JavascriptInterface
    fun openLink(url: String, title: String) {
        if (!trusted()) return
        val u = runCatching { android.net.Uri.parse(url) }.getOrNull() ?: return
        if (u.scheme != "https") return
        activity.runOnUiThread {
            val d = android.app.Dialog(activity, android.R.style.Theme_Black_NoTitleBar_Fullscreen)
            val root = android.widget.LinearLayout(activity).apply { orientation = android.widget.LinearLayout.VERTICAL; setBackgroundColor(0xFF000000.toInt()) }
            val bar = android.widget.LinearLayout(activity).apply {
                orientation = android.widget.LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding((16 * activity.resources.displayMetrics.density).toInt(), (28 * activity.resources.displayMetrics.density).toInt(), (8 * activity.resources.displayMetrics.density).toInt(), (6 * activity.resources.displayMetrics.density).toInt())
            }
            val label = android.widget.TextView(activity).apply { text = title.ifBlank { u.host ?: "" }; setTextColor(0xFFF5F3EE.toInt()); textSize = 15f; maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END }
            val close = android.widget.Button(activity).apply { text = "Close"; setOnClickListener { d.dismiss() } }
            bar.addView(label, android.widget.LinearLayout.LayoutParams(0, android.widget.LinearLayout.LayoutParams.WRAP_CONTENT, 1f)); bar.addView(close)
            val page = WebView(activity).apply {
                settings.javaScriptEnabled = true; settings.domStorageEnabled = true; settings.allowFileAccess = false; settings.allowContentAccess = false
                webViewClient = object : android.webkit.WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest) = request.url.scheme != "https"
                }
                loadUrl(url)
            }
            root.addView(bar); root.addView(page, android.widget.LinearLayout.LayoutParams(android.widget.LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
            d.setContentView(root); d.setOnDismissListener { runCatching { page.destroy() } }; d.show()
        }
    }

    /** Answer a question from the web. Body: {"question":"...","query":"..."}. Answers {"ok":true,"say":"...","sources":[{"title","url","host"}]} or {"ok":false}. */
    @JavascriptInterface
    fun webAnswerAsync(id: String, bodyJson: String) {
        if (!trusted()) return
        Thread {
            val out = try {
                val b = JSONObject(bodyJson)
                val q = b.optString("query").ifBlank { b.optString("question") }
                val a = WebSearch.answer(activity, b.optString("question").ifBlank { q }, q, NativeBrain.nowText())
                if (a == null) JSONObject().put("ok", false) else JSONObject().put("ok", true).put("say", a.say).put("sources", org.json.JSONArray(a.sources.map {
                    JSONObject().put("title", it.title).put("url", it.url).put("host", android.net.Uri.parse(it.url).host?.removePrefix("www.") ?: "") }))
            } catch (e: Throwable) { JSONObject().put("ok", false).put("error", e.message ?: "") }
            val js = "window.__nativeHttp(${JSONObject.quote(id)}, 200, ${JSONObject.quote(out.toString())})"
            activity.runOnUiThread { web.evaluateJavascript(js, null) }
        }.start()
    }

    /** A request to do something in an app goes straight to screen control (NativeBrain.appTask). True when it started. */
    @JavascriptInterface
    fun deviceAction(text: String): String = if (!trusted()) "" else NativeBrain.deviceAction(activity, text)?.let {
        JSONObject().put("say", it.say).put("did", org.json.JSONArray(it.did)).toString() } ?: ""

    @JavascriptInterface
    fun routeAppTask(text: String): Boolean = trusted() && NativeBrain.startAppTask(activity, text)

    private fun jsonToMap(o: JSONObject): Map<String, Any> = o.keys().asSequence().associateWith { k -> jsonValue(o.get(k)) }
    private fun jsonValue(v: Any): Any = when (v) {
        is JSONObject -> jsonToMap(v)
        is org.json.JSONArray -> (0 until v.length()).map { jsonValue(v.get(it)) }
        else -> v
    }

    @JavascriptInterface fun setPlanner(name: String) { if (trusted()) Store.setPlanner(activity, name) }
    @JavascriptInterface fun getPlanner(): String = if (trusted()) Store.planner(activity) else "auto"
    @JavascriptInterface fun setCloudModel(name: String) { if (trusted()) Store.setCloudModel(activity, name) }

    private fun toast(text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()
}
