package app.mbuddy.mobile

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import org.json.JSONObject
import org.vosk.Recognizer

/**
 * Hears one request with the offline model (no Google speech service, no internet) when the phone's own recognizer cannot be used, as on
 * a phone whose speech service does not connect. It is less accurate than Google's for names and long sentences, but it is always there.
 */
object OfflineListen {
    /**
     * Hears which of [phrases] was said (it cannot hear anything else, which is why it is accurate: "yes" or "no" cannot be mistaken for
     * a name). Returns the phrase said, or "" when none was (or when the offline model is not installed).
     */
    fun oneOf(ctx: Context, phrases: List<String>, seconds: Int = 12): String {
        val model = WakeWord.model(ctx) ?: return ""
        val grammar = org.json.JSONArray().also { a -> phrases.forEach { a.put(it.lowercase()) }; a.put("[unk]") }.toString()
        var rec: AudioRecord? = null
        var recognizer: Recognizer? = null
        try {
            recognizer = Recognizer(model, 16000f, grammar)
            val min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val buf = ShortArray(maxOf(min, 4096))
            rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 8192))
            if (rec.state != AudioRecord.STATE_INITIALIZED) return ""
            rec.startRecording()
            val until = System.currentTimeMillis() + seconds * 1000L
            while (System.currentTimeMillis() < until) {
                if (Voice.busySpeaking()) { recognizer.reset(); Thread.sleep(100); rec.read(buf, 0, buf.size); continue }       // not while it is talking itself
                val n = rec.read(buf, 0, buf.size)
                if (n < 0) break
                if (n == 0) continue
                if (recognizer.acceptWaveForm(buf, n)) {
                    val said = JSONObject(recognizer.result).optString("text").trim()
                    if (said.isNotEmpty() && said != "[unk]") return phrases.firstOrNull { it.equals(said, true) } ?: said
                }
            }
            return JSONObject(recognizer.finalResult).optString("text").trim().takeIf { it != "[unk]" } ?: ""
        } catch (e: Exception) {
            android.util.Log.w("MBuddyOffline", "closed-list listening failed", e); return ""
        } finally {
            runCatching { rec?.stop() }; runCatching { rec?.release() }; runCatching { recognizer?.close() }
        }
    }

    /** What the offline model makes of a WAV recording (16 kHz mono, 16-bit): the backup when the model on the phone cannot transcribe. */
    fun fromWav(ctx: Context, wav: ByteArray): String = try {
        val model = WakeWord.model(ctx)
        if (model == null || wav.size <= 44) "" else Recognizer(model, 16000f).use { r ->
            var i = 44
            while (i < wav.size) { val n = minOf(8000, wav.size - i); r.acceptWaveForm(wav.copyOfRange(i, i + n), n); i += n }
            JSONObject(r.finalResult).optString("text").trim()
        }
    } catch (e: Exception) { android.util.Log.w("MBuddyOffline", "could not read the recording", e); "" }

    /** Calls [onHeard] once, from a background thread, with what was said ("" when nothing was). */
    fun once(ctx: Context, seconds: Int = 9, onHeard: (String) -> Unit) {
        Thread({
            var text = ""
            var rec: AudioRecord? = null
            var recognizer: Recognizer? = null
            try {
                val model = WakeWord.model(ctx) ?: return@Thread onHeard("")
                recognizer = Recognizer(model, 16000f)
                val min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                val buf = ShortArray(maxOf(min, 4096))
                rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 8192))
                if (rec.state != AudioRecord.STATE_INITIALIZED) return@Thread onHeard("")
                rec.startRecording()
                val until = System.currentTimeMillis() + seconds * 1000L
                var spoke = false
                while (System.currentTimeMillis() < until) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n < 0) break
                    if (n == 0) continue
                    if (recognizer.acceptWaveForm(buf, n)) {                      // the model decided the sentence ended
                        text = JSONObject(recognizer.result).optString("text"); break
                    }
                    if (JSONObject(recognizer.partialResult).optString("partial").isNotBlank()) spoke = true
                    else if (!spoke && System.currentTimeMillis() > until - (seconds - 5) * 1000L) break      // nothing said in the first five seconds
                }
                if (text.isBlank()) text = JSONObject(recognizer.finalResult).optString("text")
            } catch (e: Exception) {
                android.util.Log.w("MBuddyOffline", "offline listening failed", e)
            } finally {
                runCatching { rec?.stop() }; runCatching { rec?.release() }; runCatching { recognizer?.close() }
            }
            onHeard(text.trim())
        }, "mbuddy-offline-listen").start()
    }
}
