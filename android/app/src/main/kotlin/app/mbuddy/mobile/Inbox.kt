package app.mbuddy.mobile

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Things the buddy did while the app's screens were not open (a task you asked for by voice, a note, what was said). The screens keep
 * the phone's data; this is a short list the app hands to them the next time they open, so nothing said by voice is lost.
 * Each entry is {"type": "task" | "event" | "note" | "convo" | "log" | "timer", ...}.
 */
object Inbox {
    private const val FILE = "mbuddy_inbox"
    private const val KEY = "items"
    private const val MAX = 200

    private fun prefs(ctx: Context) = ctx.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    @Synchronized
    fun add(ctx: Context, item: JSONObject) {
        val all = runCatching { JSONArray(prefs(ctx).getString(KEY, "[]")) }.getOrDefault(JSONArray())
        all.put(item.put("at", System.currentTimeMillis()))
        val keep = JSONArray()
        for (i in maxOf(0, all.length() - MAX) until all.length()) keep.put(all.get(i))
        prefs(ctx).edit().putString(KEY, keep.toString()).apply()
    }

    /** Everything waiting, as a JSON array text; the list is emptied. */
    @Synchronized
    fun drain(ctx: Context): String {
        val text = prefs(ctx).getString(KEY, "[]") ?: "[]"
        prefs(ctx).edit().putString(KEY, "[]").apply()
        return text
    }
}
