package app.mbuddy.mobile

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

/**
 * The app's one screen: a web view. It shows the phone tools page that is bundled in the app (so it works with no PC and no
 * internet), and "Open the whole MBUDDY app" shows the PC's own app (over the private tunnel) with the pairing token already in place.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    @Volatile var currentUrl: String = ""
    private var calendarCallback: ((Boolean) -> Unit)? = null

    private val askCalendarLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        calendarCallback?.invoke(result.values.all { it })
        calendarCallback = null
    }
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    /** Restore from a file: the system's own picker, then the text of the file (at most 6 MB) is handed to the page, which checks it. */
    private val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val text = if (uri == null) null else runCatching {
            contentResolver.openInputStream(uri)?.use { s -> String(s.readBytes().let { if (it.size > 6_000_000) it.copyOf(6_000_000) else it }, Charsets.UTF_8) }
        }.getOrNull()
        if (::web.isInitialized) web.evaluateJavascript("window.__nativePicked && window.__nativePicked(" + (if (text == null) "null" else org.json.JSONObject.quote(text)) + ")", null)
    }
    fun pickFileNow() { pickFile.launch(arrayOf("application/json", "text/plain", "application/octet-stream")) }
    private val askMicrophone = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        // the tap on the microphone that asked for it carries on by itself, so it does not have to be tapped twice
        if (granted && ::web.isInitialized) web.evaluateJavascript("window.dispatchEvent(new Event('mbuddy-mic-granted'))", null)
    }

    /** So the buddy can hear your yes or no. Refusing only means the buttons on its card are used instead. */
    private val askContacts = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    fun askContactsNow() { askContacts.launch(Manifest.permission.READ_CONTACTS) }

    fun askMic() { askMicrophone.launch(Manifest.permission.RECORD_AUDIO) }
    fun askNotificationsNow() { if (Build.VERSION.SDK_INT >= 33) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS) }

    fun askCalendar(then: (Boolean) -> Unit) {
        calendarCallback = then
        askCalendarLauncher.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR))
    }

    fun openFullApp() {
        val base = Store.pcBase(this)
        if (base.isEmpty()) Toast.makeText(this, "Pair this phone with your PC first.", Toast.LENGTH_LONG).show() else web.loadUrl("$base/")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onResume() {
        super.onResume()
        window.decorView.postDelayed({ LocalModel.preload(applicationContext) }, 3500)      // after the screen has settled
        Thread({ Brains.probePc(applicationContext) }, "mbuddy-probe-pc").start()       // so the first request does not wait on a PC that is off
    }

    /** Brought to the front again (a widget, a shortcut): the page picks up what was queued for it. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (::web.isInitialized) web.evaluateJavascript("window.dispatchEvent(new Event('mbuddy-inbox'))", null)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) WebView.setWebContentsDebuggingEnabled(true)

        with(web.settings) {
            textZoom = (resources.configuration.fontScale * 100).toInt().coerceIn(100, 130)       // the phone's font size, so text is as large as the owner chose
            javaScriptEnabled = true
            domStorageEnabled = true            // the page keeps its tasks on the phone
            allowFileAccess = false
            allowContentAccess = false
            mediaPlaybackRequiresUserGesture = true
        }

        // The bundled page gets a real https address (appassets.androidplatform.net) instead of file://, so the service worker,
        // local storage and the page's own scripts behave exactly as they do from the PC.
        val loader = WebViewAssetLoader.Builder().addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? = loader.shouldInterceptRequest(request.url)

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) { currentUrl = url }
            override fun onPageFinished(view: WebView, url: String) { currentUrl = url }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.host == "appassets.androidplatform.net") {
                    if (uri.path == "/web/" || uri.path == "/web") { openFullApp(); return true }    // the page's "Open the whole MBUDDY app" link
                    return false
                }
                val base = Store.pcBase(this@MainActivity)
                if (base.isNotEmpty() && uri.toString().startsWith(base)) return false              // the PC's own pages stay in the app
                return openOutside(uri.toString())
            }
        }
        web.addJavascriptInterface(Bridge(this, web), "Native")

        // The PC's pages read the pairing token from local storage: put it there before they run, only for the PC's own address.
        val base = Store.pcBase(this)
        if (base.isNotEmpty() && WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            val origin = Uri.parse(base).let { "${it.scheme}://${it.authority}" }
            WebViewCompat.addDocumentStartJavaScript(web, "try{localStorage.setItem('mbuddy_phone_token'," + org.json.JSONObject.quote(Store.token(this)) + ")}catch(e){}", setOf(origin))
        }

        onBackPressedDispatcher.addCallback(this) { if (web.canGoBack()) web.goBack() else finish() }
        web.loadUrl(HOME)
        if (Build.VERSION.SDK_INT >= 33) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        ActionsWorker.runNow(this)           // anything the PC put on the phone while the app was closed
        // "Hey buddy" the owner switched on keeps listening; after a restart of the phone it starts again the first time the app is opened.
        if (Store.wake(this) && !WakeService.running && WakeWord.installed(this) && Voice.canHear(this)) runCatching { WakeService.start(this) }
    }

    /** Links the page opens that are not the page: Android intents (the alarm clock, the dialer), maps, the calendar, the Play Store. */
    private fun openOutside(url: String): Boolean {
        return try {
            val intent = if (url.startsWith("intent:")) Intent.parseUri(url, Intent.URI_INTENT_SCHEME).apply { component = null; selector = null }
            else Intent(Intent.ACTION_VIEW, Uri.parse(url))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(intent)
            true
        } catch (e: Exception) {
            android.util.Log.w("MBuddy", "could not open " + url.take(200), e)
            // An intent link names a page to open when no app can do it (the Play Store page for AI Edge Gallery).
            val fallback = runCatching { Intent.parseUri(url, Intent.URI_INTENT_SCHEME).getStringExtra("browser_fallback_url") }.getOrNull()
            if (fallback != null) runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(fallback)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            else Toast.makeText(this, "No app on this phone can do that.", Toast.LENGTH_LONG).show()
            true
        }
    }

    companion object {
        const val ASSET_ORIGIN = "https://appassets.androidplatform.net/"
        const val HOME = "https://appassets.androidplatform.net/web/phoneapp.html"
    }
}
