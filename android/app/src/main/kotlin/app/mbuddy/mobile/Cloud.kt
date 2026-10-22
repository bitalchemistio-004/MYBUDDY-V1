package app.mbuddy.mobile

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * The cloud brain, straight from the phone (no PC needed): Ollama Cloud, with the owner's own key. The key lives in Store (encrypted
 * with a key that never leaves the phone's secure hardware); the page never sees it, and it only ever goes to ollama.com.
 */
object Cloud {
    private const val BASE = "https://ollama.com"
    private const val CHECK_MODEL = "gpt-oss:120b"

    fun hasKey(ctx: Context) = Store.cloudKey(ctx).isNotEmpty()

    private fun call(ctx: Context, method: String, path: String, body: String, timeoutMs: Int): Reply {
        val key = Store.cloudKey(ctx)
        if (key.isEmpty()) return Reply(0, JSONObject().put("ok", false).put("error", "no_key").toString())
        return try {
            val c = URL(BASE + path).openConnection() as HttpURLConnection
            c.requestMethod = method
            c.connectTimeout = 10_000
            c.readTimeout = timeoutMs
            // ollama.com answers an HTML 403 to Android's default "Dalvik/..." agent before it looks at the key
            c.setRequestProperty("User-Agent", "MBUDDY-mobile/0.1")
            c.setRequestProperty("Authorization", "Bearer $key")
            c.setRequestProperty("Accept", "application/json")
            if (body.isNotEmpty()) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = c.responseCode
            val text = (if (status >= 400) c.errorStream else c.inputStream)?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            c.disconnect()
            Reply(status, text)
        } catch (e: IOException) {
            Reply(0, JSONObject().put("ok", false).put("error", "offline").toString())
        }
    }

    /** One chat request (the body is Ollama's /api/chat body, built by the page). */
    fun chat(ctx: Context, body: String): Reply = call(ctx, "POST", "/api/chat", body, 90_000)

    fun models(ctx: Context): Reply = call(ctx, "GET", "/api/tags", "", 20_000)

    /** Models the free plan has answered for (read from ollama.com, 2026-10-08); tried when the chosen one is refused. */
    private val FALLBACKS = listOf("gpt-oss:120b", "gpt-oss:20b", "gemma4:31b", "nemotron-3-super")

    private fun tiny(ctx: Context, model: String): Int {
        val probe = JSONObject().put("model", model).put("stream", false)
            .put("messages", org.json.JSONArray().put(JSONObject().put("role", "user").put("content", "hi")))
            .put("options", JSONObject().put("num_predict", 8)).toString()
        return chat(ctx, probe).status
    }

    /** The first cloud model that answers with this key (the chosen one first), or null. It is remembered as the chosen model. */
    fun findModel(ctx: Context): String? {
        val chosen = Store.cloudModel(ctx)
        for (m in (listOf(chosen) + FALLBACKS).distinct()) {
            val s = tiny(ctx, m)
            if (s == 200) { if (m != chosen) Store.setCloudModel(ctx, m); return m }
            if (s == 401 || s == 0) return null          // the key itself is refused, or no network: other models won't change it
        }
        return null
    }

    /** What was pasted, cleaned (quotes and a "Bearer " prefix removed), or null when it cannot be a key (too short, or has spaces in it). */
    fun cleanKey(raw: String): String? {
        val k = raw.trim().trim('"', '\'').removePrefix("Bearer ").trim()
        return if (k.length < 20 || k.any { it.isWhitespace() }) null else k
    }

    /** Saves the key if the cloud accepts it. Returns an error in words, or null when saved. */
    fun saveKey(ctx: Context, key: String): String? {
        val k = cleanKey(key) ?: return "That doesn't look like an Ollama key (a long string with no spaces)."
        val old = Store.cloudKey(ctx)
        Store.setCloudKey(ctx, k)
        val probe = JSONObject().put("model", CHECK_MODEL).put("stream", false)
            .put("messages", org.json.JSONArray().put(JSONObject().put("role", "user").put("content", "hi")))
            .put("options", JSONObject().put("num_predict", 8)).toString()
        val r = chat(ctx, probe)
        return when {
            r.status == 200 -> null
            r.status == 401 -> { Store.setCloudKey(ctx, old); "ollama.com refused this key (HTTP ${r.status}). It has to be an Ollama Cloud key, made at ollama.com/settings/keys. Keys from Gemini, OpenAI or Anthropic do not work here." }
            r.status == 0 -> { Store.setCloudKey(ctx, old); "I can't reach the cloud right now. Check the internet and try again." }
            r.status == 402 || r.status == 403 -> {      // not a bad key: that model is not on the plan. Look for one that is
                if (findModel(ctx) != null) null
                else "The key was saved, but ollama.com answered 403 for every model I tried (${FALLBACKS.joinToString(", ")}). Check that this Ollama account has cloud access at ollama.com."
            }
            else -> { Store.setCloudKey(ctx, old); "The cloud answered with an error (${r.status})." }
        }
    }
}
