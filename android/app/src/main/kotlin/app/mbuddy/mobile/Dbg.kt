package app.mbuddy.mobile

/**
 * Logging for lines that carry what the owner said, typed or was answered. In a debug build (a developer's phone, the test suite) the
 * content is logged; in a release build only its length is, so nothing private is readable from the system log.
 */
object Dbg {
    @Volatile var debuggable = false
    fun i(tag: String, msg: String) { android.util.Log.i(tag, if (debuggable) msg else "(${msg.length} characters)") }
}
