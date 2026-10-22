package app.mbuddy.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * "Send Baba hi on WhatsApp": look Baba up in the contacts, open that chat with the text already typed, then let the agent do the one
 * thing left, tapping Send, which (like every send) stops to read the message back and waits for the owner's yes.
 * That is a few seconds and two steps, instead of a model working out WhatsApp's screens from scratch.
 */
object Messaging {
    /** Returns why it could not, or null when the chat is open and the send step has been handed on. */
    fun send(ctx: Context, app: String, to: String, text: String): String? {
        val who = to.trim(); val body = text.trim()
        if (who.isEmpty() || body.isEmpty()) return "I need who to message and what to say."
        val digits = Contacts.digits(who)
        val match = if (digits.length >= 7 && who.none { it.isLetter() }) Contacts.Match(who, who) else Contacts.find(ctx, who).firstOrNull()
        if (match == null) return if (!Contacts.allowed(ctx)) "I can't look up contacts yet. Allow it in MBUDDY, Settings." else "I couldn't find a contact called $who."
        val number = Contacts.international(ctx, match.number)
        val sms = app.lowercase().let { it.contains("sms") || it.contains("text") || it.contains("message") && !it.contains("whats") }
        val intent = if (sms) Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$number")).putExtra("sms_body", body)
        else Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/$number?text=" + Uri.encode(body))).setPackage("com.whatsapp")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val svc = ControlService.instance
        svc?.overlay?.show("working", "Opening ${if (sms) "your messages" else "WhatsApp"}…", "To ${match.name}")
        val opened = runCatching { (svc ?: ctx).startActivity(intent); true }.getOrDefault(false)
        if (!opened) return "I couldn't open ${if (sms) "the messages app" else "WhatsApp"}."
        if (svc == null) return null                       // no screen control: the message is typed and waiting; the owner taps send
        val pkg = if (sms) (android.provider.Telephony.Sms.getDefaultSmsPackage(ctx) ?: "") else "com.whatsapp"
        Agent.start("The message to ${match.name} is already typed in the chat that is open. Tap the Send button. Do nothing else.", recipient = match.name, message = body, inApp = pkg)
        return null
    }

    /**
     * "Call Baba": the dialer opens with Baba's number on it (that never calls by itself), then the agent taps the call button, which
     * like every call first asks the owner. Returns why it could not, or null when the dialer is open.
     */
    fun call(ctx: Context, to: String): String? {
        val match = Contacts.find(ctx, to.trim()).firstOrNull()
            ?: return if (!Contacts.allowed(ctx)) "I can't look up contacts yet. Allow it in MBUDDY, Settings." else "I couldn't find a contact called $to."
        val intent = Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(match.number))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val dialer = ctx.packageManager.resolveActivity(intent, 0)?.activityInfo?.packageName?.takeIf { it != "android" } ?: ""
        val svc = ControlService.instance
        svc?.overlay?.show("working", "Opening the dialer…", "To ${match.name}")
        if (!runCatching { (svc ?: ctx).startActivity(intent); true }.getOrDefault(false)) return "I couldn't open the dialer."
        if (svc == null) return null                       // no screen control: the number is on the dialer; the owner taps call
        Agent.start("The dialer is open with ${match.name}'s number. Tap the call button. Do nothing else.", recipient = match.name, inApp = dialer)
        return null
    }
}
