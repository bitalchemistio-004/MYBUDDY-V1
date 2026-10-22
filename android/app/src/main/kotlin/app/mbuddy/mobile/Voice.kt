package app.mbuddy.mobile

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.core.content.ContextCompat
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The agent's voice (it talks back) and ears (it hears a yes or a no). Uses the phone's own speech engines. */
object Voice {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private val pending = ArrayList<Pair<String, Runnable?>>()
    private var recognizer: SpeechRecognizer? = null
    @Volatile var listening = false

    /** True from the moment the buddy starts talking until it has finished (and a moment after: the room still echoes). */
    @Volatile var speaking = false
        private set
    @Volatile private var spokeUntil = 0L
    private var pendingUtterances = 0

    /** Whether the buddy is talking now or was a moment ago: its own voice must never count as someone calling it. */
    fun busySpeaking(): Boolean = speaking || System.currentTimeMillis() < spokeUntil

    /** The other transcriptions the recognizer offered for the last thing heard (best first), so the brain can pick the one that makes sense. */
    @Volatile var alternatives: List<String> = emptyList()
        private set

    /** The names of the installed apps (what people say after "open"). */
    fun appNames(ctx: Context): List<String> = runCatching {
        val pm = ctx.packageManager
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0).map { it.loadLabel(pm).toString() }.filter { it.length in 3..24 }.distinct()
    }.getOrDefault(emptyList())

    /** Names the recognizer should expect: the installed apps and the words commands start with. They make "WhatsApp" win over "what's up". */
    private fun hints(ctx: Context): ArrayList<String> {
        val out = LinkedHashSet<String>()
        runCatching {
            val pm = ctx.packageManager
            pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0).map { it.loadLabel(pm).toString() }
                .filter { it.length in 3..24 }.take(160).forEach { out.add(it); out.add("open $it") }
        }
        listOf("hey buddy", "set a timer", "set an alarm", "remind me to", "send a message to", "WhatsApp", "TikTok", "message").forEach { out.add(it) }
        return ArrayList(out.take(220))
    }

    fun canHear(ctx: Context) =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED &&
            (SpeechRecognizer.isRecognitionAvailable(ctx) || (android.os.Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)))

    private fun engine(ctx: Context) {
        appCtx = ctx.applicationContext
        if (tts != null) return
        tts = TextToSpeech(ctx.applicationContext) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                tts?.language = Locale.getDefault().let { if (tts?.isLanguageAvailable(it) ?: TextToSpeech.LANG_NOT_SUPPORTED >= TextToSpeech.LANG_AVAILABLE) it else Locale.ENGLISH }
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) { speaking = true }
                    override fun onDone(utteranceId: String?) { finishedOne(); done(utteranceId) }
                    @Deprecated("Deprecated in Java") override fun onError(utteranceId: String?) { finishedOne(); done(utteranceId) }
                })
                val queued = ArrayList(pending); pending.clear()
                for ((t, r) in queued) speakNow(t, r)
            }
        }
    }

    private val callbacks = HashMap<String, Runnable>()
    private fun done(id: String?) { id ?: return; val r = synchronized(callbacks) { callbacks.remove(id) }; if (r != null) main.post(r) }

    private fun finishedOne() {
        synchronized(callbacks) { pendingUtterances = maxOf(0, pendingUtterances - 1); if (pendingUtterances == 0) { speaking = false; spokeUntil = System.currentTimeMillis() + 1500 } }
    }

    // Each buddy has its own pitch and pace; and where the phone has several English voices installed, its own voice.
    private val PROFILE = mapOf("blink" to (1.12f to 1.0f), "void" to (0.78f to 0.9f), "glitch" to (1.35f to 1.16f), "cinder" to (0.96f to 1.1f), "oracle" to (0.88f to 0.92f))
    @Volatile private var appCtx: android.content.Context? = null
    fun englishVoices(): List<android.speech.tts.Voice> =
        runCatching { tts?.voices?.filter { it.locale.language == "en" && !it.isNetworkConnectionRequired }?.sortedBy { it.name } }.getOrNull() ?: emptyList()
    @Volatile private var forced: String? = null          // one utterance with this voice: the owner is auditioning it
    private fun applyBuddy() {
        val ctx = appCtx ?: return; val t = tts ?: return
        forced?.let { f -> forced = null; englishVoices().firstOrNull { it.name == f }?.let { v -> runCatching { t.setPitch(1f); t.setSpeechRate(1f); t.voice = v }; return } }
        val ch = Store.character(ctx)
        val (pitch, rate) = PROFILE[ch] ?: (1f to 1f)
        runCatching {
            t.setPitch(pitch); t.setSpeechRate(rate)
            val list = englishVoices(); val chosen = Store.voiceName(ctx, ch)
            val v = list.firstOrNull { it.name == chosen } ?: if (list.size >= 2) list[listOf("blink", "void", "glitch", "cinder", "oracle").indexOf(ch).coerceAtLeast(0) % list.size] else null
            if (v != null) t.voice = v
        }
    }
    /** Let the owner hear one particular installed voice, in its own plain pitch and pace. */
    fun auditionVoice(ctx: android.content.Context, name: String) { forced = name; say(ctx, "Hi, I'm ${Store.character(ctx).replaceFirstChar { it.uppercase() }}. This is how I would sound.") }
    /** Let the owner hear the current buddy's voice. */
    fun preview(ctx: android.content.Context) { say(ctx, "Hi, I'm ${Store.character(ctx).replaceFirstChar { it.uppercase() }}. This is how I sound.") }

    private fun speakNow(text: String, then: Runnable?) {
        applyBuddy()
        val id = "u" + System.nanoTime()
        synchronized(callbacks) { pendingUtterances++; speaking = true }
        if (then != null) synchronized(callbacks) { callbacks[id] = then }
        tts?.speak(text, TextToSpeech.QUEUE_ADD, null, id)
    }

    /** Say it (queued after what is already being said). [then] runs on the main thread when it has been said. */
    fun say(ctx: Context, text: String, then: Runnable? = null) {
        main.post {
            engine(ctx)
            if (ttsReady) speakNow(text, then) else { pending.add(text to then) }
        }
    }

    fun shutUp() { main.post { tts?.stop(); synchronized(callbacks) { callbacks.clear(); pendingUtterances = 0; speaking = false } } }

    /** Say it and wait until it has been said (or [maxMs] passes). For the agent's own thread, never the main one. */
    fun sayAndWait(ctx: Context, text: String, maxMs: Long = 20_000) {
        val latch = CountDownLatch(1)
        say(ctx, text, Runnable { latch.countDown() })
        latch.await(maxMs, TimeUnit.MILLISECONDS)
    }

    /**
     * The cue that it is listening. It is played on the media stream, not the notification one: a phone or tablet set to silent mutes
     * the notification stream, and a cue nobody can hear is no cue (on a tablet in that state there was no beep and it looked dead).
     */
    fun cue(ms: Int = 150) { runCatching { android.media.ToneGenerator(android.media.AudioManager.STREAM_MUSIC, 80).startTone(android.media.ToneGenerator.TONE_PROP_BEEP, ms) } }

    /** Listen once. [onHeard] gets what was said and 0, or "" and the Android error code (7 = nothing understood, 6 = silence, 9 = no permission). */
    fun listen(ctx: Context, onHeard: (String, Int) -> Unit) = listen(ctx, {}, onHeard)

    /** As [listen], and [onReady] is called when the recogniser is really ready for speech (the moment to talk). */
    fun listen(ctx: Context, onReady: () -> Unit, onHeard: (String, Int) -> Unit) {
        main.post {
            if (!canHear(ctx)) {
                // No usable recognizer on the phone at all: the offline model still can, if it is there and the microphone is allowed.
                if (androidx.core.content.ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED && WakeWord.installed(ctx)) OfflineListen.once(ctx) { t -> onHeard(t, if (t.isBlank()) 7 else 0) }
                else onHeard("", 9)
                return@post
            }
            stopNow()          // here, at once: a queued stop would run after the recognizer below was made, and destroy it
            // Android 12 and later have a recognizer that runs on the phone itself (no internet): prefer it, then the default one.
            val hasOnDevice = android.os.Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx.applicationContext)
            // the owner's choice (Settings, Buddy): some phones hear better with one engine than the other
            val onDevice = when (Store.voiceEngine(ctx)) { "online" -> !SpeechRecognizer.isRecognitionAvailable(ctx) && hasOnDevice; else -> hasOnDevice }
            android.util.Log.i("MBuddyVoice", "listening with ${if (onDevice) "the phone's own recogniser" else "the standard recogniser"} (choice: ${Store.voiceEngine(ctx)})")
            val engine = Store.voiceEngine(ctx)
            val louder = android.os.Build.VERSION.SDK_INT >= 33 && (engine == "boosted" || (engine == "auto" && Store.quietMic(ctx)))
            if (louder) startBoosted(ctx, engine == "auto", onReady, onHeard)
            else start(ctx, onDevice, onReady, onHeard)
        }
    }

    /**
     * For a quiet microphone: the app records the voice itself, raises its level (a gain that follows the loudest moment, so it never
     * clips), and hands that audio to the recogniser instead of letting it open the microphone (Android 13 and later:
     * EXTRA_AUDIO_SOURCE). Some tablets deliver speech so quietly that the recogniser's own capture finds "no speech".
     */
    private fun startBoosted(ctx: Context, automatic: Boolean, onReady: () -> Unit, onHeard: (String, Int) -> Unit) {
        val pipe = android.os.ParcelFileDescriptor.createPipe()
        val r = SpeechRecognizer.createSpeechRecognizer(ctx.applicationContext)
        recognizer = r
        listening = true
        val done = java.util.concurrent.atomic.AtomicBoolean(false)
        r.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                if (recognizer !== r) return
                done.set(true); listening = false
                val all = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: arrayListOf()
                alternatives = all.filter { it.isNotBlank() }
                android.util.Log.i("MBuddyVoice", "heard (boosted): " + alternatives.joinToString(" | "))
                if (automatic) Store.setQuietMic(ctx, true, 0)             // it helped: keep it
                onHeard(all.firstOrNull() ?: "", 0)
                stopNow()
            }
            override fun onError(error: Int) {
                if (recognizer !== r) return
                done.set(true); listening = false; stopNow()
                android.util.Log.i("MBuddyVoice", "recognizer error $error (boosted)")
                // louder did not help three times running: stop doing it by itself
                if (automatic && (error == 7 || error == 6)) { val m = Store.quietMisses(ctx) + 1; if (m >= 3) Store.setQuietMic(ctx, false, 0) else Store.setQuietMic(ctx, true, m) }
                onHeard("", error)
            }
            override fun onReadyForSpeech(params: Bundle?) { android.util.Log.i("MBuddyVoice", "ready for speech (boosted)"); cue(); runCatching { onReady() } }
            override fun onBeginningOfSpeech() { android.util.Log.i("MBuddyVoice", "speech began (boosted)") }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() { android.util.Log.i("MBuddyVoice", "speech ended (boosted)") }
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        Thread {
            var rec: android.media.AudioRecord? = null
            try {
                val rate = 16000
                val min = android.media.AudioRecord.getMinBufferSize(rate, android.media.AudioFormat.CHANNEL_IN_MONO, android.media.AudioFormat.ENCODING_PCM_16BIT)
                rec = android.media.AudioRecord(android.media.MediaRecorder.AudioSource.MIC, rate, android.media.AudioFormat.CHANNEL_IN_MONO, android.media.AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 16384))
                if (rec.state != android.media.AudioRecord.STATE_INITIALIZED) { main.post { onHeard("", 9) }; return@Thread }
                rec.startRecording()
                val out = android.os.ParcelFileDescriptor.AutoCloseOutputStream(pipe[1])
                val frame = ShortArray(320); val bytes = ByteArray(640)
                var loud = 600.0                              // the loudest recent level, falling slowly, so the gain follows the room
                val end = System.currentTimeMillis() + 9000
                while (!done.get() && System.currentTimeMillis() < end) {
                    val k = rec.read(frame, 0, frame.size); if (k <= 0) continue
                    var peak = 0; for (i in 0 until k) { val v = kotlin.math.abs(frame[i].toInt()); if (v > peak) peak = v }
                    loud = maxOf(peak.toDouble(), loud * 0.998, 500.0)
                    val gain = (18000.0 / loud).coerceIn(1.0, 12.0)
                    for (i in 0 until k) {
                        val v = (frame[i] * gain).toInt().coerceIn(-32768, 32767)
                        bytes[2 * i] = (v and 0xff).toByte(); bytes[2 * i + 1] = ((v shr 8) and 0xff).toByte()
                    }
                    out.write(bytes, 0, k * 2)
                }
                runCatching { out.close() }
            } catch (e: Exception) {
                android.util.Log.w("MBuddyVoice", "boosted capture stopped: ${e.message}")
            } finally { runCatching { rec?.stop(); rec?.release() } }
        }.start()
        r.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().let { if (it.language == "en") it.toLanguageTag() else "en-US" })
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, pipe[0])
            .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
            .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, android.media.AudioFormat.ENCODING_PCM_16BIT)
            .putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, 16000)
            .putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, false)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5))
    }

    private fun start(ctx: Context, onDevice: Boolean, onReady: () -> Unit, onHeard: (String, Int) -> Unit) {
        val r = if (onDevice) SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx.applicationContext) else SpeechRecognizer.createSpeechRecognizer(ctx.applicationContext)
        recognizer = r
        listening = true
        r.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                if (recognizer !== r) return                // an old recognizer that was replaced
                listening = false
                val all = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION) ?: arrayListOf()
                alternatives = all.filter { it.isNotBlank() }
                android.util.Log.i("MBuddyVoice", "heard: " + alternatives.joinToString(" | "))
                onHeard(all.firstOrNull() ?: "", 0)
                stopNow()
            }
            override fun onError(error: Int) {
                if (recognizer !== r) return
                listening = false; stopNow()
                android.util.Log.i("MBuddyVoice", "recognizer error $error (on the phone itself: $onDevice)")
                // "no speech" when the owner did speak is what a quiet microphone looks like: from the next time, listen with the voice made
                // louder (automatic mode only; the owner's own choice in Settings is never overridden)
                if ((error == 7 || error == 6) && Store.voiceEngine(ctx) == "auto" && android.os.Build.VERSION.SDK_INT >= 33 && !Store.quietMic(ctx)) {
                    android.util.Log.i("MBuddyVoice", "no speech heard: the next listen will use the voice made louder")
                    Store.setQuietMic(ctx, true, 0)
                }
                // the speech pack for the language is not on this phone (12, 13), or its own recognizer is not usable here: use the standard one
                if (onDevice && (error == 12 || error == 13 || error == SpeechRecognizer.ERROR_CLIENT || error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED) && SpeechRecognizer.isRecognitionAvailable(ctx)) {
                    main.post { start(ctx, false, onReady, onHeard) }
                    return
                }
                // the phone's recognizer could not connect or is busy: hear it with the offline model instead
                if ((error == SpeechRecognizer.ERROR_CLIENT || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY || error == SpeechRecognizer.ERROR_SERVER) && WakeWord.installed(ctx)) {
                    android.util.Log.i("MBuddyVoice", "the phone's recognizer failed ($error): using the offline model")
                    OfflineListen.once(ctx) { t -> onHeard(t, if (t.isBlank()) error else 0) }
                } else onHeard("", error)
            }
            // the cue: a short tone when the recogniser is really ready. A tone before that is a promise it cannot keep: the speech
            // engine takes a moment to start, and words said at the tone were lost.
            override fun onReadyForSpeech(params: Bundle?) { android.util.Log.i("MBuddyVoice", "ready for speech"); cue(); runCatching { onReady() } }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        r.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().let { if (it.language == "en") it.toLanguageTag() else "en-US" })
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, onDevice)
            .putExtra("android.speech.extra.BIASING_STRINGS", hints(ctx))        // API 33: words the recognizer should lean towards
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1600L)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1400L)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5))
    }

    /** Stop listening. On the main thread it happens at once; from another thread it is queued for the main thread. */
    fun stopListening() {
        if (Looper.myLooper() == Looper.getMainLooper()) stopNow() else main.post { stopNow() }
    }

    private fun stopNow() {
        listening = false
        recognizer?.let { runCatching { it.cancel(); it.destroy() } }
        recognizer = null
    }
}
