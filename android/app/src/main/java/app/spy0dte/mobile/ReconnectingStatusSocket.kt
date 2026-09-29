package app.spy0dte.mobile

import android.os.Handler
import android.os.Looper
import java.util.concurrent.atomic.AtomicBoolean
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

internal fun connectStatusSocket(
    http: OkHttpClient,
    request: Request,
    onMessage: (String) -> Unit,
    onError: (String) -> Unit,
): AutoCloseable {
    val handler = Handler(Looper.getMainLooper())
    val stopped = AtomicBoolean(false)
    var socket: WebSocket? = null
    var generation = 0
    var failures = 0
    lateinit var open: () -> Unit

    fun retry(epoch: Int, message: String) {
        if (stopped.get() || epoch != generation) return
        generation += 1
        onError(message)
        socket?.cancel()
        failures += 1
        val delay = minOf(30_000L, 1_000L * (1L shl minOf(failures - 1, 5)))
        handler.postDelayed({ if (!stopped.get()) open() }, delay)
    }

    open = {
        val epoch = ++generation
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                handler.post {
                    if (!stopped.get() && epoch == generation) {
                        runCatching { onMessage(text) }
                            .onSuccess { failures = 0 }
                            .onFailure { retry(epoch, "Could not read engine status; reconnecting") }
                    }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                handler.post { retry(epoch, "Connection interrupted; reconnecting automatically") }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                handler.post {
                    if (code == 4401 && !stopped.get() && epoch == generation) {
                        stopped.set(true)
                        onError("Owner session expired. Sign in again. Server automation continues.")
                    } else {
                        retry(epoch, "Engine connection closed; reconnecting automatically")
                    }
                }
            }
        })
    }
    open()
    return AutoCloseable {
        stopped.set(true)
        handler.removeCallbacksAndMessages(null)
        socket?.cancel()
    }
}
