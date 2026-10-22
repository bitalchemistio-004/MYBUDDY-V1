package app.mbuddy.mobile

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Asks the PC what it wants done on the phone ("set a timer on my phone", "remind my phone...") and does it:
 * a calendar event is written silently; the rest become a notification you tap. Runs about every 15 minutes (the shortest
 * Android allows for background work), and at once whenever the app is opened.
 */
class ActionsWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val ctx = applicationContext
        if (!Store.paired(ctx)) return Result.success()
        val reply = Pc.request(ctx, "GET", "/remote/ui/phone-actions")
        if (reply.status != 200) return Result.success()            // the PC is off or away: nothing to do, try again next time
        val actions = runCatching { JSONObject(reply.body).optJSONArray("actions") }.getOrNull() ?: return Result.success()
        for (i in 0 until actions.length()) {
            val a = actions.getJSONObject(i)
            val id = a.getString("id")
            Actions.execute(ctx, a.getString("kind"), a.getJSONObject("params"), onScreen = false, notificationId = id.hashCode())
            // Told to the user (done, or put in a notification): it is not offered again.
            Pc.request(ctx, "POST", "/remote/ui/phone-actions", JSONObject().put("id", id).put("status", "done").toString())
        }
        return Result.success()
    }

    companion object {
        private const val NAME = "mbuddy_actions"

        fun schedule(ctx: Context) {
            val network = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                NAME, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<ActionsWorker>(15, TimeUnit.MINUTES).setConstraints(network).build(),
            )
        }

        fun runNow(ctx: Context) {
            WorkManager.getInstance(ctx).enqueue(OneTimeWorkRequestBuilder<ActionsWorker>().build())
        }
    }
}
