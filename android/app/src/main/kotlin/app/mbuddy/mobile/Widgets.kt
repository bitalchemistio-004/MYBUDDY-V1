package app.mbuddy.mobile

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import org.json.JSONArray
import org.json.JSONObject

/**
 * Everything the home-screen widgets share. The page keeps the data (tasks, level, streak); it hands a short copy to the phone
 * (Bridge.setWidget) whenever it changes, and the widgets draw from that copy. A tap on a widget cannot reach the page's storage, so it
 * is written to the Inbox ({"type":"done"|"snooze"|"focus"|"go"}) and the page applies it, with the xp and streak, the next time it
 * is open. The copy is also changed at once so the widget shows the result without waiting.
 */
object Widgets {
    const val ACT_TICK = "app.mbuddy.mobile.WIDGET_TICK"
    const val ACT_SNOOZE = "app.mbuddy.mobile.WIDGET_SNOOZE"
    const val ACT_FOCUS = "app.mbuddy.mobile.WIDGET_FOCUS"
    const val ACT_FOCUS_END = "app.mbuddy.mobile.WIDGET_FOCUS_END"
    const val ACT_FOCUS_STOP = "app.mbuddy.mobile.WIDGET_FOCUS_STOP"
    const val ACT_CALL = "app.mbuddy.mobile.WIDGET_CALL"

    private val COLORS = mapOf("blink" to 0xFFFFCC33.toInt(), "void" to 0xFFA78BFA.toInt(), "glitch" to 0xFF22D3EE.toInt(),
        "cinder" to 0xFFFF7A2F.toInt(), "oracle" to 0xFF2DD4BF.toInt())

    fun accent(ctx: Context): Int = COLORS[Store.character(ctx)] ?: COLORS.getValue("blink")

    /** The copy the page handed over: tasks (open), today (events and timed tasks), level, pct, streak, floor, doneToday, daily. */
    fun data(ctx: Context): JSONObject = runCatching { JSONObject(Store.widgetData(ctx)) }.getOrDefault(JSONObject())

    fun tasks(ctx: Context): List<JSONObject> { val a = data(ctx).optJSONArray("tasks") ?: JSONArray(); return (0 until a.length()).mapNotNull { a.optJSONObject(it) } }

    /** Opens the app on [route] (a place such as "today" or "play/quest"). */
    fun open(ctx: Context, route: String, req: Int): PendingIntent {
        val i = Intent(ctx, SetupActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra("route", route)
        i.action = "open-$route"            // a different action each, so the PendingIntents are different
        return PendingIntent.getActivity(ctx, req, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    /** A tap that is handled by [WidgetActionReceiver] without opening the app. */
    fun act(ctx: Context, action: String, req: Int, id: String = "", minutes: Int = 0): PendingIntent {
        val i = Intent(ctx, WidgetActionReceiver::class.java).setAction(action).putExtra("id", id).putExtra("minutes", minutes)
        return PendingIntent.getBroadcast(ctx, req, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private val faces = HashMap<String, Bitmap>()

    /** The buddy's resting picture, scaled to [px] (kept, so six widgets do not each decode it); null if it cannot be read. */
    @Synchronized
    fun face(ctx: Context, px: Int = 120): Bitmap? = faces["${Store.character(ctx)}@$px"] ?: decodeFace(ctx, px)?.also { faces["${Store.character(ctx)}@$px"] = it }

    private fun decodeFace(ctx: Context, px: Int): Bitmap? = runCatching {
        ctx.assets.open("web/assets/buddy/chars/${Store.character(ctx)}/still.webp").use { s ->
            val b = BitmapFactory.decodeStream(s) ?: return@runCatching null
            Bitmap.createScaledBitmap(b, px, (px * b.height.toFloat() / b.width).toInt().coerceAtLeast(1), true)
        }
    }.getOrNull()

    /** A level ring: [pct] of the way round in the buddy's colour, the level in the middle. */
    fun ring(ctx: Context, level: Int, pct: Int, px: Int = 180): Bitmap {
        val bmp = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        val stroke = px * 0.1f
        val r = RectF(stroke / 2, stroke / 2, px - stroke / 2, px - stroke / 2)
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = stroke; strokeCap = Paint.Cap.ROUND }
        p.color = 0xFF26262C.toInt(); c.drawArc(r, 0f, 360f, false, p)
        p.color = accent(ctx); c.drawArc(r, -90f, 360f * pct.coerceIn(0, 100) / 100f, false, p)
        val t = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFF5F3EE.toInt(); textSize = px * 0.34f; textAlign = Paint.Align.CENTER; isFakeBoldText = true }
        c.drawText("$level", px / 2f, px / 2f + t.textSize * 0.35f, t)
        return bmp
    }

    private val ui = android.os.Handler(android.os.Looper.getMainLooper())
    private var pending: Runnable? = null

    /** Redraw every widget now (a tap on one: the owner is looking at it). */
    fun refreshNow(ctx: Context) {
        NextWidget.refresh(ctx); TasksWidget.refresh(ctx); AskWidget.refresh(ctx); PlayWidget.refresh(ctx); TodayWidget.refresh(ctx); FocusWidget.refresh(ctx)
    }

    /** Redraw every widget soon: the page hands over data in bursts (an agenda and a widget copy, one after another), so many calls
     *  inside half a second become one redraw. */
    @Synchronized
    fun refreshAll(ctx: Context) {
        val app = ctx.applicationContext
        pending?.let { ui.removeCallbacks(it) }
        pending = Runnable { pending = null; refreshNow(app) }.also { ui.postDelayed(it, 500) }
    }

    /** Debug builds only: builds every widget's views and inflates them, so a broken layout shows up without placing the widget. */
    fun selfTest(activityCtx: Context): String {
        // a launcher inflates widgets without the activity's AppCompat view factory (which swaps in AppCompatImageView and refuses some
        // remote methods), so the test uses the application context, as the real host does
        val ctx = activityCtx.applicationContext
        val host = android.widget.FrameLayout(ctx)
        val all = listOf<Pair<String, () -> android.widget.RemoteViews>>(
            "next" to { NextWidget.views(ctx) }, "tasks" to { TasksWidget.views(ctx) }, "ask" to { AskWidget.views(ctx) },
            "play" to { PlayWidget.views(ctx) }, "today" to { TodayWidget.views(ctx) }, "focus" to { FocusWidget.views(ctx) })
        return all.joinToString("; ") { (name, build) ->
            runCatching { build().apply(ctx, host); "$name ok" }.getOrElse { "$name FAILED: ${it.javaClass.simpleName} ${it.message?.take(120)}" }
        }
    }

    fun ids(ctx: Context, cls: Class<*>): IntArray = AppWidgetManager.getInstance(ctx).getAppWidgetIds(ComponentName(ctx, cls))

    // ---- what a tap does ----
    fun tick(ctx: Context, id: String) {
        val d = data(ctx); val a = d.optJSONArray("tasks") ?: JSONArray(); val keep = JSONArray(); var title = ""
        for (i in 0 until a.length()) { val o = a.optJSONObject(i) ?: continue; if (o.optString("id") == id) title = o.optString("title") else keep.put(o) }
        d.put("tasks", keep).put("doneToday", d.optInt("doneToday") + 1)
        Store.setWidgetData(ctx, d.toString())
        Inbox.add(ctx, JSONObject().put("type", "done").put("id", id).put("title", title))
        refreshNow(ctx)
    }

    fun snooze(ctx: Context, id: String, minutes: Int) {
        Inbox.add(ctx, JSONObject().put("type", "snooze").put("id", id).put("mins", minutes))
        val d = data(ctx); val a = d.optJSONArray("tasks") ?: JSONArray(); val keep = JSONArray()
        for (i in 0 until a.length()) { val o = a.optJSONObject(i) ?: continue; if (o.optString("id") != id) keep.put(o) }
        Store.setWidgetData(ctx, d.put("tasks", keep).toString()); refreshNow(ctx)
    }

    fun startFocus(ctx: Context, minutes: Int) {
        val end = System.currentTimeMillis() + minutes * 60_000L
        Store.setFocusEnd(ctx, end, minutes)
        ctx.getSystemService(AlarmManager::class.java)?.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, end, act(ctx, ACT_FOCUS_END, 7190, minutes = minutes))
        refreshNow(ctx)
    }

    fun stopFocus(ctx: Context) {
        Store.setFocusEnd(ctx, 0L, 0)
        ctx.getSystemService(AlarmManager::class.java)?.cancel(act(ctx, ACT_FOCUS_END, 7190))
        refreshNow(ctx)
    }
}

/** Taps on widgets and on the buttons of a notification: tick a task, snooze it, start or end a focus sprint, call the buddy. */
class WidgetActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: ""
        val minutes = intent.getIntExtra("minutes", 0)
        when (intent.action) {
            Widgets.ACT_TICK -> { Widgets.tick(ctx, id); dismiss(ctx, id) }
            Widgets.ACT_SNOOZE -> { Widgets.snooze(ctx, id, if (minutes > 0) minutes else 10); dismiss(ctx, id) }
            Widgets.ACT_FOCUS -> Widgets.startFocus(ctx, if (minutes > 0) minutes else 25)
            Widgets.ACT_FOCUS_STOP -> Widgets.stopFocus(ctx)
            Widgets.ACT_FOCUS_END -> {
                val m = Store.focusMinutes(ctx).takeIf { it > 0 } ?: minutes
                Store.setFocusEnd(ctx, 0L, 0)
                Inbox.add(ctx, JSONObject().put("type", "focus").put("minutes", m))
                Smart.notify(ctx, "Focus is over", "$m minutes done. Nicely done.")
                Widgets.refreshNow(ctx)
            }
            Widgets.ACT_CALL -> CallBuddy.now(ctx)
        }
    }

    private fun dismiss(ctx: Context, id: String) {
        runCatching { androidx.core.app.NotificationManagerCompat.from(ctx).cancel(Smart.taskNotificationId(id)) }
    }
}

/** Calls the buddy: the assistant session if MBUDDY is the phone's assistant, otherwise it opens the app. */
object CallBuddy {
    fun intent(ctx: Context): Intent {
        val assistant = android.os.Build.VERSION.SDK_INT >= 29 && runCatching {
            ctx.getSystemService(android.app.role.RoleManager::class.java).isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT)
        }.getOrDefault(false)
        return (if (assistant) Intent(Intent.ACTION_ASSIST) else Intent(ctx, SetupActivity::class.java).putExtra("route", "buddy"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    fun now(ctx: Context) { runCatching { ctx.startActivity(intent(ctx)) } }
    fun pending(ctx: Context, req: Int): PendingIntent = PendingIntent.getActivity(ctx, req, intent(ctx), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
}
