package app.mbuddy.mobile

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * One spoken request, start to finish: a chime, listen, think (NativeBrain), do it, say the answer. The same for "hey buddy" (WakeService)
 * and the phone's assistant button (AssistSession). The buddy's card is shown over whatever is on screen when screen control is on;
 * otherwise it is voice only.
 */
object Command {
    /** Counts requests: a newer one (or a tap on a tile of the card) makes an older one that is still listening drop what it hears. */
    @Volatile private var session = 0

    /** A tap on the card means the listening in progress is no longer wanted. */
    fun supersede() { session++; Recorder.cancel(); runCatching { Voice.stopListening() } }

    /**
     * Call off the main thread. [status] gets a few words about what is happening (the notification shows them).
     * [tiles] are shown on the card while it listens (the quick panel: say it, or tap one); when nothing is said, [fallback] is shown
     * instead of closing the card, so the panel is still there to tap.
     */
    fun run(ctx: Context, tiles: List<Triple<String, String, String>> = emptyList(), fallback: List<Triple<String, String, String>> = emptyList(), status: (String) -> Unit = {}) {
        listening = true
        try { runInner(ctx, tiles, fallback, status) } finally { listening = false }
    }

    /** True while a request is being listened for or understood: other cards (a message, a heads-up) wait, so they never take the card from under the owner's voice. */
    @Volatile var listening = false

    private fun runInner(ctx: Context, tiles: List<Triple<String, String, String>>, fallback: List<Triple<String, String, String>>, status: (String) -> Unit) {
        if (Privacy.on(ctx)) { Smart.notify(ctx, "Privacy mode is on", Privacy.REFUSAL); return }
        val mine = ++session
        // with the model on the phone the recording starts at once, so the tone is the cue; with the speech engine the cue is given by
        // the engine itself when it is ready (Voice), so it is not sounded here as well
        if (LocalModel.active(ctx) != null) Voice.cue(180)
        if (ControlService.instance == null) screenControlOff(ctx)
        val card = ControlService.instance?.overlay
        card?.show("asking", "I'm listening.", if (tiles.isEmpty()) "Say what you need" else "Say it, or tap one", tiles = tiles)              // shown as "listening" (Overlay)
        val silent = {
            if (!Agent.running && mine == session) { if (fallback.isNotEmpty()) card?.show("asking", "What now?", "Tap one, or tap Talk", tiles = fallback) else card?.hide() }
        }
        status("Listening…")
        var said = ""
        var alternatives: List<String> = emptyList()
        if (LocalModel.active(ctx) != null) {
            // The model on the phone listens: far more accurate than a recognizer with no context, because it is told which words to expect.
            val wav = Recorder.utterance()
            if (mine != session) return
            if (wav == null) { silent(); return }
            card?.show("working", "…", "Understanding you")
            status("Understanding…")
            said = try { LocalModel.transcribe(ctx, wav, Voice.appNames(ctx)) } catch (e: Exception) { android.util.Log.w("MBuddyCommand", "model could not transcribe: ${e.message}"); "" }
            if (said.isBlank()) said = OfflineListen.fromWav(ctx, wav)             // the backup: the small offline model, on the same recording
            Dbg.i("MBuddyCommand", "heard: $said")
        } else {
            val latch = CountDownLatch(1)
            Voice.listen(ctx) { t, _ -> said = t; latch.countDown() }
            latch.await(15, TimeUnit.SECONDS)
            Voice.stopListening()
            alternatives = Voice.alternatives
        }
        if (mine != session) return              // a tile was tapped while it listened: that is what the owner chose
        if (said.isBlank()) { silent(); return }
        handle(ctx, said, alternatives, status)
    }

    /** Listen once and return what was said ("" when nothing was). The card shows [prompt]. Call off the main thread. */
    fun hear(ctx: Context, prompt: String): String {
        runCatching { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 70).startTone(ToneGenerator.TONE_PROP_BEEP, 180) }
        val card = ControlService.instance?.overlay
        card?.show("asking", prompt, "Say it now")
        var said = ""
        if (LocalModel.active(ctx) != null) {
            val wav = Recorder.utterance() ?: return ""
            card?.show("working", "…", "Understanding you")
            said = try { LocalModel.transcribe(ctx, wav, Voice.appNames(ctx)) } catch (e: Exception) { "" }
            if (said.isBlank()) said = OfflineListen.fromWav(ctx, wav)
        } else {
            val latch = CountDownLatch(1)
            Voice.listen(ctx) { t, _ -> said = t; latch.countDown() }
            latch.await(15, TimeUnit.SECONDS)
            Voice.stopListening()
        }
        return said.trim()
    }

    /**
     * Screen control is off (Android switches it off when the app is reinstalled or force-stopped), so the card cannot be drawn and the
     * owner would see nothing. Say so, and put the one tap that turns it back on in a notification.
     */
    private fun screenControlOff(ctx: Context) {
        runCatching {
            val open = android.app.PendingIntent.getActivity(ctx, 77, android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT)
            val n = androidx.core.app.NotificationCompat.Builder(ctx, "mbuddy_wake").setSmallIcon(R.drawable.ic_notify).setContentTitle("MBUDDY can't show itself")
                .setContentText("Screen control is off. Tap to turn MBUDDY back on.").setContentIntent(open).setAutoCancel(true)
                .setPriority(androidx.core.app.NotificationCompat.PRIORITY_HIGH).build()
            ctx.getSystemService(android.app.NotificationManager::class.java).notify(4343, n)
        }
        if (Store.voice(ctx)) Voice.say(ctx, "Screen control is off, so I can't show my card. Tap the notification to turn it on.")
        android.util.Log.w("MBuddyCommand", "screen control is off: the card cannot be shown")
    }

    /** What was heard, handled: the card shows it, the brain answers and acts, the answer is spoken. The tests use this same path. */
    fun handle(ctx: Context, said: String, alternatives: List<String> = emptyList(), status: (String) -> Unit = {}): NativeBrain.Result {
        // "privacy mode on" or "turn privacy mode off", by voice
        Privacy.voiceCommand(ctx, said)?.let { return NativeBrain.Result(it, emptyList(), "rules") }
        // privacy mode: whatever would be done inside another app is not done, and the answer says so instead of promising it
        if (Privacy.on(ctx) && (NativeBrain.needsApp(said) || QuickRules.parseMessage(ctx, said) != null)) {
            if (Store.voice(ctx)) Voice.say(ctx, Privacy.REFUSAL)
            return NativeBrain.Result(Privacy.REFUSAL, emptyList(), "rules")
        }
        val card = ControlService.instance?.overlay
        card?.show("working", "“$said”", "I heard")
        status("Thinking…")
        val r = NativeBrain.respond(ctx, said, alternatives)
        Dbg.i("MBuddyCommand", "answer (${r.source}): ${r.say.take(200)} | did: ${r.did.joinToString("; ")}")
        val line = r.say.ifBlank { if (r.did.isNotEmpty()) "Done." else "" }
        if (!Agent.running) card?.show(if (r.source == "error") "fail" else "done", line.ifBlank { "Done." })
        if (line.isNotBlank() && Store.voice(ctx)) Voice.sayAndWait(ctx, line, 20_000)
        // the card stays long enough to read the answer: about 15 characters a second after it was spoken, at least 4 s, at most 20 s
        Thread.sleep(if (line.isBlank()) 800 else (line.length * 65L).coerceIn(4000L, 20000L))
        if (!Agent.running) card?.hide()
        return r
    }
}
