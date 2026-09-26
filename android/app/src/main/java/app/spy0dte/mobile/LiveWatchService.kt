package app.spy0dte.mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

class LiveWatchService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private var token: String? = null
    private var socket: WebSocket? = null
    private var lastAlertKey: String? = null
    private var stopped = false

    override fun onCreate() {
        super.onCreate()
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nextToken = intent?.getStringExtra(EXTRA_ID_TOKEN)?.takeIf { it.isNotBlank() }
        if (nextToken != null) token = nextToken
        val idToken = token
        if (idToken == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        stopped = false
        startForeground(FOREGROUND_ID, watcherNotification("Watching for SPY 0DTE strategy alerts"))
        socket?.close(1000, "refreshing watcher")
        connect(idToken)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        stopped = true
        handler.removeCallbacksAndMessages(null)
        socket?.close(1000, "watcher stopped")
        socket = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun connect(idToken: String) {
        val base = BuildConfig.API_BASE_URL.trim().trimEnd('/')
        if (base.isBlank()) return
        val wsBase = when {
            base.startsWith("https://") -> "wss://${base.removePrefix("https://")}"
            base.startsWith("http://") -> "ws://${base.removePrefix("http://")}"
            else -> base
        }
        val request = Request.Builder()
            .url("$wsBase/v1/live")
            .header("Authorization", "Bearer $idToken")
            .build()
        socket = http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    updateForeground("Connected — watching for strategy alerts")
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    handleStatus(text)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    updateForeground("Connection interrupted — retrying")
                    scheduleReconnect(idToken)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (!stopped) scheduleReconnect(idToken)
                }
            },
        )
    }

    private fun scheduleReconnect(idToken: String) {
        if (stopped) return
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ if (!stopped) connect(idToken) }, 3_000L)
    }

    private fun handleStatus(text: String) {
        val root = runCatching { JSONObject(text) }.getOrNull() ?: return
        val alert = root.optJSONObject("live_alert") ?: return
        if (!alert.optBoolean("action_required", false)) return
        val generatedAt = alert.optString("generated_at")
        val signal = alert.optJSONObject("signal") ?: JSONObject()
        val symbol = signal.optString("symbol", "SPY 0DTE")
        val risk = alert.optJSONObject("risk")
        val contracts = risk?.optInt("contracts", 0) ?: 0
        val key = "$generatedAt|$symbol|$contracts"
        if (key == lastAlertKey) return
        lastAlertKey = key

        val title = "SPY 0DTE strategy alert"
        val body = buildString {
            append(symbol)
            if (contracts > 0) append(" • $contracts contract(s)")
            val reason = alert.optString("reason")
            if (reason.isNotBlank()) append(" • Tap to review")
        }
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(key.hashCode(), approvalNotification(title, body))
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                WATCH_CHANNEL,
                "SPY 0DTE watcher",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
        manager.createNotificationChannel(
            NotificationChannel(
                ALERT_CHANNEL,
                "SPY 0DTE strategy alerts",
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )
    }

    private fun contentIntent(): PendingIntent {
        val intent = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        return PendingIntent.getActivity(
            this,
            100,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun watcherNotification(text: String) = NotificationCompat.Builder(this, WATCH_CHANNEL)
        .setSmallIcon(android.R.drawable.ic_popup_sync)
        .setContentTitle("SPY 0DTE")
        .setContentText(text)
        .setOngoing(true)
        .setContentIntent(contentIntent())
        .build()

    private fun approvalNotification(title: String, text: String) =
        NotificationCompat.Builder(this, ALERT_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(contentIntent())
            .build()

    private fun updateForeground(text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(FOREGROUND_ID, watcherNotification(text))
    }

    companion object {
        const val EXTRA_ID_TOKEN = "id_token"
        private const val WATCH_CHANNEL = "spy0dte_watch"
        private const val ALERT_CHANNEL = "spy0dte_strategy_alerts"
        private const val FOREGROUND_ID = 7001
    }
}
