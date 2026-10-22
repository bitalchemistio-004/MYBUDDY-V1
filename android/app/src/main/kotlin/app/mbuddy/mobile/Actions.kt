package app.mbuddy.mobile

import android.Manifest
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.provider.Settings
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.URLEncoder
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.TimeZone

/**
 * The things MBUDDY can make the phone do. Each kind matches core/phone_actions.py on the PC and js/phone-actions.js in the page:
 * alarm, timer, calendar, call, sms, whatsapp, navigate, search, email, open_gallery.
 *
 * What happens without a tap, and what needs one (Android's rules, not a choice):
 *  - a calendar event is written straight into the calendar once the user has allowed it (silent, even in the background);
 *  - everything else opens an app (the clock, the dialer, maps...). Android does not let an app start another app's screen from
 *    the background, so when MBUDDY is not on screen the action becomes a notification and one tap does it.
 *  - nothing is ever called, sent or paid for on its own: a call or a message opens the dialer or the composer, ready to send.
 */
object Actions {
    const val GALLERY_PACKAGE = "com.google.ai.edge.gallery"
    private const val GALLERY_PLAY = "https://play.google.com/store/apps/details?id=$GALLERY_PACKAGE"

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    /** The intent that does this action when started from a screen, or null when the kind or its details are not usable. */
    fun intentFor(ctx: Context, kind: String, p: JSONObject): Intent? = runCatching {
        when (kind) {
            "alarm" -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, p.getInt("hour")).putExtra(AlarmClock.EXTRA_MINUTES, p.optInt("minute", 0))
                .putExtra(AlarmClock.EXTRA_MESSAGE, p.optString("label").ifEmpty { "MBUDDY" }).putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            "timer" -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, p.getInt("seconds"))
                .putExtra(AlarmClock.EXTRA_MESSAGE, p.optString("label").ifEmpty { "MBUDDY" }).putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            "calendar" -> {
                val (start, end) = times(p)
                Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI)
                    .putExtra(CalendarContract.Events.TITLE, p.optString("title", "Event"))
                    .putExtra(CalendarContract.Events.EVENT_LOCATION, p.optString("place"))
                    .putExtra(CalendarContract.Events.DESCRIPTION, p.optString("notes"))
                    .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, start).putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end)
            }
            "call" -> Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + p.getString("number")))      // the dialer, ready: it never calls by itself
            "sms" -> Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + p.getString("number"))).putExtra("sms_body", p.optString("body"))
            "whatsapp" -> Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/" + p.getString("number").filter { it.isDigit() } +
                (p.optString("body").takeIf { it.isNotEmpty() }?.let { "?text=" + enc(it) } ?: "")))
            "navigate" -> Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination=" + enc(p.getString("place"))))
            "search" -> Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/search?q=" + enc(p.getString("query"))))
            "email" -> Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:" + p.optString("to")))
                .putExtra(Intent.EXTRA_SUBJECT, p.optString("subject")).putExtra(Intent.EXTRA_TEXT, p.optString("body"))
            "map" -> Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + enc(p.getString("place"))))
            "wifi_settings" -> Intent(Settings.ACTION_WIFI_SETTINGS)
            "contact" -> Intent(Intent.ACTION_INSERT, android.provider.ContactsContract.Contacts.CONTENT_URI)
                .putExtra(android.provider.ContactsContract.Intents.Insert.NAME, p.optString("name"))
                .putExtra(android.provider.ContactsContract.Intents.Insert.PHONE, p.optString("number"))
                .putExtra(android.provider.ContactsContract.Intents.Insert.EMAIL, p.optString("email"))
            "open_app" -> appLaunchIntent(ctx, p.getString("app"))
            "open_gallery" -> ctx.packageManager.getLaunchIntentForPackage(GALLERY_PACKAGE) ?: Intent(Intent.ACTION_VIEW, Uri.parse(GALLERY_PLAY))
            else -> null
        }?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }.getOrNull()

    private fun editDistance(a: String, b: String): Int {
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val cur = IntArray(b.length + 1); cur[0] = i
            for (j in 1..b.length) cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            prev = cur
        }
        return prev[b.length]
    }

    /** The intent that opens the installed app called [name] (by the name shown under its icon); null when there is none, or when it is an app MBUDDY never operates. */
    fun appLaunchIntent(ctx: Context, name: String): Intent? = appMatch(ctx, name)?.let { ctx.packageManager.getLaunchIntentForPackage(it.first) }

    /** The installed app (package, name shown under its icon) that [name] means, or null. */
    fun appMatch(ctx: Context, name: String, fuzzy: Boolean = true): Pair<String, String>? {
        val pm = ctx.packageManager
        val all = pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0).map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
        val squash = { t: String -> t.lowercase().filter { it.isLetterOrDigit() } }      // "tick tock", "TikTok" and "Tik Tok" are the same name
        val want = squash(name)
        if (want.isEmpty()) return null
        val hit = all.firstOrNull { squash(it.second) == want }
            // part of a name ("insta" for Instagram), but never a sentence that merely contains one: "open WhatsApp and send hi" is not the app's name
            ?: all.firstOrNull { want.length >= 4 && squash(it.second).startsWith(want) }
            // the full name said for a shorter one ("Google Maps" for the app called Maps), as long as little else is said with it
            ?: all.filter { squash(it.second).length >= 4 && want.contains(squash(it.second)) && want.length <= squash(it.second).length + 8 }
                .maxByOrNull { squash(it.second).length }
            // close enough to sound alike ("what's up" for WhatsApp, "tick tock" for TikTok): the nearest name, if it is near
            ?: (if (!fuzzy) return null else all).map { it to editDistance(squash(it.second), want) }
                .filter { (a, d) -> squash(a.second).length >= 4 && want.length >= 4 && d <= maxOf(1, (maxOf(squash(a.second).length, want.length) * 0.34).toInt()) }
                .minByOrNull { it.second }?.first
            ?: return null
        if (Risk.appDenied(hit.first) != null) return null
        return hit
    }

    /** start and end of an event in milliseconds; an end is one hour after the start when none was given. */
    private fun times(p: JSONObject): Pair<Long, Long> {
        val start = parseTime(p.getString("start"))
        val end = p.optString("end").takeIf { it.isNotEmpty() }?.let { parseTime(it) } ?: (start + 3_600_000)
        return start to end
    }

    private fun parseTime(text: String): Long = runCatching { OffsetDateTime.parse(text).toInstant().toEpochMilli() }
        .getOrElse { LocalDateTime.parse(text.take(19)).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli() }

    fun describe(kind: String, p: JSONObject): String = when (kind) {
        "alarm" -> "Set the alarm for %02d:%02d".format(p.optInt("hour"), p.optInt("minute"))
        "timer" -> "Start a ${p.optInt("seconds") / 60} min timer"
        "open_app" -> "Open ${p.optString("app")}"
        "calendar" -> "Add “${p.optString("title", "Event")}” to the calendar"
        "call" -> "Call ${p.optString("number")}"
        "sms" -> "Text ${p.optString("number")}"
        "whatsapp" -> "WhatsApp ${p.optString("number")}"
        "navigate" -> "Directions to ${p.optString("place")}"
        "search" -> "Search for ${p.optString("query")}"
        "email" -> "Write an email"
        "map" -> "Show ${p.optString("place")} on the map"
        "wifi_settings" -> "Open the Wi-Fi settings"
        "contact" -> "Add ${p.optString("name")} as a contact"
        else -> "Open AI Edge Gallery"
    }

    fun calendarAllowed(ctx: Context) =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.WRITE_CALENDAR) == PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** Write an event into the phone's own calendar, with a reminder ten minutes before. Needs the calendar permission. */
    fun addToCalendar(ctx: Context, p: JSONObject): Boolean {
        if (!calendarAllowed(ctx)) return false
        val cr = ctx.contentResolver
        val calendarId = primaryCalendar(ctx) ?: return false
        val (start, end) = times(p)
        val values = ContentValues().apply {
            put(CalendarContract.Events.DTSTART, start)
            put(CalendarContract.Events.DTEND, end)
            put(CalendarContract.Events.TITLE, p.optString("title", "Event"))
            put(CalendarContract.Events.DESCRIPTION, p.optString("notes"))
            put(CalendarContract.Events.EVENT_LOCATION, p.optString("place"))
            put(CalendarContract.Events.CALENDAR_ID, calendarId)
            put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
        }
        val uri = cr.insert(CalendarContract.Events.CONTENT_URI, values) ?: return false
        val eventId = uri.lastPathSegment?.toLongOrNull() ?: return true
        cr.insert(CalendarContract.Reminders.CONTENT_URI, ContentValues().apply {
            put(CalendarContract.Reminders.EVENT_ID, eventId)
            put(CalendarContract.Reminders.MINUTES, 10)
            put(CalendarContract.Reminders.METHOD, CalendarContract.Reminders.METHOD_ALERT)
        })
        return true
    }

    /** The torch, on or off. Needs no permission and no screen. */
    fun flashlight(ctx: Context, on: Boolean): String = runCatching {
        val cm = ctx.getSystemService(Context.CAMERA_SERVICE) as android.hardware.camera2.CameraManager
        val id = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(android.hardware.camera2.CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
            ?: return "This phone has no flashlight."
        cm.setTorchMode(id, on)
        if (on) "Flashlight on." else "Flashlight off."
    }.getOrElse { "I can't reach the flashlight." }

    /** The calendar the user would expect: the primary one they can write to; else the first they can. */
    private fun primaryCalendar(ctx: Context): Long? {
        val projection = arrayOf(CalendarContract.Calendars._ID, CalendarContract.Calendars.IS_PRIMARY)
        ctx.contentResolver.query(
            CalendarContract.Calendars.CONTENT_URI, projection,
            "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ? AND ${CalendarContract.Calendars.VISIBLE} = 1",
            arrayOf(CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR.toString()), null,
        )?.use { c ->
            var first: Long? = null
            while (c.moveToNext()) {
                val id = c.getLong(0)
                if (c.getInt(1) == 1) return id
                if (first == null) first = id
            }
            return first
        }
        return null
    }

    /**
     * Do one action. [onScreen] is true when MBUDDY is in front (an app may then open another app's screen); in the background
     * only a calendar event can be done silently and everything else becomes a notification. Returns what happened, in words.
     */
    /** What may still be done while privacy mode is on: things that open no other app (a timer or an alarm with no screen, the torch). */
    private val PRIVATE_OK = setOf("timer", "alarm", "flashlight")

    fun execute(ctx: Context, kind: String, p: JSONObject, onScreen: Boolean, notificationId: Int = 0): String {
        if (Privacy.on(ctx) && kind !in PRIVATE_OK && !(kind == "calendar" && addToCalendar(ctx, p))) return Privacy.REFUSAL
        if (kind == "flashlight") return flashlight(ctx, p.optBoolean("on", true))
        if (kind == "calendar" && addToCalendar(ctx, p)) return "Added to your calendar."
        val intent = intentFor(ctx, kind, p) ?: return "I can't do that on this phone."
        if (onScreen) {
            return try { ctx.startActivity(intent); "Done." } catch (e: Exception) { "No app on this phone can do that." }
        }
        // Screen control, when it is on, may open an app while another is in front or the screen is off; if Android refuses, a tap-to-do notification is the way.
        val svc = ControlService.instance
        if (svc != null && kind != "calendar") {
            val opened = runCatching { svc.startActivity(intent); true }.getOrDefault(false)
            if (opened) {
                // Android drops a background start silently, so check the app really came to the front a moment later
                Thread {
                    Thread.sleep(1200)
                    val top = runCatching { svc.root()?.packageName?.toString() }.getOrNull()
                    val wanted = intent.component?.packageName ?: intent.`package`
                    if (wanted != null && top != wanted && !Settings.canDrawOverlays(ctx)) Notifier.offer(ctx, notificationId, describe(kind, p), intent)
                }.start()
                return "Done."
            }
        }
        Notifier.offer(ctx, notificationId, describe(kind, p), intent)
        return "Waiting for your tap."
    }
}
