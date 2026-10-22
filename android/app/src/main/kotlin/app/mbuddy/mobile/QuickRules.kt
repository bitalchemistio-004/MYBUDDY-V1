package app.mbuddy.mobile

import android.content.Context
import org.json.JSONObject

/**
 * The commands that need no thinking: "open WhatsApp", "set a 5 minute timer", "alarm at 7:30". They are done at once, without waiting
 * for a model (a model on the phone takes about ten seconds). Anything else goes to the brain. Spoken numbers arrive as digits or words.
 */
object QuickRules {
    private val WORDS = mapOf("a" to 1, "an" to 1, "one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5, "six" to 6, "seven" to 7, "eight" to 8, "nine" to 9,
        "ten" to 10, "fifteen" to 15, "twenty" to 20, "thirty" to 30, "forty" to 40, "forty five" to 45, "sixty" to 60, "ninety" to 90)

    private fun number(s: String): Double? = s.trim().lowercase().let { it.toDoubleOrNull() ?: WORDS[it]?.toDouble() }

    /**
     * The reply and what was done, or null when this is not a simple command. [candidates] are the recognizer's transcriptions, best first:
     * the first one that names a real app exactly wins; else the best one that matches any rule.
     */
    fun run(ctx: Context, candidates: List<String>, act: (String, JSONObject) -> Boolean): NativeBrain.Result? {
        val open = Regex("^(?:please\\s+)?(?:open|launch|start|go to)\\s+(?:the\\s+)?(.+?)(?:\\s+app)?$")
        for (c in candidates) {
            val app = open.find(c.trim().lowercase())?.groupValues?.get(1) ?: continue
            val (_, label) = Actions.appMatch(ctx, app, fuzzy = false) ?: continue
            act("open_app", JSONObject().put("app", label))
            return NativeBrain.Result("Opening $label.", listOf("Open $label"), "rules")
        }
        for (c in candidates) call(ctx, c)?.let { return it }
        for (c in candidates) message(ctx, c)?.let { return it }
        for (c in candidates) one(ctx, c, act)?.let { return it }
        return null
    }

    private val CUES = setOf("send", "message", "text", "tell", "whatsapp", "sms", "say", "write", "ask", "reply")
    private val LEAD = setOf("send", "message", "text", "tell", "whatsapp", "sms", "say", "write", "ask", "reply", "a", "the", "in", "on", "via", "using", "through", "and",
        "to", "only", "just", "please", "open", "that", "saying", "message,", "app", "hey", "buddy")
    private val POINTERS = setOf("send", "tell", "message", "text", "to", "ask", "whatsapp", "say", "write", "reply", "call")
    private val TRAIL = setOf("on", "in", "via", "using", "through", "whatsapp", "sms", "text", "messages", "message", "app", "please", "and")

    /**
     * "send hi to Baba on WhatsApp", "In WhatsApp and send Baba only hi", "tell Sara that I'm late": the person is found in the sentence by
     * name (they must be in the contacts, which is what tells a message from any other sentence), the words around the name that only say
     * how to send are dropped, and what is left is what to say.
     */
    class Parsed(val person: Contacts.Match, val body: String, val app: String)

    /** "call Baba", "phone my mum": a person in the contacts gets the dialer with their number, then the call after the owner's yes. A call in another app (WhatsApp) is left to the brain. */
    private fun call(ctx: Context, said: String): NativeBrain.Result? {
        val t = said.trim().lowercase().trimEnd('.', '!', '?')
        val who = Regex("^(?:please\\s+)?(?:call|phone|ring|dial)\\s+(.+?)(?:\\s+(?:now|please|for me))?$").find(t)?.groupValues?.get(1) ?: return null
        if (Regex("\\b(whats\\s?app|telegram|messenger|signal|viber|skype|teams|zoom|meet|video)\\b").containsMatchIn(who)) return null
        if (!Contacts.allowed(ctx) || Contacts.find(ctx, who).isEmpty()) return null
        val name = Contacts.find(ctx, who).first().name
        val why = Messaging.call(ctx, who)
        return if (why == null) NativeBrain.Result("Calling $name. I'll ask you before it rings.", listOf("Call $name"), "rules") else NativeBrain.Result(why, emptyList(), "rules")
    }

    private fun message(ctx: Context, said: String): NativeBrain.Result? {
        val m = parseMessage(ctx, said) ?: return null
        val why = Messaging.send(ctx, m.app, m.person.name, m.body.replaceFirstChar { it.uppercase() })
        val where = if (m.app == "sms") "your messages" else "WhatsApp"
        return if (why == null) NativeBrain.Result("Okay, messaging ${m.person.name} on $where: \"${m.body}\". I'll read it back before it goes.", listOf("Message to ${m.person.name}"), "rules")
        else NativeBrain.Result(why, emptyList(), "rules")
    }

    /** Who a sentence sends a message to, and what it says (nothing is sent here); null when it is not a message to someone in the contacts. */
    fun parseMessage(ctx: Context, said: String): Parsed? {
        if (!Contacts.allowed(ctx)) return null
        val words = said.trim().trimEnd('.', '!', '?').split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.size < 3 || words.none { it.lowercase().trim(',', '.') in CUES }) return null
        val lower = words.map { it.lowercase().trim(',', '.', '!', '?') }
        val contacts = Contacts.all(ctx)
        // the best place in the sentence for a name: a window of one or two words that matches a contact, outside the "how to send" words
        var best: Triple<Int, Int, Contacts.Match>? = null; var bestScore = 99
        for (len in 2 downTo 1) for (i in 0..lower.size - len) {
            val window = lower.subList(i, i + len)
            if (window.all { it in LEAD || it in TRAIL }) continue
            val spoken = window.joinToString(" ")
            val afterPointer = i > 0 && lower[i - 1] in POINTERS
            val hit = contacts.mapNotNull { c -> Contacts.fit(c.name, spoken)?.let { sc -> c to sc } }
                .filter { (_, sc) -> sc <= 2 || (sc == 3 && afterPointer && spoken.length >= 4) }.minByOrNull { it.second } ?: continue
            if (hit.second < bestScore || (hit.second == bestScore && len > (best?.let { it.second - it.first } ?: 0))) { bestScore = hit.second; best = Triple(i, i + len, hit.first) }
        }
        val (from, to, person) = best ?: return null
        // "Close YouTube and open WhatsApp send Mustafa hi": what comes before the last cue word in front of the name is an instruction
        // to the buddy, not part of the message (measured on the phone 2026-10-10: it had become the message)
        val cueAt = (0 until from).lastOrNull { lower[it] in CUES }
        val start = if (cueAt != null) cueAt + 1 else 0
        val before = words.subList(start, from).toMutableList(); val after = words.subList(to, words.size).toMutableList()
        while (before.isNotEmpty() && before.first().lowercase().trim(',', '.') in LEAD) before.removeAt(0)
        while (before.isNotEmpty() && before.last().lowercase().trim(',', '.') in setOf("to", "for")) before.removeAt(before.size - 1)
        while (after.isNotEmpty() && after.first().lowercase().trim(',', '.') in LEAD + TRAIL) after.removeAt(0)
        while (after.isNotEmpty() && after.last().lowercase().trim(',', '.') in TRAIL) after.removeAt(after.size - 1)
        val body = (before + after).joinToString(" ").trim()
        if (body.length < 2) return null
        val others = setOf("telegram", "signal", "instagram", "messenger", "discord", "snapchat", "viber", "line", "wechat", "teams", "slack")
        if (lower.any { it in others }) return null                                  // another chat app: the agent works it out
        val appWord = when {
            lower.any { it == "whatsapp" || it == "whatsapp," } || lower.zipWithNext().any { (a, b) -> a == "whats" && b == "app" } -> "whatsapp"
            lower.any { it == "sms" || it == "text" || it == "messages" } -> "sms"
            else -> "whatsapp"
        }
        // "send Baba Hi to Baba on WhatsApp": the person said twice is one person
        val bodyClean = body.replace(Regex("(?i)\\s+to\\s+" + Regex.escape(person.name.split(' ').first()) + "\\b.*$"), "").trim().ifBlank { body }
        return Parsed(person, bodyClean, appWord)
    }

    private fun one(ctx: Context, said: String, act: (String, JSONObject) -> Boolean): NativeBrain.Result? {
        val t = said.trim().lowercase().removePrefix("please ").removeSuffix(" please").trim()
        Regex("^(?:open|launch|start|go to)\\s+(?:the\\s+)?(.+?)(?:\\s+app)?$").find(t)?.let { m ->
            val app = m.groupValues[1]
            Actions.appMatch(ctx, app)?.let { (_, label) ->
                act("open_app", JSONObject().put("app", label))
                return NativeBrain.Result("Opening $label.", listOf("Open $label"), "rules")
            }
        }
        Regex("(\\d+(?:\\.\\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty five|forty|sixty|ninety)\\s*(second|sec|minute|min|hour|hr)s?\\s*timer|timer\\s*(?:for\\s*)?(\\d+(?:\\.\\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty five|forty|sixty|ninety)\\s*(second|sec|minute|min|hour|hr)s?").find(t)?.let { m ->
            val n = number(m.groupValues[1].ifEmpty { m.groupValues[3] }) ?: return@let
            val unit = m.groupValues[2].ifEmpty { m.groupValues[4] }
            val seconds = (n * when { unit.startsWith("sec") -> 1.0; unit.startsWith("min") -> 60.0; else -> 3600.0 }).toInt()
            if (seconds in 1..86400) {
                act("timer", JSONObject().put("seconds", seconds).put("label", ""))
                val said2 = if (seconds % 3600 == 0) "${seconds / 3600} hour" else if (seconds % 60 == 0) "${seconds / 60} minute" else "$seconds second"
                return NativeBrain.Result("Okay, a $said2 timer.", listOf("Timer $said2"), "rules")
            }
        }
        Regex("alarm\\s*(?:for|at)\\s*(\\d{1,2})(?::(\\d{2}))?\\s*(a\\.?m\\.?|p\\.?m\\.?)?").find(t)?.let { m ->
            var h = m.groupValues[1].toInt(); val min = m.groupValues[2].ifEmpty { "0" }.toInt()
            val ap = m.groupValues[3].replace(".", "")
            if (ap == "pm" && h < 12) h += 12; if (ap == "am" && h == 12) h = 0
            if (h in 0..23 && min in 0..59) {
                act("alarm", JSONObject().put("hour", h).put("minute", min).put("label", ""))
                return NativeBrain.Result("Alarm set for %02d:%02d.".format(h, min), listOf("Alarm at %02d:%02d".format(h, min)), "rules")
            }
        }
        return null
    }
}
