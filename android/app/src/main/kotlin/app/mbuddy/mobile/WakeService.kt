package app.mbuddy.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

/**
 * "Hey buddy", with the app closed. A foreground service (with its notification, and the microphone indicator Android shows) listens on
 * the phone for the two words and nothing else (an offline recognizer limited to that phrase; no audio is kept or sent). When it hears
 * them it chimes, listens for what you want, thinks (NativeBrain), does it and speaks the answer, then goes back to waiting.
 * The owner turns it on in Settings and can stop it from its notification.
 */
class WakeService : Service() {
    private var model: Model? = null
    private var recognizer: Recognizer? = null
    private var loop: Thread? = null
    private var lock: PowerManager.WakeLock? = null
    @Volatile private var busy = false
    @Volatile private var stopped = false
    private var lastWake = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { Store.setWake(this, false); stopSelf(); return START_NOT_STICKY }
        channel()
        val n = notification("Listening for “hey buddy”.")
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE) else startForeground(ID, n)
        if (!running) {
            running = true; stopped = false
            lock = (getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mbuddy:wake").apply { setReferenceCounted(false); acquire() }
            Thread({ load() }, "mbuddy-wake-load").start()
        }
        return START_STICKY
    }

    private fun load() {
        if (!WakeWord.installed(this)) { status("The wake-word model isn't downloaded. Open MBUDDY, Settings."); stopSelf(); return }
        try {
            val m = WakeWord.model(this) ?: throw IllegalStateException("the model didn't load")
            model = m
            recognizer = Recognizer(m, 16000f, GRAMMAR).also { it.setWords(true) }      // each word with how sure it is
            listen()
        } catch (e: Exception) {
            status("I couldn't start listening (${e.javaClass.simpleName}).")
            stopSelf()
        }
    }

    /**
     * The listening loop, our own (Vosk's ready-made one throws an uncaught exception on an audio read error, which would close the whole
     * app). It records from the microphone, feeds the recognizer, and when anything goes wrong with the microphone (another app took it,
     * the audio system restarted) it lets go, waits a few seconds and tries again. While a request is being handled it is released.
     */
    private fun listen() {
        if (stopped || loop?.isAlive == true) return
        loop = Thread({ listenLoop() }, "mbuddy-wake-listen").also { it.start() }
    }

    private fun listenLoop() {
        val r = recognizer ?: return
        val rate = 16000
        val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val buf = ShortArray(maxOf(min, 4096))
        var announced = false
        while (!stopped) {
            if (busy || Agent.running) { Thread.sleep(200); continue }          // while a task runs the microphone is the task's (it waits for your yes)
            var rec: AudioRecord? = null
            try {
                rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min * 2, 8192))
                if (rec.state != AudioRecord.STATE_INITIALIZED) throw IllegalStateException("the microphone isn't available")
                rec.startRecording()
                r.reset()
                if (!announced) { status("Listening for “hey buddy”."); announced = true }
                while (!stopped && !busy && !Agent.running) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n < 0) throw java.io.IOException("microphone read error $n")
                    if (n == 0) continue
                    // only a finished phrase counts: a half-heard guess, from a list this short, is "hey buddy" for almost any speech
                    if (!r.acceptWaveForm(buf, n)) continue
                    val result = r.result
                    if (Voice.busySpeaking()) { r.reset(); continue }              // the buddy's own voice is never its name being called
                    heard(result)
                }
            } catch (e: Exception) {
                android.util.Log.w("MBuddyWake", "listening paused: ${e.message}")
                announced = false
                if (!stopped) { status("Microphone busy: trying again…"); Thread.sleep(3000) }
            } finally {
                runCatching { rec?.stop() }; runCatching { rec?.release() }
            }
        }
    }

    private fun heard(h: String?) {
        h ?: return
        val j = runCatching { JSONObject(h) }.getOrNull() ?: return
        if (!j.optString("text").contains("hey buddy")) return
        // both words, and sure of both: the recognizer gives each word a confidence from 0 to 1
        val words = j.optJSONArray("result")
        val confs = (0 until (words?.length() ?: 0)).map { words!!.getJSONObject(it) }.filter { it.optString("word") in setOf("hey", "buddy") }.map { it.optDouble("conf", 0.0) }
        val sure = confs.size >= 2 && confs.all { it >= MIN_CONF }
        android.util.Log.i("MBuddyWake", "heard \"${j.optString("text")}\" conf=${confs.joinToString(",") { "%.2f".format(it) }} ${if (sure) "-> awake" else "-> ignored"}")
        if (!sure || busy || System.currentTimeMillis() - lastWake < 3000) return
        busy = true; lastWake = System.currentTimeMillis()
        Thread({ command() }, "mbuddy-wake-command").start()
    }

    /** It heard its name: the shared routine (chime, listen, think, do, answer), then back to waiting. */
    private fun command() {
        try {
            Thread.sleep(400)                                        // the listening loop lets go of the microphone (the request needs it)
            Command.run(this) { status(it) }
        } catch (e: Exception) {
            android.util.Log.w("MBuddyWake", "command failed", e)
        } finally {
            busy = false
        }
    }

    private fun channel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val c = NotificationChannel(CHANNEL, "Hey buddy", NotificationManager.IMPORTANCE_LOW)
            c.description = "Shown while MBUDDY listens for its name. It listens on the phone only."
            getSystemService(NotificationManager::class.java).createNotificationChannel(c)
        }
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 1, Intent(this, SetupActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 2, Intent(this, WakeService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_notify).setContentTitle("MBUDDY")
            .setContentText(text).setOngoing(true).setContentIntent(open).addAction(0, "Stop listening", stop).setPriority(NotificationCompat.PRIORITY_LOW).build()
    }

    private fun status(text: String) {
        runCatching { getSystemService(NotificationManager::class.java).notify(ID, notification(text)) }
    }

    override fun onDestroy() {
        stopped = true; running = false
        runCatching { recognizer?.close() }; recognizer = null
        model = null                                                  // the model is shared: it stays loaded for the fallback listener
        runCatching { lock?.let { if (it.isHeld) it.release() } }
        super.onDestroy()
    }

    companion object {
        @Volatile var running = false
        private const val CHANNEL = "mbuddy_wake"
        private const val ID = 4242
        const val ACTION_STOP = "app.mbuddy.mobile.STOP_WAKE"
        // "hey buddy", the words that sound like it (so they are not taken for it), and anything else
        private const val GRAMMAR = "[\"hey buddy\", \"hey\", \"buddy\", \"baby\", \"body\", \"bud\", \"daddy\", \"hey google\", \"okay\", \"[unk]\"]"
        private const val MIN_CONF = 0.80

        fun start(ctx: Context) { ContextCompat.startForegroundService(ctx, Intent(ctx, WakeService::class.java)) }
        fun stop(ctx: Context) { ctx.stopService(Intent(ctx, WakeService::class.java)) }
    }
}
