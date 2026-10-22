package app.mbuddy.mobile

import android.app.Application

class MBuddyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Dbg.debuggable = applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0
        // a crash is kept on the phone (the owner can read or copy it in Settings, Your data): what failed and where, not what was said.
        // The message is left out of release builds because an exception message can carry the owner's words.
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, e ->
            runCatching {
                val nl = "\n"
                val frames = e.stackTrace.take(14).joinToString(nl) { "  at ${it.className}.${it.methodName}(${it.fileName}:${it.lineNumber})" }
                val msg = if (Dbg.debuggable) ": ${e.message?.take(200)}" else ""
                Store.setLastCrash(this, "${java.util.Date()} on ${thread.name}$nl${e.javaClass.name}$msg$nl$frames")
            }
            previous?.uncaughtException(thread, e)
        }
        Notifier.createChannel(this)
        ActionsWorker.schedule(this)         // harmless while not paired: the worker does nothing then
    }
}
