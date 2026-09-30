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
                    .put("confirmation", "ARM LIVE TODAY"),
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
    val market = if (root.optString("mode") == "LIVE") {
        root.optJSONObject("live_autonomy")?.optJSONObject("market") ?: JSONObject()
    } else root.optJSONObject("market") ?: JSONObject()
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

    val guard = if (liveAuto.optBoolean("configured", false)) liveAuto.optJSONObject("guard") ?: JSONObject()
        else broker.optJSONObject("guard") ?: gate.optJSONObject("guard") ?: JSONObject()
    val brokerState = BrokerState(
        connected = guard.optBoolean("connected", broker.optBoolean("connected", false)),
        entryAllowed = guard.optBoolean("entry_allowed", false),
        cashAvailable = number(guard, "cash_available"),
        totalEquity = number(guard, "total_equity"),
        dailyPnl = number(guard, "daily_total_pnl"),
        openPnl = number(guard, "daily_open_pnl"),
        openPositions = liveAuto.optJSONArray("positions")?.length() ?: guard.optInt("open_positions", 0),
        pendingOrders = liveAuto.optJSONArray("pending_orders")?.length() ?: guard.optInt("pending_orders_count", 0),
        maxEntryDebit = number(guard, "max_entry_debit"),
    )

    val aiDecision = automation.optJSONObject("ai_decision") ?: JSONObject()
    val blend = aiDecision.optJSONObject("blend") ?: JSONObject()
    val directConsensus = aiDecision.optJSONObject("consensus")
    val advisoryConsensus = automation
        .optJSONObject("ai_advisory")
        ?.optJSONObject("latest")
    val consensus = directConsensus ?: advisoryConsensus ?: JSONObject()

    val providerStates = buildList {
        consensus.optJSONArray("providers")?.let { values ->
            for (index in 0 until values.length()) {
                val provider = values.optJSONObject(index) ?: continue
                val name = provider.optString("provider").trim()
                if (name.isBlank()) continue
                add(
                    AiProviderState(
                        provider = name,
                        probabilityUp = number(provider, "probability_up"),
                        confidence = number(provider, "confidence"),
                    ),
                )
            }
        }
    }

    val ai = AiDecisionState(
        active = aiDecision.optBoolean("active", false),
        quantProbabilityUp = number(blend, "quant_probability_up"),
        aiProbabilityUp = number(blend, "ai_probability_up") ?: number(consensus, "probability_up"),
        hybridProbabilityUp = number(blend, "hybrid_probability_up"),
        effectiveWeight = number(blend, "effective_weight"),
        consensusConfidence = number(consensus, "confidence"),
        disagreement = number(consensus, "disagreement"),
        providers = providerStates,
    )

    val livePositions = buildList {
        liveAuto.optJSONArray("positions")?.let { rows ->
            for (index in 0 until rows.length()) {
                val row = rows.optJSONObject(index) ?: continue
                add(LivePosition(row.optString("symbol"), row.optInt("quantity"), number(row, "average_price")))
            }
        }
    }
    return ScreenStatus(
        liveEnabled = liveAuto.optBoolean("enabled", false),
        liveState = if (liveAuto.optBoolean("worker_current", false)) liveAuto.optString("state", "STARTING") else "RECOVERING",
        liveReason = liveAuto.optString("reason", "Waiting for live worker"),
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
        ai = ai,
        alert = alert,
        paperStartingCash = number(paper, "starting_cash"),
        paperCash = number(paper, "settled_cash"),
        paperPnl = number(paper, "realized_pnl"),
        paperPositions = paper.optInt("open_positions", 0),
        paperTrades = paper.optInt("execution_count", paper.optInt("trade_count", 0)),
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

private fun startWatcher(activity: Activity, token: String) {
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
            Text("SPY 0DTE", color = GlassColors.Cyan, fontWeight = FontWeight.Black)
            Text("GLASS · Railway AI + Webull")
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                scope.launch {
                    loginError = null
                    googleSignIn(activity).onSuccess { token = it }.onFailure { loginError = it.message }
                }
            }) { Text("SIGN IN AS OWNER") }
            loginError?.let {
                Spacer(Modifier.height(10.dp))
                Text(it, color = GlassColors.Red)
            }
        }
        return
    }

    TradeConsole(
        activity = activity,
        token = token!!,
        preview = token == "preview",
        onSignOut = if (oauthConfigured) ({ activity.stopService(Intent(activity, TradeWatchService::class.java)); token = null }) else null,
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
            val confirmedMode = result.optString("mode", if (armed) "PAPER" else "SHADOW")
            status = status.copy(mode = confirmedMode, paperArmed = result.optBoolean("armed", armed))
            if (armed) "AUTONOMOUS PAPER · ARMED" else "AUTONOMOUS PAPER · NEW ENTRIES STOPPED"
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
        backend.armRisk(loss, gain, exposure, contracts).map { "Live limits saved. Turn on Auto trade to use them each session." }
    },
    disarmRisk = { backend.disarmRisk().map { "LIVE ENVELOPE · DISARMED" } },
),
        onSignOut = onSignOut,
    )
}
