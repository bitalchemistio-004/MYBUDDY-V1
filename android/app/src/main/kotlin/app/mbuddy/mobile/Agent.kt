package app.mbuddy.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Does a task on the phone, in any app, until it is finished: look at the screen, ask the brain (on the PC) for the next step, do it,
 * look again. The buddy's card shows what it is doing and talks. Three things are decided HERE, never by the brain:
 *  - anything that commits (send, pay, delete, post, call, allow...) stops and reads back what is about to happen, and goes ahead only on a yes;
 *  - some apps (banking, payment, passwords, the phone's settings and permission screens) are never operated;
 *  - password fields are never filled.
 * The owner can stop it at any moment (the cross on the card, saying stop, or turning screen control off).
 */
object Agent {
    private const val MAX_STEPS = 30
    private const val ASK_TIMEOUT_S = 90L
    private val answers = LinkedBlockingQueue<String>()
    @Volatile private var cancel = false
    @Volatile var running = false
        private set
    @Volatile var goal = ""
        private set

    @Volatile private var recipient = ""
    @Volatile private var message = ""
    @Volatile private var inApp = ""

    /** Debug builds only: every question to the owner is answered "no" straight away and logged, so a task can be run to the point where it would send, pay or ask, and no further. */
    @Volatile var testMode = false

    /**
     * Begin a task. Returns why it cannot, or null when it has started. [recipient] and [message] are set for a message already typed into
     * an open chat: the message box is made to hold exactly [message] (an app may keep an old draft in it) before anything else, and the
     * task goes on only if that chat is in [inApp] (a package), so a message meant for WhatsApp is never sent from another app.
     */
    fun start(goal: String, recipient: String = "", message: String = "", inApp: String = ""): String? {
        val svc = ControlService.instance ?: return "Screen control is off. Turn it on in Android settings, Accessibility, MBUDDY."
        if (Privacy.on(svc)) return Privacy.REFUSAL
        if (running) return "I'm already working on something. Stop it first."
        if (!Cloud.hasKey(svc) && !Store.paired(svc) && LocalModel.active(svc) == null)
            return "I have no brain to plan the steps yet. Add your cloud key, pair with your PC, or download the model on the phone (Settings, Brain)."
        if (goal.isBlank()) return "Tell me what to do."
        cancel = false; answers.clear(); running = true; this.goal = goal.take(600); Agent.recipient = recipient; Agent.message = message.trim(); Agent.inApp = inApp
        worker = Thread({
            try { run(svc, this.goal) } catch (e: Exception) { android.util.Log.w("MBuddy", "agent failed", e) } finally { running = false; svc.overlay.hide(); Voice.stopListening() }
        }, "mbuddy-agent").also { it.start() }
        return null
    }

    @Volatile private var worker: Thread? = null

    /**
     * Stop, at once: the card goes the moment the owner taps (it used to wait for the agent to reach its next check, which could be
     * a model's answer, a pause or a sentence being spoken, several seconds later), the voice is cut, and the agent's thread is
     * interrupted so its pauses end. Nothing is done after this: every step looks at [cancel] before it acts.
     */
    private fun stopNow() {
        cancel = true; answers.offer("stop")
        Voice.shutUp(); Voice.stopListening()
        ControlService.instance?.overlay?.hide()
        worker?.takeIf { it.isAlive && it !== Thread.currentThread() }?.interrupt()
    }

    fun stop(@Suppress("UNUSED_PARAMETER") why: String) { if (running) stopNow() else { cancel = true; answers.offer("stop"); Voice.shutUp() } }

    /** What the owner chose on the card or said: "yes", "no", "stop", or their own words. */
    fun answer(what: String) { if (what == "stop" && running) stopNow() else { if (what == "stop") { cancel = true; Voice.shutUp() }; answers.offer(what) } }

    private class Pending(val yes: Boolean, val said: String)

    private fun run(svc: ControlService, goal: String) {
        val ctx = svc.applicationContext
        val ov = svc.overlay
        val speak = Store.voice(ctx)
        val history = ArrayList<JSONObject>()
        var lastSig = ""
        var repeats = 0
        var prevHash = 0
        var needsNote = false
        val missingApps = HashSet<String>()                 // apps the planner asked for that are not on the phone

        fun show(mode: String, text: String, n: Int = 0, buttons: List<Pair<String, String>> = emptyList()) =
            ov.show(mode, text, if (n > 0) "step $n" else "", buttons)
        fun say(text: String) { if (speak && text.isNotBlank()) Voice.say(ctx, text) }
        fun finish(mode: String, text: String) {
            Dbg.i("MBuddyAgent", "finish $mode: $text")
            if (cancel) return                  // the owner stopped it: the card is already gone, nothing more is shown or said
            show(mode, text)
            if (speak) Voice.sayAndWait(ctx, text, 8000)
            Thread.sleep(if (speak) 800 else 3500)
        }

        show("working", "On it.")
        (Shortcuts.appInGoal(ctx, goal)?.let { (pkg, _) -> Risk.appDenied(pkg) } ?: deniedNamed(ctx, goal))?.let { finish("fail", "I don't operate $it."); return }
        if (message.isEmpty() && inApp.isEmpty()) Shortcuts.prepare(ctx, svc, goal)?.let { pre ->
            android.util.Log.i("MBuddyAgent", "first move: ${pre.did}")
            history.add(entry(pre.did, pre.result))
            settle(svc, minMs = 2500, maxMs = 9000, still = 3)     // an app or a page that was just opened loads its list after its frame
        }
        // a message or call handed on from Messaging: its app must be the one in front, and a message box must hold exactly the message
        if (inApp.isNotEmpty() && !waitFor(svc, inApp)) {
            val name = runCatching { ctx.packageManager.getApplicationLabel(ctx.packageManager.getApplicationInfo(inApp, 0)).toString() }.getOrDefault("The app")
            finish("fail", "$name didn't open, so I did nothing."); return
        }
        if (message.isNotEmpty()) exactMessage(svc, message)?.let { history.add(entry(it, "the message box holds exactly the message")) }
        for (n in 1..MAX_STEPS) {
            if (cancel) { finish("fail", "Okay, I stopped."); return }
            var snap: Snapshot? = null
            for (i in 0 until 5) { snap = svc.snapshot(); if (snap != null) break; Thread.sleep(400) }
            if (snap == null) { history.add(entry("looked at the screen", "nothing readable yet")); Thread.sleep(700); continue }
            if (snap.pkg != ctx.packageName) Risk.appDenied(snap.pkg)?.let { finish("fail", "I don't operate $it."); return }

            // Tell the planner whether its last action changed anything: a small model otherwise repeats a step that did nothing.
            if (needsNote && history.isNotEmpty()) {
                val h = history.last()
                h.put("result", h.optString("result") + if (snap.lines.hashCode() == prevHash) " (the screen did NOT change)" else " (the screen changed)")
                needsNote = false
            }
            android.util.Log.i("MBuddyAgent", "step $n app=${snap.pkg} elements=${snap.els.size}")
            if (snap.hidden > 0) {
                // on-screen text that tried to give the buddy instructions: hidden from the brain, and the owner is told
                android.util.Log.w("MBuddyAgent", "hid ${snap.hidden} piece(s) of screen text that read like instructions")
                Inbox.add(ctx, org.json.JSONObject().put("type", "log").put("text", "Hid ${snap.hidden} piece${if (snap.hidden == 1) "" else "s"} of text on the screen that tried to give me instructions (in ${snap.pkg.substringAfterLast('.')})."))
            }
            val app = runCatching { ctx.packageManager.getApplicationLabel(ctx.packageManager.getApplicationInfo(snap.pkg, 0)).toString() }.getOrDefault(snap.pkg)
            val reply = Planner.step(ctx, goal, snap, history, app)
            if (reply.error != null) { finish("fail", reply.error); return }
            val a = reply.action!!
            val kind = a.getString("do")
            android.util.Log.i("MBuddyAgent", "brain says $a")
            val line = a.optString("say")
            if (line.isNotBlank() && kind != "done" && kind != "fail" && kind != "ask") { show("working", line, n); say(line) }
            else show("working", describe(kind, a, snap), n)

            val sig = snap.pkg + snap.lines.hashCode() + a.toString()
            repeats = if (sig == lastSig) repeats + 1 else 0
            lastSig = sig
            if (repeats >= 3) { finish("fail", "I'm going round in circles, so I stopped."); return }
            if (repeats >= 1 && kind != "done" && kind != "fail" && kind != "ask" && kind != "wait") {
                // the same step on the same screen: it did nothing last time, so it is not done again; the planner is told and asked again
                history.add(entry(describe(kind, a, snap), "NOT DONE: you just did exactly this and the screen did not change. If the task is already finished, answer done; otherwise choose a different element"))
                continue
            }

            when (kind) {
                "done" -> { finish("done", line.ifBlank { "Done." }); return }
                "fail" -> { finish("fail", line.ifBlank { "I couldn't do that." }); return }
                "ask" -> {
                    val opts = a.optJSONArray("options")
                    val choices = (0 until (opts?.length() ?: 0)).map { "opt$it" to opts!!.getString(it) }
                    val said = askOwner(ctx, line.ifBlank { "What should I do?" }, choices, input = choices.isEmpty())
                    if (cancel) { finish("fail", "Okay, I stopped."); return }
                    val answer = choices.firstOrNull { it.first == said }?.second ?: said
                    history.add(entry("asked the owner: ${line.take(100)}", "owner said: ${answer.take(100)}"))
                }
                else -> {
                    var result = execute(svc, ctx, snap, a, history)
                    if (result == null) { finish("fail", "Okay, I stopped."); return }
                    if (result.startsWith("refused: I don't operate")) { finish("fail", result.removePrefix("refused: ").replaceFirstChar { it.uppercase() } + "."); return }
                    if (kind == "open" && result.startsWith("no installed app")) {
                        // the second time the planner asks for an app that is not there, it is said plainly instead of going round
                        val name = a.optString("app").trim().lowercase()
                        if (!missingApps.add(name)) { finish("fail", "There's no app called ${a.optString("app").trim()} on this phone."); return }
                    }
                    var did = describe(kind, a, snap)
                    // further actions chained in the same step (a calculator's keys, a few letters): each is done on a fresh look at the screen
                    val more = a.optJSONArray("more")
                    if (more != null && !result.startsWith("there is no") && !result.startsWith("could not") && !result.startsWith("refused")) {
                        for (i in 0 until more.length()) {
                            if (cancel) break
                            Thread.sleep(450)
                            val s2 = svc.snapshot() ?: break
                            if (s2.pkg != snap.pkg) break
                            val m = more.getJSONObject(i)
                            val r2 = execute(svc, ctx, s2, m, history) ?: break
                            did += ", then " + describe(m.getString("do"), m, s2)
                            result = r2
                            if (r2.startsWith("there is no") || r2.startsWith("could not") || r2.startsWith("refused") || r2.startsWith("did NOT")) break
                        }
                    }
                    history.add(entry(did, result))
                    prevHash = snap.lines.hashCode(); needsNote = true
                }
            }
            settle(svc, minMs = 500, maxMs = 3000, still = 2)      // look again once the screen has stopped changing
        }
        finish("fail", "That took too many steps, so I stopped.")
    }

    private fun entry(did: String, result: String) = JSONObject().put("did", did).put("result", result)

    /**
     * Wait until the screen has stopped changing: [still] looks in a row, 400 ms apart, that are the same, after at least [minMs] and at
     * most [maxMs]. Measured on YouTube (2026-10-09): a search page shows its frame at once and its results about 8 s later; a planner that
     * looks at the frame acts on a screen with nothing on it.
     */
    private fun settle(svc: ControlService, minMs: Long, maxMs: Long, still: Int) {
        val start = System.currentTimeMillis()
        var last = 0; var same = 0
        while (!cancel) {
            val now = System.currentTimeMillis() - start
            if (now >= maxMs) break
            val snap = svc.snapshot()
            val h = if (snap == null || snap.loading) 0 else snap.lines.hashCode()      // a spinner on screen is never settled
            same = if (h == last && h != 0) same + 1 else 0
            last = h
            if (same >= still - 1 && now >= minMs) break
            Thread.sleep(400)
        }
        android.util.Log.i("MBuddyAgent", "screen settled in ${System.currentTimeMillis() - start} ms")
    }

    /**
     * Make the open chat's message box hold exactly [text]. Waits up to 5 s for the chat to appear. Returns what was done, or null when
     * the box already held it or no box was found (the planner and the read-back still see the screen as it is).
     */
    /** Whether [pkg] comes to the front within 6 s. */
    private fun waitFor(svc: ControlService, pkg: String): Boolean {
        repeat(12) { if (svc.snapshot()?.pkg == pkg) return true; Thread.sleep(500) }
        android.util.Log.w("MBuddyAgent", "expected $pkg in front, found ${svc.snapshot()?.pkg}")
        return false
    }

    private fun exactMessage(svc: ControlService, text: String): String? {
        val own = svc.packageName
        repeat(10) {
            val snap = svc.snapshot()
            val box = snap?.takeIf { it.pkg != own }?.els?.filter { it.editable && !it.password }?.let { boxes ->
                boxes.firstOrNull { it.idName == "entry" } ?: boxes.firstOrNull { it.node.isFocused } ?: boxes.lastOrNull()
            }
            if (box != null) {
                if (box.text.trim() == text) return null
                Dbg.i("MBuddyAgent", "message box held '${box.text.take(80)}', set to the message")
                return if (svc.type(box.node, text)) "replaced what was in the message box with the message" else null
            }
            Thread.sleep(500)
        }
        return null
    }

    private fun describe(kind: String, a: JSONObject, snap: Snapshot): String = when (kind) {
        "tap" -> "tap " + (target(snap, a)?.let { "\"${it.label}\"" } ?: a.optString("label").ifEmpty { "#${a.optInt("id")}" })
        "type" -> "type \"${a.optString("text").take(30)}\" into " + (target(snap, a)?.label?.let { "\"$it\"" } ?: a.optString("label").ifEmpty { "#${a.optInt("id")}" })
        "open" -> "open ${a.optString("app")}"
        "open_url" -> "open a link"
        "scroll" -> "scroll ${a.optString("dir")}"
        else -> kind
    }

    private class BrainReply(val action: JSONObject?, val error: String?)

    private fun askBrain(ctx: Context, goal: String, snap: Snapshot, history: List<JSONObject>): BrainReply {
        val app = runCatching { ctx.packageManager.getApplicationLabel(ctx.packageManager.getApplicationInfo(snap.pkg, 0)).toString() }.getOrDefault(snap.pkg)
        val body = JSONObject().put("goal", goal).put("screen", snap.lines.take(8500)).put("app", app)
            .put("history", JSONArray(history.takeLast(8).map { it }))
        var tries = 0
        while (true) {
            val r = Pc.request(ctx, "POST", "/remote/ui/phone-agent/step", body.toString())
            when {
                r.status == 200 -> {
                    val a = runCatching { JSONObject(r.body).getJSONObject("action") }.getOrNull()
                    return if (a != null) BrainReply(a, null) else BrainReply(null, "The brain's answer didn't make sense.")
                }
                r.status == 502 && ++tries < 3 -> { Thread.sleep(500); continue }
                r.status == 401 -> return BrainReply(null, "Your PC doesn't know this phone. Pair it again.")
                r.status == 503 || r.status == 0 -> {
                    val why = runCatching { JSONObject(r.body).optString("error") }.getOrDefault("")
                    return BrainReply(null, if (r.status == 0) "I can't reach your PC, and it holds the brain for this." else why.ifBlank { "The brain isn't available right now." })
                }
                else -> return BrainReply(null, "The brain couldn't plan the next step.")
            }
        }
    }

    /** Do one step. Returns what happened in a few words, or null when the owner stopped everything. */
    private fun execute(svc: ControlService, ctx: Context, snap: Snapshot, a: JSONObject, history: List<JSONObject>): String? {
        when (a.getString("do")) {
            "back" -> return if (svc.back()) "went back" else "could not go back"
            "home" -> return if (svc.home()) "went home" else "could not go home"
            "wait" -> { Thread.sleep(1500); return "waited" }
            "scroll" -> return if (svc.scroll(snap, a.optString("dir") != "up")) "scrolled" else "nothing to scroll"
            "open" -> return openApp(ctx, a.getString("app"))
            "open_url" -> return openUrl(ctx, a.getString("url"))
            "type" -> {
                val el = target(snap, a) ?: return "there is no such element"
                val text = a.optString("text")
                if (el.password) return "refused: that is a password field"
                // An email address, a phone number or a code is typed only when the owner said it: never one the model made up or read elsewhere
                if (Regex("@|\\d{6,}").containsMatchIn(text) && !goal.contains(text.trim(), ignoreCase = true)) return "refused: I only type addresses, numbers and codes that you gave me"
                if (!el.editable) {
                    // The keys of a calculator or keypad: "type 17×23" means tapping those keys
                    keysFor(snap, text)?.let { keys ->
                        for (k in keys) { svc.tap(k.node); Thread.sleep(250) }
                        return "tapped the keys ${keys.joinToString(" ") { it.label }}"
                    }
                    // A search bar that opens a text field when tapped: tap it, then type in the field that appears
                    if (svc.tap(el.node)) {
                        Thread.sleep(700)
                        val s2 = svc.snapshot()
                        val field = s2?.els?.firstOrNull { it.editable && !it.password && it.node.isFocused } ?: s2?.els?.firstOrNull { it.editable && !it.password }
                        if (field != null && svc.type(field.node, text)) return "tapped it, then typed in the text field that opened"
                    }
                    return "that is not a text field, and tapping it did not open one"
                }
                if (el.text.trim().equals(text.trim(), true)) return "that text is already there"
                return if (svc.type(el.node, text)) "typed it" else "could not type there"
            }
            "tap" -> {
                val el = target(snap, a) ?: return "there is no such element"
                val verb = Risk.commits(el)
                if (verb != null) {
                    val decision = confirm(ctx, snap, el, verb)
                    if (cancel) return null
                    if (!decision.yes) {
                        return if (decision.said.isBlank() || Risk.isNo(decision.said)) { cancel = true; null }
                        else "did NOT tap; the owner said: ${decision.said.take(120)}"
                    }
                }
                return if (svc.tap(el.node)) "tapped" else "could not tap it"
            }
        }
        return "unknown step"
    }

    /** The keys to tap for [text] on a keypad (each character a key whose name is that character), or null when they are not all there. */
    private fun keysFor(snap: Snapshot, text: String): List<El>? {
        val alias = mapOf('*' to listOf("×", "*", "x", "multiply", "times"), 'x' to listOf("×", "x", "multiply"), '/' to listOf("÷", "/", "divide"),
            '-' to listOf("−", "-", "minus", "subtract"), '+' to listOf("+", "plus", "add"), '=' to listOf("=", "equals"), '%' to listOf("%", "percent"),
            '.' to listOf(".", "point", "decimal"))
        val chars = text.replace(" ", "").lowercase()
        if (chars.isEmpty() || chars.length > 20) return null
        val keys = chars.map { c ->
            val names = alias[c] ?: listOf(c.toString())
            snap.els.firstOrNull { e -> !e.editable && names.any { n -> e.label.trim().equals(n, true) } } ?: return null
        }
        return keys
    }

    /** The element an action means: its number, or else its name (exact, then a name that contains it). */
    private fun target(snap: Snapshot, a: JSONObject): El? {
        if (a.has("id")) return snap.el(a.getInt("id"))
        val want = a.optString("label").trim().lowercase()
        if (want.isEmpty()) return null
        return snap.els.firstOrNull { it.label.trim().lowercase() == want && !it.password }
            ?: snap.els.firstOrNull { it.label.lowercase().contains(want) && it.kind != "text" && !it.password }
    }

    /** Read back what is about to happen and wait for the owner's yes. Buttons on the card, or the voice. */
    private fun confirm(ctx: Context, snap: Snapshot, el: El, verb: String): Pending {
        val v = verb.lowercase()
        val writes = v in setOf("send", "post", "publish", "share", "reply", "tweet")
        val prompt = if (writes && snap.typed.isNotBlank()) "${if (recipient.isNotBlank()) "To $recipient. " else ""}I wrote: ${snap.typed.take(280)}. Shall I $v it?"
        else if (writes && message.isNotBlank()) "${if (recipient.isNotBlank()) "To $recipient. " else ""}The message: $message. Shall I $v it?"   // an app that hides its text box (Google Messages): the text MBUDDY handed it
        else if (v == "call" && recipient.isNotBlank()) "Call $recipient now?"
        else "I'm about to tap ${if (el.label.isNotBlank()) "\"${el.label}\"" else v}. Is that okay?"
        val said = askOwner(ctx, prompt, listOf("yes" to "Yes, $v", "no" to "No"))
        return when {
            said == "yes" || Risk.isYes(said) && !Risk.isNo(said) -> Pending(true, said)
            else -> Pending(false, if (said == "no") "" else said)
        }
    }

    /** Show a question with buttons, say it, listen, and return "yes", "no", "stop" or what was said. */
    private fun askOwner(ctx: Context, question: String, buttons: List<Pair<String, String>>, input: Boolean = false): String {
        val ov = ControlService.instance?.overlay ?: return "no"
        if (testMode) { android.util.Log.i("MBuddyTest", "WOULD ASK: $question"); return "no" }
        answers.clear()
        ov.show("asking", question, "", buttons, input)
        if (Store.voice(ctx)) Voice.sayAndWait(ctx, question, 25_000)
        val closed = buttons.map { it.second.lowercase().removePrefix("yes, ") }.let { labels ->
            if (buttons.isEmpty()) emptyList() else (labels + buttons.map { it.first } +
                (if (buttons.any { it.first == "yes" }) listOf("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "send it", "do it", "confirm") else emptyList()) +
                (if (buttons.any { it.first == "no" }) listOf("no", "nope", "cancel", "stop", "don't", "never mind") else emptyList())).distinct()
        }
        val hear = (Voice.canHear(ctx) || WakeWord.installed(ctx)) && Store.voice(ctx)
        // Keep listening until the owner answers (or the time is up): a quiet moment or a mumble just starts another listen.
        val listenAgain = {
            if (closed.isNotEmpty() && WakeWord.installed(ctx)) {
                // a question with a short list of answers: only those words can be heard, so "yes" cannot become a name
                Thread {
                    val t = OfflineListen.oneOf(ctx, closed, 10)
                    Dbg.i("MBuddyAgent", "heard (closed list): \"$t\"")
                    answers.offer(if (t.isNotBlank()) "voice:$t" else "silence")
                }.start()
            } else Voice.listen(ctx) { t, err ->
                Dbg.i("MBuddyAgent", "heard: \"$t\" (code $err)")
                answers.offer(if (t.isNotBlank()) "voice:$t" else if (err == 9) "nomic" else "silence")
            }
        }
        val prompt = if (input) "Listening. Say your answer, or type it." else if (buttons.isNotEmpty()) "Listening. Say " + buttons.joinToString(" or ") { "\"" + it.second.lowercase() + "\"" } + ", or tap." else ""
        if (hear) { ov.show("asking", question, prompt, buttons, input); listenAgain() }
        val until = System.currentTimeMillis() + ASK_TIMEOUT_S * 1000
        while (System.currentTimeMillis() < until && !cancel) {
            val got = answers.poll(1, TimeUnit.SECONDS) ?: continue
            when {
                got == "yes" || got == "no" || got.startsWith("opt") -> { Voice.stopListening(); return got }
                got.startsWith("text:") -> { Voice.stopListening(); return got.removePrefix("text:") }
                got == "stop" -> { Voice.stopListening(); return "stop" }
                got == "silence" -> { if (hear) { Thread.sleep(300); listenAgain() } }
                got == "nomic" -> ov.show("asking", question, "No microphone access: use the buttons", buttons, input)
                got.startsWith("voice:") -> {
                    val t = got.removePrefix("voice:")
                    Voice.stopListening()
                    if (input) return t
                    val byName = buttons.firstOrNull { it.second.equals(t.trim(), true) }
                    if (byName != null) return byName.first
                    return if (Risk.isNo(t) && !Risk.isYes(t)) "no" else if (Risk.isYes(t) && !Risk.isNo(t)) "yes" else t
                }
            }
        }
        Voice.stopListening()
        return "no"           // no answer is a no: nothing is ever done on silence
    }

    /** The reason, when [words] name an installed app that is never operated (by its name on the phone, or a distinctive part of its package). */
    private fun deniedNamed(ctx: Context, words: String): String? {
        val w = words.lowercase()
        val generic = setOf("android", "mobile", "money", "bank", "wallet", "google", "apps", "client", "global", "lite")
        val pm = ctx.packageManager
        for (ri in pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)) {
            val pkg = ri.activityInfo.packageName
            val why = Risk.appDenied(pkg) ?: continue
            val label = ri.loadLabel(pm).toString().lowercase()
            if (label.length >= 4 && w.contains(label)) return why
            if (pkg.lowercase().split('.').any { it.length >= 5 && it !in generic && Regex("\\b" + Regex.escape(it) + "\\b").containsMatchIn(w) }) return why
        }
        return null
    }

    private fun openApp(ctx: Context, name: String): String {
        val pm = ctx.packageManager
        val all = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
        val hit = Actions.appMatch(ctx, name) ?: run {
            // Say what IS installed, near that name, so the planner can pick one instead of asking again
            val first = name.lowercase().take(3)
            val near = all.map { it.second }.filter { it.lowercase().contains(first) || name.lowercase().split(' ').any { w -> w.length > 2 && it.lowercase().contains(w) } }.take(6)
            val denied = all.firstOrNull { Risk.appDenied(it.first) != null && it.second.equals(name, true) } ?: deniedNamed(ctx, name)
            return if (denied != null) "refused: I don't operate banking, payment, password or security apps" else
                "no installed app is called '$name'" + if (near.isNotEmpty()) "; installed apps with similar names: ${near.joinToString(", ")}" else ""
        }
        Risk.appDenied(hit.first)?.let { return "refused: I don't operate $it" }
        val launch = pm.getLaunchIntentForPackage(hit.first) ?: return "could not open ${hit.second}"
        return try { ctx.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); "opened ${hit.second}" } catch (e: Exception) { "could not open ${hit.second}" }
    }

    private fun openUrl(ctx: Context, url: String): String {
        val scheme = url.substringBefore(':').lowercase()
        if (scheme !in setOf("https", "tel", "smsto", "sms", "mailto", "geo")) return "refused: not a link I open"
        val uri = Uri.parse(url)
        val intent = when (scheme) {
            "tel" -> Intent(Intent.ACTION_DIAL, uri)                                   // the dialer, ready: never a call by itself
            "smsto", "sms" -> Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + (uri.schemeSpecificPart.substringBefore('?')))).putExtra("sms_body", uri.getQueryParameter("body") ?: "")
            "mailto" -> Intent(Intent.ACTION_SENDTO, uri)
            else -> Intent(Intent.ACTION_VIEW, uri)
        }
        Risk.appDenied(intent.resolveActivity(ctx.packageManager)?.packageName ?: "")?.let { return "refused: I don't operate $it" }
        return try { ctx.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); "opened the link" } catch (e: Exception) { "no app can open that link" }
    }
}
