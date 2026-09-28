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

    fun connect(onStatus: (ScreenStatus) -> Unit, onError: (String) -> Unit): WebSocket? {
        if (base.isBlank()) {
            onError("Backend URL is not configured")
            return null
        }
        val wsBase = when {
            base.startsWith("https://") -> "wss://${base.removePrefix("https://")}"
            base.startsWith("http://") -> "ws://${base.removePrefix("http://")}"
            else -> base
        }
        return http.newWebSocket(
            Request.Builder()
                .url("$wsBase/v1/live")
                .header("Authorization", "Bearer $token")
                .build(),
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching { parseStatus(text) }
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
        val json = JSONObject().put("mode", mode)
        if (mode == "LIVE") json.put("confirmation", "ENABLE LIVE TRADING")
        post("/v1/mode", json)
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
                    ticket = root.getString("ticket_token"),
                    expiresSeconds = root.optInt("expires_in_seconds", 45),
                    orderJson = order.toString(),
                    optionType = order.getString("option_type"),
                    strike = order.getDouble("strike_price"),
                    expiration = order.getString("expiration_date"),
                    quantity = order.getInt("quantity"),
                    limitPrice = order.getDouble("limit_price"),
                    maxDebit = order.getDouble("max_debit"),
                )
            }
    }

    suspend fun submitTrade(trade: PreparedTrade): Result<JSONObject> = withContext(Dispatchers.IO) {
        post(
            "/v1/live/order/submit",
            JSONObject()
                .put("ticket_token", trade.ticket)
                .put("order", JSONObject(trade.orderJson))
                .put("confirmation", "CONFIRM TRADE"),
        )
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

private fun parseStatus(text: String): ScreenStatus {
    val root = JSONObject(text)
    val market = root.optJSONObject("market") ?: JSONObject()
    val gate = root.optJSONObject("live_gate") ?: JSONObject()
    val broker = root.optJSONObject("live_broker") ?: JSONObject()
    val risk = root.optJSONObject("live_risk") ?: JSONObject()
    val automation = root.optJSONObject("paper_automation") ?: JSONObject()
    val paper = root.optJSONObject("paper") ?: JSONObject()
    val autonomy = root.optJSONObject("paper_autonomy") ?: JSONObject()
    val liveAlert = root.optJSONObject("live_alert")

    fun number(obj: JSONObject?, key: String): Double? =
        if (obj == null || !obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

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

    val guard = broker.optJSONObject("guard") ?: gate.optJSONObject("guard") ?: JSONObject()
    val brokerState = BrokerState(
        connected = guard.optBoolean("connected", broker.optBoolean("connected", false)),
        entryAllowed = guard.optBoolean("entry_allowed", false),
        cashAvailable = number(guard, "cash_available"),
        totalEquity = number(guard, "total_equity"),
        dailyPnl = number(guard, "daily_total_pnl"),
        openPnl = number(guard, "daily_open_pnl"),
        openPositions = guard.optInt("open_positions", 0),
        pendingOrders = guard.optInt("pending_orders_count", 0),
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

    return ScreenStatus(
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
    var status by remember { mutableStateOf(ScreenStatus()) }
    var connectionError by remember { mutableStateOf<String?>(null) }

    DisposableEffect(token) {
        startWatcher(activity, token)
        val socket = backend.connect(
            onStatus = { next -> activity.runOnUiThread { status = next; connectionError = null } },
            onError = { error -> activity.runOnUiThread { connectionError = error } },
        )
        onDispose { socket?.close(1000, "screen closed") }
    }

    GlassDashboard(
        status = status,
        preview = preview,
        connectionError = connectionError,
        actions = remember(backend) {
            GlassActions(
                prepareTrade = { backend.prepareTrade() },
                submitTrade = { trade ->
                    backend.submitTrade(trade).map { result ->
                        val state = result.optJSONObject("order_detail")?.optString("status")
                        "WEBULL SUBMISSION · ${state?.ifBlank { "RECEIVED" }?.uppercase() ?: "RECEIVED"}"
                    }
                },
                setMode = { mode ->
                    backend.setMode(mode).map { "MODE · $mode" }
                },
                setPaperAutonomy = { armed ->
                    backend.setPaperAutonomy(armed).map {
                        if (armed) "AUTONOMOUS PAPER · ARMED" else "AUTONOMOUS PAPER · NEW ENTRIES STOPPED"
                    }
                },
                armRisk = { loss, gain, exposure, contracts ->
                    backend.armRisk(loss, gain, exposure, contracts).map { "TODAY'S LIVE LIMITS · ARMED" }
                },
                disarmRisk = {
                    backend.disarmRisk().map { "LIVE ENVELOPE · DISARMED" }
                },
            )
        },
        onSignOut = onSignOut,
    )
}
