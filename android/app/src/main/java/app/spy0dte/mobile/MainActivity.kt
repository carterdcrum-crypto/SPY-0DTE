package app.spy0dte.mobile

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
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

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 33 &&
            ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                1001,
            )
        }
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    SpyApp(this)
                }
            }
        }
    }
}

data class DailyLiveRisk(
    val configured: Boolean = false,
    val armedToday: Boolean = false,
    val tradingDate: String? = null,
    val dailyLossLimit: Double? = null,
    val dailyGainLimit: Double? = null,
    val maxAccountExposurePct: Double? = null,
    val maxContracts: Int? = null,
)

data class StrategyAlert(
    val generatedAt: String? = null,
    val strategy: String? = null,
    val reason: String? = null,
    val symbol: String? = null,
    val right: String? = null,
    val ask: Double? = null,
    val bid: Double? = null,
    val contracts: Int = 0,
    val sizingMode: String? = null,
)

data class LiveStatus(
    val mode: String = "PAPER",
    val decision: String = "CONNECTING",
    val decisionReason: String = "Waiting for backend",
    val spot: Double? = null,
    val rows: Int = 0,
    val dataAgeSeconds: Double? = null,
    val feedDelaySeconds: Double? = null,
    val engineTickSeconds: Double = 1.0,
    val liveReady: Boolean = false,
    val liveReasons: List<String> = emptyList(),
    val tradierConfigured: Boolean = false,
    val tradierConnected: Boolean = false,
    val risk: DailyLiveRisk = DailyLiveRisk(),
    val alert: StrategyAlert? = null,
)

private class BackendClient(private val idToken: String) {
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()
    private val baseUrl = BuildConfig.API_BASE_URL.trim().trimEnd('/')

    fun connectLive(
        onStatus: (LiveStatus) -> Unit,
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
            .header("Authorization", "Bearer $idToken")
            .build()
        return http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { parseStatus(text) }
                        .onSuccess(onStatus)
                        .onFailure { onError("Bad backend message: ${it.message}") }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onError(t.message ?: "Live connection failed")
                }
            },
        )
    }

    suspend fun setMode(mode: String): Result<Unit> = withContext(Dispatchers.IO) {
        val body = JSONObject().put("mode", mode)
        if (mode == "LIVE") body.put("confirmation", "ENABLE LIVE TRADING")
        post("/v1/mode", body)
    }

    suspend fun armLiveRisk(
        dailyLoss: Double,
        dailyGain: Double,
        maxExposurePct: Double,
        maxContracts: Int,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        post(
            "/v1/live/risk-envelope",
            JSONObject()
                .put("daily_loss_limit", dailyLoss)
                .put("daily_gain_limit", dailyGain)
                .put("max_account_exposure_pct", maxExposurePct)
                .put("max_contracts", maxContracts)
                .put("confirmation", "ARM LIVE TODAY"),
        )
    }

    suspend fun disarmLiveRisk(): Result<Unit> = withContext(Dispatchers.IO) {
        post("/v1/live/risk-envelope/disarm", JSONObject())
    }

    private fun post(path: String, json: JSONObject): Result<Unit> {
        if (baseUrl.isBlank()) {
            return Result.failure(IllegalStateException("Backend URL is not configured"))
        }
        val request = Request.Builder()
            .url("$baseUrl$path")
            .header("Authorization", "Bearer $idToken")
            .post(json.toString().toRequestBody("application/json".toMediaType()))
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

private fun parseStatus(text: String): LiveStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market") ?: JSONObject()
    val cadence = root.optJSONObject("cadence") ?: JSONObject()
    val gate = root.optJSONObject("live_gate") ?: JSONObject()
    val broker = root.optJSONObject("live_broker") ?: JSONObject()
    val decision = root.optJSONObject("decision") ?: JSONObject()
    val riskJson = root.optJSONObject("live_risk") ?: JSONObject()
    val alertJson = root.optJSONObject("live_alert")
    val reasonsJson = gate.optJSONArray("reasons")
    val reasons = buildList {
        if (reasonsJson != null) {
            for (index in 0 until reasonsJson.length()) add(reasonsJson.optString(index))
        }
    }

    fun nullableDouble(obj: JSONObject, key: String): Double? =
        if (!obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

    fun nullableInt(obj: JSONObject, key: String): Int? =
        if (!obj.has(key) || obj.isNull(key)) null else obj.optInt(key)

    val alert = if (alertJson == null) {
        null
    } else {
        val signal = alertJson.optJSONObject("signal") ?: JSONObject()
        val alertRisk = alertJson.optJSONObject("risk") ?: JSONObject()
        StrategyAlert(
            generatedAt = alertJson.optString("generated_at").takeIf { it.isNotBlank() },
            strategy = alertJson.optString("strategy").takeIf { it.isNotBlank() },
            reason = alertJson.optString("reason").takeIf { it.isNotBlank() },
            symbol = signal.optString("symbol").takeIf { it.isNotBlank() },
            right = signal.optString("right").takeIf { it.isNotBlank() },
            ask = nullableDouble(signal, "ask"),
            bid = nullableDouble(signal, "bid"),
            contracts = alertRisk.optInt("contracts", 0),
            sizingMode = alertRisk.optString("sizing_mode").takeIf { it.isNotBlank() },
        )
    }

    return LiveStatus(
        mode = root.optString("mode", "PAPER"),
        decision = decision.optString("state", "RESEARCH_ONLY"),
        decisionReason = decision.optString("reason", ""),
        spot = nullableDouble(market, "spot"),
        rows = market.optInt("rows", 0),
        dataAgeSeconds = nullableDouble(market, "data_age_seconds"),
        feedDelaySeconds = nullableDouble(market, "feed_delay_seconds"),
        engineTickSeconds = cadence.optDouble("engine_tick_seconds", 1.0),
        liveReady = gate.optBoolean("ready", false),
        liveReasons = reasons,
        tradierConfigured = broker.optBoolean("configured", false),
        tradierConnected = broker.optBoolean("connected", false),
        risk = DailyLiveRisk(
            configured = riskJson.optBoolean("configured", false),
            armedToday = riskJson.optBoolean("armed_today", false),
            tradingDate = riskJson.optString("trading_date").takeIf { it.isNotBlank() },
            dailyLossLimit = nullableDouble(riskJson, "daily_loss_limit"),
            dailyGainLimit = nullableDouble(riskJson, "daily_gain_limit"),
            maxAccountExposurePct = nullableDouble(riskJson, "max_account_exposure_pct"),
            maxContracts = nullableInt(riskJson, "max_contracts"),
        ),
        alert = alert,
    )
}

private suspend fun googleSignIn(activity: Activity): Result<String> {
    if (BuildConfig.GOOGLE_WEB_CLIENT_ID.isBlank()) {
        return Result.failure(IllegalStateException("Google OAuth client ID is not configured"))
    }
    return try {
        val manager = CredentialManager.create(activity)
        val option = GetGoogleIdOption.Builder()
            .setFilterByAuthorizedAccounts(false)
            .setServerClientId(BuildConfig.GOOGLE_WEB_CLIENT_ID)
            .build()
        val request = GetCredentialRequest.Builder()
            .addCredentialOption(option)
            .build()
        val response = manager.getCredential(context = activity, request = request)
        val credential = response.credential
        if (
            credential is CustomCredential &&
            credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
        ) {
            Result.success(GoogleIdTokenCredential.createFrom(credential.data).idToken)
        } else {
            Result.failure(IllegalStateException("Google did not return an ID token"))
        }
    } catch (error: Exception) {
        Result.failure(error)
    }
}

private fun startLiveWatcher(activity: Activity, idToken: String) {
    val intent = Intent(activity, LiveWatchService::class.java)
        .putExtra(LiveWatchService.EXTRA_ID_TOKEN, idToken)
    ContextCompat.startForegroundService(activity, intent)
}

private fun stopLiveWatcher(activity: Activity) {
    activity.stopService(Intent(activity, LiveWatchService::class.java))
}

@Composable
private fun SpyApp(activity: Activity) {
    var idToken by remember { mutableStateOf<String?>(null) }
    var signInError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    if (idToken == null) {
        SignInScreen(
            error = signInError,
            onSignIn = {
                scope.launch {
                    signInError = null
                    googleSignIn(activity)
                        .onSuccess {
                            idToken = it
                            startLiveWatcher(activity, it)
                        }
                        .onFailure { signInError = it.message ?: "Google sign-in failed" }
                }
            },
        )
    } else {
        MainShell(
            activity = activity,
            idToken = idToken!!,
            onSignOut = {
                stopLiveWatcher(activity)
                idToken = null
            },
        )
    }
}

@Composable
private fun SignInScreen(error: String?, onSignIn: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("SPY 0DTE", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text("Private trading research + live alert console")
        Spacer(Modifier.height(28.dp))
        Button(onClick = onSignIn, enabled = BuildConfig.GOOGLE_WEB_CLIENT_ID.isNotBlank()) {
            Text("Sign in with Google")
        }
        if (BuildConfig.GOOGLE_WEB_CLIENT_ID.isBlank()) {
            Spacer(Modifier.height(12.dp))
            Text("Google sign-in is not configured in this build.")
        }
        error?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun MainShell(activity: Activity, idToken: String, onSignOut: () -> Unit) {
    val backend = remember(idToken) { BackendClient(idToken) }
    var status by remember { mutableStateOf(LiveStatus()) }
    var connectionError by remember { mutableStateOf<String?>(null) }
    var tab by remember { mutableStateOf("LIVE") }

    DisposableEffect(idToken) {
        startLiveWatcher(activity, idToken)
        val socket = backend.connectLive(
            onStatus = { activity.runOnUiThread { status = it; connectionError = null } },
            onError = { message -> activity.runOnUiThread { connectionError = message } },
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
                Text(if (connectionError == null) "● CONNECTED" else "○ DISCONNECTED")
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
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onSignOut) { Text("Sign out") }
        }

        if (tab == "LIVE") LiveDashboard(status, connectionError, backend)
        else SettingsScreen(status, backend)
    }
}

@Composable
private fun LiveDashboard(status: LiveStatus, connectionError: String?, backend: BackendClient) {
    val scope = rememberCoroutineScope()
    var actionMessage by remember { mutableStateOf<String?>(null) }
    var showLiveConfirm by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { Text(it, color = MaterialTheme.colorScheme.error) }

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Metric("SPY", status.spot?.let { "$%.2f".format(it) } ?: "—")
            Metric("Data", status.dataAgeSeconds?.let { "%.1fs".format(it) } ?: "—")
            Metric("Engine", "${status.engineTickSeconds.toInt()}s")
            Metric("Rows", status.rows.toString())
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("CURRENT DECISION", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(8.dp))
                Text(status.decision, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Text(status.decisionReason)
                Text("Feed delay: ${status.feedDelaySeconds?.let { "%.0fs".format(it) } ?: "—"}")
            }
        }

        status.alert?.let { alert ->
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                Column(Modifier.fillMaxWidth().padding(18.dp)) {
                    Text("STRATEGY ALERT", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Text(alert.symbol ?: "SPY 0DTE", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("Direction: ${alert.right?.uppercase() ?: "—"}")
                    Text("Reference ask: ${alert.ask?.let { "$%.2f".format(it) } ?: "—"}")
                    Text("Reference bid: ${alert.bid?.let { "$%.2f".format(it) } ?: "—"}")
                    Text("Model size: ${alert.contracts} contract(s)")
                    Text("Sizing: ${alert.sizingMode ?: "—"}")
                    Text("Strategy: ${alert.strategy ?: "—"}")
                    Spacer(Modifier.height(6.dp))
                    Text(alert.reason ?: "Qualified strategy signal ready for review.")
                    Spacer(Modifier.height(8.dp))
                    Text("No broker order is sent by this alert.", fontWeight = FontWeight.Bold)
                }
            }
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("TODAY'S RISK ENVELOPE", fontWeight = FontWeight.Bold)
                Text(if (status.risk.armedToday) "ARMED" else "NOT ARMED")
                Text("Loss stop: ${money(status.risk.dailyLossLimit)}")
                Text("Gain stop: ${money(status.risk.dailyGainLimit)}")
                Text("Max account exposure: ${status.risk.maxAccountExposurePct?.let { "%.1f%%".format(it * 100.0) } ?: "—"}")
                Text("Contract ceiling: ${status.risk.maxContracts ?: 0}")
                Text("Max open positions: 1")
            }
        }

        Text("MODE", fontWeight = FontWeight.Bold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("SHADOW", "PAPER").forEach { mode ->
                if (status.mode == mode) {
                    Button(onClick = {}) { Text(mode) }
                } else {
                    OutlinedButton(onClick = {
                        scope.launch {
                            backend.setMode(mode)
                                .onSuccess { actionMessage = "$mode selected" }
                                .onFailure { actionMessage = it.message }
                        }
                    }) { Text(mode) }
                }
            }
            OutlinedButton(
                onClick = { showLiveConfirm = true },
                enabled = status.liveReady,
            ) { Text(if (status.liveReady) "LIVE ALERTS" else "LIVE 🔒") }
        }

        if (!status.liveReady) {
            Text(
                "Live locked: ${status.liveReasons.joinToString().ifBlank { "production setup incomplete" }}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        actionMessage?.let { Text(it) }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("BROKER", fontWeight = FontWeight.Bold)
                Text("Tradier credentials: ${if (status.tradierConfigured) "configured" else "not configured"}")
                Text("Alert workflow: strategy decides → phone notifies → you review")
            }
        }
    }

    if (showLiveConfirm) {
        AlertDialog(
            onDismissRequest = { showLiveConfirm = false },
            title = { Text("Enable live alerts?") },
            text = {
                Text("The backend will run the same strategy as SHADOW and notify you when it finds a qualified setup. Alerts do not submit broker orders.")
            },
            confirmButton = {
                Button(onClick = {
                    showLiveConfirm = false
                    scope.launch {
                        backend.setMode("LIVE")
                            .onSuccess { actionMessage = "LIVE alerts enabled" }
                            .onFailure { actionMessage = it.message }
                    }
                }) { Text("Enable LIVE alerts") }
            },
            dismissButton = { TextButton(onClick = { showLiveConfirm = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun SettingsScreen(status: LiveStatus, backend: BackendClient) {
    val scope = rememberCoroutineScope()
    var lossText by remember(status.risk.dailyLossLimit) {
        mutableStateOf(status.risk.dailyLossLimit?.let { "%.2f".format(it) } ?: "")
    }
    var gainText by remember(status.risk.dailyGainLimit) {
        mutableStateOf(status.risk.dailyGainLimit?.let { "%.2f".format(it) } ?: "")
    }
    var exposureText by remember(status.risk.maxAccountExposurePct) {
        mutableStateOf(status.risk.maxAccountExposurePct?.let { "%.1f".format(it * 100.0) } ?: "")
    }
    var contractsText by remember(status.risk.maxContracts) {
        mutableStateOf(status.risk.maxContracts?.toString() ?: "")
    }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Daily live limits", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("These limits expire at the end of the Eastern trading date. You arm them again the next day.")

        OutlinedTextField(
            value = lossText,
            onValueChange = { lossText = numericInput(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Daily loss stop ($)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = gainText,
            onValueChange = { gainText = numericInput(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Daily gain stop ($)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = exposureText,
            onValueChange = { exposureText = numericInput(it) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Max account exposure (%)") },
            singleLine = true,
        )
        OutlinedTextField(
            value = contractsText,
            onValueChange = { contractsText = it.filter(Char::isDigit) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Max contracts in the one open position") },
            singleLine = true,
        )

        Button(
            onClick = {
                val loss = lossText.toDoubleOrNull()
                val gain = gainText.toDoubleOrNull()
                val exposure = exposureText.toDoubleOrNull()?.div(100.0)
                val contracts = contractsText.toIntOrNull()
                if (loss == null || gain == null || exposure == null || contracts == null ||
                    loss <= 0.0 || gain <= 0.0 || exposure <= 0.0 || exposure > 1.0 || contracts < 1
                ) {
                    message = "Enter valid positive limits; exposure must be 0–100%."
                } else {
                    scope.launch {
                        backend.armLiveRisk(loss, gain, exposure, contracts)
                            .onSuccess { message = "Today's live limits are armed" }
                            .onFailure { message = it.message ?: "Could not arm limits" }
                    }
                }
            },
        ) { Text(if (status.risk.armedToday) "Re-arm today's limits" else "Arm LIVE limits for today") }

        OutlinedButton(
            onClick = {
                scope.launch {
                    backend.disarmLiveRisk()
                        .onSuccess { message = "LIVE limits disarmed; backend returned to SHADOW if needed" }
                        .onFailure { message = it.message ?: "Could not disarm" }
                }
            },
            enabled = status.risk.configured,
        ) { Text("Disarm") }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("Current", fontWeight = FontWeight.Bold)
                Text("Date: ${status.risk.tradingDate ?: "—"}")
                Text("Armed today: ${if (status.risk.armedToday) "yes" else "no"}")
                Text("Max open positions: 1")
            }
        }

        Spacer(Modifier.height(8.dp))
        Text("Broker connection", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("Tradier: ${if (status.tradierConfigured) "credentials configured on Railway" else "production credentials not configured"}")
                Text("Broker credentials stay on Railway and are never stored in the APK.")
            }
        }

        message?.let { Text(it) }
    }
}

private fun numericInput(value: String): String = value.filter { it.isDigit() || it == '.' }

private fun money(value: Double?): String = value?.let { "$${"%.2f".format(it)}" } ?: "—"

@Composable
private fun Metric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}
