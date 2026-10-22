package app.mbuddy.mobile

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** One answer from the PC: the HTTP status (0 when it could not be reached) and the body text. */
data class Reply(val status: Int, val body: String)

/**
 * Talking to the PC's phone gateway (api/remote_gateway.py on the PC), the same server the phone web page uses.
 * Calls block: use them off the main thread (the JS bridge and the worker both do).
 */
object Pc {
    private const val TIMEOUT_MS = 3000

    private fun failure(why: String) = Reply(0, JSONObject().put("ok", false).put("error", why).toString())

    /** `path` is as the web page writes it: /remote/ui/..., /health, /pair or /mcp/<anything>. */
    fun request(ctx: Context, method: String, path: String, body: String = ""): Reply {
        val base = Store.pcBase(ctx)
        if (base.isEmpty()) return failure("This phone isn't paired with a PC yet.")
        // The address Google AI Edge Gallery uses carries the token in its path: the page cannot know it, the app does.
        val real = if (path.startsWith("/mcp/")) "/mcp/" + Store.token(ctx) else path
        return try {
            val conn = URL(base + real).openConnection() as HttpURLConnection
            conn.requestMethod = method
            conn.connectTimeout = TIMEOUT_MS
            conn.readTimeout = TIMEOUT_MS * 4          // the PC may be thinking about a question
            conn.setRequestProperty("Accept", "application/json")
            if (real.startsWith("/remote/")) conn.setRequestProperty("Authorization", "Bearer " + Store.token(ctx))
            if (body.isNotEmpty() && method != "GET") {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            conn.disconnect()
            Reply(status, text)
        } catch (e: IOException) {
            failure("The PC didn't answer (${e.javaClass.simpleName}).")
        }
    }

    /** Pair with the six-digit code the PC shows (Away, New pairing code). Returns an error text, or null when paired. */
    fun pair(ctx: Context, baseUrl: String, code: String, deviceName: String): String? {
        val base = baseUrl.trim().trimEnd('/')
        if (base.startsWith("http://") && !privateHost(base)) return "Use https:// unless the PC is on your own network (an address like 192.168..., a .local name or a Tailscale address)."
        if (!base.startsWith("https://") && !base.startsWith("http://")) return "The address should start with https:// (or http:// on your home Wi-Fi)."
        return try {
            val conn = URL("$base/pair").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = TIMEOUT_MS
            conn.readTimeout = TIMEOUT_MS
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(JSONObject().put("code", code.trim()).put("name", deviceName).toString().toByteArray()) }
            val status = conn.responseCode
            val text = (if (status >= 400) conn.errorStream else conn.inputStream)?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            val json = runCatching { JSONObject(text) }.getOrNull()
            if (json != null && json.optBoolean("ok")) {
                Store.save(ctx, base, json.getString("token"))
                null
            } else json?.optString("error")?.takeIf { it.isNotEmpty() } ?: "The PC refused the code (HTTP $status)."
        } catch (e: IOException) {
            "I can't reach that address (${e.javaClass.simpleName}). Is Tailscale on, and is MBUDDY running on the PC?"
        }
    }

    /** Whether a plain-http address is on the owner's own network: loopback, private ranges, link-local, Tailscale, or a .local name. */
    fun privateHost(url: String): Boolean {
        val host = runCatching { java.net.URI(url).host }.getOrNull()?.lowercase() ?: return false
        if (host == "localhost" || host.endsWith(".local") || host.endsWith(".ts.net")) return true
        val p = host.split('.').mapNotNull { it.toIntOrNull() }
        if (p.size != 4) return false
        return p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168) || (p[0] == 172 && p[1] in 16..31) || (p[0] == 169 && p[1] == 254) || (p[0] == 100 && p[1] in 64..127)
    }
}
