package app.spy0dte.mobile

import android.Manifest
import android.app.Activity
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
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
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

class WebullTradeActivity : ComponentActivity() {
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
                1201,
            )
        }
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    WebullTradeApp(this)
                }
            }
        }
    }
}

private data class AppRisk(
    val armed: Boolean = false,
    val loss: Double? = null,
    val gain: Double? = null,
    val exposure: Double? = null,
    val contracts: Int? = null,
)

private data class AppAlert(
    val symbol: String,
    val right: String,
    val bid: Double?,
    val ask: Double?,
    val contracts: Int,
    val maxDebit: Double?,
)

private data class AppStatus(
    val mode: String = "PAPER",
    val connected: Boolean = false,
    val decision: String = "CONNECTING",
    val reason: String = "Waiting for Railway",
    val strategy: String = "—",
    val riskProfile: String = "—",
    val exitProfile: String = "—",
    val spot: Double? = null,
    val dataAge: Double? = null,
    val feedDelay: Double? = null,
    val liveReady: Boolean = false,
    val liveReasons: List<String> = emptyList(),
    val brokerProvider: String = "webull",
    val brokerConfigured: Boolean = false,
    val brokerConnected: Boolean = false,
    val ownerAuthRequired: Boolean = true,
    val risk: AppRisk = AppRisk(),
    val alert: AppAlert? = null,
    val paperCash: Double? = null,
    val paperPnl: Double? = null,
    val paperPositions: Int = 0,
    val paperTrades: Int = 0,
    val paperArmed: Boolean = false,
)

private data class PreparedTrade(
    val ticketToken: String,
    val expiresSeconds: Int,
    val orderJson: String,
    val accountId: String,
    val clientOrderId: String,
    val optionType: String,
    val strike: Double,
    val expiration: String,
    val quantity: Int,
    val limitPrice: Double,
    val maxDebit: Double,
)

private class TradeBackend(private val token: String) {
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val baseUrl = BuildConfig.API_BASE_URL.trim().trimEnd('/')

    fun connect(onStatus: (AppStatus) -> Unit, onError: (String) -> Unit): WebSocket? {
        if (baseUrl.isBlank()) {
            onError("Backend URL is not configured")
            return null
        }
        val wsBase = when {
            baseUrl.startsWith("https://") -> "wss://${baseUrl.removePrefix("https://")}"
            baseUrl.startsWith("http://") -> "ws://${baseUrl.removePrefix("http://")}"
            else -> baseUrl
        }
        return http.newWebSocket(
            Request.Builder()
                .url("$wsBase/v1/live")
                .header("Authorization", "Bearer $token")
                .build(),
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { parseAppStatus(text) }
                        .onSuccess(onStatus)
                        .onFailure { onError("Bad Railway status: ${it.message}") }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onError(t.message ?: "Railway connection failed")
                }
            },
        )
    }

    suspend fun setMode(mode: String): Result<JSONObject> = withContext(Dispatchers.IO) {
        val body = JSONObject().put("mode", mode)
        if (mode == "LIVE") body.put("confirmation", "ENABLE LIVE TRADING")
        post("/v1/mode", body)
    }

    suspend fun setPaperAutonomy(armed: Boolean): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/paper/autonomy", JSONObject().put("armed", armed))
    }

    suspend fun armRisk(loss: Double, gain: Double, exposure: Double, contracts: Int): Result<JSONObject> =
        withContext(Dispatchers.IO) {
            post(
                "/v1/live/risk-envelope",
                JSONObject()
                    .put("daily_loss_limit", loss)
                    .put("daily_gain_limit", gain)
                    .put("max_account_exposure_pct", exposure)
                    .put("max_contracts", contracts)
                    .put("confirmation", "ARM LIVE TODAY"),
            )
        }

    suspend fun disarmRisk(): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/live/risk-envelope/disarm", JSONObject())
    }

    suspend fun prepareTrade(): Result<PreparedTrade> = withContext(Dispatchers.IO) {
        post("/v1/live/order/prepare", JSONObject().put("confirmation", "PREPARE LIVE ORDER"))
            .mapCatching { root ->
                val order = root.getJSONObject("order")
                PreparedTrade(
                    ticketToken = root.getString("ticket_token"),
                    expiresSeconds = root.optInt("expires_in_seconds", 45),
                    orderJson = order.toString(),
                    accountId = order.getString("account_id"),
                    clientOrderId = order.getString("client_order_id"),
                    optionType = order.getString("option_type"),
                    strike = order.getDouble("strike_price"),
                    expiration = order.getString("expiration_date"),
                    quantity = order.getInt("quantity"),
                    limitPrice = order.getDouble("limit_price"),
                    maxDebit = order.getDouble("max_debit"),
                )
            }
    }

    suspend fun submitTrade(prepared: PreparedTrade): Result<JSONObject> = withContext(Dispatchers.IO) {
        post(
            "/v1/live/order/submit",
            JSONObject()
                .put("ticket_token", prepared.ticketToken)
                .put("order", JSONObject(prepared.orderJson))
                .put("confirmation", "CONFIRM TRADE"),
        )
    }

    private fun post(path: String, body: JSONObject): Result<JSONObject> {
        if (baseUrl.isBlank()) return Result.failure(IllegalStateException("Backend URL is not configured"))
        return try {
            val request = Request.Builder()
                .url("$baseUrl$path")
                .header("Authorization", "Bearer $token")
                .post(body.toString().toRequestBody("application/json".toMediaType()))
                .build()
            http.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (response.isSuccessful) {
                    Result.success(if (text.isBlank()) JSONObject() else JSONObject(text))
                } else {
                    val detail = runCatching { JSONObject(text).opt("detail")?.toString() }.getOrNull()
                    Result.failure(IllegalStateException(detail ?: text.take(500).ifBlank { "HTTP ${response.code}" }))
                }
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }
}

private fun parseAppStatus(text: String): AppStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market") ?: JSONObject()
    val gate = root.optJSONObject("live_gate") ?: JSONObject()
    val broker = root.optJSONObject("live_broker") ?: JSONObject()
    val risk = root.optJSONObject("live_risk") ?: JSONObject()
    val automation = root.optJSONObject("paper_automation") ?: JSONObject()
    val paper = root.optJSONObject("paper") ?: JSONObject()
    val autonomy = root.optJSONObject("paper_autonomy") ?: JSONObject()
    val alertRoot = root.optJSONObject("live_alert")

    fun number(obj: JSONObject?, key: String): Double? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

    val reasonArray = gate.optJSONArray("reasons")
    val reasons = buildList {
        if (reasonArray != null) {
            for (index in 0 until reasonArray.length()) add(reasonArray.optString(index))
        }
    }

    val alert = alertRoot?.let {
        val signal = it.optJSONObject("signal") ?: JSONObject()
        val alertRisk = it.optJSONObject("risk") ?: JSONObject()
        val symbol = signal.optString("symbol")
        if (symbol.isBlank()) null else AppAlert(
            symbol = symbol,
            right = signal.optString("right", "").uppercase(),
            bid = number(signal, "bid"),
            ask = number(signal, "ask"),
            contracts = alertRisk.optInt("contracts", 0),
            maxDebit = number(alertRisk, "bounded_entry_debit"),
        )
    }

    return AppStatus(
        mode = root.optString("mode", "PAPER"),
        connected = true,
        decision = automation.optString("state", "CONNECTING"),
        reason = automation.optString("reason", "Waiting for Railway"),
        strategy = automation.optString("strategy", "—"),
        riskProfile = automation.optString("risk_profile", "—"),
        exitProfile = automation.optString("exit_profile", "—"),
        spot = number(market, "spot"),
        dataAge = number(market, "data_age_seconds"),
        feedDelay = number(market, "feed_delay_seconds"),
        liveReady = gate.optBoolean("ready", false),
        liveReasons = reasons,
        brokerProvider = broker.optString("provider", "webull"),
        brokerConfigured = broker.optBoolean("configured", false),
        brokerConnected = broker.optBoolean("connected", false),
        ownerAuthRequired = broker.optBoolean("owner_auth_required", false),
        risk = AppRisk(
            armed = risk.optBoolean("armed_today", false),
            loss = number(risk, "daily_loss_limit"),
            gain = number(risk, "daily_gain_limit"),
            exposure = number(risk, "max_account_exposure_pct"),
            contracts = if (risk.has("max_contracts") && !risk.isNull("max_contracts")) risk.optInt("max_contracts") else null,
        ),
        alert = alert,
        paperCash = number(paper, "settled_cash"),
        paperPnl = number(paper, "realized_pnl"),
        paperPositions = paper.optInt("open_positions", 0),
        paperTrades = paper.optInt("trade_count", 0),
        paperArmed = autonomy.optBoolean("armed", false),
    )
}

private suspend fun googleSignIn(activity: Activity): Result<String> {
    if (BuildConfig.GOOGLE_WEB_CLIENT_ID.isBlank()) {
        return Result.failure(IllegalStateException("Google owner login is not configured in this build"))
    }
    return try {
        val manager = CredentialManager.create(activity)
        val option = GetGoogleIdOption.Builder()
            .setFilterByAuthorizedAccounts(false)
            .setServerClientId(BuildConfig.GOOGLE_WEB_CLIENT_ID)
            .build()
        val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
        val response = manager.getCredential(context = activity, request = request)
        val credential = response.credential
        if (
            credential is CustomCredential &&
            credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
        ) {
            Result.success(GoogleIdTokenCredential.createFrom(credential.data).idToken)
        } else {
            Result.failure(IllegalStateException("Google did not return an owner ID token"))
        }
    } catch (error: Exception) {
        Result.failure(error)
    }
}

private fun startTradeWatcher(activity: Activity, token: String) {
    val intent = Intent(activity, TradeWatchService::class.java)
        .putExtra(TradeWatchService.EXTRA_ID_TOKEN, token)
    ContextCompat.startForegroundService(activity, intent)
}

@Composable
private fun WebullTradeApp(activity: Activity) {
    val oauthConfigured = BuildConfig.GOOGLE_WEB_CLIENT_ID.isNotBlank()
    var token by remember { mutableStateOf<String?>(if (oauthConfigured) null else "preview") }
    var loginError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    if (token == null) {
        Column(
            modifier = Modifier.fillMaxSize().padding(28.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("SPY 0DTE", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text("Railway AI + Webull confirmed trading")
            Spacer(Modifier.height(24.dp))
            Button(onClick = {
                scope.launch {
                    loginError = null
                    googleSignIn(activity)
                        .onSuccess { token = it }
                        .onFailure { loginError = it.message }
                }
            }) { Text("SIGN IN AS OWNER") }
            loginError?.let {
                Spacer(Modifier.height(12.dp))
                Text(it, color = MaterialTheme.colorScheme.error)
            }
        }
        return
    }

    TradeConsole(
        activity = activity,
        token = token!!,
        preview = token == "preview",
        onSignOut = if (oauthConfigured) ({ token = null }) else null,
    )
}

@Composable
private fun TradeConsole(
    activity: Activity,
    token: String,
    preview: Boolean,
    onSignOut: (() -> Unit)?,
) {
    val backend = remember(token) { TradeBackend(token) }
    var status by remember { mutableStateOf(AppStatus()) }
    var error by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf("LIVE") }

    DisposableEffect(token) {
        startTradeWatcher(activity, token)
        val socket = backend.connect(
            onStatus = { activity.runOnUiThread { status = it; error = null } },
            onError = { message -> activity.runOnUiThread { error = message } },
        )
        onDispose { socket?.close(1000, "screen closed") }
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text("SPY 0DTE", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text(if (status.connected && error == null) "● RAILWAY CONNECTED" else "○ RECONNECTING")
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
            Spacer(Modifier.weight(1f))
            onSignOut?.let { TextButton(onClick = it) { Text("Sign out") } }
        }
        when (tab) {
            "PAPER" -> PaperScreen(status, backend, error)
            "SETTINGS" -> SettingsScreen(status, backend, preview)
            else -> LiveScreen(status, backend, preview, error)
        }
    }
}

@Composable
private fun LiveScreen(status: AppStatus, backend: TradeBackend, preview: Boolean, connectionError: String?) {
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }
    var prepared by remember { mutableStateOf<PreparedTrade?>(null) }
    var submitting by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (preview) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                Column(Modifier.fillMaxWidth().padding(14.dp)) {
                    Text("PREVIEW BUILD", fontWeight = FontWeight.Bold)
                    Text("The TRADE button is installed, but real submission stays locked until owner sign-in is configured.")
                }
            }
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("RAILWAY AI BRAIN", fontWeight = FontWeight.Bold)
                Text("${status.strategy} • ${status.riskProfile}")
                Text("Exit: ${status.exitProfile}")
                Text("Decision: ${status.decision}")
                Text(status.reason)
            }
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Metric("SPY", status.spot?.let { "$%.2f".format(it) } ?: "—")
            Metric("Data", status.dataAge?.let { "%.1fs".format(it) } ?: "—")
            Metric("Feed", status.feedDelay?.let { "%.0fs".format(it) } ?: "—")
            Metric("Broker", status.brokerProvider.uppercase())
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("WEBULL LIVE", fontWeight = FontWeight.Bold)
                Text("API: ${if (status.brokerConfigured) "configured" else "not configured"}")
                Text("Account: ${if (status.brokerConnected) "connected" else "not connected"}")
                Text("Daily envelope: ${if (status.risk.armed) "armed" else "not armed"}")
                if (!status.liveReady) {
                    Text("Locked: ${status.liveReasons.joinToString().ifBlank { "waiting for live prerequisites" }}")
                } else {
                    Text("Confirmed live path ready")
                }
            }
        }

        val alert = status.alert
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("LIVE TRADE", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                if (alert == null) {
                    Text("Waiting for a qualified Railway strategy alert.")
                } else {
                    Text(alert.symbol, fontWeight = FontWeight.Bold)
                    Text("${alert.right} • ${alert.contracts} contract(s)")
                    Text("Bid ${price(alert.bid)} • Ask ${price(alert.ask)}")
                    alert.maxDebit?.let { Text("Bounded debit: $%.2f".format(it)) }
                }
                Spacer(Modifier.height(12.dp))
                val enabled = !preview && status.liveReady && status.mode == "LIVE" && alert != null && !submitting
                Button(
                    onClick = {
                        scope.launch {
                            message = "Preparing exact Webull preview…"
                            backend.prepareTrade()
                                .onSuccess { prepared = it; message = null }
                                .onFailure { message = it.message ?: "Could not prepare trade" }
                        }
                    },
                    enabled = enabled,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(if (preview) "TRADE — OWNER LOGIN REQUIRED" else "TRADE")
                }
                if (!preview && status.mode != "LIVE") {
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                backend.setMode("LIVE")
                                    .onSuccess { message = "LIVE mode requested" }
                                    .onFailure { message = it.message }
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("ENABLE LIVE MODE") }
                }
            }
        }

        message?.let { Text(it) }
    }

    prepared?.let { trade ->
        AlertDialog(
            onDismissRequest = { if (!submitting) prepared = null },
            title = { Text("Confirm Webull trade") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("SPY ${trade.optionType} ${trade.strike}")
                    Text("Expiration: ${trade.expiration}")
                    Text("Quantity: ${trade.quantity}")
                    Text("Limit: $%.2f".format(trade.limitPrice))
                    Text("Maximum debit: $%.2f".format(trade.maxDebit))
                    Text("Ticket expires in ${trade.expiresSeconds}s")
                    Text("Only this exact order can use this confirmation.")
                }
            },
            confirmButton = {
                Button(
                    enabled = !submitting,
                    onClick = {
                        submitting = true
                        scope.launch {
                            backend.submitTrade(trade)
                                .onSuccess { result ->
                                    message = "Submitted to Webull: ${result.optJSONObject("order_detail")?.optString("status") ?: "received"}"
                                    prepared = null
                                }
                                .onFailure { message = it.message ?: "Trade submission failed" }
                            submitting = false
                        }
                    },
                ) { Text(if (submitting) "SUBMITTING…" else "CONFIRM TRADE") }
            },
            dismissButton = {
                TextButton(enabled = !submitting, onClick = { prepared = null }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun PaperScreen(status: AppStatus, backend: TradeBackend, connectionError: String?) {
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Card {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("AUTONOMOUS PAPER", fontWeight = FontWeight.Bold)
                Text("Cash: ${money(status.paperCash)}")
                Text("Realized P&L: ${money(status.paperPnl)}")
                Text("Open positions: ${status.paperPositions}")
                Text("Trades: ${status.paperTrades}")
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = {
                        scope.launch {
                            backend.setPaperAutonomy(!status.paperArmed)
                                .onSuccess { message = if (status.paperArmed) "Paper entries disarmed" else "Paper autonomy armed" }
                                .onFailure { message = it.message }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (status.paperArmed) "STOP NEW PAPER ENTRIES" else "ARM AUTONOMOUS PAPER") }
            }
        }
        message?.let { Text(it) }
    }
}

@Composable
private fun SettingsScreen(status: AppStatus, backend: TradeBackend, preview: Boolean) {
    val scope = rememberCoroutineScope()
    var loss by remember { mutableStateOf(status.risk.loss?.toString() ?: "25") }
    var gain by remember { mutableStateOf(status.risk.gain?.toString() ?: "40") }
    var exposurePct by remember { mutableStateOf(status.risk.exposure?.let { (it * 100).toString() } ?: "20") }
    var contracts by remember { mutableStateOf(status.risk.contracts?.toString() ?: "1") }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("DAILY LIVE ENVELOPE", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text("These are ceilings, not targets. A new entry is also blocked while a position or broker order is open.")
        OutlinedTextField(loss, { loss = it }, label = { Text("Daily loss stop ($)") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(gain, { gain = it }, label = { Text("Daily gain stop ($)") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(exposurePct, { exposurePct = it }, label = { Text("Max account exposure (%)") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(contracts, { contracts = it }, label = { Text("Max contracts") }, modifier = Modifier.fillMaxWidth())
        Button(
            onClick = {
                val l = loss.toDoubleOrNull()
                val g = gain.toDoubleOrNull()
                val e = exposurePct.toDoubleOrNull()?.div(100.0)
                val c = contracts.toIntOrNull()
                if (l == null || g == null || e == null || c == null) {
                    message = "Enter valid numeric limits"
                } else {
                    scope.launch {
                        backend.armRisk(l, g, e, c)
                            .onSuccess { message = "Daily live envelope armed" }
                            .onFailure { message = it.message }
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("ARM TODAY'S LIMITS") }
        OutlinedButton(
            onClick = {
                scope.launch {
                    backend.disarmRisk()
                        .onSuccess { message = "Daily live envelope disarmed" }
                        .onFailure { message = it.message }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("DISARM LIVE") }
        if (preview) Text("Owner login is not configured in this APK; live order submission remains locked.")
        message?.let { Text(it) }
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}

private fun money(value: Double?): String = value?.let { "$%.2f".format(it) } ?: "—"
private fun price(value: Double?): String = value?.let { "$%.2f".format(it) } ?: "—"
