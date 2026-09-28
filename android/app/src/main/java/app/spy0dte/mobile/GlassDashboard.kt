package app.spy0dte.mobile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch

internal data class GlassActions(
    val prepareTrade: suspend () -> Result<PreparedTrade>,
    val submitTrade: suspend (PreparedTrade) -> Result<String>,
    val setMode: suspend (String) -> Result<String>,
    val setPaperAutonomy: suspend (Boolean) -> Result<String>,
    val armRisk: suspend (Double, Double, Double, Int) -> Result<String>,
    val disarmRisk: suspend () -> Result<String>,
)

@Composable
internal fun GlassDashboard(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: GlassActions,
    onSignOut: (() -> Unit)?,
) {
    var tab by remember { mutableStateOf("LIVE") }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        GlassColors.BackgroundDeep,
                        GlassColors.Background,
                        Color(0xFF0A1230),
                    ),
                ),
            )
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        GlassBackdrop()
        Column(
            modifier = Modifier
                .fillMaxSize()
                .widthIn(max = 680.dp)
                .align(Alignment.TopCenter),
        ) {
            GlassHeader(status)
            Box(Modifier.weight(1f)) {
                when (tab) {
                    "POSITIONS" -> GlassPositionsScreen(status)
                    "ANALYTICS" -> GlassAnalyticsScreen(status)
                    "SETTINGS" -> GlassSettingsScreen(status, preview, actions, onSignOut)
                    else -> GlassLiveScreen(
                        status = status,
                        preview = preview,
                        connectionError = connectionError,
                        actions = actions,
                        onEditRisk = { tab = "SETTINGS" },
                    )
                }
            }
            GlassBottomNav(tab) { tab = it }
        }
    }
}

@Composable
private fun GlassBackdrop() {
    Canvas(Modifier.fillMaxSize()) {
        drawCircle(
            brush = Brush.radialGradient(
                listOf(GlassColors.Blue.copy(alpha = 0.20f), Color.Transparent),
                center = Offset(size.width * 0.14f, size.height * 0.18f),
                radius = size.width * 0.70f,
            ),
            radius = size.width * 0.70f,
            center = Offset(size.width * 0.14f, size.height * 0.18f),
        )
        drawCircle(
            brush = Brush.radialGradient(
                listOf(GlassColors.Violet.copy(alpha = 0.18f), Color.Transparent),
                center = Offset(size.width * 0.88f, size.height * 0.48f),
                radius = size.width * 0.64f,
            ),
            radius = size.width * 0.64f,
            center = Offset(size.width * 0.88f, size.height * 0.48f),
        )
        drawCircle(
            brush = Brush.radialGradient(
                listOf(GlassColors.Cyan.copy(alpha = 0.08f), Color.Transparent),
                center = Offset(size.width * 0.30f, size.height * 0.90f),
                radius = size.width * 0.55f,
            ),
            radius = size.width * 0.55f,
            center = Offset(size.width * 0.30f, size.height * 0.90f),
        )
    }
}

@Composable
private fun GlassHeader(status: ScreenStatus) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text("SPY", color = GlassColors.Text, fontSize = 28.sp, fontWeight = FontWeight.Black)
                Spacer(Modifier.width(8.dp))
                Text(
                    "0DTE",
                    color = GlassColors.Violet,
                    fontSize = 28.sp,
                    fontWeight = FontWeight.Black,
                )
            }
            Text(
                "AI-POWERED OPTIONS TRADING",
                color = GlassColors.TextFaint,
                fontSize = 9.sp,
                letterSpacing = 2.1.sp,
            )
        }
        Column(horizontalAlignment = Alignment.End) {
            GlassModePill(status.mode)
            Spacer(Modifier.height(5.dp))
            Text(
                if (status.connected) "●  Engine Connected" else "○  Connecting",
                color = if (status.connected) GlassColors.Mint else GlassColors.Amber,
                fontSize = 10.sp,
            )
        }
    }
}

@Composable
private fun GlassModePill(mode: String) {
    Surface(
        color = GlassColors.PanelSoft,
        shape = RoundedCornerShape(24.dp),
        border = BorderStroke(1.dp, GlassColors.Border),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 13.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(8.dp).background(if (mode == "LIVE") GlassColors.Mint else GlassColors.Cyan, CircleShape))
            Spacer(Modifier.width(8.dp))
            Text(mode, color = GlassColors.Text, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun GlassLiveScreen(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: GlassActions,
    onEditRisk: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var prepared by remember { mutableStateOf<PreparedTrade?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        connectionError?.let { GlassNotice("$it · retrying", danger = true) }
        if (preview) {
            GlassNotice(
                "Preview build · LIVE submission waits for owner authentication. Real Railway data remains visible.",
                danger = true,
            )
        }

        GlassMarketCard(status)
        GlassAiCard(status.ai, status.alert)
        GlassSetupCard(
            status = status,
            preview = preview,
            busy = submitting,
            onTrade = {
                scope.launch {
                    message = "Preparing exact Webull preview…"
                    actions.prepareTrade()
                        .onSuccess { prepared = it; message = null }
                        .onFailure { message = it.message }
                }
            },
            onEnableLive = {
                scope.launch {
                    actions.setMode("LIVE")
                        .onSuccess { message = it }
                        .onFailure { message = it.message }
                }
            },
        )
        GlassRiskCard(status.risk, onEditRisk)
        GlassAccountCard(status)
        GlassPositionCard(status)
        message?.let { GlassNotice(it, danger = it.contains("fail", true) || it.contains("lock", true)) }
        Spacer(Modifier.height(2.dp))
    }

    prepared?.let { trade ->
        AlertDialog(
            onDismissRequest = { if (!submitting) prepared = null },
            containerColor = GlassColors.PanelStrong,
            titleContentColor = GlassColors.Text,
            textContentColor = GlassColors.Text,
            title = { Text("Confirm Webull trade", fontWeight = FontWeight.Black) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "SPY ${trade.optionType} ${trimNumber(trade.strike)}",
                        fontSize = 21.sp,
                        fontWeight = FontWeight.Black,
                    )
                    GlassKeyValue("Expiration", trade.expiration)
                    GlassKeyValue("Quantity", trade.quantity.toString())
                    GlassKeyValue("Limit", money(trade.limitPrice))
                    GlassKeyValue("Maximum debit", money(trade.maxDebit))
                    GlassKeyValue("Authorization", "${trade.expiresSeconds}s")
                    Text(
                        "This confirmation authorizes only this exact order.",
                        color = GlassColors.TextMuted,
                        fontSize = 11.sp,
                    )
                }
            },
            confirmButton = {
                GlassPrimaryButton(
                    text = if (submitting) "Submitting…" else "Confirm Trade  →",
                    enabled = !submitting,
                    onClick = {
                        submitting = true
                        scope.launch {
                            actions.submitTrade(trade)
                                .onSuccess {
                                    message = it
                                    prepared = null
                                }
                                .onFailure { message = it.message ?: "Trade submission failed" }
                            submitting = false
                        }
                    },
                )
            },
            dismissButton = {
                TextButton(enabled = !submitting, onClick = { prepared = null }) {
                    Text("Cancel", color = GlassColors.TextMuted)
                }
            },
        )
    }
}

@Composable
private fun GlassMarketCard(status: ScreenStatus) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("SPY", color = GlassColors.TextMuted, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                Text(
                    status.spot?.let { "$%.2f".format(it) } ?: "—",
                    color = GlassColors.Text,
                    fontSize = 38.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = (-1).sp,
                )
                Spacer(Modifier.height(5.dp))
                Text(
                    if (status.connected) "● LIVE ENGINE LINK" else "○ ENGINE LINK",
                    color = if (status.connected) GlassColors.Mint else GlassColors.Amber,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Column(Modifier.width(148.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                GlassTelemetry("Data age", status.dataAge?.let { "%.1fs".format(it) } ?: "—")
                GlassTelemetry("Feed delay", status.feedDelay?.let { "%.0fs".format(it) } ?: "—")
                GlassTelemetry("Broker", status.broker.uppercase())
                GlassTelemetry("Feed", if (status.connected) "CONNECTED" else "WAITING", if (status.connected) GlassColors.Mint else GlassColors.Amber)
            }
        }
        Spacer(Modifier.height(12.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp)
                .clip(RoundedCornerShape(14.dp))
                .background(GlassColors.PanelSoft),
            contentAlignment = Alignment.Center,
        ) {
            Canvas(Modifier.fillMaxSize()) {
                val step = size.width / 8f
                for (i in 1..7) {
                    val x = step * i
                    drawLine(GlassColors.Border.copy(alpha = 0.22f), Offset(x, 0f), Offset(x, size.height), 1f)
                }
                val row = size.height / 3f
                for (i in 1..2) {
                    val y = row * i
                    drawLine(GlassColors.Border.copy(alpha = 0.18f), Offset(0f, y), Offset(size.width, y), 1f)
                }
            }
            Text(
                "CHART SERIES NOT EXPOSED BY BACKEND",
                color = GlassColors.TextFaint,
                fontSize = 8.sp,
                letterSpacing = 1.2.sp,
            )
        }
    }
}

@Composable
private fun GlassTelemetry(label: String, value: String, valueColor: Color = GlassColors.Text) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = GlassColors.TextFaint, fontSize = 9.sp)
        Text(value, color = valueColor, fontSize = 9.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun GlassAiCard(ai: AiDecisionState, alert: LiveAlert?) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("AI CONSENSUS", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black, modifier = Modifier.weight(1f))
            Text(
                if (ai.active) "● ACTIVE" else "○ WAITING",
                color = if (ai.active) GlassColors.Mint else GlassColors.TextFaint,
                fontSize = 9.sp,
                fontWeight = FontWeight.Bold,
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            GlassConsensusGauge(ai, alert, Modifier.size(132.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                GlassProbabilityRow("Quant", ai.quantProbabilityUp, GlassColors.Cyan)
                ai.providers.forEach { provider ->
                    val color = when (provider.provider.lowercase()) {
                        "openai" -> GlassColors.Mint
                        "anthropic" -> Color(0xFFFF8B68)
                        "gemini" -> GlassColors.Violet
                        else -> GlassColors.Blue
                    }
                    GlassProbabilityRow(providerDisplayName(provider.provider), provider.probabilityUp, color)
                }
                if (ai.providers.isEmpty()) GlassProbabilityRow("AI", ai.aiProbabilityUp, GlassColors.Violet)
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            GlassMicroMetric("AI weight", ai.effectiveWeight?.let(::percent) ?: "—", Modifier.weight(1f))
            GlassMicroMetric("Confidence", ai.consensusConfidence?.let(::percent) ?: "—", Modifier.weight(1f))
            GlassMicroMetric("Disagree", ai.disagreement?.let(::percent) ?: "—", Modifier.weight(1f))
        }
    }
}

@Composable
private fun GlassConsensusGauge(ai: AiDecisionState, alert: LiveAlert?, modifier: Modifier = Modifier) {
    val probability = ai.hybridProbabilityUp
    val directionProbability = when {
        probability == null -> null
        alert?.right.equals("PUT", true) || alert?.right.equals("P", true) -> 1.0 - probability
        else -> probability
    }
    val progress = (directionProbability ?: 0.0).coerceIn(0.0, 1.0).toFloat()
    val label = when {
        probability == null -> "WAITING"
        probability >= 0.53 -> "BULLISH"
        probability <= 0.47 -> "BEARISH"
        else -> "NEUTRAL"
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 9.dp.toPx()
            val inset = stroke / 2f + 4.dp.toPx()
            drawArc(
                color = GlassColors.Border.copy(alpha = 0.35f),
                startAngle = 135f,
                sweepAngle = 270f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
            drawArc(
                brush = Brush.sweepGradient(listOf(GlassColors.Violet, GlassColors.Cyan, GlassColors.Mint)),
                startAngle = 135f,
                sweepAngle = 270f * progress,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(directionProbability?.let(::percent) ?: "—", color = GlassColors.Text, fontSize = 28.sp, fontWeight = FontWeight.Black)
            Text(label, color = GlassColors.Mint, fontSize = 10.sp, fontWeight = FontWeight.Black)
        }
    }
}

@Composable
private fun GlassProbabilityRow(label: String, value: Double?, tint: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = GlassColors.TextMuted, fontSize = 10.sp, modifier = Modifier.width(62.dp))
        Box(
            Modifier
                .weight(1f)
                .height(7.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(GlassColors.Border.copy(alpha = 0.30f)),
        ) {
            Box(
                Modifier
                    .fillMaxHeight()
                    .fillMaxWidth((value ?: 0.0).coerceIn(0.0, 1.0).toFloat())
                    .background(Brush.horizontalGradient(listOf(tint.copy(alpha = 0.62f), tint))),
            )
        }
        Spacer(Modifier.width(8.dp))
        Text(value?.let(::percent) ?: "—", color = tint, fontSize = 10.sp, fontWeight = FontWeight.Bold, modifier = Modifier.width(40.dp), textAlign = TextAlign.End)
    }
}

@Composable
private fun GlassSetupCard(
    status: ScreenStatus,
    preview: Boolean,
    busy: Boolean,
    onTrade: () -> Unit,
    onEnableLive: () -> Unit,
) {
    val alert = status.alert
    GlassPanel(accent = GlassColors.Violet) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("TOP SETUP", color = GlassColors.TextMuted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                Text(
                    alert?.let { optionLabel(it.symbol, it.right) } ?: "Waiting for qualified setup",
                    color = GlassColors.Text,
                    fontSize = if (alert == null) 17.sp else 22.sp,
                    fontWeight = FontWeight.Black,
                )
            }
            Surface(
                color = GlassColors.Violet.copy(alpha = 0.14f),
                shape = RoundedCornerShape(18.dp),
                border = BorderStroke(1.dp, GlassColors.Violet.copy(alpha = 0.55f)),
            ) {
                Text(
                    "AI RECOMMENDED",
                    modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
                    color = GlassColors.Violet,
                    fontSize = 8.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            GlassMetric("Hybrid", status.ai.hybridProbabilityUp?.let(::percent) ?: "—", Modifier.weight(1f))
            GlassMetric("Bid / Ask", alert?.let { "${price(it.bid)} / ${price(it.ask)}" } ?: "—", Modifier.weight(1f))
            GlassMetric("Contracts", alert?.contracts?.takeIf { it > 0 }?.toString() ?: "—", Modifier.weight(1f))
        }
        Spacer(Modifier.height(12.dp))
        val canTrade = !preview && status.liveReady && status.mode == "LIVE" && alert != null && !busy
        GlassPrimaryButton(
            text = if (preview) "Owner login required" else if (busy) "Preparing…" else "Preview Trade  →",
            enabled = canTrade,
            onClick = onTrade,
            modifier = Modifier.fillMaxWidth(),
        )
        if (!preview && status.mode != "LIVE") {
            Spacer(Modifier.height(8.dp))
            GlassOutlineButton("Enable Live Mode", onEnableLive, Modifier.fillMaxWidth())
        }
        if (!status.liveReady && status.liveReasons.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(
                "Locked · ${status.liveReasons.joinToString(" · ")}",
                color = GlassColors.Amber,
                fontSize = 9.sp,
            )
        }
    }
}

@Composable
private fun GlassRiskCard(risk: RiskState, onEdit: () -> Unit) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("RISK CONTROLS", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black, modifier = Modifier.weight(1f))
            Text("EDIT", color = GlassColors.Cyan, fontSize = 9.sp, fontWeight = FontWeight.Bold, modifier = Modifier.clickable(onClick = onEdit).padding(6.dp))
        }
        Spacer(Modifier.height(9.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            GlassRiskTile("DAILY GAIN", risk.gain?.let { "+${money(it)}" } ?: "—", GlassColors.Mint, Modifier.weight(1f))
            GlassRiskTile("DAILY LOSS", risk.loss?.let { "-${money(it)}" } ?: "—", GlassColors.Red, Modifier.weight(1f))
            GlassRiskTile("MAX CONTRACTS", risk.contracts?.toString() ?: "—", GlassColors.Cyan, Modifier.weight(1f))
            GlassRiskTile("MAX EXPOSURE", risk.exposure?.let(::percent) ?: "—", GlassColors.Violet, Modifier.weight(1f))
        }
    }
}

@Composable
private fun GlassAccountCard(status: ScreenStatus) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${status.mode} ACCOUNT", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black, modifier = Modifier.weight(1f))
            Text(
                "● ${status.broker.uppercase()}",
                color = if (status.brokerConnected) GlassColors.Mint else GlassColors.TextFaint,
                fontSize = 9.sp,
                fontWeight = FontWeight.Bold,
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            GlassMetric("Cash", money(status.brokerState.cashAvailable ?: status.paperCash), Modifier.weight(1f))
            GlassMetric("Day P&L", signedMoney(status.brokerState.dailyPnl ?: status.paperPnl), Modifier.weight(1f), pnlColor(status.brokerState.dailyPnl ?: status.paperPnl))
            GlassMetric("Open Positions", status.brokerState.openPositions.toString(), Modifier.weight(1f))
        }
    }
}

@Composable
private fun GlassPositionCard(status: ScreenStatus) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("POSITIONS", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black, modifier = Modifier.weight(1f))
            Surface(
                color = if (status.brokerState.openPositions == 0) GlassColors.Mint.copy(alpha = 0.10f) else GlassColors.Violet.copy(alpha = 0.12f),
                shape = RoundedCornerShape(18.dp),
                border = BorderStroke(1.dp, if (status.brokerState.openPositions == 0) GlassColors.Mint.copy(alpha = 0.55f) else GlassColors.Violet.copy(alpha = 0.55f)),
            ) {
                Text(
                    if (status.brokerState.openPositions == 0) "FLAT" else "${status.brokerState.openPositions} OPEN",
                    modifier = Modifier.padding(horizontal = 11.dp, vertical = 5.dp),
                    color = if (status.brokerState.openPositions == 0) GlassColors.Mint else GlassColors.Violet,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        if (status.brokerState.openPositions == 0) {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("◇", color = GlassColors.TextFaint, fontSize = 31.sp)
                Text("No open positions", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                Text("The engine is scanning SPY 0DTE contracts.", color = GlassColors.TextMuted, fontSize = 10.sp)
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(3.dp).height(42.dp).background(Brush.verticalGradient(listOf(GlassColors.Cyan, GlassColors.Violet))))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("WEBULL LIVE POSITION", color = GlassColors.Text, fontWeight = FontWeight.Bold)
                    Text("${status.brokerState.openPositions} open · ${status.brokerState.pendingOrders} pending", color = GlassColors.TextMuted, fontSize = 10.sp)
                }
                Text(signedMoney(status.brokerState.openPnl), color = pnlColor(status.brokerState.openPnl), fontWeight = FontWeight.Black)
            }
        }
    }
}

@Composable
private fun GlassPositionsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        GlassSectionTitle("Positions")
        GlassPositionCard(status)
        GlassPanel {
            GlassKeyValue("Pending broker orders", status.brokerState.pendingOrders.toString())
            GlassKeyValue("Open P&L", signedMoney(status.brokerState.openPnl), pnlColor(status.brokerState.openPnl))
            GlassKeyValue("Day P&L", signedMoney(status.brokerState.dailyPnl), pnlColor(status.brokerState.dailyPnl))
            GlassKeyValue("Maximum new debit", money(status.brokerState.maxEntryDebit))
            GlassKeyValue("Entry eligible", if (status.brokerState.entryAllowed) "YES" else "NO", if (status.brokerState.entryAllowed) GlassColors.Mint else GlassColors.Amber)
        }
        if (status.brokerState.openPositions > 0) {
            GlassNotice("The status stream currently exposes reconciled position count and P&L, not full per-contract position details.")
        }
    }
}

@Composable
private fun GlassAnalyticsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        GlassSectionTitle("Analytics")
        GlassAiCard(status.ai, status.alert)
        GlassPanel {
            Text("ENGINE", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(9.dp))
            GlassKeyValue("Strategy", status.strategy)
            GlassKeyValue("Risk", status.riskProfile)
            GlassKeyValue("Exit", status.exitProfile)
            GlassKeyValue("Decision", status.decision)
            Spacer(Modifier.height(6.dp))
            Text(status.reason, color = GlassColors.TextMuted, fontSize = 10.sp)
        }
        GlassPanel {
            Text("PAPER LEDGER", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(9.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                GlassMetric("Cash", money(status.paperCash), Modifier.weight(1f))
                GlassMetric("P&L", signedMoney(status.paperPnl), Modifier.weight(1f), pnlColor(status.paperPnl))
                GlassMetric("Trades", status.paperTrades.toString(), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun GlassSettingsScreen(
    status: ScreenStatus,
    preview: Boolean,
    actions: GlassActions,
    onSignOut: (() -> Unit)?,
) {
    val scope = rememberCoroutineScope()
    var loss by remember(status.risk.loss) { mutableStateOf(status.risk.loss?.toString() ?: "25") }
    var gain by remember(status.risk.gain) { mutableStateOf(status.risk.gain?.toString() ?: "40") }
    var exposure by remember(status.risk.exposure) { mutableStateOf(status.risk.exposure?.let { (it * 100).toString() } ?: "20") }
    var contracts by remember(status.risk.contracts) { mutableStateOf(status.risk.contracts?.toString() ?: "1") }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        GlassSectionTitle("Settings")
        GlassPanel {
            Text("TRADING MODE", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(9.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                listOf("LIVE", "PAPER", "SHADOW").forEach { mode ->
                    val active = status.mode == mode
                    OutlinedButton(
                        onClick = {
                            scope.launch {
                                actions.setMode(mode)
                                    .onSuccess { message = it }
                                    .onFailure { message = it.message }
                            }
                        },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.outlinedButtonColors(
                            containerColor = if (active) GlassColors.Blue.copy(alpha = 0.15f) else Color.Transparent,
                            contentColor = if (active) GlassColors.Cyan else GlassColors.TextMuted,
                        ),
                        border = BorderStroke(1.dp, if (active) GlassColors.Blue else GlassColors.Border),
                        shape = RoundedCornerShape(16.dp),
                    ) { Text(mode, fontSize = 10.sp, fontWeight = FontWeight.Bold) }
                }
            }
        }

        GlassPanel {
            Text("DAILY LIVE ENVELOPE", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Text("Server-authoritative limits. New entries also stop while a position or broker order is open.", color = GlassColors.TextMuted, fontSize = 10.sp)
            Spacer(Modifier.height(9.dp))
            GlassTextField(loss, { loss = it }, "Daily loss stop ($)")
            GlassTextField(gain, { gain = it }, "Daily gain stop ($)")
            GlassTextField(exposure, { exposure = it }, "Max account exposure (%)")
            GlassTextField(contracts, { contracts = it }, "Max contracts")
            Spacer(Modifier.height(4.dp))
            GlassPrimaryButton(
                text = "Arm Today's Limits",
                enabled = true,
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    val l = loss.toDoubleOrNull()
                    val g = gain.toDoubleOrNull()
                    val e = exposure.toDoubleOrNull()?.div(100.0)
                    val c = contracts.toIntOrNull()
                    if (l == null || g == null || e == null || c == null) {
                        message = "Enter valid numeric limits"
                    } else {
                        scope.launch {
                            actions.armRisk(l, g, e, c)
                                .onSuccess { message = it }
                                .onFailure { message = it.message }
                        }
                    }
                },
            )
            Spacer(Modifier.height(7.dp))
            GlassOutlineButton(
                text = "Disarm Live",
                onClick = {
                    scope.launch {
                        actions.disarmRisk()
                            .onSuccess { message = it }
                            .onFailure { message = it.message }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }

        GlassPanel {
            Text("AUTONOMOUS PAPER", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(7.dp))
            GlassKeyValue("Cash", money(status.paperCash))
            GlassKeyValue("Realized P&L", signedMoney(status.paperPnl), pnlColor(status.paperPnl))
            GlassKeyValue("Trades", status.paperTrades.toString())
            Spacer(Modifier.height(8.dp))
            GlassOutlineButton(
                text = if (status.paperArmed) "Stop New Paper Entries" else "Arm Autonomous Paper",
                onClick = {
                    scope.launch {
                        actions.setPaperAutonomy(!status.paperArmed)
                            .onSuccess { message = it }
                            .onFailure { message = it.message }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }

        GlassPanel {
            Text("BROKER", color = GlassColors.Text, fontSize = 13.sp, fontWeight = FontWeight.Black)
            Spacer(Modifier.height(7.dp))
            GlassKeyValue("Provider", status.broker.uppercase())
            GlassKeyValue("API configured", if (status.brokerConfigured) "YES" else "NO")
            GlassKeyValue("Account connected", if (status.brokerConnected) "YES" else "NO")
            GlassKeyValue("Owner auth", if (preview) "LOCKED" else "ACTIVE", if (preview) GlassColors.Amber else GlassColors.Mint)
            onSignOut?.let {
                Spacer(Modifier.height(8.dp))
                GlassOutlineButton("Sign Out", it, Modifier.fillMaxWidth())
            }
        }
        if (preview) GlassNotice("Owner login is not configured in this APK, so Webull submission remains locked.", danger = true)
        message?.let { GlassNotice(it, danger = it.contains("fail", true) || it.contains("invalid", true)) }
        Spacer(Modifier.height(2.dp))
    }
}

@Composable
private fun GlassBottomNav(selected: String, onSelect: (String) -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp)
            .background(GlassColors.Border.copy(alpha = 0.55f), RoundedCornerShape(28.dp))
            .padding(1.dp)
            .background(GlassColors.PanelStrong, RoundedCornerShape(27.dp)),
    ) {
        Row(Modifier.fillMaxWidth().height(58.dp), verticalAlignment = Alignment.CenterVertically) {
            listOf(
                "LIVE" to "⌁",
                "POSITIONS" to "☷",
                "ANALYTICS" to "▥",
                "SETTINGS" to "⚙",
            ).forEach { (label, icon) ->
                val active = selected == label
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .padding(horizontal = 3.dp, vertical = 6.dp)
                        .clip(RoundedCornerShape(22.dp))
                        .background(
                            if (active) Brush.horizontalGradient(listOf(GlassColors.Violet.copy(alpha = 0.52f), GlassColors.Blue.copy(alpha = 0.45f)))
                            else Brush.horizontalGradient(listOf(Color.Transparent, Color.Transparent)),
                        )
                        .clickable { onSelect(label) },
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(icon, color = if (active) GlassColors.Text else GlassColors.TextFaint, fontSize = 16.sp)
                        Text(
                            label.lowercase().replaceFirstChar { it.uppercase() },
                            color = if (active) GlassColors.Text else GlassColors.TextMuted,
                            fontSize = 8.sp,
                            fontWeight = if (active) FontWeight.Bold else FontWeight.Normal,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun GlassPanel(
    accent: Color = GlassColors.Blue,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = RoundedCornerShape(22.dp)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                Brush.linearGradient(
                    listOf(
                        GlassColors.BorderBright.copy(alpha = 0.70f),
                        accent.copy(alpha = 0.38f),
                        GlassColors.Violet.copy(alpha = 0.28f),
                    ),
                ),
                shape,
            )
            .padding(1.dp)
            .background(
                Brush.linearGradient(
                    listOf(
                        Color(0xB81A2A52),
                        Color(0x9D122040),
                        Color(0xB0172148),
                    ),
                ),
                shape,
            ),
    ) {
        Column(Modifier.fillMaxWidth().padding(15.dp), content = content)
    }
}

@Composable
private fun GlassNotice(text: String, danger: Boolean = false) {
    val tint = if (danger) GlassColors.Red else GlassColors.Cyan
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(tint.copy(alpha = 0.22f), RoundedCornerShape(16.dp))
            .padding(1.dp)
            .background(GlassColors.PanelStrong, RoundedCornerShape(15.dp)),
    ) {
        Text(text, modifier = Modifier.padding(11.dp), color = if (danger) GlassColors.Red else GlassColors.TextMuted, fontSize = 10.sp)
    }
}

@Composable
private fun GlassPrimaryButton(
    text: String,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val brush = if (enabled) {
        Brush.horizontalGradient(listOf(GlassColors.Blue, GlassColors.Violet))
    } else {
        Brush.horizontalGradient(listOf(GlassColors.PanelSoft, GlassColors.PanelSoft))
    }
    Box(
        modifier = modifier
            .height(48.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(brush)
            .then(if (enabled) Modifier.clickable(onClick = onClick) else Modifier),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = if (enabled) GlassColors.Text else GlassColors.TextFaint, fontWeight = FontWeight.Black, fontSize = 12.sp)
    }
}

@Composable
private fun GlassOutlineButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.dp, GlassColors.BorderBright.copy(alpha = 0.65f)),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = GlassColors.Text),
    ) {
        Text(text, fontWeight = FontWeight.Bold, fontSize = 11.sp)
    }
}

@Composable
private fun GlassTextField(value: String, onValueChange: (String) -> Unit, label: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        label = { Text(label) },
        shape = RoundedCornerShape(16.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = GlassColors.Cyan,
            unfocusedBorderColor = GlassColors.Border,
            focusedLabelColor = GlassColors.Cyan,
            unfocusedLabelColor = GlassColors.TextMuted,
            focusedTextColor = GlassColors.Text,
            unfocusedTextColor = GlassColors.Text,
            cursorColor = GlassColors.Cyan,
            focusedContainerColor = GlassColors.PanelSoft,
            unfocusedContainerColor = GlassColors.PanelSoft,
        ),
    )
}

@Composable
private fun GlassRiskTile(label: String, value: String, tint: Color, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = GlassColors.PanelSoft,
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.dp, GlassColors.Border.copy(alpha = 0.55f)),
    ) {
        Column(Modifier.padding(horizontal = 7.dp, vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.size(26.dp).background(tint.copy(alpha = 0.14f), CircleShape), contentAlignment = Alignment.Center) {
                Text("•", color = tint, fontSize = 18.sp)
            }
            Spacer(Modifier.height(5.dp))
            Text(label, color = GlassColors.TextFaint, fontSize = 7.sp, maxLines = 1)
            Text(value, color = tint, fontSize = 12.sp, fontWeight = FontWeight.Black, maxLines = 1)
        }
    }
}

@Composable
private fun GlassMetric(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = GlassColors.Text) {
    Box(
        modifier = modifier
            .background(GlassColors.PanelSoft, RoundedCornerShape(15.dp))
            .padding(horizontal = 10.dp, vertical = 9.dp),
    ) {
        Column {
            Text(label.uppercase(), color = GlassColors.TextFaint, fontSize = 8.sp, letterSpacing = 0.6.sp)
            Spacer(Modifier.height(2.dp))
            Text(value, color = valueColor, fontSize = 13.sp, fontWeight = FontWeight.Black, maxLines = 1)
        }
    }
}

@Composable
private fun GlassMicroMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .background(GlassColors.PanelSoft, RoundedCornerShape(14.dp))
            .padding(horizontal = 8.dp, vertical = 7.dp),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
            Text(label.uppercase(), color = GlassColors.TextFaint, fontSize = 7.sp)
            Text(value, color = GlassColors.Cyan, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun GlassKeyValue(label: String, value: String, valueColor: Color = GlassColors.Text) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = GlassColors.TextMuted, fontSize = 10.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 10.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.End)
    }
}

@Composable
private fun GlassSectionTitle(title: String) {
    Text(title, color = GlassColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Black, modifier = Modifier.padding(top = 4.dp))
}

private fun money(value: Double?): String = value?.let { "$%.2f".format(it) } ?: "—"
private fun price(value: Double?): String = value?.let { "%.2f".format(it) } ?: "—"
private fun signedMoney(value: Double?): String = value?.let { (if (it >= 0) "+" else "") + "$%.2f".format(it) } ?: "—"
private fun percent(value: Double): String = "%.0f%%".format(value.coerceIn(0.0, 1.0) * 100.0)
private fun trimNumber(value: Double): String = if (value % 1.0 == 0.0) value.toInt().toString() else "%.1f".format(value)
private fun pnlColor(value: Double?): Color = when {
    value == null -> GlassColors.Text
    value > 0 -> GlassColors.Mint
    value < 0 -> GlassColors.Red
    else -> GlassColors.Text
}

private fun providerDisplayName(provider: String): String = when (provider.lowercase()) {
    "openai" -> "OpenAI"
    "anthropic" -> "Claude"
    "gemini" -> "Gemini"
    else -> provider.replaceFirstChar { it.uppercase() }
}

private fun optionLabel(symbol: String, right: String): String {
    val match = Regex("^([A-Z]{1,6})(\\d{6})([CP])(\\d{8})$").matchEntire(symbol.uppercase())
        ?: return symbol
    val underlying = match.groupValues[1]
    val yymmdd = match.groupValues[2]
    val cp = match.groupValues[3]
    val strike = match.groupValues[4].toIntOrNull()?.div(1000.0)
    val expiration = runCatching {
        LocalDate.parse("20$yymmdd", DateTimeFormatter.ofPattern("yyyyMMdd"))
    }.getOrNull()
    val dte = expiration?.let { java.time.temporal.ChronoUnit.DAYS.between(LocalDate.now(), it).coerceAtLeast(0) }
    val rightLetter = if (right.equals("PUT", true) || cp == "P") "P" else "C"
    return buildString {
        append(underlying)
        append(' ')
        append(strike?.let(::trimNumber) ?: "?")
        append(rightLetter)
        dte?.let { append("  ${it}DTE") }
    }
}
