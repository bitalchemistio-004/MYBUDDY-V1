package app.mbuddy.mobile

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.SweepGradient
import android.os.Build
import android.view.RoundedCorner
import android.view.View
import android.view.WindowManager
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator

/**
 * The buddy's light around the edge of the whole screen while it listens or works, the way Gemini shows it is on.
 *
 * Two styles, the owner's choice (Settings): "bar", a band of the buddy's colour that travels round the border; "island", where the
 * light is born at the camera cutout, rings it, and unravels from there along the top edge and down both sides until the whole border
 * glows, then shimmers. It breathes faster when the buddy needs you, turns green when done and red when it fails, then fades.
 * Drawn natively (a few strokes, no blur) so it stays smooth over any app, and it never takes a touch. The corners follow the screen's
 * real rounded corners and the cutout's real shape when Android reports them; nothing is assumed about notches.
 */
class EdgeGlow(private val ctx: Context, private val wm: WindowManager) {
    private var view: GlowView? = null

    /**
     * [mode]: listening, working, asking, done, fail. [color] is the buddy's colour. [geo] is given for the island style.
     * Returns true when a new window was added: it is then on top of every window added before it (the card included), and the caller
     * puts the card back above it, so the light never draws over the island or the buttons.
     */
    fun show(mode: String, color: Int, island: Boolean, geo: Cutout.Geo?, colorful: Boolean = true): Boolean {
        var created = false
        val v = view ?: GlowView(ctx).also { gv ->
            val lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT,
            )
            if (Build.VERSION.SDK_INT >= 30) lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            else if (Build.VERSION.SDK_INT >= 28) lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            runCatching { wm.addView(gv, lp) }.onFailure { android.util.Log.w("MBuddyAgent", "edge glow could not be shown", it); return false }
            view = gv
            created = true
            gv.begin(island, geo)
        }
        v.colorful = colorful
        v.set(mode, color)
        return created
    }

    fun hide() {
        val v = view ?: return
        view = null
        v.fadeOut { runCatching { wm.removeView(v) } }
    }

    private class GlowView(ctx: Context) : View(ctx) {
        private val d = ctx.resources.displayMetrics.density
        private val rect = RectF()
        private val m = Matrix()
        private val layers = listOf(20f to 0.12f, 12f to 0.24f, 6.5f to 0.50f, 3.2f to 1.0f)
        private val paints = layers.map { (w, _) -> Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = w * d; strokeCap = Paint.Cap.ROUND } }
        private val base = layers.map { (it.second * 255).toInt() }      // each layer's own strength (setting a colour would reset it)
        private val head = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        private var color = Color.parseColor("#ffcc33")
        private var mode = "working"
        private var angle = 0f
        private var level = 0f                  // 0..1, how bright, for fading in
        private var pulse = 1f
        private var shader: SweepGradient? = null
        private var shaderKey = 0
        private var island = false
        var colorful = true
        private var geo: Cutout.Geo? = null
        private var unravel = 1f               // 0..1: how far the light has run from the camera round the border (island style)
        private val seg = Path()
        private val pos = FloatArray(2)
        private val spin = ValueAnimator.ofFloat(0f, 360f).apply {
            duration = 2600; repeatCount = ValueAnimator.INFINITE; interpolator = LinearInterpolator()
            addUpdateListener { angle = it.animatedValue as Float; tick(); invalidate() }
        }
        private var t0 = System.currentTimeMillis()

        fun begin(isIsland: Boolean, g: Cutout.Geo?) {
            island = isIsland; geo = g
            if (island) {
                unravel = 0f
                ValueAnimator.ofFloat(0f, 1f).apply {
                    duration = 1100; startDelay = 150; interpolator = android.view.animation.AccelerateDecelerateInterpolator()
                    addUpdateListener { unravel = it.animatedValue as Float; invalidate() }
                }.start()
            }
        }

        fun set(newMode: String, newColor: Int) {
            mode = newMode
            color = when (newMode) { "done" -> Color.parseColor("#4ade80"); "fail" -> Color.parseColor("#ff5a5f"); else -> newColor }
            spin.duration = when (newMode) { "asking" -> 2200L; "listening" -> 3000L; else -> if (island) 6500L else 3800L }
            t0 = System.currentTimeMillis()
            if (!spin.isStarted) spin.start()
            animate().cancel(); alpha = 1f
            invalidate()
        }

        fun fadeOut(after: () -> Unit) {
            animate().alpha(0f).setDuration(450).withEndAction { spin.cancel(); after() }.start()
        }

        private fun tick() {
            val t = (System.currentTimeMillis() - t0) / 1000f
            level = (level + 0.025f).coerceAtMost(1f)                      // fades in over about 0.7 s
            pulse = when (mode) {
                "asking" -> 0.65f + 0.35f * kotlin.math.abs(kotlin.math.sin(t * 3.2f))
                "listening" -> 0.75f + 0.25f * kotlin.math.abs(kotlin.math.sin(t * 2.0f))
                else -> 1f
            }
        }

        private fun cornerRadius(): Float {
            if (Build.VERSION.SDK_INT >= 31) {
                val r = rootWindowInsets?.getRoundedCorner(RoundedCorner.POSITION_TOP_LEFT)?.radius
                if (r != null && r > 0) return r.toFloat()
            }
            return 0f                            // square corners: the screen did not say it is rounded
        }

        private fun c(a: Int) = Color.argb(a, Color.red(color), Color.green(color), Color.blue(color))
        private fun near(dh: Float): Int {
            if (!colorful) return color
            val hsv = FloatArray(3); Color.colorToHSV(color, hsv)
            hsv[0] = (hsv[0] + dh + 360f) % 360f; hsv[1] = maxOf(hsv[1], 0.7f); hsv[2] = maxOf(hsv[2], 0.9f)
            return Color.HSVToColor(hsv)
        }
        private fun a(col: Int, al: Int) = Color.argb(al, Color.red(col), Color.green(col), Color.blue(col))

        private fun gradient(w: Int, h: Int): SweepGradient {
            val key = color * 31 + w + (if (island) 7 else 0) + (if (colorful) 13 else 0)
            if (shader == null || shaderKey != key) {
                shader = if (island)
                    // the whole border lit, with a lighter sheen that drifts round it
                    SweepGradient(w / 2f, h / 2f, intArrayOf(color, near(-55f), a(near(-55f), 200), near(70f), color, a(color, 170), color), floatArrayOf(0f, 0.18f, 0.32f, 0.5f, 0.68f, 0.84f, 1f))
                else
                    // a bright band, a long tail, and a gap: the band is what is seen travelling round the edge
                    SweepGradient(w / 2f, h / 2f, intArrayOf(c(0), a(near(-55f), 140), near(-55f), color, near(70f), a(near(70f), 140), c(0), c(0)), floatArrayOf(0f, 0.16f, 0.28f, 0.36f, 0.44f, 0.56f, 0.72f, 1f))
                shaderKey = key
            }
            return shader!!
        }

        /** The border as one path that starts at the top edge above x0 and goes clockwise back to it. */
        private fun border(w: Float, h: Float, r: Float, i: Float, x0: Float): Path {
            val p = Path()
            p.moveTo(x0, i)
            p.lineTo(w - i - r, i)
            if (r > 0) p.arcTo(RectF(w - i - 2 * r, i, w - i, i + 2 * r), -90f, 90f)
            p.lineTo(w - i, h - i - r)
            if (r > 0) p.arcTo(RectF(w - i - 2 * r, h - i - 2 * r, w - i, h - i), 0f, 90f)
            p.lineTo(i + r, h - i)
            if (r > 0) p.arcTo(RectF(i, h - i - 2 * r, i + 2 * r, h - i), 90f, 90f)
            p.lineTo(i, i + r)
            if (r > 0) p.arcTo(RectF(i, i, i + 2 * r, i + 2 * r), 180f, 90f)
            p.lineTo(x0, i)
            return p
        }

        override fun onDraw(cv: Canvas) {
            val w = width; val h = height
            if (w == 0 || h == 0) return
            val g = gradient(w, h)
            m.setRotate(angle, w / 2f, h / 2f); g.setLocalMatrix(m)
            val r = cornerRadius()
            val cut: Rect? = geo?.rect
            val x0 = cut?.exactCenterX() ?: (w / 2f)
            val inset = paints.last().strokeWidth / 2f
            if (island && unravel < 1f) {
                // the light running out from the camera both ways round the border, a bright point leading each side
                val path = border(w.toFloat(), h.toFloat(), (r - inset).coerceAtLeast(0f), inset, x0)
                val pm = PathMeasure(path, false)
                val len = pm.length
                val dist = unravel * len / 2f
                for ((k, p) in paints.withIndex()) {
                    p.shader = null; p.color = color
                    p.alpha = (base[k] * level * pulse).toInt().coerceIn(0, 255)
                    seg.reset(); pm.getSegment(0f, dist, seg, true); cv.drawPath(seg, p)
                    seg.reset(); pm.getSegment(len - dist, len, seg, true); cv.drawPath(seg, p)
                }
                head.color = Color.WHITE
                for (at in listOf(dist, len - dist)) {
                    pm.getPosTan(at, pos, null)
                    head.alpha = (220 * level).toInt(); cv.drawCircle(pos[0], pos[1], 3.2f * d, head)
                    head.color = color; head.alpha = (90 * level).toInt(); cv.drawCircle(pos[0], pos[1], 9f * d, head); head.color = Color.WHITE
                }
            } else {
                for ((k, p) in paints.withIndex()) {
                    p.color = Color.BLACK; p.shader = g
                    p.alpha = (base[k] * level * pulse).toInt().coerceIn(0, 255)
                    val i = p.strokeWidth / 2f
                    rect.set(i, i, w - i, h - i)
                    cv.drawRoundRect(rect, (r - i).coerceAtLeast(0f), (r - i).coerceAtLeast(0f), p)
                }
            }
            // island style: a ring of light round the camera itself, where everything starts
            if (island && cut != null) {
                val ring = geo?.path
                for ((k, p) in paints.withIndex()) {
                    if (k == 0) continue
                    p.shader = null; p.color = color
                    p.alpha = (base[k] * level * pulse * 0.8f).toInt().coerceIn(0, 255)
                    if (ring != null) cv.drawPath(ring, p)
                    else {
                        val rr = RectF(cut); val rad = minOf(rr.width(), rr.height()) / 2f
                        cv.drawRoundRect(rr, rad, rad, p)
                    }
                }
            }
        }

        override fun onDetachedFromWindow() { spin.cancel(); super.onDetachedFromWindow() }
    }
}
