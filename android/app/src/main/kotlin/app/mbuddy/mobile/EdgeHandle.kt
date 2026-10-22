package app.mbuddy.mobile

import android.graphics.BitmapFactory
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.ImageView

/**
 * The edge handle: the buddy's character docked at one edge of the screen, over every app, so it can be reached without opening MBUDDY
 * and without any microphone. Tap it for what you use most; drag it up or down, or to the other edge, and it stays where it is dropped.
 * It is an accessibility overlay (the same permission as screen control), so it needs nothing more, and it is off until the owner turns it on.
 */
class EdgeHandle(private val svc: ControlService) {
    private val wm = svc.getSystemService(WindowManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val d = svc.resources.displayMetrics.density
    private var view: ImageView? = null
    private val w = (64 * d).toInt()
    private val h = (56 * d).toInt()
    private val tuck = (14 * d).toInt()            // how much of it sits past the edge, so it reads as docked

    /** Show it, or take it away, to match the owner's setting. Safe to call at any time and from any thread. */
    fun refresh() = main.post { if (Store.handle(svc) && !Privacy.on(svc)) show() else hide() }

    fun hide() { main.post { view?.let { runCatching { wm.removeView(it) } }; view = null } }

    private fun screenW() = svc.resources.displayMetrics.widthPixels
    private fun screenH() = svc.resources.displayMetrics.heightPixels
    private fun xFor(side: String) = if (side == "left") -tuck else screenW() - w + tuck

    private fun show() {
        view?.let { runCatching { wm.removeView(it) } }; view = null
        val bmp = runCatching { svc.assets.open("web/assets/buddy/chars/${Store.character(svc)}/still.webp").use { BitmapFactory.decodeStream(it) } }.getOrNull() ?: return
        val iv = ImageView(svc).apply { setImageBitmap(bmp); scaleType = ImageView.ScaleType.FIT_CENTER; alpha = 0.92f; contentDescription = "MBUDDY" }
        val lp = WindowManager.LayoutParams(w, h, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT).apply {
            gravity = Gravity.TOP or Gravity.START
            x = xFor(Store.handleSide(svc)); y = (Store.handleY(svc) * (screenH() - h)).toInt().coerceIn(0, screenH() - h)
        }
        val slop = ViewConfiguration.get(svc).scaledTouchSlop
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var dragging = false
        iv.setOnTouchListener { v: View, e: MotionEvent ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; dragging = false; v.alpha = 1f; true }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (!dragging && (kotlin.math.abs(dx) > slop || kotlin.math.abs(dy) > slop)) dragging = true
                    if (dragging) { lp.x = (startX + dx).toInt(); lp.y = (startY + dy).toInt().coerceIn(0, screenH() - h); runCatching { wm.updateViewLayout(v, lp) } }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    v.alpha = 0.92f
                    if (dragging) {
                        // dock at whichever edge it was dropped nearer to, and remember where
                        val side = if (lp.x + w / 2 < screenW() / 2) "left" else "right"
                        lp.x = xFor(side); runCatching { wm.updateViewLayout(v, lp) }
                        Store.setHandleSide(svc, side); Store.setHandleY(svc, lp.y.toFloat() / (screenH() - h).coerceAtLeast(1))
                    } else svc.overlay.menu()
                    true
                }
                else -> false
            }
        }
        // in landscape it steps out of the way (full-screen video is not detected: its own card also hides the status bar)
        iv.visibility = if (svc.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE) View.INVISIBLE else View.VISIBLE
        runCatching { wm.addView(iv, lp); view = iv }.onFailure { android.util.Log.w("MBuddyHandle", "the handle could not be shown", it) }
    }
}
