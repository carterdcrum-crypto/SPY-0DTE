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

class RailwayWatchService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private var socket: WebSocket? = null
    private var stopped = false
    private var lastEventKey: String? = null

    override fun onCreate() {
        super.onCreate()
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        stopped = false
        startForeground(
            FOREGROUND_ID,
            watcherNotification("Railway autonomous engine connected"),
        )
        socket?.close(1000, "refreshing Railway watcher")
        connect()
        return START_STICKY
    }

    override fun onDestroy() {
        stopped = true
        handler.removeCallbacksAndMessages(null)
        socket?.close(1000, "Railway watcher stopped")
        socket = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun connect() {
        val base = BuildConfig.API_BASE_URL.trim().trimEnd('/')
        if (base.isBlank()) {
            updateForeground("Backend URL is not configured")
            return
        }
        val wsBase = when {
            base.startsWith("https://") -> "wss://${base.removePrefix("https://")}"
            base.startsWith("http://") -> "ws://${base.removePrefix("http://")}"
            else -> base
        }
        val request = Request.Builder()
            .url("$wsBase/v1/live")
            .header("Authorization", "Bearer preview")
            .build()
        socket = http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    updateForeground("Connected — watching Railway trader")
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    handleStatus(text)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    updateForeground("Connection interrupted — retrying")
                    scheduleReconnect()
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    if (!stopped) scheduleReconnect()
                }
            },
        )
    }

    private fun scheduleReconnect() {
        if (stopped) return
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ if (!stopped) connect() }, 3_000L)
    }

    private fun handleStatus(text: String) {
        val root = runCatching { JSONObject(text) }.getOrNull() ?: return

        val liveAlert = root.optJSONObject("live_alert")
        if (liveAlert != null && liveAlert.optBoolean("action_required", false)) {
            val signal = liveAlert.optJSONObject("signal") ?: JSONObject()
            val risk = liveAlert.optJSONObject("risk") ?: JSONObject()
            val symbol = signal.optString("symbol", "SPY 0DTE")
            val contracts = risk.optInt("contracts", 0)
            val generated = liveAlert.optString("generated_at")
            notifyOnce(
                key = "LIVE|$generated|$symbol|$contracts",
                title = "SPY 0DTE live strategy alert",
                body = buildString {
                    append(symbol)
                    if (contracts > 0) append(" • $contracts contract(s)")
                    append(" • Tap to review")
                },
            )
            return
        }

        val automation = root.optJSONObject("paper_automation") ?: return
        val state = automation.optString("state")
        val tick = automation.optString("last_tick")
        val signal = automation.optJSONObject("last_signal")
        val position = automation.optJSONObject("position")
        val closed = automation.optJSONObject("closed_trade")

        when (state) {
            "POSITION_OPENED" -> {
                val symbol = position?.optString("symbol")
                    ?.takeIf { it.isNotBlank() }
                    ?: signal?.optString("symbol")?.takeIf { it.isNotBlank() }
                    ?: "SPY 0DTE"
                val quantity = position?.optInt("quantity", 0) ?: 0
                notifyOnce(
                    key = "OPEN|$tick|$symbol|$quantity",
                    title = "Railway trader opened a paper position",
                    body = "$symbol${if (quantity > 0) " • $quantity contract(s)" else ""}",
                )
            }
            "POSITION_CLOSED" -> {
                val symbol = closed?.optString("symbol")?.takeIf { it.isNotBlank() } ?: "SPY 0DTE"
                val pnl = if (closed != null && closed.has("realized_pnl") && !closed.isNull("realized_pnl")) {
                    "$${"%.2f".format(closed.optDouble("realized_pnl"))}"
                } else {
                    "P&L unavailable"
                }
                notifyOnce(
                    key = "CLOSE|$tick|$symbol|$pnl",
                    title = "Railway trader closed its paper position",
                    body = "$symbol • $pnl",
                )
            }
            "SHADOW_SIGNAL" -> {
                val symbol = signal?.optString("symbol")?.takeIf { it.isNotBlank() } ?: "SPY 0DTE"
                notifyOnce(
                    key = "SIGNAL|$tick|$symbol",
                    title = "Railway strategy signal",
                    body = "$symbol • Qualified setup ready to review",
                )
            }
        }
    }

    private fun notifyOnce(key: String, title: String, body: String) {
        if (key == lastEventKey) return
        lastEventKey = key
        getSystemService(NotificationManager::class.java)
            .notify(key.hashCode(), eventNotification(title, body))
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                WATCH_CHANNEL,
                "SPY 0DTE Railway watcher",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
        manager.createNotificationChannel(
            NotificationChannel(
                EVENT_CHANNEL,
                "SPY 0DTE trader alerts",
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )
    }

    private fun contentIntent(): PendingIntent {
        val intent = Intent(this, RailwayLiveActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        return PendingIntent.getActivity(
            this,
            200,
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

    private fun eventNotification(title: String, body: String) =
        NotificationCompat.Builder(this, EVENT_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(contentIntent())
            .build()

    private fun updateForeground(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(FOREGROUND_ID, watcherNotification(text))
    }

    companion object {
        private const val WATCH_CHANNEL = "spy0dte_railway_watch"
        private const val EVENT_CHANNEL = "spy0dte_railway_events"
        private const val FOREGROUND_ID = 7101
    }
}
