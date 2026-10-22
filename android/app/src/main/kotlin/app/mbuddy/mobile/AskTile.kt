package app.mbuddy.mobile

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * The quick-settings tile: pull the shade down, tap "Ask MBUDDY". It calls the buddy the same way the volume-keys shortcut does: as the
 * phone's assistant it opens the assistant session; otherwise, with screen control on, it opens the buddy's card; else it opens the app.
 */
class AskTile : TileService() {
    override fun onStartListening() {
        qsTile?.apply { state = Tile.STATE_INACTIVE; label = "Ask MBUDDY"; updateTile() }
    }

    override fun onClick() {
        val intent = CallBuddy.intent(this)
        if (Build.VERSION.SDK_INT >= 34) {
            val pi = PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            startActivityAndCollapse(pi)
        } else {
            @Suppress("DEPRECATION") startActivityAndCollapse(intent)
        }
    }
}
