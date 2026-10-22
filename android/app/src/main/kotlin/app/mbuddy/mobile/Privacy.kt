package app.mbuddy.mobile

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/**
 * Privacy mode: for when the owner is doing something private. While it is on MBUDDY does not look at the screen, does not read other
 * apps' notifications (so no message or car cards), shows nothing over other apps (no card, no edge buddy), and does not listen for a
 * request. It is a switch the owner turns on and off, for as long as they choose; a notification says it is on and turns it off.
 *
 * What it does not do: it does not turn Android's own screen-control permission off (that stays the owner's, in Android settings), and
 * it does not touch the phone's own notifications, alarms or timers, which still ring.
 */
object Privacy {
    private const val CHANNEL = "mbuddy_privacy"
    private const val NOTIF_ID = 7700
    const val ACTION_OFF = "app.mbuddy.mobile.PRIVACY_OFF"

    /** Pure rule, so it can be tested: on, and not past its end time (0 = until switched off). */
    fun active(on: Boolean, until: Long, now: Long): Boolean = on && (until == 0L || now < until)

    /**
     * What the owner said about privacy mode: true to turn it on, false to turn it off, null when it was not about privacy mode.
     * Saying it off works too (the in-app microphone is the owner's own tap), so it can be turned off the way it was turned on.
     */
    fun parse(said: String): Boolean? {
        val s = said.lowercase()
        if (!Regex("\\bprivacy\\b|\\bprivate mode\\b").containsMatchIn(s)) return null
        if (Regex("\\b(off|disable|disabled|stop|end|exit|leave|cancel|quit|finish|done)\\b").containsMatchIn(s)) return false
        if (Regex("\\b(on|enable|enabled|start|activate|turn|switch|put|set|go|use|enter)\\b").containsMatchIn(s)) return true
        return null
    }

    /** Apply [parse] and return what to say, or null when [said] was not about privacy mode. */
    fun voiceCommand(ctx: Context, said: String): String? {
        val want = parse(said) ?: return null
        set(ctx, want)
        return if (want) "Privacy mode is on. I am not looking at the screen or listening. Say turn privacy mode off, or use the notification."
        else "Privacy mode is off. I am back to normal."
    }

    /** Whether privacy mode is on right now. A timed one that has run out switches itself off here as well as by its alarm. */
    fun on(ctx: Context): Boolean {
        val c = ctx.applicationContext
        if (!Store.privacy(c)) return false
        val until = Store.privacyUntil(c)
        if (active(true, until, System.currentTimeMillis())) return true
        set(c, false)
        return false
    }

    /** Turn it on (for [minutes], or until switched off when 0) or off. Everything that shows or reads is told at once. */
    fun set(ctx: Context, on: Boolean, minutes: Int = 0) {
        val c = ctx.applicationContext
        val until = if (on && minutes > 0) System.currentTimeMillis() + minutes * 60_000L else 0L
        Store.setPrivacy(c, on, until)
        val am = c.getSystemService(AlarmManager::class.java)
        val off = PendingIntent.getBroadcast(c, 7701, Intent(c, PrivacyReceiver::class.java).setAction(ACTION_OFF), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        if (on && until > 0) runCatching { am?.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, until, off) } else am?.cancel(off)
        if (on) {
            // what is on screen right now is taken away, and whatever the buddy was doing stops
            Agent.stop("privacy mode")
            Voice.shutUp(); Voice.stopListening()
            ControlService.instance?.let { it.overlay.hide(); it.handle.hide() }
            announce(c, until)
        } else {
            runCatching { NotificationManagerCompat.from(c).cancel(NOTIF_ID) }
            ControlService.instance?.handle?.refresh()
        }
        runCatching { android.service.quicksettings.TileService.requestListeningState(c, ComponentName(c, PrivacyTile::class.java)) }
    }

    private fun announce(c: Context, until: Long) {
        if (Build.VERSION.SDK_INT >= 26) c.getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, "Privacy mode", NotificationManager.IMPORTANCE_LOW).apply { description = "Shown while privacy mode is on." })
        val offIntent = PendingIntent.getBroadcast(c, 7702, Intent(c, PrivacyReceiver::class.java).setAction(ACTION_OFF), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val text = if (until > 0) "Not looking at the screen or listening, until " + java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(java.util.Date(until)) + "."
        else "Not looking at the screen, reading notifications or listening. Turn it off when you are done."
        val n = NotificationCompat.Builder(c, CHANNEL).setSmallIcon(R.drawable.ic_notify).setContentTitle("Privacy mode is on").setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text)).setOngoing(true).setOnlyAlertOnce(true).setPriority(NotificationCompat.PRIORITY_LOW)
            .addAction(0, "Turn off", offIntent).build()
        runCatching { NotificationManagerCompat.from(c).notify(NOTIF_ID, n) }
    }

    /** What the owner is told when something is asked of the buddy while it is on. */
    const val REFUSAL = "Privacy mode is on, so I am not looking or listening. Turn it off to use me."
}

/** The "Turn off" of the notification, and the end of a timed privacy mode. */
class PrivacyReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Privacy.ACTION_OFF) Privacy.set(ctx, false)
    }
}
