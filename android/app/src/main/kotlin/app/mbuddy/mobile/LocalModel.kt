package app.mbuddy.mobile

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ResponseFormat
import com.google.ai.edge.litertlm.ThinkingConfig
import com.google.ai.edge.litertlm.OpenApiTool
import com.google.ai.edge.litertlm.tool
import com.google.ai.edge.litertlm.SamplerConfig
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * The model on the phone itself, so the buddy can think with no internet and no PC. It runs Google's on-device runtime (LiteRT-LM)
 * on a model file the owner chooses to download here (from Hugging Face, openly available, checked against its published checksum).
 * Nothing is sent anywhere when it thinks. It is kept in memory while it is being used and let go a few minutes later.
 */
object LocalModel {
    class Spec(val id: String, val label: String, val file: String, val url: String, val bytes: Long, val sha256: String, val note: String, val tool: Boolean = false)

    private const val HF = "https://huggingface.co/litert-community/"
    val SPECS = listOf(
        Spec("gemma-4-e2b", "Gemma 4 E2B", "gemma-4-E2B-it.litertlm", HF + "gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm",
            2_588_147_712L, "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c", "Smaller and quicker. Good for chat and simple commands."),
        Spec("gemma-4-e4b", "Gemma 4 E4B", "gemma-4-E4B-it.litertlm", HF + "gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm",
            3_659_530_240L, "0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0", "Bigger and smarter. Needs about 6 GB of free memory."),
        Spec("qwen3-4b", "Qwen3 4B Instruct", "qwen3_4b_instruct_2507_mixed_int4.litertlm", HF + "Qwen3-4B-Instruct-2507/resolve/main/qwen3_4b_instruct_2507_mixed_int4.litertlm",
            2_659_057_664L, "9e48b165836256f5344d9d044930607b9c47f6ef34e27f82e96881664f3ba2fd", "A different 4B model, good at following instructions."),
        // Google AI Edge Gallery's "Mobile Actions" model (FunctionGemma 270M fine-tuned): it turns a sentence into a device function call
        // in well under a second. It is not a chat model, so it is never picked to think with (see active()).
        Spec("mobile-actions", "Mobile Actions (FunctionGemma 270M)", "mobile-actions_q8_ekv1024.litertlm",
            "https://huggingface.co/litert-community/functiongemma-mobile-actions_q8_ekv1024.litertlm/resolve/main/mobile-actions_q8_ekv1024.litertlm",
            284_426_240L, "92109695f911d1872fa8ae07c1e3ff0ed70f2c3d1690d410ec6db8587c2ab409",
            "Tiny and fast: turns a command into the flashlight, a contact, an email, a map, Wi-Fi settings or a calendar event without waiting for the big model.", tool = true),
    )

    private val main = Handler(Looper.getMainLooper())
    private fun dir(ctx: Context) = File(ctx.filesDir, "models").also { it.mkdirs() }
    private fun file(ctx: Context, s: Spec) = File(dir(ctx), s.file)
    private fun part(ctx: Context, s: Spec) = File(dir(ctx), s.file + ".part")
    fun spec(id: String) = SPECS.firstOrNull { it.id == id }
    fun installed(ctx: Context, s: Spec) = file(ctx, s).let { it.isFile && it.length() == s.bytes }

    /** The model chosen to think with: the one picked in settings if it is installed, else any installed one. */
    fun active(ctx: Context): Spec? = spec(Store.localModel(ctx))?.takeIf { !it.tool && installed(ctx, it) } ?: SPECS.firstOrNull { !it.tool && installed(ctx, it) }

    // ---- download ----
    private class Download(val spec: Spec) {
        @Volatile var done = 0L
        @Volatile var phase = "downloading"          // downloading, checking
        @Volatile var error: String? = null
        @Volatile var cancelled = false
    }
    @Volatile private var dl: Download? = null

    /** Start (or resume) a download. Returns why not, or null when it started. */
    fun download(ctx: Context, id: String): String? {
        val s = spec(id) ?: return "No such model."
        if (installed(ctx, s)) return null
        if (dl?.error == null && dl != null && dl!!.phase != "failed") return "A download is already running."
        val free = dir(ctx).usableSpace
        val need = s.bytes - part(ctx, s).length()
        if (free < need + 200_000_000L) return "Not enough free storage: this needs about ${(need + 200_000_000L) / 1_000_000_000L + 1} GB free."
        val d = Download(s); dl = d
        val app = ctx.applicationContext
        Thread({ run(app, d) }, "mbuddy-model-download").start()
        return null
    }

    fun cancel() { dl?.cancelled = true }

    private fun run(ctx: Context, d: Download) {
        val s = d.spec; val target = file(ctx, s); val p = part(ctx, s)
        try {
            var have = p.length()
            val conn = URL(s.url).openConnection() as HttpURLConnection
            conn.connectTimeout = 20_000; conn.readTimeout = 30_000
            if (have > 0) conn.setRequestProperty("Range", "bytes=$have-")
            val code = conn.responseCode
            if (code == 200 && have > 0) { p.delete(); have = 0 }            // the server ignored the range: start again
            if (code != 200 && code != 206) { d.error = "The download server answered $code."; return }
            d.done = have
            FileOutputStream(p, have > 0).use { out ->
                conn.inputStream.use { input ->
                    val buf = ByteArray(256 * 1024)
                    while (true) {
                        if (d.cancelled) { d.error = "Stopped. It will carry on from here next time."; return }
                        val n = input.read(buf); if (n < 0) break
                        out.write(buf, 0, n); d.done += n
                    }
                }
            }
            conn.disconnect()
            if (p.length() != s.bytes) { d.error = "The download stopped early (${p.length()} of ${s.bytes} bytes). Start it again to carry on."; return }
            d.phase = "checking"
            val md = MessageDigest.getInstance("SHA-256")
            p.inputStream().use { i -> val buf = ByteArray(1 shl 20); while (true) { val n = i.read(buf); if (n < 0) break; md.update(buf, 0, n) } }
            val sum = md.digest().joinToString("") { "%02x".format(it) }
            if (sum != s.sha256) { p.delete(); d.error = "The file did not match its published checksum, so I threw it away. Try again."; return }
            if (!p.renameTo(target)) { d.error = "Couldn't save the model."; return }
            if (!s.tool && Store.localModel(ctx).isEmpty()) Store.setLocalModel(ctx, s.id)
            dl = null
        } catch (e: Exception) {
            d.error = "The download was interrupted (${e.javaClass.simpleName}). Start it again to carry on."
        } finally {
            if (dl === d && d.error != null) d.phase = "failed"
        }
    }

    fun delete(ctx: Context, id: String) {
        val s = spec(id) ?: return
        if (engineSpec == s.id) closeEngine()
        if (s.tool) closeActionEngine()
        file(ctx, s).delete(); part(ctx, s).delete()
        if (Store.localModel(ctx) == id) Store.setLocalModel(ctx, "")
        if (dl?.spec?.id == id) dl = null
    }

    fun state(ctx: Context): JSONObject {
        val arr = JSONArray()
        for (s in SPECS) {
            val d = dl?.takeIf { it.spec.id == s.id }
            val have = if (d != null) d.done else part(ctx, s).length()
            arr.put(JSONObject().put("id", s.id).put("label", s.label).put("note", s.note).put("bytes", s.bytes)
                .put("installed", installed(ctx, s)).put("progress", if (installed(ctx, s)) 1.0 else have.toDouble() / s.bytes)
                .put("downloading", d != null && d.error == null).put("phase", d?.phase ?: "").put("error", d?.error ?: "")
                .put("partial", !installed(ctx, s) && have > 0).put("tool", s.tool))
        }
        return JSONObject().put("models", arr).put("active", active(ctx)?.id ?: "").put("freeMb", dir(ctx).usableSpace / 1_000_000L)
            .put("loaded", engine != null).put("lastMs", lastMs)
    }

    // ---- thinking ----
    private var engine: Engine? = null
    private var engineSpec: String? = null
    @Volatile var lastMs = 0L
        private set
    private val unload = Runnable { closeEngine() }
    // Loading the model takes the graphics chip for about 5 s and freezes the screen's animation while it does (measured 2026-10-10:
    // 51 to 56 skipped frames at every load). It is kept loaded for a long while, and loaded ahead, when the app opens, not on the first ask.
    private const val IDLE_MS = 20 * 60_000L

    /** Load the model now if one is installed, off the main thread, so the first request does not pay for it. */
    fun preload(ctx: Context) {
        val s = active(ctx) ?: return
        Thread({ runCatching { synchronized(this) { engineFor(ctx, s) }; main.removeCallbacks(unload); main.postDelayed(unload, IDLE_MS) } }, "mbuddy-preload").start()
    }

    // ---- the small model that turns a command into a function call (Mobile Actions) ----
    private var actionEngine: Engine? = null
    private val actionSpec get() = SPECS.first { it.tool }
    fun actionsInstalled(ctx: Context) = installed(ctx, actionSpec)

    @Synchronized private fun closeActionEngine() { runCatching { actionEngine?.close() }; actionEngine = null }

    private class Fn(val name: String, val description: String, val params: Map<String, String>, val required: List<String>) : OpenApiTool {
        override fun getToolDescriptionJsonString(): String = JSONObject().put("name", name).put("description", description).put("parameters",
            JSONObject().put("type", "object").put("required", JSONArray(required)).put("properties", JSONObject().also { o ->
                for ((k, d) in params) o.put(k, JSONObject().put("type", "string").put("description", d))
            })).toString()
        override fun execute(paramsJson: String): String = "{}"      // never run by the library: the app does the action itself (Actions.kt)
    }
    private val FUNCTIONS by lazy {
        listOf(
            Fn("turnOnFlashlight", "Turns the flashlight on.", emptyMap(), emptyList()),
            Fn("turnOffFlashlight", "Turns the flashlight off.", emptyMap(), emptyList()),
            Fn("createContact", "Creates a contact in the phone's contact list.", mapOf("firstName" to "The first name of the contact.", "lastName" to "The last name of the contact.",
                "phoneNumber" to "The phone number of the contact.", "email" to "The email address of the contact."), listOf("firstName", "lastName")),
            Fn("sendEmail", "Sends an email.", mapOf("to" to "The email address of the recipient.", "subject" to "The subject of the email.", "body" to "The body of the email."), listOf("to", "subject")),
            Fn("showLocationOnMap", "Shows a location on the map.", mapOf("location" to "The location to search for. May be the name of a place, a business, or an address."), listOf("location")),
            Fn("openWifiSettings", "Opens the Wi-Fi settings.", emptyMap(), emptyList()),
            Fn("createCalendarEvent", "Creates a new calendar event.", mapOf("datetime" to "The date and time of the event in the format YYYY-MM-DDTHH:MM:SS.", "title" to "The title of the event."), listOf("datetime", "title")),
        )
    }

    /** One function call for [text] ("name" to arguments), or null when the model did not call one. Throws when the model is missing or cannot start. */
    @Synchronized
    fun functionCall(ctx: Context, text: String, now: java.time.LocalDateTime): Pair<String, Map<String, Any>>? {
        val s = actionSpec
        if (!installed(ctx, s)) throw IllegalStateException("The Mobile Actions model isn't downloaded.")
        val e = actionEngine ?: Engine(EngineConfig(modelPath = file(ctx, s).absolutePath, backend = Backend.CPU(), cacheDir = ctx.cacheDir.path))
            .also { it.initialize(); actionEngine = it }
        val day = now.dayOfWeek.getDisplayName(java.time.format.TextStyle.FULL, java.util.Locale.ENGLISH)
        val system = "You are a model that can do function calling with the following functions\n" +
            "Current date and time given in YYYY-MM-DDTHH:MM:SS format: ${now.withNano(0)}\nDay of week is $day"
        val conv = e.createConversation(ConversationConfig(systemInstruction = Contents.of(system), tools = FUNCTIONS.map { tool(it) },
            samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 0.0), automaticToolCalling = false))
        try {
            val t0 = System.currentTimeMillis()
            val m = conv.sendMessage(text)
            android.util.Log.i("MBuddyModel", "actions model answered in ${System.currentTimeMillis() - t0} ms: ${m.toolCalls.map { it.name }}")
            return m.toolCalls.firstOrNull()?.let { it.name to it.arguments.mapValues { (_, v) -> v ?: "" } }
        } finally { runCatching { conv.close() } }
    }

    /** Drop the loaded model so the next use loads it with the backend now chosen. */
    @Synchronized fun reload() { closeEngine() }

    @Synchronized private fun closeEngine() {
        main.removeCallbacks(unload)
        runCatching { engine?.close() }
        engine = null; engineSpec = null
    }

    @Synchronized private fun engineFor(ctx: Context, s: Spec): Engine {
        engine?.let { if (engineSpec == s.id) return it }
        closeEngine()
        val path = file(ctx, s).absolutePath
        val gpu = Store.localBackend(ctx) == "gpu"
        val threads = Regex("cpu([0-9])").matchEntire(Store.localBackend(ctx))?.groupValues?.get(1)?.toInt()      // "cpu3": the processor with 3 threads
        val t0 = System.currentTimeMillis()
        android.util.Log.i("MBuddyModel", "loading ${s.id} on the ${if (gpu) "graphics chip" else "processor"}")
        val e = try {
            Engine(EngineConfig(modelPath = path, backend = if (gpu) Backend.GPU() else if (threads != null) Backend.CPU(null, threads) else Backend.CPU(), audioBackend = Backend.CPU(), cacheDir = ctx.cacheDir.path)).also { it.initialize() }
        } catch (t: Throwable) {
            android.util.Log.w("MBuddyModel", "load failed after ${System.currentTimeMillis() - t0} ms", t)
            if (!gpu) throw t
            // the graphics chip could not run it: the processor does
            Engine(EngineConfig(modelPath = path, backend = Backend.CPU(), audioBackend = Backend.CPU(), cacheDir = ctx.cacheDir.path)).also { it.initialize() }
        }
        android.util.Log.i("MBuddyModel", "loaded in ${System.currentTimeMillis() - t0} ms")
        engine = e; engineSpec = s.id
        return e
    }

    /**
     * Listen: what was said in a recording (WAV, 16 kHz mono), as text. The model is told what kind of words to expect (the owner's apps and
     * the usual commands), which is what makes it far better than a recognizer with no context. Call off the main thread; "" when it heard nothing.
     */
    @Synchronized
    fun transcribe(ctx: Context, wav: ByteArray, expect: List<String>): String {
        val s = active(ctx) ?: throw IllegalStateException("No model is installed on this phone yet.")
        val started = System.currentTimeMillis()
        val e = try { engineFor(ctx, s) } catch (t: Throwable) { throw IllegalStateException("The model couldn't start (${t.javaClass.simpleName}: ${t.message ?: "no detail"}).") }
        val system = "You are a speech-to-text engine for a phone assistant. Write down exactly the words the speaker said, in English, and nothing else: " +
            "no quotes, no comments. It is a short request or command. Names you may hear: ${expect.take(120).joinToString(", ")}."
        val conv = e.createConversation(ConversationConfig(systemInstruction = Contents.of(system), samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 0.0)))
        try {
            val text = conv.sendMessage(Contents.of(listOf(Content.AudioBytes(wav), Content.Text("Transcribe."))), emptyMap()).toString().trim()
            Dbg.i("MBuddyModel", "heard in ${System.currentTimeMillis() - started} ms: $text")
            // The wake phrase caught at the start of the recording is not part of the request
            val heard = text.trim('"', ' ', '\n').replace(Regex("(?i)^(?:hey|hi|ok|okay)[\\s,]+buddy[\\s,.!]*"), "").trim()
            // With nothing to hear, the model sometimes repeats its instruction; that is not speech.
            return if (heard.matches(Regex("(?i)^(transcribe|transcription)[.!]*$")) || heard.length < 2) "" else heard
        } finally {
            runCatching { conv.close() }
            main.removeCallbacks(unload); main.postDelayed(unload, IDLE_MS)
        }
    }

    /**
     * Think once: a system instruction and what was said. Returns the model's words. Call off the main thread. Throws with a plain reason.
     * With a [schema] (a JSON schema as a map) the runtime forces the answer to have that shape, which a small model needs to be dependable.
     */
    @Synchronized
    fun chat(ctx: Context, system: String, user: String, schema: Map<String, Any>? = null): String {
        val s = active(ctx) ?: throw IllegalStateException("No model is installed on this phone yet.")
        val started = System.currentTimeMillis()
        val e = try { engineFor(ctx, s) } catch (t: Throwable) { throw IllegalStateException("The model couldn't start (${t.javaClass.simpleName}: ${t.message ?: "no detail"}).") }
        android.util.Log.i("MBuddyModel", "thinking")
        val conv = e.createConversation(ConversationConfig(systemInstruction = Contents.of(system), samplerConfig = SamplerConfig(topK = 20, topP = 0.9, temperature = 0.2),
            enableResponseFormat = schema != null))
        try {
            val text = (if (schema != null) conv.sendMessage(user, responseFormat = ResponseFormat.json(schema)) else conv.sendMessage(user)).toString()
            lastMs = System.currentTimeMillis() - started
            Dbg.i("MBuddyModel", "answered in $lastMs ms")
            return text
        } finally {
            runCatching { conv.close() }
            main.removeCallbacks(unload); main.postDelayed(unload, IDLE_MS)
        }
    }
}
