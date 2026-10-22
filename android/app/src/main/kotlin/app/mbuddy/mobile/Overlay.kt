package app.mbuddy.mobile

import android.annotation.SuppressLint
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import org.json.JSONArray
import org.json.JSONObject

/**
 * The buddy at work, over whatever app it is operating: a light round the edge of the screen (EdgeGlow) and a card with the chosen
 * character, what it heard or is doing, and Send / No / Stop. The card is the same bundled page for every app (shell/ui/agent.html), in
 * the owner's style: a bar at the bottom, or an island grown out of the camera cutout. Both are accessibility overlays, so they need no
 * extra permission, and touches outside the card go to the app underneath.
 *
 * The page is loaded once and kept: when the card goes, its window is taken away but the page stays ready, so the next entrance starts
 * on its first frame instead of while a web view is still being built (which made the movement skip).
 */
class Overlay(private val svc: ControlService) {
    companion object {
        /** Each character's colour, the same as the card's (agent.html). */
        private val COLORS = mapOf("blink" to 0xFFFFCC33.toInt(), "void" to 0xFFA78BFA.toInt(), "glitch" to 0xFF22D3EE.toInt(),
            "cinder" to 0xFFFF7A2F.toInt(), "oracle" to 0xFF2DD4BF.toInt())
        /** How long the card's way out takes (agent.html, agentLeave), before its window is taken away. */
        private const val LEAVE_ISLAND_MS = 1000L
        private const val LEAVE_BAR_MS = 780L
    }

    private val main = Handler(Looper.getMainLooper())
    private var web: WebView? = null
    private var attached = false
    private var loaded = false
    private var queued = ArrayList<String>()
    private var winStyle = ""
    private var removal: Runnable? = null
    private val wm = svc.getSystemService(WindowManager::class.java)
    private val glow = EdgeGlow(svc, wm)

    private inner class Ui {
        @JavascriptInterface fun answer(what: String) {
            // the handle's menu and the smart cards are not an agent's question: their buttons are handled here
            if (!Agent.running) {
                if (what == "stop" || what == "open" || what == "close" || what == "talk" || what.startsWith("do:") || what.startsWith("reply:")) Command.supersede()
                when {
                    what == "stop" -> { hide(); return }
                    what == "open" -> {
                        hide()
                        svc.packageManager.getLaunchIntentForPackage(svc.packageName)?.let { runCatching { svc.startActivity(it.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) } }
                        return
                    }
                    what.startsWith("do:") -> { val t = what.substring(3); hide(); Thread { runCatching { Command.handle(svc.applicationContext, t) } }.start(); return }
                    what.startsWith("reply:") -> {
                        val (app, who) = what.substring(6).split("|", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
                        hide()
                        Thread {
                            runCatching {
                                val said = Command.hear(svc.applicationContext, "Say your reply to $who.")
                                if (said.isBlank()) { svc.overlay.hide(); return@runCatching }
                                Messaging.send(svc.applicationContext, app, who, said)?.let { why -> svc.overlay.show("fail", why); Thread.sleep(2500); svc.overlay.hide() }
                            }
                        }.start()
                        return
                    }
                    what == "talk" -> { hide(); Thread { runCatching { Command.run(svc.applicationContext) } }.start(); return }
                    what == "close" -> { hide(); return }
                }
            }
            Agent.answer(what)
        }
        @JavascriptInterface fun resize(cssPx: Int) {
            main.post {
                val w = web ?: return@post
                if (!attached) return@post
                val lp = w.layoutParams as? WindowManager.LayoutParams ?: return@post
                val h = (cssPx * svc.resources.displayMetrics.density).toInt().coerceAtLeast(1)
                if (lp.height != h) { lp.height = h; runCatching { wm.updateViewLayout(w, lp) } }
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun page(): WebView {
        web?.let { return it }
        val w = WebView(svc)
        w.setBackgroundColor(0)
        with(w.settings) { javaScriptEnabled = true; domStorageEnabled = true; allowFileAccess = false; allowContentAccess = false }
        val loader = WebViewAssetLoader.Builder().addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(svc)).build()
        w.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? = loader.shouldInterceptRequest(request.url)
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true       // it never goes anywhere else
            override fun onPageFinished(view: WebView, url: String) {
                loaded = true
                queued.forEach { view.evaluateJavascript(it, null) }; queued.clear()
            }
        }
        w.addJavascriptInterface(Ui(), "AgentUi")
        w.loadUrl(MainActivity.ASSET_ORIGIN + "web/agent.html")
        web = w
        return w
    }

    private fun run(js: String) { if (loaded) web?.evaluateJavascript(js, null) else queued.add(js) }

    private fun params(style: String, density: Float, input: Boolean) = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, ((if (style == "island") 240 else 150) * density).toInt(),
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
            (if (style == "island") WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN else 0) or
            (if (input) 0 else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE),        // the keyboard only works in a window that can take focus
        PixelFormat.TRANSLUCENT,
    ).apply {
        if (style == "island") {
            // over the status bar, from the very top of the screen, so it can grow out of the camera
            gravity = Gravity.TOP or Gravity.START; x = 0; y = 0
            if (android.os.Build.VERSION.SDK_INT >= 30) layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            else if (android.os.Build.VERSION.SDK_INT >= 28) layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        } else { gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL; y = navBarHeight() + (4 * density).toInt() }
    }

    /** Show the card (or update it). [mode]: working, asking, done, fail. [buttons] are (id, label). */
    fun show(mode: String, text: String, step: String = "", buttons: List<Pair<String, String>> = emptyList(), input: Boolean = false, tiles: List<Triple<String, String, String>> = emptyList()) {
        if (Privacy.on(svc)) return          // privacy mode: nothing of the buddy is drawn over the owner's screen
        val style = Store.overlayStyle(svc)
        val density = svc.resources.displayMetrics.density
        val geo = if (style == "island") Cutout.read(wm, density) else null
        val state = JSONObject().put("mode", mode).put("text", text).put("step", step).put("character", Store.character(svc)).put("input", input)
            .put("buttons", JSONArray().also { a -> buttons.forEach { (id, label) -> a.put(JSONObject().put("id", id).put("label", label)) } })
            .put("layout", style).put("showSteps", Store.showSteps(svc)).put("colorful", Store.colorfulLight(svc))
            .put("goal", if (Agent.running) Agent.goal.take(160) else "")
            .put("tiles", JSONArray().also { a -> tiles.forEach { (id, label, icon) -> a.put(JSONObject().put("id", id).put("label", label).put("icon", icon)) } })
        if (geo != null) {
            // where the island grows from, in the page's pixels: the camera cutout, or a small point at the top centre when there is none
            val r = geo.rect
            val cx = (r?.exactCenterX() ?: (geo.width / 2f)) / density
            val top = (r?.top?.toFloat() ?: (geo.statusBar * 0.2f)) / density
            val h = (r?.height()?.toFloat() ?: (geo.statusBar * 0.6f)) / density
            val w = (r?.width()?.toFloat() ?: (geo.statusBar * 0.6f)) / density
            state.put("island", JSONObject().put("cx", cx).put("top", top).put("w", w).put("h", h).put("sw", geo.width / density).put("notch", r != null))
        }
        val js = "window.agentUi && window.agentUi(" + state.toString() + ")"
        val glowMode = if (mode == "asking" && buttons.isEmpty() && !input && text.contains("listening", true)) "listening" else mode
        main.post {
            removal?.let { main.removeCallbacks(it) }; removal = null              // shown again while on its way out: it stays
            val w = page()
            if (attached && winStyle != style) { runCatching { wm.removeView(w) }; attached = false }
            val glowAdded = glow.show(glowMode, COLORS[Store.character(svc)] ?: COLORS.getValue("blink"), style == "island", geo, Store.colorfulLight(svc))
            // a window added later is drawn above the ones before it: if the light was (re)created while the card was already up, the
            // light would run over the island and its buttons. The card is put back on top.
            if (glowAdded && attached) { runCatching { wm.removeViewImmediate(w); attached = false } }
            if (!attached) {
                try { wm.addView(w, params(style, density, input)); attached = true; winStyle = style; android.util.Log.i("MBuddyAgent", "card shown ($style)") }
                catch (e: Exception) { android.util.Log.w("MBuddyAgent", "card could not be shown", e) }
            } else (w.layoutParams as? WindowManager.LayoutParams)?.let { lp ->
                val focus = if (input) 0 else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                val flags = (lp.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()) or focus
                if (lp.flags != flags) { lp.flags = flags; runCatching { wm.updateViewLayout(w, lp) } }
            }
            run(js)
        }
    }

    /** The edge handle was tapped: what you use most, one to talk, one to close. */
    fun menu() {
        // Talk first and largest; then the things you use most; then the usual helpers. Every tile is one tap.
        val frequent = Store.frequent(svc).take(3).map { Triple("do:$it", if (it.length > 16) it.take(15) + "…" else it, "chat") }
        val tiles = (listOf(Triple("talk", "Talk", "mic")) + frequent +
            listOf(Triple("do:start a 10 minute timer", "10 min timer", "timer"), Triple("do:what's on today", "Today", "cal"), Triple("open", "Open MBUDDY", "app"))).take(6)
        if (Store.panelListen(svc) && !Agent.running) {
            // the owner's choice (Settings): the panel opens already listening, with the tiles there to tap instead; if nothing is said
            // the panel stays, with Talk, so it is never a dead end
            val ctx = svc.applicationContext
            Thread { runCatching { Command.run(ctx, tiles = tiles.filter { it.first != "talk" }, fallback = tiles) } }.start()
        } else show("asking", "What now?", "Tap one, or tap Talk", emptyList(), tiles = tiles)
    }

    /** The height of Android's navigation bar (buttons or the gesture line), so the bar sits just above it. */
    @SuppressLint("DiscouragedApi", "InternalInsetResource")
    private fun navBarHeight(): Int {
        val id = svc.resources.getIdentifier("navigation_bar_height", "dimen", "android")
        return if (id > 0) svc.resources.getDimensionPixelSize(id) else 0
    }

    /** The card leaves the way it came (back into the camera, or down), then its window is taken away; the page stays loaded. */
    fun hide() {
        main.post {
            glow.hide()
            val w = web ?: return@post
            if (!attached || removal != null) return@post
            run("window.agentLeave && window.agentLeave()")
            val r = Runnable { removal = null; runCatching { wm.removeView(w) }; attached = false }
            removal = r
            main.postDelayed(r, if (winStyle == "island") LEAVE_ISLAND_MS else LEAVE_BAR_MS)
        }
    }

    /** A short run through the states, so the owner can see a style before choosing it (Settings). */
    fun preview() {
        Thread {
            show("asking", "I'm listening.", "Say what you need"); Thread.sleep(3200)
            show("working", "“Open YouTube and search for lofi beats”", "I heard"); Thread.sleep(2200)
            show("working", "Opening YouTube on the search for lofi beats", "step 1"); Thread.sleep(2200)
            show("asking", "To Sara. I wrote: On my way, see you in ten. Shall I send it?", "This is only a preview", listOf("yes" to "Yes, send", "no" to "No")); Thread.sleep(3400)
            show("done", "Preview done. Nothing was sent."); Thread.sleep(2200)
            hide()
        }.start()
    }
}
