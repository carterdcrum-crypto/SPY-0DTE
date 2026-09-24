package app.spy0dte.mobile

import android.app.Activity
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject

class PreviewActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    WorkingPreviewApp(this)
                }
            }
        }
    }
}

private data class PreviewStatus(
    val mode: String = "PAPER",
    val decision: String = "CONNECTING",
    val decisionReason: String = "Connecting to Railway backend",
    val strategy: String = "bootstrap_momentum_v1",
    val spot: Double? = null,
    val rows: Int = 0,
    val dataAgeSeconds: Double? = null,
    val feedDelaySeconds: Double? = null,
    val engineTickSeconds: Double = 1.0,
    val optionRefreshSeconds: Double = 2.0,
    val fullChainRefreshSeconds: Double = 60.0,
    val paperStartingCash: Double = 115.0,
    val paperSettledCash: Double = 115.0,
    val paperUnsettledCash: Double = 0.0,
    val paperRealizedPnl: Double = 0.0,
    val paperOpenPositions: Int = 0,
    val paperTradeCount: Int = 0,
    val positionSymbol: String? = null,
    val positionQuantity: Int = 0,
    val positionAverageCost: Double? = null,
    val positionMarkValue: Double? = null,
    val positionUnrealizedPnl: Double? = null,
    val positionEntrySpot: Double? = null,
    val positionStopPrice: Double? = null,
    val positionTargetPrice: Double? = null,
    val positionHeldSeconds: Double? = null,
    val positionMaxHoldSeconds: Int? = null,
    val closedTradeSymbol: String? = null,
    val closedTradeQuantity: Int = 0,
    val closedTradeFillPrice: Double? = null,
    val closedTradeRealizedPnl: Double? = null,
    val closedTradeReason: String? = null,
    val closedTradeSettlementDate: String? = null,
)

private class PreviewBackend {
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val baseUrl = BuildConfig.API_BASE_URL.trim().trimEnd('/')

    fun connectLive(
        onStatus: (PreviewStatus) -> Unit,
        onError: (String) -> Unit,
    ): WebSocket? {
        if (baseUrl.isBlank()) {
            onError("Backend URL is not configured")
            return null
        }
        val wsBase = when {
            baseUrl.startsWith("https://") -> "wss://${baseUrl.removePrefix("https://")}"
            baseUrl.startsWith("http://") -> "ws://${baseUrl.removePrefix("http://")}"
            else -> baseUrl
        }
        val request = Request.Builder().url("$wsBase/v1/live").build()
        return http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    try {
                        onStatus(parsePreviewStatus(text))
                    } catch (error: Exception) {
                        onError("Bad backend message: ${error.message}")
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onError(t.message ?: "Live connection failed")
                }
            },
        )
    }

    suspend fun setMode(mode: String): Result<Unit> = withContext(Dispatchers.IO) {
        if (mode == "LIVE") {
            return@withContext Result.failure(IllegalStateException("LIVE is locked while login is off"))
        }
        post("/v1/mode", JSONObject().put("mode", mode))
    }

    suspend fun resetPaperAccount(startingCash: Double): Result<Unit> = withContext(Dispatchers.IO) {
        post(
            "/v1/paper/reset",
            JSONObject()
                .put("starting_cash", startingCash)
                .put("confirmation", "RESET PAPER ACCOUNT"),
        )
    }

    private fun post(path: String, json: JSONObject): Result<Unit> {
        if (baseUrl.isBlank()) {
            return Result.failure(IllegalStateException("Backend URL is not configured"))
        }
        val request = Request.Builder()
            .url("$baseUrl$path")
            .post(json.toString().toRequestBody("application/json".toMediaType()))
            .build()
        return try {
            http.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    Result.success(Unit)
                } else {
                    Result.failure(
                        IllegalStateException(response.body?.string()?.take(220) ?: "HTTP ${response.code}"),
                    )
                }
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }
}

private fun parsePreviewStatus(text: String): PreviewStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market") ?: JSONObject()
    val cadence = root.optJSONObject("cadence") ?: JSONObject()
    val decision = root.optJSONObject("decision") ?: JSONObject()
    val paper = root.optJSONObject("paper") ?: JSONObject()
    val automation = root.optJSONObject("paper_automation") ?: JSONObject()
    val position = automation.optJSONObject("position")
    val closedTrade = automation.optJSONObject("closed_trade")

    fun nullableDouble(obj: JSONObject?, key: String): Double? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

    fun nullableInt(obj: JSONObject?, key: String): Int? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optInt(key)

    fun nullableString(obj: JSONObject?, key: String): String? =
        obj?.optString(key)?.takeIf { it.isNotBlank() }

    return PreviewStatus(
        mode = root.optString("mode", "PAPER"),
        decision = decision.optString("state", automation.optString("state", "RESEARCH_ONLY")),
        decisionReason = decision.optString("reason", automation.optString("reason", "")),
        strategy = automation.optString("strategy", "bootstrap_momentum_v1"),
        spot = nullableDouble(market, "spot"),
        rows = market.optInt("rows", 0),
        dataAgeSeconds = nullableDouble(market, "data_age_seconds"),
        feedDelaySeconds = nullableDouble(market, "feed_delay_seconds"),
        engineTickSeconds = cadence.optDouble("engine_tick_seconds", 1.0),
        optionRefreshSeconds = cadence.optDouble("sandbox_active_option_refresh_seconds", 2.0),
        fullChainRefreshSeconds = cadence.optDouble("full_chain_refresh_seconds", 60.0),
        paperStartingCash = paper.optDouble("starting_cash", 115.0),
        paperSettledCash = paper.optDouble("settled_cash", 115.0),
        paperUnsettledCash = paper.optDouble("unsettled_cash", 0.0),
        paperRealizedPnl = paper.optDouble("realized_pnl", 0.0),
        paperOpenPositions = paper.optInt("open_positions", 0),
        paperTradeCount = paper.optInt("trade_count", 0),
        positionSymbol = nullableString(position, "symbol"),
        positionQuantity = position?.optInt("quantity", 0) ?: 0,
        positionAverageCost = nullableDouble(position, "average_cost"),
        positionMarkValue = nullableDouble(position, "mark_value"),
        positionUnrealizedPnl = nullableDouble(position, "unrealized_pnl"),
        positionEntrySpot = nullableDouble(position, "entry_spot"),
        positionStopPrice = nullableDouble(position, "stop_price"),
        positionTargetPrice = nullableDouble(position, "target_price"),
        positionHeldSeconds = nullableDouble(position, "held_seconds"),
        positionMaxHoldSeconds = nullableInt(position, "max_hold_seconds"),
        closedTradeSymbol = nullableString(closedTrade, "symbol"),
        closedTradeQuantity = closedTrade?.optInt("quantity", 0) ?: 0,
        closedTradeFillPrice = nullableDouble(closedTrade, "fill_price"),
        closedTradeRealizedPnl = nullableDouble(closedTrade, "realized_pnl"),
        closedTradeReason = nullableString(closedTrade, "reason"),
        closedTradeSettlementDate = nullableString(closedTrade, "settlement_date"),
    )
}

@Composable
private fun WorkingPreviewApp(activity: Activity) {
    val backend = remember { PreviewBackend() }
    val reconnectScope = rememberCoroutineScope()
    var status by remember { mutableStateOf(PreviewStatus()) }
    var connectionError by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf("LIVE") }
    var reconnectNonce by remember { mutableIntStateOf(0) }
    var retryAttempt by remember { mutableIntStateOf(0) }
    var reconnectJob by remember { mutableStateOf<Job?>(null) }

    DisposableEffect(reconnectNonce) {
        var disposed = false
        val socket = backend.connectLive(
            onStatus = { next ->
                activity.runOnUiThread {
                    if (!disposed) {
                        status = next
                        connectionError = null
                        retryAttempt = 0
                        reconnectJob?.cancel()
                        reconnectJob = null
                    }
                }
            },
            onError = { message ->
                activity.runOnUiThread {
                    if (!disposed) {
                        connectionError = message
                        reconnectJob?.cancel()
                        val delayMillis = 1_000L * (1L shl minOf(retryAttempt, 5))
                        retryAttempt = minOf(retryAttempt + 1, 6)
                        reconnectJob = reconnectScope.launch {
                            delay(delayMillis)
                            reconnectNonce += 1
                        }
                    }
                }
            },
        )
        onDispose {
            disposed = true
            reconnectJob?.cancel()
            socket?.close(1000, "reconnecting or app closed")
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text("SPY 0DTE", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text(if (connectionError == null) "● PAPER ENGINE CONNECTED" else "◌ RECONNECTING TO BACKEND")
            }
            Text(status.mode, fontWeight = FontWeight.Bold)
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf("LIVE", "SETTINGS").forEach { name ->
                if (tab == name) Button(onClick = { tab = name }) { Text(name) }
                else OutlinedButton(onClick = { tab = name }) { Text(name) }
            }
        }

        if (tab == "LIVE") {
            PreviewLive(status, connectionError, backend)
        } else {
            PreviewSettings(status, backend)
        }
    }
}

@Composable
private fun PreviewLive(
    status: PreviewStatus,
    connectionError: String?,
    backend: PreviewBackend,
) {
    val scope = rememberCoroutineScope()
    var actionMessage by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let {
            Text(
                "$it — retrying automatically",
                color = MaterialTheme.colorScheme.error,
            )
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.fillMaxWidth().padding(14.dp)) {
                Text("LOGIN TEMPORARILY OFF", fontWeight = FontWeight.Bold)
                Text("Connected to the real Railway paper engine. LIVE stays server-locked until authentication is restored.")
            }
        }

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            PreviewMetric("SPY", status.spot?.let { "$%.2f".format(it) } ?: "—")
            PreviewMetric("Data", status.dataAgeSeconds?.let { "%.1fs".format(it) } ?: "—")
            PreviewMetric("Engine", "${status.engineTickSeconds.toInt()}s")
            PreviewMetric("Rows", status.rows.toString())
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("CURRENT DECISION", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(8.dp))
                Text(status.decision, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(status.decisionReason)
                Text("Strategy: ${status.strategy}")
                Text("Feed delay: ${status.feedDelaySeconds?.let { "%.0fs".format(it) } ?: "—"}")
            }
        }

        if (status.positionSymbol != null && status.paperOpenPositions > 0) {
            Card {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text("ACTIVE PAPER POSITION", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Text(status.positionSymbol, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text("Quantity: ${status.positionQuantity}")
                    Text("Entry cost: ${money(status.positionAverageCost)}")
                    Text("Live mark: ${money(status.positionMarkValue)}")
                    val pnl = status.positionUnrealizedPnl
                    Text(
                        "Unrealized P&L: ${signedMoney(pnl)}",
                        color = if ((pnl ?: 0.0) < 0.0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                    )
                    Text("SPY at entry: ${status.positionEntrySpot?.let { "%.2f".format(it) } ?: "—"}")
                    Text("Stop: ${money(status.positionStopPrice)}")
                    Text("Target: ${money(status.positionTargetPrice)}")
                    Text(
                        "Held: ${status.positionHeldSeconds?.let { "%.0fs".format(it) } ?: "—"} / " +
                            "${status.positionMaxHoldSeconds?.let { "${it}s" } ?: "—"}",
                    )
                    Text("Exit monitoring is automatic in PAPER mode.")
                }
            }
        } else {
            Card {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text("ACTIVE PAPER POSITION", fontWeight = FontWeight.Bold)
                    Text("No open position. The engine is scanning today's SPY 0DTE contracts.")
                }
            }
        }

        if (status.closedTradeSymbol != null) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text("LAST CLOSED TRADE", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Text(status.closedTradeSymbol, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Text("Quantity: ${status.closedTradeQuantity}")
                    Text("Exit fill: ${money(status.closedTradeFillPrice)}")
                    val realized = status.closedTradeRealizedPnl
                    Text(
                        "Realized P&L: ${signedMoney(realized)}",
                        color = if ((realized ?: 0.0) < 0.0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                    )
                    Text("Exit reason: ${status.closedTradeReason ?: "—"}")
                    Text("Settlement: ${status.closedTradeSettlementDate ?: "—"}")
                }
            }
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("PAPER ACCOUNT", fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(8.dp))
                Text("Starting cash: $${"%.2f".format(status.paperStartingCash)}")
                Text("Settled cash: $${"%.2f".format(status.paperSettledCash)}")
                Text("Unsettled cash: $${"%.2f".format(status.paperUnsettledCash)}")
                Text("Realized P&L: ${signedMoney(status.paperRealizedPnl)}")
                Text("Open positions: ${status.paperOpenPositions}")
                Text("Trades: ${status.paperTradeCount}")
            }
        }

        Text("MODE", fontWeight = FontWeight.Bold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("SHADOW", "PAPER").forEach { item ->
                if (status.mode == item) {
                    Button(onClick = {}) { Text(item) }
                } else {
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                backend.setMode(item)
                                    .onSuccess { actionMessage = "$item selected on backend" }
                                    .onFailure { actionMessage = it.message }
                            }
                        },
                    ) { Text(item) }
                }
            }
            OutlinedButton(onClick = {}, enabled = false) { Text("LIVE 🔒") }
        }
        actionMessage?.let { Text(it) }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("RUNTIME", fontWeight = FontWeight.Bold)
                Text("Decision loop: every ${status.engineTickSeconds}s")
                Text("Active option refresh: every ${status.optionRefreshSeconds}s")
                Text("Full chain refresh: every ${status.fullChainRefreshSeconds}s")
                Text("Backend: ${BuildConfig.API_BASE_URL}")
            }
        }
    }
}

private fun money(value: Double?): String = value?.let { "$${"%.2f".format(it)}" } ?: "—"

private fun signedMoney(value: Double?): String = value?.let {
    val sign = if (it >= 0.0) "+" else "-"
    "$sign$${"%.2f".format(kotlin.math.abs(it))}"
} ?: "—"

@Composable
private fun PreviewMetric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun PreviewSettings(status: PreviewStatus, backend: PreviewBackend) {
    val scope = rememberCoroutineScope()
    var paperCash by remember(status.paperStartingCash) {
        mutableStateOf("%.2f".format(status.paperStartingCash))
    }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Paper account", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        OutlinedTextField(
            value = paperCash,
            onValueChange = { paperCash = it.filter { ch -> ch.isDigit() || ch == '.' } },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Starting paper cash") },
            singleLine = true,
        )
        Button(
            onClick = {
                val amount = paperCash.toDoubleOrNull()
                if (amount == null || amount <= 0.0) {
                    message = "Enter a valid paper balance"
                } else {
                    scope.launch {
                        backend.resetPaperAccount(amount)
                            .onSuccess { message = "Paper account reset to $${"%.2f".format(amount)}" }
                            .onFailure { message = it.message ?: "Paper reset failed" }
                    }
                }
            },
            enabled = (paperCash.toDoubleOrNull() ?: 0.0) > 0.0,
        ) { Text("Reset paper account") }
        Text("This changes the simulated paper account on Railway; it does not touch real money.")

        Spacer(Modifier.height(8.dp))
        Text("Broker settings", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("Temporarily locked while login is off", fontWeight = FontWeight.Bold)
                Text("Webull keys are not accepted by the anonymous preview backend. Credential entry can return after the app UI is approved and authentication is restored.")
            }
        }

        message?.let { Text(it) }
    }
}
