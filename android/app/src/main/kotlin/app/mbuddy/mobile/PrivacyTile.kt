package app.mbuddy.mobile

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/** The quick-settings tile for privacy mode: tap to switch it on (until you switch it off) or off, without opening anything. */
class PrivacyTile : TileService() {
    override fun onStartListening() = paint()

    override fun onClick() {
        Privacy.set(this, !Privacy.on(this))
        paint()
    }

    private fun paint() {
        val t = qsTile ?: return
        val on = Privacy.on(this)
        t.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        t.label = "Privacy mode"
        if (android.os.Build.VERSION.SDK_INT >= 29) t.subtitle = if (on) "On" else "Off"
        t.updateTile()
    }
}
