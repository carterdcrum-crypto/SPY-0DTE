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
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.OutlinedTextField
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.material3.Button
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
            GlassTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = GlassColors.Background) {
                    WebullTradeApp(this)
                }
            }
        }
    }
}

internal class TradeBackend(private val token: String) {
    private val base = BuildConfig.API_BASE_URL.trim().trimEnd('/')
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    fun connect(onStatus: (ScreenStatus) -> Unit, onError: (String) -> Unit): AutoCloseable {
        if (base.isBlank()) {
            onError("Backend URL is not configured")
            return AutoCloseable { }
        }
        val wsBase = when {
            base.startsWith("https://") -> "wss://${base.removePrefix("https://")}"
            base.startsWith("http://") -> "ws://${base.removePrefix("http://")}"
            else -> base
        }
        return connectStatusSocket(http, Request.Builder().url("$wsBase/v1/live")
            .header("Authorization", "Bearer $token").build(),
            { onStatus(parseStatus(it)) }, onError)
    }

    suspend fun setMode(mode: String): Result<JSONObject> = withContext(Dispatchers.IO) {
        val json = JSONObject().put("mode", mode)
        if (mode == "LIVE") json.put("confirmation", "ENABLE LIVE TRADING")
        post("/v1/mode", json)
    }

    suspend fun setPaperAutonomy(armed: Boolean): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/paper/autonomy", JSONObject().put("armed", armed))
    }

    suspend fun resetPaperAccount(startingCash: Double): Result<JSONObject> = withContext(Dispatchers.IO) {
        post(
            "/v1/paper/reset",
            JSONObject().put("starting_cash", startingCash).put("confirmation", "RESET PAPER ACCOUNT"),
        )
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
                    .put("confirmation", "SAVE STANDING LIVE LIMITS"),
            )
        }

    suspend fun disarmRisk(): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/live/risk-envelope/disarm", JSONObject())
    }

    suspend fun setLiveAutonomy(enabled: Boolean): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/live/autonomy", JSONObject().put("enabled", enabled)
            .put("confirmation", if (enabled) "ENABLE AUTONOMOUS LIVE" else "STOP AUTONOMOUS LIVE"))
    }

    suspend fun saveProductionCredentials(key: String, secret: String): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/broker/webull", JSONObject().put("environment", "production").put("app_key", key).put("app_secret", secret))
    }

    suspend fun listCashAccounts(): Result<List<WebullAccountChoice>> = withContext(Dispatchers.IO) {
        if (base.isBlank()) return@withContext Result.failure(IllegalStateException("Backend URL is not configured"))
        try {
            val request = Request.Builder().url("$base/v1/broker/webull/accounts")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(request).execute().use { response ->
                val raw = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return@use Result.failure(IllegalStateException(
                        "Webull cash account lookup failed (HTTP ${response.code})."
                    ))
                }
                val rows = JSONObject(raw).optJSONArray("accounts")
                val choices = buildList {
                    if (rows != null) for (index in 0 until rows.length()) {
                        val item = rows.optJSONObject(index) ?: continue
                        add(WebullAccountChoice(
                            accountId = item.optString("account_id"),
                            label = item.optString("label"),
                            isCash = item.optString("account_type") == "CASH",
                            selected = item.optBoolean("selected", false),
                        ))
                    }
                }
                Result.success(choices)
            }
        } catch (error: Exception) { Result.failure(error) }
    }

    suspend fun selectCashAccount(accountId: String): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/broker/webull/select-account",
            JSONObject().put("account_id", accountId)
                .put("confirmation", "SELECT WEBULL CASH ACCOUNT"))
    }

    suspend fun logout(): Result<JSONObject> = withContext(Dispatchers.IO) {
        post("/v1/auth/logout", JSONObject())
    }

    suspend fun verifyOwner(): Result<String> = withContext(Dispatchers.IO) {
        if (base.isBlank()) return@withContext Result.failure(IllegalStateException("Backend URL is not configured"))
        try {
            val request = Request.Builder().url("$base/v1/auth/me")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(request).execute().use { response ->
                val payload = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return@use Result.failure(IllegalStateException(
                        "Owner session rejected by Railway (HTTP ${response.code}). Please sign in again.",
                    ))
                }
                val owner = JSONObject(payload)
                if (!owner.optBoolean("authenticated") || !owner.optBoolean("owner")) {
                    Result.failure(IllegalStateException("Your session is not authorized as owner"))
                } else Result.success(owner.optString("username"))
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }

    private fun post(path: String, payload: JSONObject): Result<JSONObject> {
        if (base.isBlank()) return Result.failure(IllegalStateException("Backend URL is not configured"))
        return try {
            val request = Request.Builder()
                .url("$base$path")
                .header("Authorization", "Bearer $token")
                .post(payload.toString().toRequestBody("application/json".toMediaType()))
                .build()
            http.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (response.isSuccessful) {
                    Result.success(if (text.isBlank()) JSONObject() else JSONObject(text))
                } else {
                    val detail = runCatching { JSONObject(text).opt("detail")?.toString() }.getOrNull()
                    Result.failure(
                        IllegalStateException(detail ?: text.take(500).ifBlank { "HTTP ${response.code}" }),
                    )
                }
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }
}

internal fun parseStatus(text: String): ScreenStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market")
        ?: root.optJSONObject("live_autonomy")?.optJSONObject("market")
        ?: JSONObject()
    val chartJson = root.optJSONObject("spy_chart_30m")
        ?: root.optJSONObject("spy_chart_5m") ?: JSONObject()
    val verifiedCandles = buildList {
        chartJson.optJSONArray("candles")?.let { data ->
            for (i in 0 until data.length()) {
                val bar = data.optJSONObject(i) ?: continue
                fun finite(key: String): Double? =
                    if (bar.isNull(key) || !bar.has(key)) null
                    else bar.optDouble(key).takeIf { it.isFinite() && it > 0.0 }
                val open = finite("open")
                val high = finite("high")
                val low = finite("low")
                val close = finite("close")
                val volume = bar.optLong("volume", -1L)
                val time = bar.optString("time").trim()
                if (open != null && high != null && low != null && close != null &&
                    high >= maxOf(open, close, low) && low <= minOf(open, close, high) &&
                    volume > 0 && time.isNotEmpty()
                ) add(SpyCandle(time, open, high, low, close, volume,
                    forming = bar.optBoolean("forming", false)))
            }
        }
    }
    val spyChart = SpyChartState(
        state = chartJson.optString("state", "UNAVAILABLE"),
        source = chartJson.optString("source", "Webull OpenAPI · SPY M1"),
        reason = chartJson.optString("reason").takeIf { it.isNotBlank() },
        asof = chartJson.optString("asof").takeIf { it.isNotBlank() },
        ageSeconds = chartJson.optDouble("age_seconds").takeIf {
            chartJson.has("age_seconds") && !chartJson.isNull("age_seconds") && it.isFinite()
        },
        hasFormingCandle = chartJson.optBoolean("has_forming_candle", false),
        candles = verifiedCandles.takeLast(30),
    )
    val gate = root.optJSONObject("live_gate") ?: JSONObject()
    val broker = root.optJSONObject("live_broker") ?: JSONObject()
    val risk = root.optJSONObject("live_risk") ?: JSONObject()
    val paperAutomation = root.optJSONObject("paper_automation") ?: JSONObject()
    val liveAuto = root.optJSONObject("live_autonomy") ?: JSONObject()
    val isLive = root.optString("mode") == "LIVE"
    val automation = if (isLive) liveAuto else paperAutomation
    val paper = root.optJSONObject("paper") ?: JSONObject()
    val autonomy = root.optJSONObject("paper_autonomy") ?: JSONObject()
    val liveAlert = if (isLive) liveAuto.optJSONObject("entry_alert") else root.optJSONObject("live_alert")

    fun number(obj: JSONObject?, key: String): Double? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optDouble(key).takeIf { it.isFinite() }

    val reasons = mutableListOf<String>()
    gate.optJSONArray("reasons")?.let { values ->
        for (index in 0 until values.length()) reasons += values.optString(index)
    }

    val alert = liveAlert?.let { item ->
        val signal = item.optJSONObject("signal") ?: JSONObject()
        val alertRisk = item.optJSONObject("risk") ?: JSONObject()
        val symbol = signal.optString("symbol")
        if (symbol.isBlank()) null else LiveAlert(
            symbol = symbol,
            right = signal.optString("right").uppercase(),
            bid = number(signal, "bid"),
            ask = number(signal, "ask"),
            contracts = alertRisk.optInt("contracts", 0),
            maxDebit = number(alertRisk, "bounded_entry_debit"),
        )
    }

    val entryJson = liveAuto.optJSONObject("entry_diagnostics")
    val counts = mutableMapOf<String, Int>()
    entryJson?.optJSONObject("rejection_counts")?.let { obj ->
        obj.keys().forEach { key ->
            val count = obj.optInt(key, 0)
            if (count > 0) counts[key] = count
        }
    }
    val liveEntryDiagnostics = LiveEntryDiagnostics(
        signalDetected = entryJson?.let { if (it.has("signal_detected") && !it.isNull("signal_detected")) it.optBoolean("signal_detected") else null },
        spyCandleDataReady = entryJson?.let { if (it.has("data_ready") && !it.isNull("data_ready")) it.optBoolean("data_ready") else null },
        settledCashUsd = number(entryJson, "settled_cash_usd"),
        cheapestQualifiedContractUsd = number(entryJson, "cheapest_qualified_contract_usd"),
        quotesObserved = entryJson?.takeIf { it.has("quotes_observed") }?.optInt("quotes_observed"),
        rejectionCode = entryJson?.optString("rejection_code")?.takeIf { it.isNotBlank() },
        rejectionCounts = counts.toMap(),
    )

    val guard = if (liveAuto.optBoolean("configured", false)) liveAuto.optJSONObject("guard") ?: JSONObject()
        else broker.optJSONObject("guard") ?: gate.optJSONObject("guard") ?: JSONObject()
    val accountGuard = broker.optJSONObject("guard") ?: gate.optJSONObject("guard") ?: JSONObject()
    val brokerState = BrokerState(
        connected = guard.optBoolean("connected", broker.optBoolean("connected", false)),
        entryAllowed = guard.optBoolean("entry_allowed", false),
        cashAvailable = number(guard, "cash_available") ?: number(accountGuard, "cash_available"),
        totalEquity = number(guard, "total_equity") ?: number(accountGuard, "total_equity"),
        dailyPnl = number(guard, "daily_total_pnl"),
        openPnl = number(guard, "daily_open_pnl"),
        openPositions = liveAuto.optJSONArray("positions")?.length() ?: guard.optInt("open_positions", 0),
        pendingOrders = liveAuto.optJSONArray("pending_orders")?.length() ?: guard.optInt("pending_orders_count", 0),
        maxEntryDebit = number(guard, "max_entry_debit"),
    )

    // Original V4 uses strict quantitative signals. LLM consensus/provider
    // probabilities are intentionally excluded from this trading client.
    val quant = AiDecisionState()

    val livePositions = buildList {
        liveAuto.optJSONArray("positions")?.let { rows ->
            for (index in 0 until rows.length()) {
                val row = rows.optJSONObject(index) ?: continue
                add(LivePosition(row.optString("symbol"), row.optInt("quantity"), number(row, "average_price")))
            }
        }
    }
    return ScreenStatus(
        liveStrategyProfile = root.optString("live_strategy_profile", "v4_original_3x"),
        liveEnabled = liveAuto.optBoolean("enabled", false),
        liveState = if (liveAuto.optBoolean("worker_current", false)) liveAuto.optString("state", "STARTING") else "RECOVERING",
        // The quant frequently emits a precise state without a prose reason.
        // Never misreport that as a missing worker heartbeat. In particular
        // NO_EXECUTABLE_DEPTH_OR_FRESH_OPTION_QUOTE is a trade gate, not
        // evidence the background worker has stopped.
        liveReason = if (!liveAuto.optBoolean("worker_current", false)) {
            "Live worker heartbeat unavailable; checking connection"
        } else {
            liveAuto.optString("reason").trim().ifBlank {
                liveAuto.optString("state", "WAITING_FOR_WORKER")
                    .replace('_', ' ').lowercase().replaceFirstChar { it.titlecase() }
            }
        },
        liveEntryDiagnostics = liveEntryDiagnostics,
        livePositions = livePositions,
        workerState = if (isLive) liveAuto.optString("state", "STARTING") else autonomy.optJSONObject("worker")?.optString("state", "STARTING") ?: "STARTING",
        paperBuys = paper.optInt("buy_count", 0),
        paperSells = paper.optInt("sell_count", 0),
        paperUnrealizedPnl = if (paper.optInt("open_positions", 0) == 0) 0.0 else number(paperAutomation.optJSONObject("position"), "unrealized_pnl"),
        mode = root.optString("mode", "PAPER"),
        connected = true,
        decision = automation.optString("state", "CONNECTING"),
        reason = automation.optString("reason", "Waiting for Railway"),
        strategy = automation.optString("strategy", "—"),
        riskProfile = automation.optString("risk_profile", "—"),
        exitProfile = automation.optString("exit_profile", "—"),
        spyChart = spyChart,
        spot = number(market, "spot"),
        dataAge = number(market, "data_age_seconds"),
        feedDelay = number(market, "feed_delay_seconds"),
        liveReady = gate.optBoolean("ready", false),
        liveReasons = reasons,
        broker = broker.optString("provider", "webull"),
        brokerConfigured = broker.optBoolean("configured", false),
        brokerConnected = broker.optBoolean("connected", false),
        risk = RiskState(
            armed = risk.optBoolean("armed_today", false),
            loss = number(risk, "daily_loss_limit"),
            gain = number(risk, "daily_gain_limit"),
            exposure = number(risk, "max_account_exposure_pct"),
            contracts = if (risk.has("max_contracts") && !risk.isNull("max_contracts")) {
                risk.optInt("max_contracts")
            } else null,
        ),
        brokerState = brokerState,
        ai = quant,
        alert = alert,
        paperStartingCash = number(paper, "starting_cash"),
        paperCash = number(paper, "settled_cash"),
        paperPnl = number(paper, "realized_pnl"),
        paperPositions = paper.optInt("open_positions", 0),
        paperTrades = paper.optInt("execution_count", paper.optInt("trade_count", 0)),
        paperArmed = autonomy.optBoolean("armed", false),
        paperEngineRunning = autonomy.optBoolean("engine_running", false),
    )
}

internal class OwnerAuthBackend {
    private val base = BuildConfig.API_BASE_URL.trim().trimEnd('/')
    private val http = OkHttpClient.Builder().callTimeout(20, TimeUnit.SECONDS).build()

    suspend fun registrationState(): Result<Boolean> = withContext(Dispatchers.IO) {
        try {
            http.newCall(Request.Builder().url("$base/v1/auth/setup-status").get().build())
                .execute().use { response ->
                    if (!response.isSuccessful) return@use Result.failure(
                        IllegalStateException("Railway backend has not deployed owner login (HTTP ${response.code})."),
                    )
                    val state = JSONObject(response.body?.string().orEmpty())
                    Result.success(state.optBoolean("owner_created", false))
                }
        } catch (error: Exception) { Result.failure(error) }
    }

    suspend fun authenticate(signup: Boolean, username: String, password: String, code: String): Result<String> =
        withContext(Dispatchers.IO) {
            try {
                val path = if (signup) "/v1/auth/signup" else "/v1/auth/login"
                val data = JSONObject().put("username", username.trim()).put("password", password)
                if (signup) data.put("setup_code", code)
                val request = Request.Builder().url("$base$path")
                    .post(data.toString().toRequestBody("application/json".toMediaType()))
                    .build()
                http.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    val json = runCatching { JSONObject(body) }.getOrElse { JSONObject() }
                    if (!response.isSuccessful) return@use Result.failure(
                        IllegalStateException(
                            when (response.code) {
                                409 -> "Owner account already created. Sign in instead."
                                429 -> "Too many attempts. Try again in 15 minutes."
                                503 -> "Owner setup code is not configured on Railway."
                                401 -> "Incorrect username or password."
                                403 -> "Invalid owner setup code."
                                422 -> {
                                    val first = json.optJSONArray("detail")?.optJSONObject(0)
                                    val field = first?.optString("field")?.takeIf { it.isNotBlank() }
                                        ?: first?.optJSONArray("loc")?.optString(1).orEmpty()
                                    when (field) {
                                        "username" -> "Username must contain 3–32 letters, numbers, dots, underscores, or dashes."
                                        "password" -> "Password must be at least 14 characters long."
                                        "setup_code" -> "Owner setup code must be at least 32 characters. Paste it without extra spaces."
                                        else -> "Check username (3–32 characters), password (14+ characters), and the full setup code."
                                    }
                                }
                                else -> "Owner login failed (HTTP ${response.code})."
                            },
                        ),
                    )
                    val bearer = json.optString("access_token")
                    if (bearer.isBlank()) Result.failure(IllegalStateException("Railway returned no owner session"))
                    else Result.success(bearer)
                }
            } catch (error: Exception) { Result.failure(error) }
        }
}

private fun startWatcher(activity: Activity, token: String) {
    val intent = Intent(activity, TradeWatchService::class.java)
        .putExtra(TradeWatchService.EXTRA_ID_TOKEN, token)
    ContextCompat.startForegroundService(activity, intent)
}

@Composable
private fun WebullTradeApp(activity: Activity) {
    val auth = remember { OwnerAuthBackend() }
    val scope = rememberCoroutineScope()
    var token by remember { mutableStateOf<String?>(null) }
    var existingOwner by remember { mutableStateOf<Boolean?>(null) }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var setupCode by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        auth.registrationState()
            .onSuccess { existingOwner = it; error = null }
            .onFailure { error = it.message }
    }

    if (token == null) {
        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 26.dp, vertical = 36.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("SPY 0DTE", color = GlassColors.Cyan, fontWeight = FontWeight.Black)
            Text(if (existingOwner == false) "CREATE YOUR ONE OWNER ACCOUNT" else "OWNER SIGN IN")
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value = username, onValueChange = { username = it },
                label = { Text("Username") }, singleLine = true,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = password, onValueChange = { password = it },
                label = { Text("Password") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
            )
            if (existingOwner == false) {
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = setupCode, onValueChange = { setupCode = it },
                    label = { Text("One-time owner setup code") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                )
                Spacer(Modifier.height(8.dp))
                Text("Only one signup is permitted, ever. Password: 14+ characters. Username: 3–32. Paste the complete setup code.")
            }
            Spacer(Modifier.height(18.dp))
            Button(
                enabled = existingOwner != null && !busy &&
                    username.isNotBlank() && password.isNotBlank() &&
                    (existingOwner == true || setupCode.isNotBlank()),
                onClick = {
                    scope.launch {
                        busy = true
                        error = null
                        val registering = existingOwner == false
                        try {
                            if (registering && password.length < 14) {
                                throw IllegalArgumentException("Password must be at least 14 characters long.")
                            }
                            if (registering && setupCode.length < 32) {
                                throw IllegalArgumentException("Paste the complete one-time setup code (32+ characters).")
                            }
                            val bearer = auth.authenticate(registering, username, password, setupCode).getOrThrow()
                            TradeBackend(bearer).verifyOwner().getOrThrow()
                            password = ""
                            setupCode = ""
                            existingOwner = true
                            token = bearer
                        } catch (failure: Exception) {
                            error = failure.message ?: "Owner sign-in failed"
                            if (registering) auth.registrationState()
                                .onSuccess { existingOwner = it }
                        } finally {
                            busy = false
                        }
                    }
                },
            ) {
                Text(when {
                    busy -> "VERIFYING..."
                    existingOwner == false -> "CREATE OWNER ACCOUNT"
                    else -> "SIGN IN"
                })
            }
            if (existingOwner == null) {
                Spacer(Modifier.height(12.dp))
                Button(enabled = !busy, onClick = {
                    scope.launch {
                        error = null
                        auth.registrationState().onSuccess { existingOwner = it }
                            .onFailure { error = it.message }
                    }
                }) { Text("RETRY CONNECTION") }
            }
            error?.let {
                Spacer(Modifier.height(10.dp))
                Text(it, color = GlassColors.Red)
            }
        }
        return
    }

    TradeConsole(
        activity = activity,
        token = token!!,
        preview = false,
        onSignOut = {
            val session = token
            token = null
            activity.stopService(Intent(activity, TradeWatchService::class.java))
            if (session != null) scope.launch { TradeBackend(session).logout() }
        },
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
    var status by remember { mutableStateOf(ScreenStatus()) }
    var connectionError by remember { mutableStateOf<String?>(null) }

    DisposableEffect(token) {
        startWatcher(activity, token)
        val socket = backend.connect(
            onStatus = { next -> activity.runOnUiThread { status = next; connectionError = null } },
            onError = { error -> activity.runOnUiThread {
                status = status.copy(connected = false, brokerConnected = false)
                connectionError = error
            } },
        )
        onDispose { socket.close() }
    }

    GlassDashboard(
        status = status,
        preview = preview,
        connectionError = connectionError,
        actions = GlassActions(
    saveCredentials = { key, secret ->
        backend.saveProductionCredentials(key, secret).map { "Production credentials saved securely on Railway." }
    },
    listCashAccounts = { backend.listCashAccounts() },
    selectCashAccount = { accountId ->
        backend.selectCashAccount(accountId).map { it.optString("message", "Webull CASH account selected.") }
    },
    setLiveAutonomy = { enabled ->
        backend.setLiveAutonomy(enabled).map { result ->
            status = status.copy(liveEnabled = result.optBoolean("enabled", false), mode = if (enabled) "LIVE" else status.mode)
            if (enabled) "Auto trade is on. Railway will trade when all live checks pass."
            else "Auto trade is off. Pending buys are cancelled and owned positions are closed when executable."
        }
    },
    setMode = { mode ->
        backend.setMode(mode).map { result ->
            val confirmedMode = result.optString("mode", mode)
            status = status.copy(mode = confirmedMode, paperArmed = confirmedMode == "PAPER")
            "MODE · $confirmedMode"
        }
    },
    setPaperAutonomy = { armed ->
        backend.setPaperAutonomy(armed).map { result ->
            // The endpoint returns PAPER/SHADOW as the simulation *mode*.
            // NEVER overwrite the primary LIVE mode: it may own real orders
            // and the live switch must remain truthful in the dashboard.
            status = status.copy(
                paperArmed = result.optBoolean("armed", armed),
                paperEngineRunning = result.optBoolean("engine_running", false),
            )
            if (armed) "AUTONOMOUS PAPER · ARMED INDEPENDENTLY OF LIVE"
            else "AUTONOMOUS PAPER · NEW SIMULATED ENTRIES STOPPED; LIVE UNCHANGED"
        }
    },
    resetPaperAccount = { startingCash ->
        backend.resetPaperAccount(startingCash).map { result ->
            status = status.copy(
                paperStartingCash = result.optDouble("starting_cash", startingCash),
                paperCash = result.optDouble("settled_cash", startingCash),
                paperPnl = result.optDouble("realized_pnl", 0.0),
                paperPositions = result.optInt("open_positions", 0),
                paperTrades = result.optInt("execution_count", result.optInt("trade_count", 0)),
                paperBuys = result.optInt("buy_count", 0),
                paperSells = result.optInt("sell_count", 0),
                paperUnrealizedPnl = 0.0,
            )
            "Paper account reset to $%.2f.".format(startingCash)
        }
    },
    armRisk = { loss, gain, exposure, contracts ->
        backend.armRisk(loss, gain, exposure, contracts).map { "Standing safety settings saved. No daily re-arming needed." }
    },
    disarmRisk = { backend.disarmRisk().map { "LIVE ENVELOPE · DISARMED" } },
),
        onSignOut = onSignOut,
    )
}
