package app.mbuddy.mobile

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Screen control: lets MBUDDY see the screen of the app in front and tap and type in it, ONLY while a task the owner asked for is
 * running (Agent.kt). The owner turns this on in Android settings (Accessibility); turning it off there stops everything at once.
 * It listens to nothing by itself: no events are read, nothing happens unless Agent has been started by the owner's own request.
 */
class ControlService : AccessibilityService() {
    val overlay by lazy { Overlay(this) }
    val handle by lazy { EdgeHandle(this) }

    override fun onServiceConnected() { super.onServiceConnected(); instance = this; Store.setControlWasOn(this, true); handle.refresh(); Smart.reschedule(this) }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) { /* deliberately empty: the agent looks when it needs to */ }
    // Both volume keys pressed together (within half a second) call the buddy from anywhere, the screen on or the app closed, with no
    // microphone open until then. Only when the owner turned it on (Settings, Calling me). The keys are read for nothing else.
    private var upAt = 0L
    private var downAt = 0L
    private var chordUntil = 0L
    override fun onKeyEvent(event: android.view.KeyEvent): Boolean {
        val code = event.keyCode
        if (code != android.view.KeyEvent.KEYCODE_VOLUME_UP && code != android.view.KeyEvent.KEYCODE_VOLUME_DOWN) return false
        if (!Store.chord(this) || Privacy.on(this)) return false
        val now = System.currentTimeMillis()
        if (event.action == android.view.KeyEvent.ACTION_DOWN) {
            if (code == android.view.KeyEvent.KEYCODE_VOLUME_UP) upAt = now else downAt = now
            if (upAt > 0 && downAt > 0 && Math.abs(upAt - downAt) < 500 && now > chordUntil) {
                chordUntil = now + 4000; upAt = 0; downAt = 0
                callBuddy()
                return true
            }
        }
        return now < chordUntil          // the keys of the chord itself do not change the volume
    }

    private fun callBuddy() {
        android.util.Log.i("MBuddyChord", "both volume keys: calling the buddy")
        val assistant = Build.VERSION.SDK_INT >= 29 && runCatching { getSystemService(android.app.role.RoleManager::class.java).isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT) }.getOrDefault(false)
        // As the assistant, the phone itself opens the buddy's session (the same as holding the power button), which may use the microphone from the background.
        if (assistant && runCatching { startActivity(Intent(Intent.ACTION_ASSIST).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true }.getOrDefault(false)) return
        Thread { runCatching { Command.run(applicationContext) } }.start()
    }

    override fun onInterrupt() { Agent.stop("interrupted") }
    override fun onUnbind(intent: Intent?): Boolean {
        Agent.stop("screen control was turned off")
        overlay.hide(); handle.hide()
        instance = null
        return super.onUnbind(intent)
    }

    /** The window in front, or null: with privacy mode on the screen is not looked at, whoever asks. */
    fun root(): AccessibilityNodeInfo? = if (Privacy.on(this)) null else rootInActiveWindow

    fun snapshot(): Snapshot? = Screen.read(root())

    fun back() = performGlobalAction(GLOBAL_ACTION_BACK)
    fun home() = performGlobalAction(GLOBAL_ACTION_HOME)

    /** Tap: the element itself, or the nearest thing around it that can be tapped. */
    fun tap(node: AccessibilityNodeInfo): Boolean {
        var n: AccessibilityNodeInfo? = node
        var hops = 0
        while (n != null && hops < 6) {
            if (n.isEnabled && n.isClickable && n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true
            n = n.parent; hops++
        }
        return false
    }

    fun type(node: AccessibilityNodeInfo, text: String): Boolean {
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().putCharSequenceArg(text)
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun scroll(snap: Snapshot, down: Boolean): Boolean {
        val target = snap.els.firstOrNull { it.node.isScrollable }?.node ?: return false
        return target.performAction(if (down) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
    }

    private fun Bundle.putCharSequenceArg(text: String): Bundle {
        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        return this
    }

    companion object {
        @Volatile var instance: ControlService? = null

        /** Whether the owner has switched screen control on in Android settings. */
        fun enabled(ctx: Context): Boolean {
            val name = ComponentName(ctx, ControlService::class.java)
            val list = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            // Android writes the name in its short form (app.mbuddy.mobile/.ControlService) or in full, depending on the phone.
            return list.split(':').any { it.equals(name.flattenToString(), true) || it.equals(name.flattenToShortString(), true) }
        }
    }
}
