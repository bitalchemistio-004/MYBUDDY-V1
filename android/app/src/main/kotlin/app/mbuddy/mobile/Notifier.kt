package app.mbuddy.mobile

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Notifications: what MBUDDY wants done on the phone, as a button. Tapping it does the action (Android allows that). */
object Notifier {
    const val CHANNEL = "mbuddy_actions"

    fun createChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL, "From MBUDDY", NotificationManager.IMPORTANCE_HIGH)
            channel.description = "Things MBUDDY asked your phone to do: an alarm, a timer, a call, directions. Tap to do it."
            ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    fun offer(ctx: Context, id: Int, text: String, intent: Intent) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val pending = PendingIntent.getActivity(ctx, id, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("MBUDDY")
            .setContentText("$text. Tap to do it.")
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        NotificationManagerCompat.from(ctx).notify(id, n)
    }
}
