package app.spy0dte.mobile

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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

class PreviewActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    LoginFreePreview()
                }
            }
        }
    }
}

@Composable
private fun LoginFreePreview() {
    var tab by remember { mutableStateOf("LIVE") }
    var mode by remember { mutableStateOf("PAPER") }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text("SPY 0DTE", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text("PREVIEW BUILD • LOGIN OFF")
            }
            Text(mode, fontWeight = FontWeight.Bold)
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf("LIVE", "SETTINGS").forEach { name ->
                if (tab == name) {
                    Button(onClick = { tab = name }) { Text(name) }
                } else {
                    OutlinedButton(onClick = { tab = name }) { Text(name) }
                }
            }
        }

        if (tab == "LIVE") {
            PreviewLive(mode = mode, onModeChange = { mode = it })
        } else {
            PreviewSettings()
        }
    }
}

@Composable
private fun PreviewLive(mode: String, onModeChange: (String) -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.fillMaxWidth().padding(14.dp)) {
                Text("LOGIN TEMPORARILY REMOVED", fontWeight = FontWeight.Bold)
                Text("This build opens directly into the app so you can inspect the Android UI before we wire authentication back in.")
            }
        }

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            PreviewMetric("SPY", "—")
            PreviewMetric("Data", "—")
            PreviewMetric("Engine", "1s")
            PreviewMetric("Rows", "0")
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.fillMaxWidth().padding(18.dp)) {
                Text("CURRENT DECISION", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(8.dp))
                Text("PREVIEW", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text("Live backend data is intentionally not authenticated in this preview build.")
                Text("Feed delay: —")
            }
        }

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("PAPER ACCOUNT", fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(8.dp))
                Text("Starting cash: $115.00")
                Text("Settled cash: $115.00")
                Text("Unsettled cash: $0.00")
                Text("Realized P&L: $0.00")
                Text("Open positions: 0")
                Text("Trades: 0")
            }
        }

        Text("MODE", fontWeight = FontWeight.Bold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("SHADOW", "PAPER").forEach { item ->
                if (mode == item) {
                    Button(onClick = { onModeChange(item) }) { Text(item) }
                } else {
                    OutlinedButton(onClick = { onModeChange(item) }) { Text(item) }
                }
            }
            OutlinedButton(onClick = {}, enabled = false) { Text("LIVE 🔒") }
        }

        Text(
            "Live stays locked in this preview build. No real orders can be sent.",
            style = MaterialTheme.typography.bodySmall,
        )

        Card {
            Column(Modifier.fillMaxWidth().padding(16.dp)) {
                Text("RUNTIME", fontWeight = FontWeight.Bold)
                Text("Decision loop target: every 1s")
                Text("Active option target: every 2s")
                Text("Full chain target: every 60s")
                Text("Sandbox Webull: preview only")
                Text("Production Webull: not configured")
            }
        }
    }
}

@Composable
private fun PreviewMetric(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun PreviewSettings() {
    var environment by remember { mutableStateOf("sandbox") }
    var paperCash by remember { mutableStateOf("115.00") }
    var appKey by remember { mutableStateOf("") }
    var appSecret by remember { mutableStateOf("") }
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
        Button(onClick = { message = "Preview only — paper account was not changed." }) {
            Text("Reset paper account")
        }

        Spacer(Modifier.height(8.dp))
        Text("Broker settings", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("These controls are visible so you can inspect the flow. This preview does not send credentials anywhere.")

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("sandbox", "production").forEach { item ->
                if (environment == item) {
                    Button(onClick = { environment = item }) { Text(item.uppercase()) }
                } else {
                    OutlinedButton(onClick = { environment = item }) { Text(item.uppercase()) }
                }
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
            onClick = { message = "Preview only — credentials were not saved." },
            enabled = appKey.length >= 8 && appSecret.length >= 8,
        ) {
            Text("Save Webull credentials")
        }

        message?.let { Text(it) }
    }
}
