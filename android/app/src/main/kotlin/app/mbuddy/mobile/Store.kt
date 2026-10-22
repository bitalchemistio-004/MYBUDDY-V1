package app.mbuddy.mobile

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * What the app remembers about the PC it is paired with: its address and the token it got when it was paired.
 * Both are kept encrypted with a key that never leaves the phone's secure hardware (Android Keystore). The token is never
 * shown and never put in a log; it only goes to the PC, in the Authorization header.
 */
object Store {
    // Opening the encrypted file (the key from the Keystore, then the file) takes 100 to 200 ms. It used to be opened again for every read,
    // and a screen that reads four values stood still for most of a second (measured 2026-10-11: brainsState 645 ms, paired 221 ms).
    @Volatile private var secure: SharedPreferences? = null
    private fun prefs(ctx: Context): SharedPreferences = secure ?: synchronized(this) {
        secure ?: run {
            val key = MasterKey.Builder(ctx.applicationContext).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
            EncryptedSharedPreferences.create(
                ctx.applicationContext, "mbuddy_secure", key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            ).also { secure = it }
        }
    }

    fun pcBase(ctx: Context): String = prefs(ctx).getString("pc_base", "") ?: ""
    fun token(ctx: Context): String = prefs(ctx).getString("token", "") ?: ""
    fun paired(ctx: Context): Boolean = pcBase(ctx).isNotEmpty() && token(ctx).isNotEmpty()

    fun save(ctx: Context, base: String, token: String) {
        prefs(ctx).edit().putString("pc_base", base.trim().trimEnd('/')).putString("token", token).apply()
    }

    /** Pairing is forgotten; the cloud key is the owner's own and stays until they remove it. */
    fun clear(ctx: Context) {
        val key = cloudKey(ctx)
        prefs(ctx).edit().clear().apply()
        if (key.isNotEmpty()) setCloudKey(ctx, key)
    }

    fun cloudKey(ctx: Context): String = prefs(ctx).getString("cloud_key", "") ?: ""
    fun setCloudKey(ctx: Context, key: String) { prefs(ctx).edit().putString("cloud_key", key).apply() }

    // Settings that are not secrets (which buddy, whether it speaks): kept apart, and kept when the pairing is cleared.
    private fun plain(ctx: Context) = ctx.applicationContext.getSharedPreferences("mbuddy_plain", Context.MODE_PRIVATE)
    private val CAST = setOf("blink", "void", "glitch", "cinder", "oracle")

    fun character(ctx: Context): String = plain(ctx).getString("character", "blink")?.takeIf { it in CAST } ?: "blink"
    /** The voice the owner picked for a buddy ("" = automatic: a profile and an installed voice chosen for that buddy). */
    fun voiceName(ctx: Context, character: String): String = plain(ctx).getString("voice_$character", "") ?: ""
    fun setVoiceName(ctx: Context, character: String, name: String) { plain(ctx).edit().putString("voice_$character", name).apply() }
    fun setCharacter(ctx: Context, name: String) { if (name in CAST) plain(ctx).edit().putString("character", name).apply() }
    /** Which brain plans a task: auto (cloud, then the PC, then the phone), or one of cloud / pc / phone. */
    fun planner(ctx: Context): String = plain(ctx).getString("planner", "auto")?.takeIf { it in setOf("auto", "cloud", "pc", "phone") } ?: "auto"
    fun setPlanner(ctx: Context, v: String) { plain(ctx).edit().putString("planner", v).apply() }
    fun cloudModel(ctx: Context): String = plain(ctx).getString("cloud_model", "gpt-oss:120b")?.ifBlank { "gpt-oss:120b" } ?: "gpt-oss:120b"
    fun setCloudModel(ctx: Context, v: String) { plain(ctx).edit().putString("cloud_model", v.take(80)).apply() }
    /** "gpu" (the default: about four times quicker on this phone; its very first start compiles programs and can take a few minutes, then it is kept) or "cpu". */
    fun localBackend(ctx: Context): String = plain(ctx).getString("local_backend", "gpu") ?: "gpu"
    fun setLocalBackend(ctx: Context, v: String) { plain(ctx).edit().putString("local_backend", if (v == "gpu") "gpu" else if (Regex("cpu[0-9]").matches(v)) v else "cpu").apply() }
    fun localModel(ctx: Context): String = plain(ctx).getString("local_model", "") ?: ""
    fun setLocalModel(ctx: Context, v: String) { plain(ctx).edit().putString("local_model", v).apply() }
    /** The buddy's instructions as the screens built them (with {{NOW}} for the time), so a voice command heard with the app closed knows the same things. */
    fun systemTemplate(ctx: Context): String = plain(ctx).getString("system_template", "") ?: ""
    /** The same instructions without the owner's tasks, events and notes, for the cloud brain unless they chose to share them. */
    fun systemTemplateCloud(ctx: Context): String = plain(ctx).getString("system_template_cloud", "")?.ifBlank { null } ?: systemTemplate(ctx)
    fun setSystemTemplateCloud(ctx: Context, t: String) { plain(ctx).edit().putString("system_template_cloud", t.take(20000)).apply() }
    fun setSystemTemplate(ctx: Context, v: String) { plain(ctx).edit().putString("system_template", v.take(12000)).apply() }

    /** The last few things said, as (what the owner said, what the buddy said). */
    fun recent(ctx: Context): List<Pair<String, String>> {
        val arr = runCatching { org.json.JSONArray(plain(ctx).getString("recent", "[]")) }.getOrDefault(org.json.JSONArray())
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let { o -> o.optString("you") to o.optString("me") } }
    }
    fun setRecent(ctx: Context, json: String) { plain(ctx).edit().putString("recent", json.take(8000)).apply() }
    fun addRecent(ctx: Context, you: String, me: String) {
        val arr = runCatching { org.json.JSONArray(plain(ctx).getString("recent", "[]")) }.getOrDefault(org.json.JSONArray())
        arr.put(org.json.JSONObject().put("you", you.take(300)).put("me", me.take(300)))
        val keep = org.json.JSONArray(); for (i in maxOf(0, arr.length() - 6) until arr.length()) keep.put(arr.get(i))
        plain(ctx).edit().putString("recent", keep.toString()).apply()
    }

    // ---- the edge handle and the smart cards ----
    /** Screen control was on before: after an update Android turns it off, and the owner is told. */
    fun controlWasOn(ctx: Context): Boolean = plain(ctx).getBoolean("control_was_on", false)
    fun setControlWasOn(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("control_was_on", on).apply() }
    fun handle(ctx: Context): Boolean = plain(ctx).getBoolean("handle", false)
    fun setHandle(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("handle", on).apply() }
    fun handleSide(ctx: Context): String = plain(ctx).getString("handle_side", "right")?.takeIf { it == "left" || it == "right" } ?: "right"
    fun setHandleSide(ctx: Context, v: String) { plain(ctx).edit().putString("handle_side", if (v == "left") "left" else "right").apply() }
    fun handleY(ctx: Context): Float = plain(ctx).getFloat("handle_y", 0.55f)
    fun setHandleY(ctx: Context, v: Float) { plain(ctx).edit().putFloat("handle_y", v.coerceIn(0f, 1f)).apply() }
    /** The commands used most, handed over by the screens: what the handle's menu offers. */
    fun frequent(ctx: Context): List<String> = runCatching { val a = org.json.JSONArray(plain(ctx).getString("frequent", "[]")); (0 until a.length()).map { a.getString(it) } }.getOrDefault(emptyList())
    fun setFrequent(ctx: Context, json: String) { plain(ctx).edit().putString("frequent", json.take(2000)).apply() }
    /** Events and tasks with a time, for the next day and a half, as [{kind,title,place,at}] (at in milliseconds). */
    fun agenda(ctx: Context): String = plain(ctx).getString("agenda", "[]") ?: "[]"
    fun setAgenda(ctx: Context, json: String) { plain(ctx).edit().putString("agenda", json.take(20000)).apply() }

    /** The short copy the home-screen widgets draw from (tasks, today, level, streak). See Widgets. */
    fun widgetData(ctx: Context): String = plain(ctx).getString("widget_data", "{}") ?: "{}"
    fun setWidgetData(ctx: Context, json: String) { plain(ctx).edit().putString("widget_data", json.take(30000)).apply() }

    /** A focus sprint started from a widget: when it ends (0 when none) and how many minutes it was. */
    fun focusEnd(ctx: Context): Long = plain(ctx).getLong("focus_end", 0L)
    fun focusMinutes(ctx: Context): Int = plain(ctx).getInt("focus_min", 0)
    fun setFocusEnd(ctx: Context, at: Long, minutes: Int) { plain(ctx).edit().putLong("focus_end", at).putInt("focus_min", minutes).apply() }
    fun msgCards(ctx: Context): Boolean = plain(ctx).getBoolean("msg_cards", false)
    fun setMsgCards(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("msg_cards", on).apply() }
    fun carName(ctx: Context): String = plain(ctx).getString("car_name", "") ?: ""
    fun setCarName(ctx: Context, v: String) { plain(ctx).edit().putString("car_name", v.trim().take(40)).apply() }
    fun smartEvents(ctx: Context): Boolean = plain(ctx).getBoolean("smart_events", true)
    fun smartTasks(ctx: Context): Boolean = plain(ctx).getBoolean("smart_tasks", true)
    fun smartBrief(ctx: Context): Boolean = plain(ctx).getBoolean("smart_brief", false)
    fun briefHour(ctx: Context): Int = plain(ctx).getInt("brief_hour", 8)
    fun setSmart(ctx: Context, key: String, on: Boolean) { if (key in setOf("smart_events", "smart_tasks", "smart_brief")) plain(ctx).edit().putBoolean(key, on).apply() }
    fun setBriefHour(ctx: Context, h: Int) { plain(ctx).edit().putInt("brief_hour", h.coerceIn(5, 11)).apply() }

    /** Whether both volume keys pressed together call the buddy (works with the app closed; nothing listens until then). Off until the owner turns it on. */
    /** The last crash of the app (when, and the start of the trace), kept on the phone so the owner can read it. Never sent anywhere. */
    fun lastCrash(ctx: Context): String = plain(ctx).getString("last_crash", "") ?: ""
    fun setLastCrash(ctx: Context, text: String) { plain(ctx).edit().putString("last_crash", text.take(2500)).commit() }

    /** Which speech engine hears the owner: "auto" (the phone's own when it has one), "device" (only that) or "online" (the standard Google one). */
    fun voiceEngine(ctx: Context): String = plain(ctx).getString("voice_engine", "auto")?.takeIf { it in setOf("auto", "device", "online", "boosted") } ?: "auto"
    fun setVoiceEngine(ctx: Context, v: String) { plain(ctx).edit().putString("voice_engine", v).apply() }

    /** A microphone the standard recogniser could not hear (it said "no speech" more than once): it is then listened to with the voice
     *  made louder, by itself, until that stops helping. [quietMic] is the state; [quietMisses] counts the louder tries that still failed. */
    fun quietMic(ctx: Context): Boolean = plain(ctx).getBoolean("quiet_mic", false)
    fun quietMisses(ctx: Context): Int = plain(ctx).getInt("quiet_misses", 0)
    fun setQuietMic(ctx: Context, on: Boolean, misses: Int = 0) { plain(ctx).edit().putBoolean("quiet_mic", on).putInt("quiet_misses", misses).apply() }

    /** Privacy mode: on, and until when (0 = until switched off). See Privacy. */
    fun privacy(ctx: Context): Boolean = plain(ctx).getBoolean("privacy_on", false)
    fun privacyUntil(ctx: Context): Long = plain(ctx).getLong("privacy_until", 0L)
    fun setPrivacy(ctx: Context, on: Boolean, until: Long) { plain(ctx).edit().putBoolean("privacy_on", on).putLong("privacy_until", if (on) until else 0L).commit() }

    /** Whether the quick panel (the edge buddy's menu) starts listening as soon as it opens. On unless the owner turned it off. */
    fun panelListen(ctx: Context): Boolean = plain(ctx).getBoolean("panel_listen", true)
    fun setPanelListen(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("panel_listen", on).apply() }
    fun chord(ctx: Context): Boolean = plain(ctx).getBoolean("chord", false)
    fun setChord(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("chord", on).apply() }

    /** Whether the owner wants "hey buddy" listening. */
    fun wake(ctx: Context): Boolean = plain(ctx).getBoolean("wake", false)
    fun setWake(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("wake", on).apply() }

    /** How the buddy shows itself while it works over other apps: "bar" (a bar at the bottom) or "island" (it grows out of the camera). */
    fun overlayStyle(ctx: Context): String = plain(ctx).getString("overlay_style", "bar")?.takeIf { it in setOf("bar", "island") } ?: "bar"
    fun setOverlayStyle(ctx: Context, v: String) { if (v in setOf("bar", "island")) plain(ctx).edit().putString("overlay_style", v).apply() }
    /** Whether the card shows the steps in between (their numbers and what each one does); off: only the task, questions and the result. */
    fun showSteps(ctx: Context): Boolean = plain(ctx).getBoolean("show_steps", false)
    fun setShowSteps(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("show_steps", on).apply() }
    /** Whether the light and the card's outline turn through the buddy's colour and its neighbours, or stay the buddy's colour alone. */
    fun colorfulLight(ctx: Context): Boolean = plain(ctx).getBoolean("colorful_light", true)
    fun setColorfulLight(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("colorful_light", on).apply() }
    fun voice(ctx: Context): Boolean = plain(ctx).getBoolean("voice", true)
    fun setVoice(ctx: Context, on: Boolean) { plain(ctx).edit().putBoolean("voice", on).apply() }
}
