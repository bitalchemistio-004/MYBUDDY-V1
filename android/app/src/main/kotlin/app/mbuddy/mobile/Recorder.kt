package app.mbuddy.mobile

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/**
 * Records one thing the owner says: it waits for speech to start, keeps the beginning (a little before the first loud moment), and stops
 * when they have been quiet for a moment or after [maxSeconds]. Returns it as a 16 kHz mono WAV, which the model on the phone can listen to.
 */
object Recorder {
    private const val RATE = 16000
    private const val FRAME = 320                       // 20 ms

    /** Each recording takes a number; a newer one, or [cancel], makes an older one that is still waiting for speech give up at once. */
    @Volatile private var generation = 0
    fun cancel() { generation++ }

    /** The recording as WAV bytes, or null when nothing was said (or the microphone could not be used). Blocks; not for the main thread. */
    fun utterance(maxSeconds: Int = 12, waitForSpeechSeconds: Int = 6): ByteArray? {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        var rec: AudioRecord? = null
        val mine = ++generation
        try {
            rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 16384))
            if (rec.state != AudioRecord.STATE_INITIALIZED) return null
            rec.startRecording()
            val frame = ShortArray(FRAME)
            val kept = ArrayList<ShortArray>()          // everything since a moment before speech began
            val ring = ArrayDeque<ShortArray>()         // the last 300 ms, kept while waiting
            var floor = 0.0
            var peak = 0.0
            var calibrated = 0
            var started = false
            var loudRun = 0
            var quiet = 0
            val t0 = System.currentTimeMillis()
            while (true) {
                val elapsed = System.currentTimeMillis() - t0
                if (mine != generation) return null          // superseded: a newer request or a tap took over
                if (elapsed > maxSeconds * 1000L) break
                if (!started && elapsed > waitForSpeechSeconds * 1000L) { android.util.Log.i("MBuddyRecorder", "no speech: floor=${floor.toInt()} peak=${peak.toInt()}"); return null }
                var got = 0
                while (got < FRAME) { val n = rec.read(frame, got, FRAME - got); if (n < 0) return null; got += n }
                val level = rms(frame)
                if (level > peak) peak = level
                if (calibrated < 12) { floor = (floor * calibrated + level) / (calibrated + 1); calibrated++; ring.addLast(frame.copyOf()); continue }
                val threshold = maxOf(90.0, floor * 4.0)
                val loud = level > threshold
                if (!started) {
                    ring.addLast(frame.copyOf()); if (ring.size > 15) ring.removeFirst()
                    loudRun = if (loud) loudRun + 1 else 0
                    if (loudRun >= 3) { started = true; kept.addAll(ring); ring.clear() }
                } else {
                    kept.add(frame.copyOf())
                    quiet = if (loud) 0 else quiet + 1
                    if (quiet >= 45) break              // 0.9 s of quiet: they finished
                }
            }
            if (!started) return null
            return wav(kept)
        } catch (e: Exception) {
            android.util.Log.w("MBuddyRecorder", "recording failed", e)
            return null
        } finally {
            runCatching { rec?.stop() }; runCatching { rec?.release() }
        }
    }

    private fun rms(f: ShortArray): Double { var s = 0.0; for (v in f) s += v.toDouble() * v; return sqrt(s / f.size) }

    private fun wav(frames: List<ShortArray>): ByteArray {
        val samples = frames.sumOf { it.size }
        val pcm = ByteBuffer.allocate(samples * 2).order(ByteOrder.LITTLE_ENDIAN)
        for (f in frames) for (v in f) pcm.putShort(v)
        val out = ByteArrayOutputStream()
        val h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        h.put("RIFF".toByteArray()).putInt(36 + samples * 2).put("WAVE".toByteArray()).put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            .putInt(RATE).putInt(RATE * 2).putShort(2).putShort(16).put("data".toByteArray()).putInt(samples * 2)
        out.write(h.array()); out.write(pcm.array())
        return out.toByteArray()
    }
}
