package app.mbuddy.mobile

import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.view.WindowInsets
import android.view.WindowManager

/**
 * Where the screen's camera cutout is, as Android reports it: a centred punch hole, a hole in a corner, a waterdrop, a wide notch, or
 * none at all. The island style grows out of it; a phone without one gets the island from the top centre and nothing pretends to be a
 * notch. [override] lets the debug build try other phones' shapes on this one.
 */
object Cutout {
    class Geo(val width: Int, val height: Int, val rect: Rect?, val path: Path?, val statusBar: Int, val density: Float)

    /** Debug builds: a cutout to use instead of the real one (pixels), to see how the island looks on other phones. Empty rect = none. */
    @Volatile var override: Rect? = null
    @Volatile var overrideSet = false

    fun read(wm: WindowManager, density: Float): Geo {
        var w = 0; var h = 0; var rect: Rect? = null; var path: Path? = null; var bar = (24 * density).toInt()
        if (Build.VERSION.SDK_INT >= 30) {
            val m = wm.currentWindowMetrics
            w = m.bounds.width(); h = m.bounds.height()
            val insets = m.windowInsets
            bar = insets.getInsets(WindowInsets.Type.statusBars()).top.takeIf { it > 0 } ?: bar
            val c = insets.displayCutout
            rect = c?.boundingRectTop?.takeIf { !it.isEmpty }
            if (Build.VERSION.SDK_INT >= 31 && rect != null) path = c?.cutoutPath
        } else {
            @Suppress("DEPRECATION") val d = wm.defaultDisplay
            val p = android.graphics.Point(); @Suppress("DEPRECATION") d.getRealSize(p); w = p.x; h = p.y
            if (Build.VERSION.SDK_INT >= 29) rect = d.cutout?.boundingRectTop?.takeIf { !it.isEmpty }
        }
        if (overrideSet) { rect = override?.takeIf { !it.isEmpty }; path = null }
        return Geo(w, h, rect, path, bar, density)
    }
}
