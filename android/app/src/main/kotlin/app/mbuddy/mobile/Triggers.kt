package app.mbuddy.mobile

import android.app.Notification
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationManagerCompat

/**
 * Two more ways for the buddy to speak first, both opt-in and neither using the microphone:
 *  - a message arrived (WhatsApp or SMS, one person, not a group): a card offers "Reply by voice". The notification is read only to make
 *    that card; its text is kept in memory for the moment, never saved, never sent anywhere.
 *  - your car's Bluetooth connected: a card offers Maps and music.
 */
class MessageListener : NotificationListenerService() {
    private val lastFrom = object : LinkedHashMap<String, Long>(16, 0.75f, true) { override fun removeEldestEntry(e: MutableMap.MutableEntry<String, Long>?) = size > 64 }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (!Store.msgCards(this) || Privacy.on(this)) return          // privacy mode: other apps' notifications are not read
        val app = when (sbn.packageName) {
            "com.whatsapp" -> "whatsapp"
            "com.google.android.apps.messaging", "com.android.mms", "com.samsung.android.messaging", "com.miui.mms" -> "sms"
            else -> return
        }
        val n = sbn.notification
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val ex = n.extras
        if (ex.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false)) return        // a reply to a group is too easy to get wrong
        val who = ex.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
        val text = ex.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        if (who.isEmpty() || text.isEmpty() || who.equals("WhatsApp", true)) return
        val now = System.currentTimeMillis()
        if (now - (lastFrom[who] ?: 0) < 30_000) return          // one card per person per half minute
        lastFrom[who] = now
        val interactive = getSystemService(PowerManager::class.java)?.isInteractive == true
        val svc = ControlService.instance
        if (!interactive || svc == null || Agent.running || Command.listening) return    // the phone has already notified; no card over a task, a dark screen or the owner's voice
        svc.overlay.show("asking", "$who: ${text.take(140)}", "New message", listOf("reply:$app|$who" to "Reply by voice", "close" to "Dismiss"))
    }
}

class CarReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != BluetoothDevice.ACTION_ACL_CONNECTED || Privacy.on(ctx)) return
        val want = Store.carName(ctx).trim()
        if (want.isEmpty()) return
        val name = runCatching {
            @Suppress("DEPRECATION") val d = intent.getParcelableExtra<BluetoothDevice>(BluetoothDevice.EXTRA_DEVICE)
            d?.name.orEmpty()
        }.getOrDefault("")
        if (!name.contains(want, ignoreCase = true)) return
        val svc = ControlService.instance
        val pm = ctx.getSystemService(PowerManager::class.java)
        if (svc != null && pm?.isInteractive == true && !Agent.running && !Command.listening) {
            svc.overlay.show("asking", "Connected to $name. Where to?", "Driving", listOf("do:open Google Maps" to "Maps", "do:play some music" to "Music", "close" to "Not now"))
        } else {
            Smart.notify(ctx, "Connected to $name", "Tap to open MBUDDY, or say where to.")
        }
    }
}

fun notificationAccessGranted(ctx: Context): Boolean =
    NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName)
