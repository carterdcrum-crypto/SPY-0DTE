package app.spy0dte.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
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

class RailwayLiveActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (
            Build.VERSION.SDK_INT >= 33 &&
            ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                1101,
            )
        }
        ContextCompat.startForegroundService(this, Intent(this, RailwayWatchService::class.java))
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    RailwayConsole(this)
                }
            }
        }
    }
}

private data class RailwayRisk(
    val armedToday: Boolean = false,
    val tradingDate: String? = null,
    val lossLimit: Double? = null,
    val gainLimit: Double? = null,
    val maxExposurePct: Double? = null,
    val maxContracts: Int? = null,
)

private data class RailwayStatus(
    val mode: String = "PAPER",
    val connected: Boolean = false,
    val engineRunning: Boolean = false,
    val autonomyArmed: Boolean = false,
    val execution: String = "local_paper_ledger",
    val decision: String = "CONNECTING",
    val reason: String = "Waiting for Railway",
    val strategy: String = "—",
    val riskProfile: String = "—",
    val exitProfile: String = "—",
    val spot: Double? = null,
    val dataAge: Double? = null,
    val feedDelay: Double? = null,
    val paperCash: Double? = null,
    val paperPnl: Double? = null,
    val paperPositions: Int = 0,
    val paperTrades: Int = 0,
    val signalSymbol: String? = null,
    val signalRight: String? = null,
    val riskContracts: Int = 0,
    val positionSymbol: String? = null,
    val positionQuantity: Int = 0,
    val positionUnrealizedPnl: Double? = null,
    val closedPnl: Double? = null,
    val tradierConfigured: Boolean = false,
    val liveOrderSubmission: Boolean = false,
    val liveWorkflow: String = "strategy_alert_then_owner_review",
    val liveAlertSymbol: String? = null,
    val liveAlertContracts: Int = 0,
    val risk: RailwayRisk = RailwayRisk(),
)

private class RailwayBackend {
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val baseUrl = BuildConfig.API_BASE_URL.trim().trimEnd('/')

    fun connect(
        onStatus: (RailwayStatus) -> Unit,
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
        val request = Request.Builder()
            .url("$wsBase/v1/live")
            .header("Authorization", "Bearer preview")
            .build()
        return http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { parseRailwayStatus(text) }
                        .onSuccess(onStatus)
                        .onFailure { onError("Bad Railway message: ${it.message}") }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onError(t.message ?: "Railway connection failed")
                }
            },
        )
    }

    suspend fun setPaperAutonomy(armed: Boolean): Result<Unit> = withContext(Dispatchers.IO) {
        post("/v1/paper/autonomy", JSONObject().put("armed", armed))
    }

    suspend fun armLiveRisk(
        dailyLoss: Double,
        dailyGain: Double,
        exposureFraction: Double,
        maxContracts: Int,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        post(
            "/v1/live/risk-envelope",
            JSONObject()
                .put("daily_loss_limit", dailyLoss)
                .put("daily_gain_limit", dailyGain)
                .put("max_account_exposure_pct", exposureFraction)
                .put("max_contracts", maxContracts)
                .put("confirmation", "ARM LIVE TODAY"),
        )
    }

    suspend fun disarmLiveRisk(): Result<Unit> = withContext(Dispatchers.IO) {
        post("/v1/live/risk-envelope/disarm", JSONObject())
    }

    private fun post(path: String, body: JSONObject): Result<Unit> {
        if (baseUrl.isBlank()) return Result.failure(IllegalStateException("Backend URL is not configured"))
        val request = Request.Builder()
            .url("$baseUrl$path")
            .header("Authorization", "Bearer preview")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        return try {
            http.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    Result.success(Unit)
                } else {
                    Result.failure(
                        IllegalStateException(response.body?.string()?.take(500) ?: "HTTP ${response.code}"),
                    )
                }
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }
}

private fun parseRailwayStatus(text: String): RailwayStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market") ?: JSONObject()
    val automation = root.optJSONObject("paper_automation") ?: JSONObject()
    val autonomy = root.optJSONObject("paper_autonomy") ?: JSONObject()
    val paper = root.optJSONObject("paper") ?: JSONObject()
    val signal = automation.optJSONObject("last_signal")
    val riskDecision = automation.optJSONObject("risk")
    val position = automation.optJSONObject("position")
    val closed = automation.optJSONObject("closed_trade")
    val liveBroker = root.optJSONObject("live_broker") ?: JSONObject()
    val liveAlert = root.optJSONObject("live_alert")
    val liveRisk = root.optJSONObject("live_risk") ?: JSONObject()
    val alertSignal = liveAlert?.optJSONObject("signal")
    val alertRisk = liveAlert?.optJSONObject("risk")

    fun doubleOrNull(obj: JSONObject?, key: String): Double? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

    fun stringOrNull(obj: JSONObject?, key: String): String? =
        obj?.optString(key)?.takeIf { it.isNotBlank() }

    return RailwayStatus(
        mode = root.optString("mode", "PAPER"),
        connected = true,
        engineRunning = autonomy.optBoolean("engine_running", false),
        autonomyArmed = autonomy.optBoolean("armed", false),
        execution = autonomy.optString("execution", "local_paper_ledger"),
        decision = automation.optString("state", "STARTING"),
        reason = automation.optString("reason", "Railway autonomous trader is starting"),
        strategy = automation.optString("strategy", "—"),
        riskProfile = automation.optString("risk_profile", "—"),
        exitProfile = automation.optString("exit_profile", "—"),
        spot = doubleOrNull(market, "spot"),
        dataAge = doubleOrNull(market, "data_age_seconds"),
        feedDelay = doubleOrNull(market, "feed_delay_seconds"),
        paperCash = doubleOrNull(paper, "settled_cash"),
        paperPnl = doubleOrNull(paper, "realized_pnl"),
        paperPositions = paper.optInt("open_positions", 0),
        paperTrades = paper.optInt("trade_count", 0),
        signalSymbol = stringOrNull(signal, "symbol"),
        signalRight = stringOrNull(signal, "right"),
        riskContracts = riskDecision?.optInt("contracts", 0) ?: 0,
        positionSymbol = stringOrNull(position, "symbol"),
        positionQuantity = position?.optInt("quantity", 0) ?: 0,
        positionUnrealizedPnl = doubleOrNull(position, "unrealized_pnl"),
        closedPnl = doubleOrNull(closed, "realized_pnl"),
        tradierConfigured = liveBroker.optBoolean("configured", false),
        liveOrderSubmission = liveBroker.optBoolean("real_order_submission", false),
        liveWorkflow = liveBroker.optString("workflow", "strategy_alert_then_owner_review"),
        liveAlertSymbol = stringOrNull(alertSignal, "symbol"),
        liveAlertContracts = alertRisk?.optInt("contracts", 0) ?: 0,
        risk = RailwayRisk(
            armedToday = liveRisk.optBoolean("armed_today", false),
            tradingDate = stringOrNull(liveRisk, "trading_date"),
            lossLimit = doubleOrNull(liveRisk, "daily_loss_limit"),
            gainLimit = doubleOrNull(liveRisk, "daily_gain_limit"),
            maxExposurePct = doubleOrNull(liveRisk, "max_account_exposure_pct"),
            maxContracts = if (liveRisk.has("max_contracts") && !liveRisk.isNull("max_contracts")) {
                liveRisk.optInt("max_contracts")
            } else {
                null
            },
        ),
    )
}

@Composable
private fun RailwayConsole(activity: RailwayLiveActivity) {
    val backend = remember { RailwayBackend() }
    var status by remember { mutableStateOf(RailwayStatus()) }
    var connectionError by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf("LIVE") }

    DisposableEffect(Unit) {
        val socket = backend.connect(
            onStatus = { next ->
                activity.runOnUiThread {
                    status = next
                    connectionError = null
                }
            },
            onError = { message ->
                activity.runOnUiThread { connectionError = message }
            },
        )
        onDispose { socket?.close(1000, "app closed") }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text("SPY 0DTE", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text(if (connectionError == null) "● RAILWAY ENGINE CONNECTED" else "○ RECONNECTING")
            }
            Text(status.mode, fontWeight = FontWeight.Bold)
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf("LIVE", "PAPER", "SETTINGS").forEach { name ->
                if (tab == name) Button(onClick = { tab = name }) { Text(name) }
                else OutlinedButton(onClick = { tab = name }) { Text(name) }
            }
        }

        when (tab) {
            "PAPER" -> PaperTab(status, backend, connectionError)
            "SETTINGS" -> SettingsTab(status, backend)
            else -> LiveTab(status, connectionError)
        }
    }
}

@Composable
private fun LiveTab(status: RailwayStatus, connectionError: String?) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("RAILWAY AUTONOMOUS BRAIN", fontWeight = FontWeight.Bold)
                Text(if (status.engineRunning) "RUNNING" else "STARTING / OFFLINE")
                Text("Strategy: ${status.strategy}")
                Text("Risk: ${status.riskProfile}")
                Text("Exit: ${status.exitProfile}")
                Text("Execution now: ${status.execution}")
            }
        }

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            ConsoleMetric("SPY", status.spot?.let { "$%.2f".format(it) } ?: "—")
            ConsoleMetric("Data", status.dataAge?.let { "%.1fs".format(it) } ?: "—")
            ConsoleMetric("Feed", status.feedDelay?.let { "%.0fs".format(it) } ?: "—")
            ConsoleMetric("Mode", status.mode)
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("CURRENT DECISION", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(6.dp))
                Text(status.decision, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Text(status.reason)
                if (status.signalSymbol != null) {
                    Spacer(Modifier.height(8.dp))
                    Text("Signal: ${status.signalSymbol} ${status.signalRight?.uppercase() ?: ""}")
                    Text("Model size: ${status.riskContracts} contract(s)")
                }
            }
        }

        if (status.liveAlertSymbol != null) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
                Column(Modifier.fillMaxWidth().padding(18.dp)) {
                    Text("LIVE STRATEGY ALERT", fontWeight = FontWeight.Bold)
                    Text(status.liveAlertSymbol)
                    Text("${status.liveAlertContracts} contract(s)")
                    Text("Phone review required before any broker submission.")
                }
            }
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("LIVE WORKSPACE", fontWeight = FontWeight.Bold)
                Text("Tradier connection: ${if (status.tradierConfigured) "configured" else "not configured"}")
                Text("Daily limits: ${if (status.risk.armedToday) "armed" else "not armed"}")
                Text("Workflow: ${status.liveWorkflow.replace('_', ' ')}")
                Text("Broker submission from the autonomous loop: ${if (status.liveOrderSubmission) "enabled" else "off"}")
                Text("The same Railway strategy/risk engine drives this screen; Android does not run a second trading algorithm.")
            }
        }

        status.positionSymbol?.let { symbol ->
            Card {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text("ENGINE POSITION", fontWeight = FontWeight.Bold)
                    Text(symbol)
                    Text("Quantity: ${status.positionQuantity}")
                    Text("Unrealized: ${money(status.positionUnrealizedPnl)}")
                }
            }
        }
    }
}

@Composable
private fun PaperTab(status: RailwayStatus, backend: RailwayBackend, connectionError: String?) {
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("AUTONOMOUS PAPER", fontWeight = FontWeight.Bold)
                Text(if (status.autonomyArmed) "ARMED — new simulated entries allowed" else "DISARMED — no new simulated entries")
                Text("Settled cash: ${money(status.paperCash)}")
                Text("Realized P&L: ${money(status.paperPnl)}")
                Text("Open positions: ${status.paperPositions}")
                Text("Trades: ${status.paperTrades}")
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    scope.launch {
                        backend.setPaperAutonomy(true)
                            .onSuccess { message = "Autonomous PAPER armed" }
                            .onFailure { message = it.message }
                    }
                },
                enabled = !status.autonomyArmed,
            ) { Text("Arm PAPER") }
            OutlinedButton(
                onClick = {
                    scope.launch {
                        backend.setPaperAutonomy(false)
                            .onSuccess { message = "New paper entries disarmed" }
                            .onFailure { message = it.message }
                    }
                },
                enabled = status.autonomyArmed,
            ) { Text("Disarm") }
        }

        status.closedPnl?.let {
            Card {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Text("LAST CLOSED TRADE", fontWeight = FontWeight.Bold)
                    Text("Realized P&L: ${money(it)}")
                }
            }
        }
        message?.let { Text(it) }
    }
}

@Composable
private fun SettingsTab(status: RailwayStatus, backend: RailwayBackend) {
    val scope = rememberCoroutineScope()
    var loss by remember(status.risk.lossLimit) { mutableStateOf(status.risk.lossLimit?.let { "%.2f".format(it) } ?: "") }
    var gain by remember(status.risk.gainLimit) { mutableStateOf(status.risk.gainLimit?.let { "%.2f".format(it) } ?: "") }
    var exposure by remember(status.risk.maxExposurePct) { mutableStateOf(status.risk.maxExposurePct?.let { "%.1f".format(it * 100.0) } ?: "") }
    var contracts by remember(status.risk.maxContracts) { mutableStateOf(status.risk.maxContracts?.toString() ?: "") }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Daily live envelope", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("These are account-level limits for the live-review workflow and expire by Eastern trading date.")

        OutlinedTextField(
            value = loss,
            onValueChange = { loss = numeric(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Daily loss stop ($)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = gain,
            onValueChange = { gain = numeric(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Daily gain stop ($)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = exposure,
            onValueChange = { exposure = numeric(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Max account exposure (%)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = contracts,
            onValueChange = { contracts = it.filter(Char::isDigit) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Contract ceiling") },
            singleLine = true,
        )

        Button(onClick = {
            val lossValue = loss.toDoubleOrNull()
            val gainValue = gain.toDoubleOrNull()
            val exposureValue = exposure.toDoubleOrNull()?.div(100.0)
            val contractValue = contracts.toIntOrNull()
            if (
                lossValue == null || gainValue == null || exposureValue == null || contractValue == null ||
                lossValue <= 0.0 || gainValue <= 0.0 || exposureValue <= 0.0 || exposureValue > 1.0 || contractValue < 1
            ) {
                message = "Enter valid positive values; exposure must be 0–100%."
            } else {
                scope.launch {
                    backend.armLiveRisk(lossValue, gainValue, exposureValue, contractValue)
                        .onSuccess { message = "Today's live envelope is armed" }
                        .onFailure { message = it.message }
                }
            }
        }) { Text(if (status.risk.armedToday) "Re-arm today's limits" else "Arm today's limits") }

        OutlinedButton(
            onClick = {
                scope.launch {
                    backend.disarmLiveRisk()
                        .onSuccess { message = "Live envelope disarmed" }
                        .onFailure { message = it.message }
                }
            },
            enabled = status.risk.armedToday,
        ) { Text("Disarm live envelope") }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("CURRENT LIMITS", fontWeight = FontWeight.Bold)
                Text("Date: ${status.risk.tradingDate ?: "—"}")
                Text("Loss: ${money(status.risk.lossLimit)}")
                Text("Gain: ${money(status.risk.gainLimit)}")
                Text("Exposure: ${status.risk.maxExposurePct?.let { "%.1f%%".format(it * 100.0) } ?: "—"}")
                Text("Contracts: ${status.risk.maxContracts ?: 0}")
                Text("Max open positions: 1")
            }
        }
        message?.let { Text(it) }
    }
}

private fun numeric(value: String): String = value.filter { it.isDigit() || it == '.' }
private fun money(value: Double?): String = value?.let { "$${"%.2f".format(it)}" } ?: "—"

@Composable
private fun ConsoleMetric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}
