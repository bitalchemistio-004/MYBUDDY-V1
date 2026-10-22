package app.mbuddy.mobile

import android.content.Context
import android.net.Uri
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The buddy's brain for when the app's screens are not open: a voice command heard by "hey buddy" or the assistant button. It does what
 * the screens' brain does (js/papp/brain.js): the model answers in a small JSON (what to say, and tools to use), the tools run, and the
 * reply is spoken. The screens hand over the buddy's instructions (their persona, tasks and notes) whenever they change, so this knows
 * the same things. Tasks and notes go into the Inbox for the screens to pick up.
 */
object NativeBrain {
    class Result(val say: String, val did: List<String>, val source: String)

    private val CHAT_SCHEMA: Map<String, Any> = mapOf(
        "type" to "object",
        "properties" to mapOf(
            "do" to mapOf("type" to "array", "items" to mapOf("type" to "object", "properties" to mapOf(
                "tool" to mapOf("type" to "string", "enum" to listOf("timer", "alarm", "task", "event", "note", "call", "sms", "navigate", "search", "web", "open_app", "message", "phone")),
                "seconds" to mapOf("type" to "integer"), "hour" to mapOf("type" to "integer"), "minute" to mapOf("type" to "integer"),
                "label" to mapOf("type" to "string"), "title" to mapOf("type" to "string"), "when" to mapOf("type" to "string"),
                "start" to mapOf("type" to "string"), "place" to mapOf("type" to "string"), "text" to mapOf("type" to "string"),
                "number" to mapOf("type" to "string"), "body" to mapOf("type" to "string"), "query" to mapOf("type" to "string"), "goal" to mapOf("type" to "string"), "app" to mapOf("type" to "string"), "to" to mapOf("type" to "string"),
            ), "required" to listOf("tool"))),
            "say" to mapOf("type" to "string"),
        ),
        "required" to listOf("do", "say"),
    )

    private val CLAIMS = Regex("(?i)\\bi(?:'ve| have)?\\s+(?:just\\s+)?(?:set|sent|added|saved|created|scheduled|opened|called|messaged|started|put|made|booked|written|noted)\\b")

    fun nowText(): String = now()

    private fun notReady(where: String) = when (where) {
        "cloud" -> "You chose the cloud brain, but no cloud key is saved. Add it in Settings, Brain, or choose Auto."
        "pc" -> "You chose your PC's brain, but this phone isn't paired with a PC. Pair it in Settings, or choose Auto."
        else -> "You chose the model on this phone, but none is downloaded. Download one in Settings, Brain, or choose Auto."
    }

    /** Words to say from an answer that is not usable JSON: its "say" if it can be found, never the raw JSON itself. */
    private fun spokenFrom(raw: String): String {
        val t = raw.trim()
        if (!t.startsWith("{") && !t.contains("\"do\"")) return t.take(600)
        Regex("\"say\"\\s*[:,]\\s*\"([^\"]{2,400})\"").find(t)?.let { return it.groupValues[1] }
        return "Sorry, my words came out jumbled. Could you say that again?"
    }

    /** Start [text] as a screen-control task when it is one (see appTask). True when it started. */
    fun startAppTask(ctx: Context, text: String): Boolean {
        if (!appTask(ctx, text)) return false
        val ok = Agent.start(text) == null
        if (ok) Dbg.i("MBuddyBrain", "app task, straight to screen control: $text")
        return ok
    }

    private fun now(): String = SimpleDateFormat("EEE d MMM yyyy HH:mm", Locale.getDefault()).format(Date()) + " (" +
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm", Locale.US).format(Date()) + ")"

    /** What was said, to what happens. Call off the main thread. Never throws. */
    fun respond(ctx: Context, text: String, alternatives: List<String> = emptyList()): Result {
        val result = try { think(ctx, text, alternatives) } catch (e: Exception) { Result("I couldn't answer that: ${e.message ?: "something went wrong"}.", emptyList(), "error") }
        Inbox.add(ctx, JSONObject().put("type", "convo").put("you", text).put("me", result.say).put("source", result.source).put("did", JSONArray(result.did)))
        for (d in result.did) Inbox.add(ctx, JSONObject().put("type", "log").put("text", d))
        Store.addRecent(ctx, text, result.say)
        return result
    }

    /** An answer that only says it does not know: for a question, the web is asked instead. */
    private val DONT_KNOW = Regex("(?i)\b(i (do not|don't|dont) (have|know)|i'?m not sure|i am not sure|i (cannot|can't|can not) (access|browse|check|look)|no (real-time|current|up-to-date) (information|data)|my (knowledge|training))\b")
    private val QUESTION = Regex("(?i)^(what|who|whom|whose|why|when|where|how|which|is|are|was|were|do|does|did|can|could|would|will|should|tell me|explain)\\b|\\?\\s*$")

    /**
     * A request to do something in an app the phone has ("open YouTube and search for Mr Beast", "in Spotify play Fairuz") goes straight to
     * screen control. Measured on the phone (2026-10-09): the model on the phone sent only 3 of 8 such requests there, even with clearer
     * tool descriptions, and each decision took 7 to 9 seconds. Opening an app alone, a message, a call and a question are not this.
     */
    private fun appTask(ctx: Context, text: String): Boolean {
        if (ControlService.instance == null || Agent.running) return false
        val t = text.trim().lowercase().trimEnd('.', '!', '?')
        if (QUESTION.containsMatchIn(text.trim())) return false
        if (Regex("^(?:please\\s+)?(?:remind|set|add|note|schedule|create|save|wake me|make)\\b").containsMatchIn(t)) return false   // the buddy's own tools
        if (Shortcuts.searchLink(t) != null) return true
        val app = Shortcuts.appInGoal(ctx, t) ?: return false
        if (Risk.appDenied(app.first) != null) return false
        // more than the app's name and the words that open it
        val rest = t.replace(Regex("\\b(please|open|launch|start|go to|the|app|in|on|hey|buddy)\\b"), " ").replace(app.second.lowercase(), " ").trim()
        return rest.split(Regex("\\s+")).count { it.length > 1 } >= 2
    }

    /**
     * A question about the world (facts, people, places, news, weather, prices, sport): looked up on the web and answered from what is
     * found, the way Gemini does. Measured on the phone (2026-10-10): the model on the phone used its web tool for some questions and,
     * for others, answered from its old memory, said nothing, or said it could not find it. Questions about the buddy, about the owner's
     * own things, and sums are left to the brain.
     */
    private val PERSONAL = Regex("(?i)\\b(you|your|yourself|my|mine|me|i|we|our)\\b")
    private val OWN_THINGS = Regex("(?i)\\b(task|tasks|note|notes|remind|reminder|calendar|event|timer|alarm|schedule|agenda|time is it|what time|what day|the date|date today)\\b")
    private val SUM = Regex("(?i)^\\W*(what('?s| is)\\s+)?[-+*/x×÷().,%\\d\\s]+\\??\\s*$|\\d+(\\.\\d+)?\\s*(times|plus|minus|divided by|multiplied by|percent of|% of)\\s*\\d+")
    private fun worldQuestion(text: String): Boolean {
        val t = text.trim()
        return QUESTION.containsMatchIn(t) && !PERSONAL.containsMatchIn(t) && !OWN_THINGS.containsMatchIn(t) && !SUM.containsMatchIn(t) && t.split(Regex("\\s+")).size >= 3
    }

    // Words that point at what the Mobile Actions model can do. Only then is it asked, so a chat sentence never becomes a call by accident.
    private val DEVICE_WORDS = Regex("(?i)\\b(flash ?light|torch|contact|e-?mail|map|maps|wi-?fi|calendar|event|meeting|appointment)\\b")

    /**
     * A command for the phone itself (flashlight, a contact, an email, a map, Wi-Fi settings, a calendar event) goes to the tiny Mobile Actions
     * model (Google AI Edge Gallery), which answers with the function to call in a fraction of a second. Null when it is not installed, the
     * words do not point at one of its functions, or it chose none; the normal brain then answers.
     */
    fun deviceAction(ctx: Context, text: String): Result? {
        if (!LocalModel.actionsInstalled(ctx) || !DEVICE_WORDS.containsMatchIn(text) || QUESTION.containsMatchIn(text.trim())) return null
        val (name, a) = try { LocalModel.functionCall(ctx, text, java.time.LocalDateTime.now()) } catch (e: Throwable) {
            android.util.Log.w("MBuddyBrain", "actions model: ${e.message}"); null
        } ?: return null
        fun s(k: String) = (a[k] ?: "").toString().trim()
        val done: String? = when (name) {
            "turnOnFlashlight" -> Actions.flashlight(ctx, true)
            "turnOffFlashlight" -> Actions.flashlight(ctx, false)
            "openWifiSettings" -> if (act(ctx, "wifi_settings", JSONObject())) "Opening the Wi-Fi settings." else null
            "showLocationOnMap" -> s("location").takeIf { it.isNotEmpty() }?.let { if (act(ctx, "map", JSONObject().put("place", it))) "Showing $it on the map." else null }
            "sendEmail" -> {
                // the 270M model sometimes leaves the address out: it is in what was said
                val to = s("to").ifEmpty { Regex("[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+").find(text)?.value ?: "" }
                if (act(ctx, "email", JSONObject().put("to", to).put("subject", s("subject")).put("body", s("body")))) "Email ready to send${if (to.isEmpty()) "" else " to $to"}." else null
            }
            "createContact" -> {
                val n = (s("firstName") + " " + s("lastName")).trim()
                // the 270M model sometimes files an email address or a number as a name: that is not a contact, the normal brain decides
                if (n.isNotEmpty() && n.none { it == '@' || it.isDigit() } && act(ctx, "contact", JSONObject().put("name", n).put("number", s("phoneNumber")).put("email", s("email")))) "Contact screen ready for $n." else null
            }
            "createCalendarEvent" -> {
                val title = s("title").ifEmpty { "Event" }; val start = s("datetime")
                if (start.length >= 16) {
                    Inbox.add(ctx, JSONObject().put("type", "event").put("title", title).put("start", start).put("place", ""))
                    act(ctx, "calendar", JSONObject().put("title", title).put("start", start)); "Added “$title” to the calendar."
                } else null
            }
            else -> null
        }
        if (done == null) return null
        android.util.Log.i("MBuddyBrain", "Mobile Actions: $name $a")
        return Result(done, listOf(done.trimEnd('.')), "actions")
    }

    private fun think(ctx: Context, text: String, alternatives: List<String>): Result {
        QuickRules.run(ctx, (listOf(text) + alternatives).distinct()) { kind, params -> act(ctx, kind, params) }?.let { return it }
        deviceAction(ctx, text)?.let { return it }
        if (startAppTask(ctx, text)) return Result("On it.", listOf("Working on it: ${text.take(60)}"), "rules")
        if (worldQuestion(text)) WebSearch.answer(ctx, text, text, now())?.let { a ->
            Dbg.i("MBuddyBrain", "a question about the world, answered from the web: $text")
            return Result(a.say, listOf("Looked up: ${text.take(60)}" + (a.sources.firstOrNull()?.let { " (${Uri.parse(it.url).host?.removePrefix("www.") ?: ""})" } ?: "")), "web")
        }
        val template = Store.systemTemplate(ctx).ifBlank { "You are the owner's desk buddy on their phone. Answer in short, warm sentences. Reply with one JSON object: {\"say\":\"...\",\"do\":[]}." }
        val system = template.replace("{{NOW}}", now())
        val recent = Store.recent(ctx)
        val history = recent.joinToString("\n") { "Owner: ${it.first}\nYou: ${it.second}" }
        val user = (if (history.isNotBlank()) history + "\n" else "") + "Owner: " + text
        var firstError: String? = null
        for (where in Brains.order(ctx)) {
            if (!Brains.ready(ctx, where)) { firstError = firstError ?: notReady(where); continue }
            val t0 = System.currentTimeMillis()
            val raw = try {
                when (where) {
                    "cloud" -> viaCloud(ctx, Store.systemTemplateCloud(ctx).ifBlank { template }.replace("{{NOW}}", now()), recent, text)
                    "pc" -> viaPc(ctx, text)
                    else -> LocalModel.chat(ctx, system, user, CHAT_SCHEMA)
                }
            } catch (e: Exception) { Brains.note(where, false, System.currentTimeMillis() - t0, e.message ?: ""); if (firstError == null) firstError = e.message; continue }
            Brains.note(where, true, System.currentTimeMillis() - t0)
            return finish(ctx, raw, where, text)
        }
        return Result(firstError ?: "I need a brain to answer that. Open the app and add your cloud key, or download the model on the phone.", emptyList(), "error")
    }

    private fun viaCloud(ctx: Context, system: String, recent: List<Pair<String, String>>, text: String): String {
        val msgs = JSONArray().put(JSONObject().put("role", "system").put("content", system))
        for ((you, me) in recent) { msgs.put(JSONObject().put("role", "user").put("content", you)); if (me.isNotBlank()) msgs.put(JSONObject().put("role", "assistant").put("content", me)) }
        msgs.put(JSONObject().put("role", "user").put("content", text))
        val body = JSONObject().put("model", Store.cloudModel(ctx)).put("stream", false).put("messages", msgs).put("format", "json")
            .put("options", JSONObject().put("temperature", 0.3).put("num_predict", 1500)).toString()
        var r = Cloud.chat(ctx, body)
        if (r.status == 402 || r.status == 403) {      // that model is not on the plan: use one that is
            val m = Cloud.findModel(ctx)
            if (m != null) r = Cloud.chat(ctx, JSONObject(body).put("model", m).toString())
        }
        if (r.status == 401 || r.status == 403) throw IllegalStateException("ollama.com refused the key (HTTP ${r.status}) for ${Store.cloudModel(ctx)}. Save a fresh Ollama key in Settings, Brain.")
        if (r.status == 0) throw IllegalStateException("I can't reach the cloud right now.")
        if (r.status != 200) throw IllegalStateException("The cloud answered with an error (${r.status}).")
        val content = JSONObject(r.body).getJSONObject("message").optString("content")
        if (content.isBlank()) throw IllegalStateException("The model gave no answer.")
        return content
    }

    private fun viaPc(ctx: Context, text: String): String {
        val r = Pc.request(ctx, "POST", "/remote/control/chat", JSONObject().put("text", text).put("speak", false).toString())
        if (r.status != 200) throw IllegalStateException("I can't reach your PC: MBUDDY isn't running there, or the PC is off. Start it, or choose Auto in Settings, Brain.")
        val reply = JSONObject(r.body).optString("reply")
        if (reply.isBlank()) throw IllegalStateException("Your PC gave no answer.")
        return JSONObject().put("say", reply).toString()
    }

    /** Whether a request is about doing something in another app (so it needs the screen): refused while privacy mode is on. */
    fun needsApp(said: String): Boolean = NEEDS_APP.containsMatchIn(said.lowercase())

    private val NEEDS_APP = Regex("\\b(send|message|text|tell|whatsapp|open|call|reply|write|post|share|play|find)\\b")
    private val ACTION_WORDS = Regex("(?i)\\b(sending|setting|opening|adding|saving|calling|starting|creating|scheduling|writing|messaging|texting|booking|noting|putting|on it|will send|will set|will open|i'll send|i'll set|i'll open|sent|set|added|saved|created|scheduled|opened|called|messaged|started|written|noted)\\b")

    /** Whether [say] claims something is being or has been done, for a request that was a command (a question may be answered in any words). */
    private fun claimsAction(say: String, asked: String): Boolean {
        val q = asked.trim().lowercase()
        val isQuestion = q.endsWith("?") || Regex("^(what|who|why|when|where|how|which|is|are|do|does|can|could|would|will|did)\\b").containsMatchIn(q)
        return !isQuestion && (CLAIMS.containsMatchIn(say) || ACTION_WORDS.containsMatchIn(say))
    }

    private fun finish(ctx: Context, raw: String, source: String, said: String = ""): Result {
        Dbg.i("MBuddyBrain", "$source answered: ${raw.take(400)}")
        val j = Planner.firstObject(raw) ?: return Result(spokenFrom(raw), emptyList(), source)
        val did = ArrayList<String>()
        val all = j.optJSONArray("do")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } } ?: emptyList()
        val tools = if (all.any { it.optString("tool") == "phone" }) all.filter { it.optString("tool") != "open_app" } else all
        var say = j.optString("say").trim()
        for (t in tools) {
            if (t.optString("tool") == "web") {
                val q = t.optString("query").trim().ifBlank { said }
                val a = WebSearch.answer(ctx, said.ifBlank { q }, q, now())
                if (a == null) { say = "I couldn't reach the web just now."; continue }
                say = a.say; did.add("Looked up: ${q.take(60)}" + (a.sources.firstOrNull()?.let { " (${Uri.parse(it.url).host?.removePrefix("www.") ?: ""})" } ?: ""))
                continue
            }
            runTool(ctx, t, said)?.let { d -> did.add(d) }
        }
        // a question the model left unanswered (the small model sometimes answers with nothing at all): look it up rather than stay silent
        if (did.isEmpty() && (say.isBlank() || DONT_KNOW.containsMatchIn(say)) && QUESTION.containsMatchIn(said.trim())) {
            WebSearch.answer(ctx, said, said, now())?.let { a ->
                say = a.say; did.add("Looked up: ${said.take(60)}" + (a.sources.firstOrNull()?.let { " (${Uri.parse(it.url).host?.removePrefix("www.") ?: ""})" } ?: ""))
            }
        }
        say = say.ifBlank { if (did.isNotEmpty()) "Done." else "Sorry, I didn't get that. Could you say it again?" }
        if (did.isEmpty() && claimsAction(say, said)) {
            // The words say something is happening and no tool was asked for: nothing happened, and it must not be said to.
            if (NEEDS_APP.containsMatchIn(said.lowercase()) && ControlService.instance != null && Agent.start(said) == null)
                return Result("On it. I'll show you before anything is sent.", listOf("Working on it: ${said.take(60)}"), "rules")
            say = "I couldn't turn that into an action, so nothing was done. Try again, a little slower."
        }
        return Result(say, did, source)
    }

    /** A plain answer (no tools) from the first brain that answers: the cloud, then the model on the phone. Null when none can. */
    fun plainAnswer(ctx: Context, system: String, user: String): String? {
        if (Cloud.hasKey(ctx)) runCatching {
            val body = JSONObject().put("model", Store.cloudModel(ctx)).put("stream", false)
                .put("messages", JSONArray().put(JSONObject().put("role", "system").put("content", system)).put(JSONObject().put("role", "user").put("content", user)))
                .put("options", JSONObject().put("temperature", 0.2).put("num_predict", 1500)).toString()
            val r = Cloud.chat(ctx, body)
            if (r.status == 200) JSONObject(r.body).getJSONObject("message").optString("content").trim().takeIf { it.isNotBlank() }?.let { return it }
        }
        if (LocalModel.active(ctx) != null) runCatching { LocalModel.chat(ctx, system, user, null).trim().takeIf { it.isNotBlank() }?.let { return it } }
        return null
    }

    /** Whether this app may open another app's screen without the owner touching the phone (it may when "Display over other apps" is allowed). */
    fun canOpenScreens(ctx: Context): Boolean = Settings.canDrawOverlays(ctx)

    private fun act(ctx: Context, kind: String, p: JSONObject): Boolean {
        val r = Actions.execute(ctx, kind, p, onScreen = canOpenScreens(ctx), notificationId = (System.nanoTime() % 100000).toInt())
        return !r.startsWith("No app") && !r.startsWith("I can't")
    }

    /** The app the owner named for a message, which wins over the model's pick: "on WhatsApp" is WhatsApp, "a text" or "SMS" is the messages app. */
    private fun namedMessageApp(said: String): String? {
        val s = said.lowercase()
        return when {
            Regex("\\bwhats\\s?app\\b").containsMatchIn(s) -> "whatsapp"
            Regex("\\b(sms|text message|a text|messages app)\\b").containsMatchIn(s) -> "sms"
            else -> null
        }
    }

    private fun runTool(ctx: Context, t: JSONObject, said: String = ""): String? {
        val named = namedMessageApp(said)
        when (t.optString("tool").lowercase()) {
            "timer" -> {
                val s = t.optInt("seconds", 0); if (s !in 1..86400) return null
                act(ctx, "timer", JSONObject().put("seconds", s).put("label", t.optString("label")))
                return "Timer ${s / 60} min${if (s % 60 != 0) " ${s % 60} s" else ""}"
            }
            "alarm" -> {
                val h = t.optInt("hour", -1); val m = t.optInt("minute", 0); if (h !in 0..23 || m !in 0..59) return null
                act(ctx, "alarm", JSONObject().put("hour", h).put("minute", m).put("label", t.optString("label")))
                return "Alarm at %02d:%02d".format(h, m)
            }
            "task" -> {
                val title = t.optString("title").trim(); if (title.isEmpty()) return null
                Inbox.add(ctx, JSONObject().put("type", "task").put("title", title).put("when", t.optString("when")))
                return "Task: $title"
            }
            "note" -> {
                val text = t.optString("text").trim(); if (text.isEmpty()) return null
                Inbox.add(ctx, JSONObject().put("type", "note").put("text", text)); return "Saved a note"
            }
            "event" -> {
                val title = t.optString("title").trim().ifEmpty { "Event" }; val start = t.optString("start"); if (start.length < 16) return null
                Inbox.add(ctx, JSONObject().put("type", "event").put("title", title).put("start", start).put("place", t.optString("place")))
                act(ctx, "calendar", JSONObject().put("title", title).put("start", start).put("place", t.optString("place")))
                return "Event: $title"
            }
            "call" -> { val n = t.optString("number").filter { it.isDigit() || it == '+' }; if (n.length < 3) return null; act(ctx, "call", JSONObject().put("number", n)); return "Call screen for $n" }
            "sms" -> {
                val n = t.optString("number").filter { it.isDigit() || it == '+' }; if (n.length < 3) return null
                if (named == "whatsapp") return if (Messaging.send(ctx, "whatsapp", n, t.optString("body")) == null) "WhatsApp message to $n" else null
                act(ctx, "sms", JSONObject().put("number", n).put("body", t.optString("body"))); return "Message ready for $n"
            }
            "navigate" -> { val p = t.optString("place").trim(); if (p.isEmpty()) return null; act(ctx, "navigate", JSONObject().put("place", p)); return "Directions to $p" }
            "search" -> { val q = t.optString("query").trim(); if (q.isEmpty()) return null; act(ctx, "search", JSONObject().put("query", q)); return "Search: $q" }
            "message" -> {
                val why = Messaging.send(ctx, named ?: t.optString("app"), t.optString("to"), t.optString("text"))
                return if (why == null) "Message to ${t.optString("to")}" else null
            }
            "open_app" -> {
                val app = t.optString("app").trim(); if (app.isEmpty()) return null
                if (Actions.appLaunchIntent(ctx, app) == null) return null
                act(ctx, "open_app", JSONObject().put("app", app)); return "Open $app"
            }
            "phone" -> {
                val goal = t.optString("goal").trim(); if (goal.isEmpty()) return null
                val why = Agent.start(goal)
                return if (why == null) "Working on it: ${goal.take(60)}" else null
            }
        }
        return null
    }
}
