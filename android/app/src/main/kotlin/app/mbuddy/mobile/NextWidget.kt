package app.mbuddy.mobile

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.os.SystemClock
import android.view.View
import android.widget.RemoteViews
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/*
 * The home-screen widgets, one class each. They all draw from what the app already keeps natively: the agenda the alarms use
 * (Store.agenda) and the short copy of tasks and progress the page hands over (Store.widgetData, see Widgets). They redraw when the
 * page hands over new data and every half hour.
 */

private fun hhmm(at: Long) = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(at))

private class Item(val id: String, val title: String, val at: Long, val event: Boolean)

private fun agenda(ctx: Context, from: Long = System.currentTimeMillis() - 15 * 60_000L): List<Item> {
    val arr = runCatching { JSONArray(Store.agenda(ctx)) }.getOrDefault(JSONArray())
    return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
        .filter { it.optLong("at") >= from }
        .map { Item(it.optString("id"), it.optString("title"), it.optLong("at"), it.optString("kind") == "event") }
        .sortedBy { it.at }
}

private fun midnight(): Long = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }.timeInMillis

private fun redraw(ctx: Context, cls: Class<*>, build: () -> RemoteViews) {
    runCatching {
        val mgr = AppWidgetManager.getInstance(ctx)
        val ids = Widgets.ids(ctx, cls)
        if (ids.isNotEmpty()) { val v = build(); for (id in ids) mgr.updateAppWidget(id, v) }
    }
}

/** What is next: the time, what it is, what follows, five dots for what is left today. */
class NextWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_next)
            val accent = Widgets.accent(ctx)
            val items = agenda(ctx)
            val tomorrow = midnight() + 24 * 3_600_000L
            val first = items.firstOrNull()
            if (first == null) {
                v.setTextViewText(R.id.w_time, "Free"); v.setTextViewText(R.id.w_day, "today"); v.setTextViewText(R.id.w_label, "NOTHING NEXT")
                v.setTextViewText(R.id.w_title, "A clear day"); v.setTextViewText(R.id.w_then, "Tap to ask me anything")
            } else {
                val day = when {
                    first.at < tomorrow -> "today"
                    first.at < tomorrow + 24 * 3_600_000L -> "tomorrow"
                    else -> SimpleDateFormat("EEE d MMM", Locale.getDefault()).format(Date(first.at))
                }
                v.setTextViewText(R.id.w_time, hhmm(first.at)); v.setTextViewText(R.id.w_day, day)
                v.setTextViewText(R.id.w_label, if (first.event) "NEXT EVENT" else "NEXT TASK")
                v.setTextViewText(R.id.w_title, first.title.ifBlank { if (first.event) "Event" else "Task" })
                val second = items.getOrNull(1)
                v.setTextViewText(R.id.w_then, if (second != null) "then ${hhmm(second.at)}  ${second.title}" else "then nothing")
            }
            v.setTextColor(R.id.w_time, accent)
            Widgets.face(ctx, 100)?.let { v.setImageViewBitmap(R.id.w_face, it) }
            val left = items.count { it.at < tomorrow }
            val dots = intArrayOf(R.id.w_d1, R.id.w_d2, R.id.w_d3, R.id.w_d4, R.id.w_d5)
            for ((i, id) in dots.withIndex()) {
                if (i < left) { v.setImageViewResource(id, R.drawable.dot_on); v.setInt(id, "setColorFilter", accent) }
                else { v.setImageViewResource(id, R.drawable.dot_off); v.setInt(id, "setColorFilter", 0) }
            }
            v.setOnClickPendingIntent(R.id.w_root, Widgets.open(ctx, "today", 7110))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, NextWidget::class.java) { views(ctx) }
    }
}

/** Open tasks, up to four, each with a circle that finishes it on the spot. */
class TasksWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        private val ROWS = arrayOf(intArrayOf(R.id.r1, R.id.c1, R.id.t1, R.id.m1), intArrayOf(R.id.r2, R.id.c2, R.id.t2, R.id.m2),
            intArrayOf(R.id.r3, R.id.c3, R.id.t3, R.id.m3), intArrayOf(R.id.r4, R.id.c4, R.id.t4, R.id.m4))

        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_tasks)
            val tasks = Widgets.tasks(ctx)
            val d = Widgets.data(ctx)
            v.setTextViewText(R.id.w_head, "TASKS")
            v.setTextViewText(R.id.w_count, "${tasks.size} open" + (d.optInt("doneToday").takeIf { it > 0 }?.let { " · $it done today" } ?: ""))
            v.setTextColor(R.id.w_count, Widgets.accent(ctx))
            for ((i, r) in ROWS.withIndex()) {
                val t = tasks.getOrNull(i)
                if (t == null) { v.setViewVisibility(r[0], View.GONE); continue }
                v.setViewVisibility(r[0], View.VISIBLE)
                v.setTextViewText(r[2], t.optString("title"))
                val at = t.optLong("at")
                v.setTextViewText(r[3], if (at > 0) hhmm(at) else "")
                v.setOnClickPendingIntent(r[1], Widgets.act(ctx, Widgets.ACT_TICK, 7400 + i, t.optString("id")))
                v.setOnClickPendingIntent(r[2], Widgets.open(ctx, "do", 7410 + i))
            }
            v.setViewVisibility(R.id.w_empty, if (tasks.isEmpty()) View.VISIBLE else View.GONE)
            v.setOnClickPendingIntent(R.id.w_root, Widgets.open(ctx, "do", 7420))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, TasksWidget::class.java) { views(ctx) }
    }
}

/** A bar with the buddy's face and a mic that starts a voice request without opening the app. */
class AskWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_ask)
            Widgets.face(ctx, 120)?.let { v.setImageViewBitmap(R.id.w_face, it) }
            val next = agenda(ctx).firstOrNull()
            v.setTextViewText(R.id.w_sub, if (next != null) "Next: ${next.title} at ${hhmm(next.at)}" else "Tap to talk")
            v.setOnClickPendingIntent(R.id.w_root, CallBuddy.pending(ctx, 7120))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, AskWidget::class.java) { views(ctx) }
    }
}

/** The level ring, the streak, where you are in the quest, and two buttons. */
class PlayWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_play)
            val d = Widgets.data(ctx)
            val level = d.optInt("level", 1).coerceAtLeast(1)
            val pct = d.optInt("pct")
            val streak = d.optInt("streak")
            val floor = d.optInt("floor")
            v.setImageViewBitmap(R.id.w_ring, Widgets.ring(ctx, level, pct))
            v.setTextViewText(R.id.w_label, if (streak > 0) "STREAK" else "LEVEL $level")
            v.setTextViewText(R.id.w_title, if (streak > 0) "$streak day${if (streak == 1) "" else "s"} in a row" else "Finish a task to start")
            v.setTextViewText(R.id.w_sub, (if (floor > 0) "Quest: floor $floor" else "Quest: not started") + if (d.optBoolean("dailyReady")) " · daily game ready" else "")
            v.setTextColor(R.id.w_b1, Widgets.accent(ctx))
            v.setTextViewText(R.id.w_b1, if (d.optBoolean("dailyReady")) "Daily game" else "Play")
            v.setOnClickPendingIntent(R.id.w_b1, Widgets.open(ctx, if (d.optBoolean("dailyReady")) "play/daily" else "play", 7130))
            v.setOnClickPendingIntent(R.id.w_b2, Widgets.open(ctx, "play/quest", 7131))
            v.setOnClickPendingIntent(R.id.w_root, Widgets.open(ctx, "play", 7132))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, PlayWidget::class.java) { views(ctx) }
    }
}

/** Today as a short timeline: the next item is bright, what is past is gone. */
class TodayWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        private val ROWS = arrayOf(intArrayOf(R.id.r1, R.id.m1, R.id.t1), intArrayOf(R.id.r2, R.id.m2, R.id.t2), intArrayOf(R.id.r3, R.id.m3, R.id.t3),
            intArrayOf(R.id.r4, R.id.m4, R.id.t4), intArrayOf(R.id.r5, R.id.m5, R.id.t5))

        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_today)
            val end = midnight() + 24 * 3_600_000L
            val items = agenda(ctx).filter { it.at < end }
            val accent = Widgets.accent(ctx)
            v.setTextViewText(R.id.w_head, SimpleDateFormat("EEEE d MMM", Locale.getDefault()).format(Date()).uppercase(Locale.getDefault()))
            for ((i, r) in ROWS.withIndex()) {
                val it = items.getOrNull(i)
                if (it == null) { v.setViewVisibility(r[0], View.GONE); continue }
                v.setViewVisibility(r[0], View.VISIBLE)
                v.setTextViewText(r[1], hhmm(it.at)); v.setTextViewText(r[2], it.title)
                v.setTextColor(r[1], if (i == 0) accent else 0xFF7C7C76.toInt())
                v.setTextColor(r[2], if (i == 0) 0xFFF5F3EE.toInt() else 0xFFB9B9B3.toInt())
            }
            v.setViewVisibility(R.id.w_empty, if (items.isEmpty()) View.VISIBLE else View.GONE)
            v.setOnClickPendingIntent(R.id.w_root, Widgets.open(ctx, "today", 7140))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, TodayWidget::class.java) { views(ctx) }
    }
}

/** A focus sprint from the home screen: a live countdown, and the app is told when it ends (the xp is applied when the app opens). */
class FocusWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) { for (id in ids) mgr.updateAppWidget(id, views(ctx)) }
    companion object {
        internal fun views(ctx: Context): RemoteViews {
            val v = RemoteViews(ctx.packageName, R.layout.widget_focus)
            val end = Store.focusEnd(ctx)
            val running = end > System.currentTimeMillis()
            val accent = Widgets.accent(ctx)
            v.setTextColor(R.id.w_b1, accent)
            if (running) {
                val base = SystemClock.elapsedRealtime() + (end - System.currentTimeMillis())
                v.setChronometer(R.id.w_chrono, base, null, true)
                if (android.os.Build.VERSION.SDK_INT >= 24) v.setChronometerCountDown(R.id.w_chrono, true)
                v.setTextColor(R.id.w_chrono, accent)
                v.setViewVisibility(R.id.w_chrono, View.VISIBLE); v.setViewVisibility(R.id.w_ready, View.GONE)
                v.setTextViewText(R.id.w_sub, "${Store.focusMinutes(ctx)} minute sprint. I will tell you when it is over.")
                v.setTextViewText(R.id.w_b1, "Stop"); v.setTextViewText(R.id.w_b2, "")
                v.setOnClickPendingIntent(R.id.w_b1, Widgets.act(ctx, Widgets.ACT_FOCUS_STOP, 7150))
                v.setOnClickPendingIntent(R.id.w_b2, Widgets.act(ctx, Widgets.ACT_FOCUS_STOP, 7151))
            } else {
                v.setChronometer(R.id.w_chrono, SystemClock.elapsedRealtime(), null, false)
                v.setViewVisibility(R.id.w_chrono, View.GONE); v.setViewVisibility(R.id.w_ready, View.VISIBLE)
                v.setTextViewText(R.id.w_sub, "Finishing a sprint earns xp.")
                v.setTextViewText(R.id.w_b1, "Start 25"); v.setTextViewText(R.id.w_b2, "Break 5")
                v.setOnClickPendingIntent(R.id.w_b1, Widgets.act(ctx, Widgets.ACT_FOCUS, 7152, minutes = 25))
                v.setOnClickPendingIntent(R.id.w_b2, Widgets.act(ctx, Widgets.ACT_FOCUS, 7153, minutes = 5))
            }
            v.setOnClickPendingIntent(R.id.w_root, Widgets.open(ctx, "today", 7154))
            return v
        }
        fun refresh(ctx: Context) = redraw(ctx, FocusWidget::class.java) { views(ctx) }
    }
}
