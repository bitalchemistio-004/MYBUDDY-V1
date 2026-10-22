package app.mbuddy.mobile

import android.app.ActivityManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import org.json.JSONObject

/** What this phone really is (a web page can only guess): used to say which on-device AI model fits, and whether AI Edge Gallery is installed. */
object DeviceInfo {
    fun json(ctx: Context): String {
        val memory = ActivityManager.MemoryInfo()
        ctx.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
        val stat = StatFs(Environment.getDataDirectory().path)
        val gallery = JSONObject().put("installed", false)
        try {
            val info = ctx.packageManager.getPackageInfo(Actions.GALLERY_PACKAGE, 0)
            gallery.put("installed", true).put("version", info.versionName ?: "")
        } catch (_: PackageManager.NameNotFoundException) {
            // not installed: the page offers it
        }
        return JSONObject()
            .put("android", true)
            .put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("sdk", Build.VERSION.SDK_INT)
            .put("ramMb", memory.totalMem / (1024 * 1024))
            .put("freeStorageMb", stat.availableBytes / (1024 * 1024))
            .put("abis", Build.SUPPORTED_ABIS.joinToString(","))
            .put("gallery", gallery)
            .toString()
    }
}
