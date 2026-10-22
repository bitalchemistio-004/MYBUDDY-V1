package app.mbuddy.mobile

import android.content.Context
import android.provider.Settings
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.zip.ZipInputStream

/**
 * The small offline speech model "hey buddy" listens with (Vosk, English, 39 MB, from the Vosk project's own site; it is checked against
 * the checksum that site publishes). Downloaded once, here, when the owner turns "hey buddy" on. It listens on the phone only: no audio
 * leaves the phone while it waits for its name.
 */
object WakeWord {
    const val URL_ZIP = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    const val SIZE = 41_205_931L
    private const val MD5 = "09ab50ccd62b674cbaa231b825f9c1cb"

    private fun root(ctx: Context) = File(ctx.filesDir, "vosk").also { it.mkdirs() }
    fun modelDir(ctx: Context) = File(root(ctx), "model")
    fun installed(ctx: Context) = File(modelDir(ctx), "conf/mfcc.conf").isFile

    @Volatile private var phase = ""             // "", downloading, checking, unpacking
    @Volatile private var done = 0L
    @Volatile private var error = ""

    fun download(ctx: Context): String? {
        if (installed(ctx)) return null
        if (phase.isNotEmpty()) return "Already downloading."
        if (root(ctx).usableSpace < SIZE * 3) return "Not enough free storage (about 150 MB needed)."
        val app = ctx.applicationContext
        phase = "downloading"; done = 0; error = ""
        Thread({ run(app) }, "mbuddy-wake-model").start()
        return null
    }

    private fun run(ctx: Context) {
        val zip = File(root(ctx), "model.zip")
        try {
            val conn = URL(URL_ZIP).openConnection() as HttpURLConnection
            conn.connectTimeout = 20_000; conn.readTimeout = 30_000
            if (conn.responseCode != 200) { error = "The download server answered ${conn.responseCode}."; return }
            FileOutputStream(zip).use { out -> conn.inputStream.use { i -> val buf = ByteArray(128 * 1024); while (true) { val n = i.read(buf); if (n < 0) break; out.write(buf, 0, n); done += n } } }
            conn.disconnect()
            phase = "checking"
            val md = MessageDigest.getInstance("MD5")
            zip.inputStream().use { i -> val buf = ByteArray(1 shl 20); while (true) { val n = i.read(buf); if (n < 0) break; md.update(buf, 0, n) } }
            if (md.digest().joinToString("") { "%02x".format(it) } != MD5) { error = "The file did not match its published checksum, so I threw it away. Try again."; return }
            phase = "unpacking"
            val target = modelDir(ctx); target.deleteRecursively(); target.mkdirs()
            ZipInputStream(zip.inputStream()).use { zin ->
                while (true) {
                    val e = zin.nextEntry ?: break
                    val rel = e.name.substringAfter('/', "")          // drop the folder the zip wraps everything in
                    if (rel.isEmpty()) continue
                    val f = File(target, rel)
                    if (!f.canonicalPath.startsWith(target.canonicalPath + File.separator)) continue      // never write outside the model folder
                    if (e.isDirectory) f.mkdirs() else { f.parentFile?.mkdirs(); FileOutputStream(f).use { zin.copyTo(it) } }
                }
            }
            if (!installed(ctx)) error = "The model didn't unpack properly."
        } catch (e: Exception) {
            error = "The download was interrupted (${e.javaClass.simpleName}). Try again."
        } finally {
            zip.delete(); phase = ""
        }
    }

    @Volatile private var shared: org.vosk.Model? = null

    /** The offline model, loaded once and shared by "hey buddy" and by the fallback that hears a request when the phone's recognizer is unusable. */
    @Synchronized fun model(ctx: Context): org.vosk.Model? {
        shared?.let { return it }
        if (!installed(ctx)) return null
        return runCatching { org.vosk.Model(modelDir(ctx).absolutePath) }.getOrNull()?.also { shared = it }
    }

    @Synchronized fun releaseModel() { runCatching { shared?.close() }; shared = null }

    fun delete(ctx: Context) { WakeService.stop(ctx); releaseModel(); modelDir(ctx).deleteRecursively(); Store.setWake(ctx, false) }

    fun state(ctx: Context): JSONObject = JSONObject()
        .put("installed", installed(ctx)).put("phase", phase).put("progress", if (phase == "downloading") done.toDouble() / SIZE else if (installed(ctx)) 1.0 else 0.0)
        .put("error", error).put("sizeMb", SIZE / 1_000_000).put("enabled", Store.wake(ctx)).put("running", WakeService.running)
        .put("mic", Voice.canHear(ctx)).put("overlay", Settings.canDrawOverlays(ctx))
}
