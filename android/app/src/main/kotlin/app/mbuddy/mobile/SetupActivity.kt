package app.mbuddy.mobile

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

/** The launcher entry. The app works on its own, so there is nothing to set up first: it goes straight in (pairing with a PC is in Settings). */
class SetupActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // a widget, a launcher shortcut or a notification can ask for a place: it is handed to the page as an inbox entry
        intent?.getStringExtra("route")?.takeIf { it.isNotBlank() }?.let { Inbox.add(this, org.json.JSONObject().put("type", "go").put("to", it)) }
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}
