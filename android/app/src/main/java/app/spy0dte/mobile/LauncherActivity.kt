package app.spy0dte.mobile

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity

class LauncherActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val destination = if (BuildConfig.GOOGLE_WEB_CLIENT_ID.isBlank()) {
            PreviewActivity::class.java
        } else {
            MainActivity::class.java
        }
        startActivity(Intent(this, destination))
        finish()
    }
}
