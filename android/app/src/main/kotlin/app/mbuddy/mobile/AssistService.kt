package app.mbuddy.mobile

import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.speech.RecognitionService
import android.content.Intent
import android.content.Context

/**
 * Lets MBUDDY be chosen as the phone's assistant (Android Settings, Apps, Default apps, Digital assistant app). Then the assistant
 * gesture or button (long press on the power or home button, or the swipe from a corner) starts MBUDDY listening: the same routine as
 * "hey buddy" (Command.kt). It does not listen for a wake word by itself; that is WakeService.
 */
class AssistService : VoiceInteractionService()

class AssistSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistSession(this)
}

class AssistSession(context: Context) : VoiceInteractionSession(context) {
    override fun onCreate() { super.onCreate(); setUiEnabled(false) }       // no window of its own: the buddy's card (or just its voice) is the answer

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val ctx = context.applicationContext
        Thread({
            try { Command.run(ctx) } catch (e: Exception) { android.util.Log.w("MBuddyAssist", "request failed", e) }
            finally { hide() }
        }, "mbuddy-assist").start()
    }
}

/** Android's assistant framework asks every assistant to name a recognition service. MBUDDY does its own listening (Voice.kt), so this one says it is not available. */
class AssistRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) { runCatching { listener?.error(android.speech.SpeechRecognizer.ERROR_CLIENT) } }
    override fun onCancel(listener: Callback?) {}
    override fun onStopListening(listener: Callback?) {}
}
