package app.mbuddy.mobile

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

/**
 * Which brain thinks, decided in one place for every path (a spoken request, the app's ask bar, a step of screen control): the cloud
 * (the owner's key), the PC (paired), or the model on this phone.
 *
 * The owner's choice is kept exactly: "cloud", "pc" or "phone" use only that one. "auto" tries cloud, PC, phone in that order and
 * remembers how each did: one that just failed is skipped for a while instead of being waited on every time. Measured 2026-10-10: a
 * paired PC whose server was off cost up to 8 s of connect time on every request before the phone was tried.
 */
object Brains {
    val ALL = listOf("cloud", "pc", "phone")
    private const val SKIP_MS = 90_000L

    class Health(val ok: Boolean, val at: Long, val ms: Long, val why: String)
    private val health = ConcurrentHashMap<String, Health>()

    /** Record how a brain did (called by every path that used one). */
    fun note(name: String, ok: Boolean, ms: Long, why: String = "", count: Boolean = true) {
        health[name] = Health(ok, System.currentTimeMillis(), ms, why.take(160))
        if (count) runCatching { usageAdd(name, ok, ms) }
        android.util.Log.i("MBuddyBrains", "$name ${if (ok) "answered" else "failed"} in $ms ms${if (why.isNotBlank()) ": $why" else ""}")
    }

    // ---- usage: how many answers each brain gave (kept on the phone, nothing sent anywhere) ----
    private var appCtx: Context? = null
    fun attach(ctx: Context) { appCtx = ctx.applicationContext }
    private fun usagePrefs() = appCtx?.getSharedPreferences("mbuddy_usage", Context.MODE_PRIVATE)
    private fun today() = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())

    @Synchronized private fun usageAdd(name: String, ok: Boolean, ms: Long) {
        val p = usagePrefs() ?: return
        val day = today()
        val e = p.edit()
        if (p.getString("$name.day", "") != day) { e.putString("$name.day", day).putInt("$name.today", 0).putInt("$name.todayFail", 0) }
        if (ok) {
            e.putInt("$name.today", (if (p.getString("$name.day", "") == day) p.getInt("$name.today", 0) else 0) + 1)
            e.putInt("$name.total", p.getInt("$name.total", 0) + 1).putLong("$name.totalMs", p.getLong("$name.totalMs", 0) + ms)
        } else {
            e.putInt("$name.todayFail", (if (p.getString("$name.day", "") == day) p.getInt("$name.todayFail", 0) else 0) + 1)
            e.putInt("$name.totalFail", p.getInt("$name.totalFail", 0) + 1)
        }
        e.apply()
    }

    private fun usageOf(name: String): JSONObject {
        val p = usagePrefs() ?: return JSONObject()
        val isToday = p.getString("$name.day", "") == today()
        val total = p.getInt("$name.total", 0)
        return JSONObject().put("today", if (isToday) p.getInt("$name.today", 0) else 0).put("todayFail", if (isToday) p.getInt("$name.todayFail", 0) else 0)
            .put("total", total).put("totalFail", p.getInt("$name.totalFail", 0)).put("avgMs", if (total > 0) p.getLong("$name.totalMs", 0) / total else 0)
    }

    /** Whether a brain is set up at all (a key, a pairing, a model on the phone). */
    fun ready(ctx: Context, name: String): Boolean = when (name) {
        "cloud" -> Cloud.hasKey(ctx)
        "pc" -> Store.paired(ctx)
        "phone" -> LocalModel.active(ctx) != null
        else -> false
    }

    private fun recentlyFailed(name: String): Boolean = health[name]?.let { !it.ok && System.currentTimeMillis() - it.at < SKIP_MS } ?: false

    /** The brains to try, in order. Auto leaves out one that failed in the last 90 s, unless nothing else is ready. */
    fun order(ctx: Context): List<String> {
        val pref = Store.planner(ctx)
        if (pref in ALL) return listOf(pref)
        val set = ALL.filter { ready(ctx, it) }
        val fresh = set.filterNot { recentlyFailed(it) }
        return fresh.ifEmpty { set }
    }

    /** What the screens show: the choice, and for each brain whether it is set up and how it last did. */
    fun state(ctx: Context): JSONObject {
        attach(ctx)
        val o = JSONObject().put("choice", Store.planner(ctx)).put("order", JSONArray(order(ctx)))
        for (n in ALL) {
            val h = health[n]
            o.put(n, JSONObject().put("ready", ready(ctx, n)).put("ok", h?.ok ?: JSONObject.NULL).put("ms", h?.ms ?: 0)
                .put("ago", h?.let { (System.currentTimeMillis() - it.at) / 1000 } ?: -1).put("why", h?.why ?: "").put("usage", usageOf(n)))
        }
        return o
    }

    /** Whether the PC's server answers within [timeoutMs] (a quick look when the app opens, so the first request does not wait on it). */
    fun probePc(ctx: Context, timeoutMs: Int = 2500) {
        attach(ctx)
        if (!Store.paired(ctx)) return
        val t0 = System.currentTimeMillis()
        val ok = runCatching {
            val c = URL(Store.pcBase(ctx) + "/health").openConnection() as HttpURLConnection
            c.connectTimeout = timeoutMs; c.readTimeout = timeoutMs
            val s = c.responseCode; c.disconnect(); s in 200..499
        }.getOrDefault(false)
        note("pc", ok, System.currentTimeMillis() - t0, if (ok) "" else "the PC's server did not answer", count = false)
    }

    /** Ask each brain that is set up one tiny question, and say how each did. Blocking: call off the main thread. */
    fun test(ctx: Context): JSONObject {
        val out = JSONObject()
        for (n in ALL) {
            if (!ready(ctx, n)) { out.put(n, JSONObject().put("ready", false)); continue }
            val t0 = System.currentTimeMillis()
            val (ok, why) = try {
                when (n) {
                    "cloud" -> {
                        val body = JSONObject().put("model", Store.cloudModel(ctx)).put("stream", false)
                            .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", "Reply with the single word: ready")))
                            .put("options", JSONObject().put("num_predict", 400)).toString()
                        val r = Cloud.chat(ctx, body)
                        when (r.status) { 200 -> true to ""; 401, 403 -> false to "the cloud refused the key"; 0 -> false to "the cloud can't be reached"; else -> false to "the cloud answered ${r.status}" }
                    }
                    "pc" -> {
                        val r = Pc.request(ctx, "POST", "/remote/control/chat", JSONObject().put("text", "Reply with the single word: ready").put("speak", false).toString())
                        if (r.status == 200) true to "" else false to (if (r.status == 0) "the PC's server did not answer" else "the PC answered ${r.status}")
                    }
                    else -> {
                        val t = LocalModel.chat(ctx, "Reply with one word.", "Say ready.", null)
                        (t.isNotBlank()) to (if (t.isBlank()) "the model gave no answer" else "")
                    }
                }
            } catch (e: Exception) { false to (e.message ?: "failed") }
            val ms = System.currentTimeMillis() - t0
            note(n, ok, ms, why, count = false)
            out.put(n, JSONObject().put("ready", true).put("ok", ok).put("ms", ms).put("why", why))
        }
        return out
    }
}
