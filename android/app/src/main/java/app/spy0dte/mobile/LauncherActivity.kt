package app.spy0dte.mobile

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity

class LauncherActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Always open the full app shell. When Google OAuth is not configured,
        // MainActivity falls back to the existing login-free preview backend
        // while still exposing the LIVE workspace and setup/status controls.
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}
