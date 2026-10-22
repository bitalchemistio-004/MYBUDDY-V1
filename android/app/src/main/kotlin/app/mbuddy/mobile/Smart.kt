package app.mbuddy.mobile

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * Smart cards: the buddy speaks first, with no microphone. Before something you have on, a card ten minutes ahead (with the way there);
 * a task at its time; a short briefing each morning. They are built from the agenda the app keeps (events and tasks with a time),
 * which the screens hand over whenever it changes (Bridge.setAgenda), and fired by the phone's alarm clock, so MBUDDY need not be running.
 */
object Smart {
    const val CH = "mbuddy_smart"
    private const val ACTION = "app.mbuddy.mobile.SMART"
    private const val SLOTS = 40            // request codes 7000..7039: 0..38 for items, 39 for the briefing
    private const val LEAD_MS = 10 * 60_000L

    private fun channel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 26) ctx.getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CH, "Heads-up from MBUDDY", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "A card before an event, a task at its time, and the morning briefing." })
    }

    private fun pi(ctx: Context, slot: Int, extras: JSONObject?, flags: Int): PendingIntent? {
        val i = Intent(ctx, SmartReceiver::class.java).setAction(ACTION)
        extras?.let { e -> e.keys().forEach { k -> i.putExtra(k, e.opt(k)?.toString() ?: "") } }
        return PendingIntent.getBroadcast(ctx, 7000 + slot, i, flags or PendingIntent.FLAG_IMMUTABLE)
    }

    /** Set the phone's alarms from the stored agenda: one per upcoming item, and the morning briefing. */
    fun reschedule(ctx: Context) {
        val am = ctx.getSystemService(AlarmManager::class.java) ?: return
        for (s in 0 until SLOTS) pi(ctx, s, null, PendingIntent.FLAG_NO_CREATE)?.let { am.cancel(it); it.cancel() }
        val now = System.currentTimeMillis()
        val arr = runCatching { JSONArray(Store.agenda(ctx)) }.getOrDefault(JSONArray())
        var slot = 0
        for (i in 0 until arr.length()) {
            if (slot >= 39) break
            val o = arr.optJSONObject(i) ?: continue
            val at = o.optLong("at"); val kind = o.optString("kind")
            val fire = if (kind == "event") at - LEAD_MS else at
            val wanted = if (kind == "event") Store.smartEvents(ctx) else Store.smartTasks(ctx)
            if (!wanted || fire <= now || fire > now + 36 * 3_600_000L) continue
            val p = pi(ctx, slot, JSONObject().put("kind", kind).put("title", o.optString("title")).put("place", o.optString("place")).put("at", at.toString()).put("id", o.optString("id")), PendingIntent.FLAG_UPDATE_CURRENT) ?: continue
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, fire, p); slot++
        }
        if (Store.smartBrief(ctx)) {
            val c = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, Store.briefHour(ctx)); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
            if (c.timeInMillis <= now) c.add(Calendar.DAY_OF_YEAR, 1)
            pi(ctx, 39, JSONObject().put("kind", "brief"), PendingIntent.FLAG_UPDATE_CURRENT)?.let { am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, c.timeInMillis, it) }
        }
        android.util.Log.i("MBuddySmart", "scheduled $slot item(s)${if (Store.smartBrief(ctx)) " and the morning briefing" else ""}")
    }

    /** What the briefing says: today's events and open tasks from the agenda. */
    private fun briefing(ctx: Context): String {
        val arr = runCatching { JSONArray(Store.agenda(ctx)) }.getOrDefault(JSONArray())
        val start = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0) }.timeInMillis
        val end = start + 24 * 3_600_000L
        val today = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.filter { it.optLong("at") in start until end }.sortedBy { it.optLong("at") }
        if (today.isEmpty()) return "Nothing scheduled today. A clear day."
        val first = today.first(); val t = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(java.util.Date(first.optLong("at")))
        val events = today.count { it.optString("kind") == "event" }; val tasks = today.count { it.optString("kind") == "task" }
        return buildString {
            append(listOfNotNull(events.takeIf { it > 0 }?.let { "$it event${if (it == 1) "" else "s"}" }, tasks.takeIf { it > 0 }?.let { "$it task${if (it == 1) "" else "s"}" }).joinToString(" and "))
            append(". First: ${first.optString("title")} at $t.")
        }
    }

    /** A plain heads-up notification (no card). */
    fun notify(ctx: Context, title: String, text: String) {
        channel(ctx)
        val open = PendingIntent.getActivity(ctx, 7102, Intent(ctx, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        runCatching { NotificationManagerCompat.from(ctx).notify(7300 + (title.hashCode() and 0xff), NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title).setContentText(text).setContentIntent(open).setAutoCancel(true).build()) }
    }

    fun notifyIntent(ctx: Context, title: String, text: String, intent: Intent) {
        channel(ctx)
        val pi = PendingIntent.getActivity(ctx, 7103, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        runCatching { NotificationManagerCompat.from(ctx).notify(7400, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title).setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text)).setContentIntent(pi).setAutoCancel(true).build()) }
    }

    fun taskNotificationId(id: String) = 7600 + (id.hashCode() and 0x3ff)

    fun fire(ctx: Context, kind: String, title: String, place: String, id: String = "") {
        channel(ctx)
        val (head, text) = when (kind) {
            "event" -> "In 10 minutes" to title
            "task" -> "Now" to title
            else -> "Good morning" to briefing(ctx)
        }
        val open = PendingIntent.getActivity(ctx, 7100, Intent(ctx, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        val b = NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_notify).setContentTitle(head).setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text)).setContentIntent(open).setAutoCancel(true)
        if (place.isNotBlank()) {
            val dir = Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination=" + Uri.encode(place))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            b.addAction(0, "Directions", PendingIntent.getActivity(ctx, 7101, dir, PendingIntent.FLAG_IMMUTABLE))
        }
        // a task reminder can be ticked off or put back ten minutes without opening anything (the page applies it, with the xp, next time it is open)
        if (kind == "task" && id.isNotBlank()) {
            b.addAction(0, "Done", Widgets.act(ctx, Widgets.ACT_TICK, 7500 + (id.hashCode() and 0xff), id))
            b.addAction(0, "In 10 min", Widgets.act(ctx, Widgets.ACT_SNOOZE, 7300 + (id.hashCode() and 0xff), id, 10))
        }
        val nid = if (kind == "task" && id.isNotBlank()) taskNotificationId(id) else 7200 + (title.hashCode() and 0xff)
        runCatching { NotificationManagerCompat.from(ctx).notify(nid, b.build()) }
        // with the screen on and screen control available, the card shows over what you are doing as well
        val pm = ctx.getSystemService(PowerManager::class.java)
        ControlService.instance?.takeIf { pm?.isInteractive == true && !Agent.running && !Command.listening }?.overlay
            ?.show("asking", if (kind == "brief") text else "$head: $title", if (place.isNotBlank()) place else "", listOf("close" to "OK"))
    }
}

class SmartReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val kind = intent.getStringExtra("kind") ?: return
        Smart.fire(ctx, kind, intent.getStringExtra("title") ?: "", intent.getStringExtra("place") ?: "", intent.getStringExtra("id") ?: "")
        if (kind == "brief") Smart.reschedule(ctx)      // tomorrow's briefing
    }
}

/** After MBUDDY is updated Android switches screen control off, and with it the edge handle: tell the owner, with the tap that fixes it. */
class UpdatedReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        Smart.reschedule(ctx)
        if (!Store.controlWasOn(ctx)) return
        // the service has not reconnected yet; look again in a few seconds
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            if (ControlService.instance == null) {
                val i = Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                Smart.notifyIntent(ctx, "MBUDDY was updated", "Android switched screen control off, so the edge buddy is hidden. Tap to turn it back on.", i)
            }
        }, 6000)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) { if (intent.action == Intent.ACTION_BOOT_COMPLETED) Smart.reschedule(ctx) }
}
