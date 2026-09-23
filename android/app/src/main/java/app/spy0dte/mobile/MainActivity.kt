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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
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
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    SpyApp(this)
                }
            }
        }
    }
}

data class LiveStatus(
    val mode: String = "PAPER",
    val decision: String = "CONNECTING",
    val decisionReason: String = "Waiting for backend",
    val spot: Double? = null,
    val rows: Int = 0,
    val dataAgeSeconds: Double? = null,
    val feedDelaySeconds: Double? = null,
    val engineTickSeconds: Double = 1.0,
    val optionRefreshSeconds: Double = 2.0,
    val fullChainRefreshSeconds: Double = 60.0,
    val liveReady: Boolean = false,
    val liveReasons: List<String> = emptyList(),
    val sandboxConfigured: Boolean = false,
    val productionConfigured: Boolean = false,
)

private class BackendClient(private val idToken: String) {
    private val http = OkHttpClient()
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
                    try {
                        onStatus(parseStatus(text))
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
        val bodyJson = JSONObject().put("mode", mode)
        if (mode == "LIVE") bodyJson.put("confirmation", "ENABLE LIVE TRADING")
        post("/v1/mode", bodyJson)
    }

    suspend fun saveWebullCredentials(
        environment: String,
        appKey: String,
        appSecret: String,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        post(
            "/v1/broker/webull",
            JSONObject()
                .put("environment", environment)
                .put("app_key", appKey)
                .put("app_secret", appSecret),
        )
    }

    private fun post(path: String, json: JSONObject): Result<Unit> {
        if (baseUrl.isBlank()) return Result.failure(IllegalStateException("Backend URL is not configured"))
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
                    Result.failure(IllegalStateException(response.body?.string() ?: "HTTP ${response.code}"))
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
    val broker = root.optJSONObject("broker") ?: JSONObject()
    val decision = root.optJSONObject("decision") ?: JSONObject()
    val reasonsJson = gate.optJSONArray("reasons")
    val reasons = buildList {
        if (reasonsJson != null) {
            for (index in 0 until reasonsJson.length()) add(reasonsJson.optString(index))
        }
    }

    fun nullableDouble(obj: JSONObject, key: String): Double? =
        if (!obj.has(key) || obj.isNull(key)) null else obj.optDouble(key)

    return LiveStatus(
        mode = root.optString("mode", "PAPER"),
        decision = decision.optString("state", "RESEARCH_ONLY"),
        decisionReason = decision.optString("reason", ""),
        spot = nullableDouble(market, "spot"),
        rows = market.optInt("rows", 0),
        dataAgeSeconds = nullableDouble(market, "data_age_seconds"),
        feedDelaySeconds = nullableDouble(market, "feed_delay_seconds"),
        engineTickSeconds = cadence.optDouble("engine_tick_seconds", 1.0),
        optionRefreshSeconds = cadence.optDouble("sandbox_active_option_refresh_seconds", 2.0),
        fullChainRefreshSeconds = cadence.optDouble("full_chain_refresh_seconds", 60.0),
        liveReady = gate.optBoolean("ready", false),
        liveReasons = reasons,
        sandboxConfigured = broker.optJSONObject("sandbox")?.optBoolean("configured", false) ?: false,
        productionConfigured = broker.optJSONObject("production")?.optBoolean("configured", false) ?: false,
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
        val response = manager.getCredential(
            context = activity,
            request = request,
        )
        val credential = response.credential
        if (credential is CustomCredential &&
            credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
        ) {
            val google = GoogleIdTokenCredential.createFrom(credential.data)
            Result.success(google.idToken)
        } else {
            Result.failure(IllegalStateException("Google did not return an ID token"))
        }
    } catch (error: Exception) {
        Result.failure(error)
    }
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
                        .onSuccess { idToken = it }
                        .onFailure { signInError = it.message ?: "Google sign-in failed" }
                }
            },
        )
    } else {
        MainShell(
            activity = activity,
            idToken = idToken!!,
            onSignOut = { idToken = null },
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
        Text("Private trading research console")
        Spacer(Modifier.height(28.dp))
        if (BuildConfig.API_BASE_URL.isBlank() || BuildConfig.GOOGLE_WEB_CLIENT_ID.isBlank()) {
            Card {
                Column(Modifier.padding(16.dp)) {
                    Text("Setup required", fontWeight = FontWeight.Bold)
                    Text("The APK is built, but its Railway URL and Google OAuth client ID still need to be injected into the build.")
                }
            }
            Spacer(Modifier.height(16.dp))
        }
        Button(onClick = onSignIn, enabled = BuildConfig.GOOGLE_WEB_CLIENT_ID.isNotBlank()) {
            Text("Sign in with Google")
        }
        if (error != null) {
            Spacer(Modifier.height(12.dp))
            Text(error, color = MaterialTheme.colorScheme.error)
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
            Metric("Engine", "${status.engineTickSeconds.toInt()}s")
            Metric("Options", "${status.optionRefreshSeconds.toInt()}s")
            Metric("Rows", status.rows.toString())
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.padding(18.dp)) {
                Text("CURRENT DECISION", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(10.dp))
                Text(status.decision, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(status.decisionReason)
                Spacer(Modifier.height(10.dp))
                Text("Data age: ${status.dataAgeSeconds?.let { "%.1fs".format(it) } ?: "—"}")
                Text("Feed delay: ${status.feedDelaySeconds?.let { "%.0fs".format(it) } ?: "—"}")
            }
        }

        Text("MODE", fontWeight = FontWeight.Bold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("SHADOW", "PAPER").forEach { mode ->
                val selected = status.mode == mode
                if (selected) Button(onClick = {}) { Text(mode) }
                else OutlinedButton(onClick = {
                    scope.launch {
                        backend.setMode(mode)
                            .onSuccess { actionMessage = "$mode selected" }
                            .onFailure { actionMessage = it.message }
                    }
                }) { Text(mode) }
            }
            OutlinedButton(
                onClick = { showLiveConfirm = true },
                enabled = status.liveReady,
            ) { Text(if (status.liveReady) "LIVE" else "LIVE 🔒") }
        }

        if (!status.liveReady) {
            Text(
                "Live locked: ${status.liveReasons.joinToString().ifBlank { "production setup incomplete" }}",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        actionMessage?.let { Text(it) }

        Card {
            Column(Modifier.padding(16.dp)) {
                Text("RUNTIME", fontWeight = FontWeight.Bold)
                Text("Decision loop: every ${status.engineTickSeconds}s")
                Text("Active option refresh: every ${status.optionRefreshSeconds}s")
                Text("Full chain scan: every ${status.fullChainRefreshSeconds}s")
                Text("Sandbox Webull: ${if (status.sandboxConfigured) "configured" else "not saved in app"}")
                Text("Production Webull: ${if (status.productionConfigured) "configured" else "not configured"}")
            }
        }
    }

    if (showLiveConfirm) {
        AlertDialog(
            onDismissRequest = { showLiveConfirm = false },
            title = { Text("Enable live trading?") },
            text = { Text("This changes the backend to LIVE mode and can use real funds once the production executor is enabled.") },
            confirmButton = {
                Button(onClick = {
                    showLiveConfirm = false
                    scope.launch {
                        backend.setMode("LIVE")
                            .onSuccess { actionMessage = "LIVE enabled" }
                            .onFailure { actionMessage = it.message }
                    }
                }) { Text("Enable LIVE") }
            },
            dismissButton = { TextButton(onClick = { showLiveConfirm = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun SettingsScreen(status: LiveStatus, backend: BackendClient) {
    val scope = rememberCoroutineScope()
    var environment by remember { mutableStateOf("sandbox") }
    var appKey by remember { mutableStateOf("") }
    var appSecret by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Broker settings", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("Credentials are sent over HTTPS and encrypted on Railway. They are not stored in the APK.")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("sandbox", "production").forEach { item ->
                if (environment == item) Button(onClick = { environment = item }) { Text(item.uppercase()) }
                else OutlinedButton(onClick = { environment = item }) { Text(item.uppercase()) }
            }
        }

        OutlinedTextField(
            value = appKey,
            onValueChange = { appKey = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Webull App Key") },
            singleLine = true,
        )
        OutlinedTextField(
            value = appSecret,
            onValueChange = { appSecret = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Webull App Secret") },
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
        )
        Button(
            onClick = {
                scope.launch {
                    backend.saveWebullCredentials(environment, appKey.trim(), appSecret.trim())
                        .onSuccess {
                            appKey = ""
                            appSecret = ""
                            message = "${environment.uppercase()} credentials saved securely"
                        }
                        .onFailure { message = it.message ?: "Save failed" }
                }
            },
            enabled = appKey.length >= 8 && appSecret.length >= 8,
        ) { Text("Save Webull credentials") }

        message?.let { Text(it) }
        Card {
            Column(Modifier.padding(16.dp)) {
                Text("Current backend profiles", fontWeight = FontWeight.Bold)
                Text("Sandbox: ${if (status.sandboxConfigured) "configured" else "not configured through app"}")
                Text("Production: ${if (status.productionConfigured) "configured" else "not configured"}")
            }
        }
    }
}