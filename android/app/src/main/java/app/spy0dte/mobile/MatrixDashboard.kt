package app.spy0dte.mobile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.Button
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

internal data class MatrixActions(
    val prepareTrade: suspend () -> Result<PreparedTrade>,
    val submitTrade: suspend (PreparedTrade) -> Result<String>,
    val setMode: suspend (String) -> Result<String>,
    val setPaperAutonomy: suspend (Boolean) -> Result<String>,
    val armRisk: suspend (Double, Double, Double, Int) -> Result<String>,
    val disarmRisk: suspend () -> Result<String>,
)

@Composable
internal fun MatrixDashboard(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: MatrixActions,
    onSignOut: (() -> Unit)?,
) {
    var bottomTab by remember { mutableStateOf("LIVE") }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MatrixColors.Background)
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        MatrixBackground()
        Column(
            modifier = Modifier
                .fillMaxSize()
                .widthIn(max = 620.dp)
                .align(Alignment.TopCenter),
        ) {
            MatrixHeader(status)
            Box(Modifier.weight(1f)) {
                when (bottomTab) {
                    "POSITIONS" -> MatrixPositionsScreen(status)
                    "ANALYTICS" -> MatrixAnalyticsScreen(status)
                    "SETTINGS" -> MatrixSettingsScreen(
                        status = status,
                        preview = preview,
                        actions = actions,
                        onSignOut = onSignOut,
                    )
                    else -> MatrixLiveScreen(
                        status = status,
                        preview = preview,
                        connectionError = connectionError,
                        actions = actions,
                        onEditRisk = { bottomTab = "SETTINGS" },
                    )
                }
            }
            MatrixBottomNav(bottomTab) { bottomTab = it }
        }
    }
}

@Composable
private fun MatrixBackground() {
    Canvas(Modifier.fillMaxSize()) {
        val step = 34.dp.toPx()
        var x = 0f
        while (x < size.width) {
            drawLine(
                color = MatrixColors.Neon.copy(alpha = 0.025f),
                start = Offset(x, 0f),
                end = Offset(x, size.height),
                strokeWidth = 1f,
            )
            x += step
        }
        var y = 0f
        while (y < size.height) {
            drawLine(
                color = MatrixColors.Neon.copy(alpha = 0.02f),
                start = Offset(0f, y),
                end = Offset(size.width, y),
                strokeWidth = 1f,
            )
            y += step
        }
    }
}

@Composable
private fun MatrixHeader(status: ScreenStatus) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(36.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(MatrixColors.SurfaceRaised)
                .border(1.dp, MatrixColors.BorderSoft, RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Text("☰", color = MatrixColors.Neon, fontSize = 19.sp)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("SPY 0DTE", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 18.sp)
            Text(
                if (status.connected) "RAILWAY ONLINE" else "RAILWAY CONNECTING",
                color = if (status.connected) MatrixColors.Neon else MatrixColors.Amber,
                fontSize = 9.sp,
                letterSpacing = 1.2.sp,
            )
        }
        Surface(
            shape = RoundedCornerShape(18.dp),
            color = MatrixColors.Neon.copy(alpha = 0.10f),
            border = BorderStroke(1.dp, MatrixColors.Neon),
        ) {
            Text(
                "${status.mode}  ▾",
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 7.dp),
                color = MatrixColors.NeonBright,
                fontWeight = FontWeight.Bold,
                fontSize = 12.sp,
            )
        }
    }
}

@Composable
private fun MatrixLiveScreen(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: MatrixActions,
    onEditRisk: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var prepared by remember { mutableStateOf<PreparedTrade?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }
    var topTab by remember { mutableStateOf("AI") }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        MatrixHero(status)
        MatrixTopTabs(topTab) { topTab = it }

        connectionError?.let { MatrixNotice(it, danger = true) }
        if (preview) {
            MatrixNotice(
                "PREVIEW BUILD · LIVE submission waits for owner authentication. The complete trade UI remains visible.",
                danger = true,
            )
        }

        when (topTab) {
            "AI" -> {
                MatrixAiConsensus(status.ai, status.alert)
                MatrixSetupCard(
                    status = status,
                    preview = preview,
                    busy = submitting,
                    onTrade = {
                        scope.launch {
                            message = "PREPARING EXACT WEBULL PREVIEW…"
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
                MatrixRiskCard(status.risk, onEditRisk)
                MatrixLiveDataCard(status)
                MatrixPositionCard(status)
            }
            "CHART" -> MatrixCapabilityCard(
                "LIVE CHART",
                "The Matrix shell is ready for the real-time chart surface. No synthetic candles are rendered when the backend has no chart series.",
            )
            "OPTIONS" -> MatrixSetupCard(
                status = status,
                preview = preview,
                busy = submitting,
                onTrade = {
                    scope.launch {
                        actions.prepareTrade()
                            .onSuccess { prepared = it }
                            .onFailure { message = it.message }
                    }
                },
                onEnableLive = {
                    scope.launch { actions.setMode("LIVE") }
                },
            )
            "FLOW" -> MatrixCapabilityCard(
                "FLOW",
                "No options-flow feed is configured, so this panel stays explicitly empty instead of fabricating institutional flow.",
            )
            else -> MatrixCapabilityCard(
                "NEWS",
                "The trading AIs are currently tape-only by design. No outside-news feed is shown here until a timestamp-safe news source is connected.",
            )
        }

        message?.let { MatrixNotice(it, danger = it.contains("fail", true) || it.contains("lock", true)) }
        Spacer(Modifier.height(4.dp))
    }

    prepared?.let { trade ->
        AlertDialog(
            onDismissRequest = { if (!submitting) prepared = null },
            containerColor = MatrixColors.SurfaceRaised,
            titleContentColor = MatrixColors.NeonBright,
            textContentColor = MatrixColors.Text,
            title = { Text("CONFIRM WEBULL TRADE", fontWeight = FontWeight.Black) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    Text("SPY ${trade.optionType} ${trimNumber(trade.strike)}", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    MatrixKeyValue("Expiration", trade.expiration)
                    MatrixKeyValue("Quantity", trade.quantity.toString())
                    MatrixKeyValue("Limit", money(trade.limitPrice))
                    MatrixKeyValue("Maximum debit", money(trade.maxDebit))
                    MatrixKeyValue("Authorization", "${trade.expiresSeconds}s")
                    Text(
                        "This confirmation can submit only this exact order.",
                        color = MatrixColors.TextMuted,
                        fontSize = 12.sp,
                    )
                }
            },
            confirmButton = {
                MatrixPrimaryButton(
                    text = if (submitting) "SUBMITTING…" else "CONFIRM TRADE  >>>",
                    enabled = !submitting,
                    onClick = {
                        submitting = true
                        scope.launch {
                            actions.submitTrade(trade)
                                .onSuccess { result ->
                                    message = result
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
                    Text("CANCEL", color = MatrixColors.TextMuted)
                }
            },
        )
    }
}

@Composable
private fun MatrixHero(status: ScreenStatus) {
    Row(
        modifier = Modifier.fillMaxWidth().height(132.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(start = 6.dp)) {
            Text(
                status.spot?.let { "%.2f".format(it) } ?: "—",
                color = MatrixColors.Text,
                fontWeight = FontWeight.Black,
                fontSize = 38.sp,
                letterSpacing = (-1).sp,
            )
            Text(
                if (status.connected) "● LIVE ENGINE LINK" else "○ ENGINE LINK",
                color = if (status.connected) MatrixColors.Neon else MatrixColors.Amber,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "DATA AGE ${status.dataAge?.let { "%.1fs".format(it) } ?: "—"}   ·   FEED ${status.feedDelay?.let { "%.0fs".format(it) } ?: "—"}",
                color = MatrixColors.TextMuted,
                fontSize = 9.sp,
                letterSpacing = 0.8.sp,
            )
        }
        MatrixGlobe(Modifier.size(126.dp))
    }
}

@Composable
private fun MatrixGlobe(modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val center = Offset(size.width / 2f, size.height / 2f)
        val radius = size.minDimension * 0.36f
        drawCircle(MatrixColors.Neon.copy(alpha = 0.05f), radius * 1.45f, center)
        drawCircle(MatrixColors.Neon.copy(alpha = 0.12f), radius * 1.15f, center)
        drawCircle(MatrixColors.Neon, radius, center, style = Stroke(width = 1.7.dp.toPx()))
        drawCircle(MatrixColors.Neon.copy(alpha = 0.35f), radius * 0.72f, center, style = Stroke(width = 1.dp.toPx()))
        drawOval(
            color = MatrixColors.Neon.copy(alpha = 0.55f),
            topLeft = Offset(center.x - radius * 0.55f, center.y - radius),
            size = Size(radius * 1.1f, radius * 2f),
            style = Stroke(width = 1.dp.toPx()),
        )
        drawOval(
            color = MatrixColors.Neon.copy(alpha = 0.35f),
            topLeft = Offset(center.x - radius, center.y - radius * 0.42f),
            size = Size(radius * 2f, radius * 0.84f),
            style = Stroke(width = 1.dp.toPx()),
        )
        for (i in -2..2) {
            val yy = center.y + i * radius * 0.34f
            drawLine(
                MatrixColors.Neon.copy(alpha = 0.25f),
                Offset(center.x - radius * 0.94f, yy),
                Offset(center.x + radius * 0.94f, yy),
                0.7.dp.toPx(),
            )
        }
        val path = Path().apply {
            moveTo(0f, size.height * 0.72f)
            lineTo(size.width * 0.16f, size.height * 0.63f)
            lineTo(size.width * 0.27f, size.height * 0.70f)
            lineTo(size.width * 0.42f, size.height * 0.50f)
            lineTo(size.width * 0.54f, size.height * 0.57f)
            lineTo(size.width * 0.68f, size.height * 0.34f)
            lineTo(size.width * 0.80f, size.height * 0.43f)
            lineTo(size.width, size.height * 0.20f)
        }
        drawPath(path, MatrixColors.NeonBright.copy(alpha = 0.75f), style = Stroke(width = 1.3.dp.toPx()))
    }
}

@Composable
private fun MatrixTopTabs(selected: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, MatrixColors.BorderSoft, RoundedCornerShape(7.dp))
            .background(MatrixColors.Surface.copy(alpha = 0.85f), RoundedCornerShape(7.dp))
            .padding(3.dp),
    ) {
        listOf("AI", "CHART", "OPTIONS", "FLOW", "NEWS").forEach { label ->
            val active = selected == label
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(6.dp))
                    .background(if (active) MatrixColors.Neon.copy(alpha = 0.12f) else Color.Transparent)
                    .border(
                        width = if (active) 1.dp else 0.dp,
                        color = if (active) MatrixColors.Neon else Color.Transparent,
                        shape = RoundedCornerShape(6.dp),
                    )
                    .clickable { onSelect(label) }
                    .padding(vertical = 9.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    color = if (active) MatrixColors.NeonBright else MatrixColors.Text,
                    fontSize = 10.sp,
                    fontWeight = if (active) FontWeight.Black else FontWeight.Medium,
                )
            }
        }
    }
}

@Composable
private fun MatrixAiConsensus(ai: AiDecisionState, alert: LiveAlert?) {
    MatrixPanel {
        Text("AI CONSENSUS", color = MatrixColors.Text, fontSize = 12.sp, fontWeight = FontWeight.Black)
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            MatrixConsensusGauge(ai, alert, Modifier.size(134.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                MatrixProbabilityRow("Quant", ai.quantProbabilityUp)
                ai.providers.forEach { provider ->
                    MatrixProbabilityRow(providerDisplayName(provider.provider), provider.probabilityUp)
                }
                if (ai.providers.isEmpty()) MatrixProbabilityRow("AI", ai.aiProbabilityUp)
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MatrixMicroChip("AI WEIGHT", ai.effectiveWeight?.let { percent(it) } ?: "—", Modifier.weight(1f))
            MatrixMicroChip("CONF", ai.consensusConfidence?.let { percent(it) } ?: "—", Modifier.weight(1f))
            MatrixMicroChip("DISAGREE", ai.disagreement?.let { percent(it) } ?: "—", Modifier.weight(1f))
        }
    }
}

@Composable
private fun MatrixConsensusGauge(ai: AiDecisionState, alert: LiveAlert?, modifier: Modifier = Modifier) {
    val probability = ai.hybridProbabilityUp
    val directionProbability = when {
        probability == null -> null
        alert?.right.equals("PUT", true) || alert?.right.equals("P", true) -> 1.0 - probability
        else -> probability
    }
    val gauge = (directionProbability ?: 0.0).coerceIn(0.0, 1.0).toFloat()
    val label = when {
        probability == null -> "WAITING"
        probability >= 0.53 -> "BULLISH"
        probability <= 0.47 -> "BEARISH"
        else -> "NEUTRAL"
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 7.dp.toPx()
            val inset = stroke / 2f + 3.dp.toPx()
            drawArc(
                color = MatrixColors.BorderSoft,
                startAngle = 135f,
                sweepAngle = 270f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
            drawArc(
                brush = Brush.sweepGradient(listOf(MatrixColors.NeonDim, MatrixColors.NeonBright, MatrixColors.Neon)),
                startAngle = 135f,
                sweepAngle = 270f * gauge,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
            val r = size.minDimension * 0.32f
            val c = Offset(size.width / 2f, size.height / 2f)
            val points = List(6) { index ->
                val angle = Math.toRadians((60.0 * index - 30.0))
                Offset(c.x + (r * kotlin.math.cos(angle)).toFloat(), c.y + (r * kotlin.math.sin(angle)).toFloat())
            }
            val hex = Path().apply {
                moveTo(points.first().x, points.first().y)
                points.drop(1).forEach { lineTo(it.x, it.y) }
                close()
            }
            drawPath(hex, MatrixColors.Neon.copy(alpha = 0.16f))
            drawPath(hex, MatrixColors.Neon, style = Stroke(1.dp.toPx()))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("AI", color = MatrixColors.TextMuted, fontSize = 9.sp, letterSpacing = 1.sp)
            Text(
                directionProbability?.let { percent(it) } ?: "—",
                color = MatrixColors.Text,
                fontSize = 27.sp,
                fontWeight = FontWeight.Black,
            )
            Text(label, color = MatrixColors.Neon, fontSize = 10.sp, fontWeight = FontWeight.Black)
        }
    }
}

@Composable
private fun MatrixProbabilityRow(label: String, value: Double?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = MatrixColors.TextMuted, fontSize = 10.sp, modifier = Modifier.width(64.dp))
        Box(
            Modifier
                .weight(1f)
                .height(6.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(MatrixColors.BorderSoft),
        ) {
            Box(
                Modifier
                    .fillMaxHeight()
                    .fillMaxWidth((value ?: 0.0).coerceIn(0.0, 1.0).toFloat())
                    .background(Brush.horizontalGradient(listOf(MatrixColors.NeonDim, MatrixColors.NeonBright))),
            )
        }
        Text(
            value?.let { percent(it) } ?: "—",
            color = MatrixColors.Text,
            fontSize = 10.sp,
            textAlign = TextAlign.End,
            modifier = Modifier.width(42.dp),
        )
    }
}

@Composable
private fun MatrixSetupCard(
    status: ScreenStatus,
    preview: Boolean,
    busy: Boolean,
    onTrade: () -> Unit,
    onEnableLive: () -> Unit,
) {
    val alert = status.alert
    MatrixPanel(border = MatrixColors.NeonDim) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("TOP SETUP", color = MatrixColors.TextMuted, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Text(
                    alert?.let { optionLabel(it.symbol, it.right) } ?: "WAITING FOR QUALIFIED SETUP",
                    color = MatrixColors.Text,
                    fontSize = if (alert == null) 16.sp else 22.sp,
                    fontWeight = FontWeight.Black,
                )
            }
            Surface(
                color = MatrixColors.Neon.copy(alpha = 0.08f),
                border = BorderStroke(1.dp, MatrixColors.NeonDim),
                shape = RoundedCornerShape(10.dp),
            ) {
                Text(
                    "ITM/OTM",
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    color = MatrixColors.Neon,
                    fontSize = 8.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MatrixMetric("Hybrid", status.ai.hybridProbabilityUp?.let { percent(it) } ?: "—", Modifier.weight(1f))
            MatrixMetric("Limit", alert?.let { "${price(it.bid)} / ${price(it.ask)}" } ?: "—", Modifier.weight(1f))
        }
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MatrixMetric("Contracts", alert?.contracts?.takeIf { it > 0 }?.toString() ?: "—", Modifier.weight(1f))
            MatrixMetric("Max Debit", alert?.maxDebit?.let { money(it) } ?: "—", Modifier.weight(1f))
        }
        Spacer(Modifier.height(13.dp))

        val canTrade = !preview && status.liveReady && status.mode == "LIVE" && alert != null && !busy
        MatrixPrimaryButton(
            text = if (preview) "TRADE · OWNER LOGIN REQUIRED" else "TRADE     >>>",
            enabled = canTrade,
            onClick = onTrade,
            modifier = Modifier.fillMaxWidth(),
        )
        if (!preview && status.mode != "LIVE") {
            Spacer(Modifier.height(8.dp))
            MatrixOutlineButton("ENABLE LIVE MODE", onEnableLive, Modifier.fillMaxWidth())
        }
        if (!status.liveReady && status.liveReasons.isNotEmpty()) {
            Spacer(Modifier.height(9.dp))
            Text(
                "LOCKED · ${status.liveReasons.joinToString(" · ")}",
                color = MatrixColors.Amber,
                fontSize = 9.sp,
            )
        }
    }
}

@Composable
private fun MatrixRiskCard(risk: RiskState, onEdit: () -> Unit) {
    MatrixPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("RISK CONTROLS", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp, modifier = Modifier.weight(1f))
            Text(
                "EDIT",
                color = MatrixColors.Neon,
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.clickable(onClick = onEdit).padding(6.dp),
            )
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            MatrixRiskTile("DAILY GAIN", risk.gain?.let { "+${money(it)}" } ?: "—", MatrixColors.Neon, Modifier.weight(1f))
            MatrixRiskTile("DAILY LOSS", risk.loss?.let { "-${money(it)}" } ?: "—", MatrixColors.Red, Modifier.weight(1f))
            MatrixRiskTile("MAX CONTRACTS", risk.contracts?.toString() ?: "—", MatrixColors.Text, Modifier.weight(1f))
            MatrixRiskTile("MAX EXPOSURE", risk.exposure?.let { percent(it) } ?: "—", MatrixColors.Text, Modifier.weight(1f))
        }
    }
}

@Composable
private fun MatrixLiveDataCard(status: ScreenStatus) {
    MatrixPanel {
        Text("LIVE DATA", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            MatrixMetric("Cash", money(status.brokerState.cashAvailable), Modifier.weight(1f))
            MatrixMetric("Buying Basis", money(status.brokerState.totalEquity), Modifier.weight(1f))
        }
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            MatrixMetric(
                "Day P&L",
                signedMoney(status.brokerState.dailyPnl),
                Modifier.weight(1f),
                valueColor = pnlColor(status.brokerState.dailyPnl),
            )
            MatrixMetric("Open Positions", status.brokerState.openPositions.toString(), Modifier.weight(1f))
        }
    }
}

@Composable
private fun MatrixPositionCard(status: ScreenStatus) {
    MatrixPanel {
        Text("POSITIONS (${status.brokerState.openPositions})", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        if (status.brokerState.openPositions == 0) {
            Text("FLAT", color = MatrixColors.Neon, fontSize = 20.sp, fontWeight = FontWeight.Black)
            Text("No broker position is currently open.", color = MatrixColors.TextMuted, fontSize = 11.sp)
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(3.dp).height(42.dp).background(MatrixColors.Neon))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("WEBULL LIVE POSITION", color = MatrixColors.Text, fontWeight = FontWeight.Bold)
                    Text("${status.brokerState.openPositions} open · ${status.brokerState.pendingOrders} pending", color = MatrixColors.TextMuted, fontSize = 11.sp)
                }
                Text(signedMoney(status.brokerState.openPnl), color = pnlColor(status.brokerState.openPnl), fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun MatrixPositionsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        MatrixSectionTitle("POSITIONS")
        MatrixPositionCard(status)
        MatrixPanel {
            MatrixKeyValue("Pending broker orders", status.brokerState.pendingOrders.toString())
            MatrixKeyValue("Open P&L", signedMoney(status.brokerState.openPnl), pnlColor(status.brokerState.openPnl))
            MatrixKeyValue("Day P&L", signedMoney(status.brokerState.dailyPnl), pnlColor(status.brokerState.dailyPnl))
            MatrixKeyValue("Maximum new debit", money(status.brokerState.maxEntryDebit))
            MatrixKeyValue("Entry eligible", if (status.brokerState.entryAllowed) "YES" else "NO", if (status.brokerState.entryAllowed) MatrixColors.Neon else MatrixColors.Amber)
        }
        if (status.brokerState.openPositions > 0) {
            MatrixNotice("Webull currently exposes the reconciled position count to this dashboard. The app does not invent contract details that are absent from the status stream.")
        }
    }
}

@Composable
private fun MatrixAnalyticsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        MatrixSectionTitle("ANALYTICS")
        MatrixAiConsensus(status.ai, status.alert)
        MatrixPanel {
            Text("ENGINE", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            MatrixKeyValue("Strategy", status.strategy)
            MatrixKeyValue("Risk", status.riskProfile)
            MatrixKeyValue("Exit", status.exitProfile)
            MatrixKeyValue("Decision", status.decision)
            Text(status.reason, color = MatrixColors.TextMuted, fontSize = 11.sp)
        }
        MatrixPanel {
            Text("PAPER LEDGER", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                MatrixMetric("Cash", money(status.paperCash), Modifier.weight(1f))
                MatrixMetric("P&L", signedMoney(status.paperPnl), Modifier.weight(1f), pnlColor(status.paperPnl))
                MatrixMetric("Trades", status.paperTrades.toString(), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun MatrixSettingsScreen(
    status: ScreenStatus,
    preview: Boolean,
    actions: MatrixActions,
    onSignOut: (() -> Unit)?,
) {
    val scope = rememberCoroutineScope()
    var loss by remember(status.risk.loss) { mutableStateOf(status.risk.loss?.toString() ?: "25") }
    var gain by remember(status.risk.gain) { mutableStateOf(status.risk.gain?.toString() ?: "40") }
    var exposure by remember(status.risk.exposure) { mutableStateOf(status.risk.exposure?.let { (it * 100).toString() } ?: "20") }
    var contracts by remember(status.risk.contracts) { mutableStateOf(status.risk.contracts?.toString() ?: "1") }
    var message by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        MatrixSectionTitle("SETTINGS")
        MatrixPanel {
            Text("TRADING MODE", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
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
                            containerColor = if (active) MatrixColors.Neon.copy(alpha = 0.12f) else Color.Transparent,
                            contentColor = if (active) MatrixColors.Neon else MatrixColors.Text,
                        ),
                        border = BorderStroke(1.dp, if (active) MatrixColors.Neon else MatrixColors.BorderSoft),
                    ) { Text(mode, fontSize = 10.sp, fontWeight = FontWeight.Bold) }
                }
            }
        }

        MatrixPanel {
            Text("DAILY LIVE ENVELOPE", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Text("Server-authoritative ceilings. New entries are also blocked while a position or broker order is open.", color = MatrixColors.TextMuted, fontSize = 10.sp)
            Spacer(Modifier.height(8.dp))
            MatrixTextField(loss, { loss = it }, "Daily loss stop ($)")
            MatrixTextField(gain, { gain = it }, "Daily gain stop ($)")
            MatrixTextField(exposure, { exposure = it }, "Max account exposure (%)")
            MatrixTextField(contracts, { contracts = it }, "Max contracts")
            Spacer(Modifier.height(5.dp))
            MatrixPrimaryButton(
                text = "ARM TODAY'S LIMITS",
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
            MatrixOutlineButton(
                text = "DISARM LIVE",
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

        MatrixPanel {
            Text("AUTONOMOUS PAPER", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(6.dp))
            MatrixKeyValue("Cash", money(status.paperCash))
            MatrixKeyValue("Realized P&L", signedMoney(status.paperPnl), pnlColor(status.paperPnl))
            MatrixKeyValue("Trades", status.paperTrades.toString())
            Spacer(Modifier.height(8.dp))
            MatrixOutlineButton(
                text = if (status.paperArmed) "STOP NEW PAPER ENTRIES" else "ARM AUTONOMOUS PAPER",
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

        MatrixPanel {
            Text("BROKER", color = MatrixColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(6.dp))
            MatrixKeyValue("Provider", status.broker.uppercase())
            MatrixKeyValue("API configured", if (status.brokerConfigured) "YES" else "NO")
            MatrixKeyValue("Account connected", if (status.brokerConnected) "YES" else "NO")
            MatrixKeyValue("Owner auth", if (preview) "LOCKED" else "ACTIVE", if (preview) MatrixColors.Amber else MatrixColors.Neon)
            onSignOut?.let {
                Spacer(Modifier.height(8.dp))
                MatrixOutlineButton("SIGN OUT", it, Modifier.fillMaxWidth())
            }
        }
        if (preview) MatrixNotice("Owner login is not configured in this APK, so Webull submission remains locked.", danger = true)
        message?.let { MatrixNotice(it, danger = it.contains("fail", true) || it.contains("invalid", true)) }
    }
}

@Composable
private fun MatrixBottomNav(selected: String, onSelect: (String) -> Unit) {
    Surface(
        color = MatrixColors.Background.copy(alpha = 0.98f),
        border = BorderStroke(1.dp, MatrixColors.BorderSoft),
    ) {
        Row(Modifier.fillMaxWidth().height(64.dp)) {
            listOf(
                "LIVE" to "◆",
                "POSITIONS" to "▣",
                "ANALYTICS" to "⌁",
                "SETTINGS" to "⚙",
            ).forEach { (label, glyph) ->
                val active = selected == label
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .clickable { onSelect(label) },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Text(glyph, color = if (active) MatrixColors.Neon else MatrixColors.TextMuted, fontSize = 18.sp)
                    Text(
                        label.lowercase().replaceFirstChar { it.uppercase() },
                        color = if (active) MatrixColors.Neon else MatrixColors.TextMuted,
                        fontSize = 9.sp,
                        fontWeight = if (active) FontWeight.Bold else FontWeight.Normal,
                    )
                }
            }
        }
    }
}

@Composable
private fun MatrixPanel(
    border: Color = MatrixColors.BorderSoft,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MatrixColors.Surface.copy(alpha = 0.94f),
        shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, border),
    ) {
        Column(Modifier.fillMaxWidth().padding(13.dp), content = content)
    }
}

@Composable
private fun MatrixNotice(text: String, danger: Boolean = false) {
    Surface(
        color = if (danger) MatrixColors.Red.copy(alpha = 0.08f) else MatrixColors.Neon.copy(alpha = 0.06f),
        border = BorderStroke(1.dp, if (danger) MatrixColors.Red.copy(alpha = 0.55f) else MatrixColors.BorderSoft),
        shape = RoundedCornerShape(8.dp),
    ) {
        Text(
            text,
            modifier = Modifier.fillMaxWidth().padding(10.dp),
            color = if (danger) MatrixColors.Red else MatrixColors.TextMuted,
            fontSize = 10.sp,
        )
    }
}

@Composable
private fun MatrixCapabilityCard(title: String, body: String) {
    MatrixPanel {
        Text(title, color = MatrixColors.Neon, fontWeight = FontWeight.Black, fontSize = 15.sp)
        Spacer(Modifier.height(8.dp))
        Text(body, color = MatrixColors.TextMuted, fontSize = 11.sp)
    }
}

@Composable
private fun MatrixPrimaryButton(
    text: String,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(48.dp),
        shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = MatrixColors.Neon,
            contentColor = MatrixColors.Black,
            disabledContainerColor = MatrixColors.BorderSoft,
            disabledContentColor = MatrixColors.TextFaint,
        ),
    ) {
        Text(text, fontWeight = FontWeight.Black, letterSpacing = 0.7.sp)
    }
}

@Composable
private fun MatrixOutlineButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MatrixColors.NeonDim),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = MatrixColors.NeonBright),
    ) {
        Text(text, fontWeight = FontWeight.Bold, fontSize = 11.sp)
    }
}

@Composable
private fun MatrixTextField(value: String, onValueChange: (String) -> Unit, label: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        label = { Text(label) },
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = MatrixColors.Neon,
            unfocusedBorderColor = MatrixColors.BorderSoft,
            focusedLabelColor = MatrixColors.Neon,
            unfocusedLabelColor = MatrixColors.TextMuted,
            focusedTextColor = MatrixColors.Text,
            unfocusedTextColor = MatrixColors.Text,
            cursorColor = MatrixColors.Neon,
        ),
    )
}

@Composable
private fun MatrixRiskTile(label: String, value: String, valueColor: Color, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = MatrixColors.SurfaceRaised,
        shape = RoundedCornerShape(7.dp),
        border = BorderStroke(1.dp, MatrixColors.BorderSoft),
    ) {
        Column(Modifier.padding(horizontal = 6.dp, vertical = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, color = MatrixColors.TextMuted, fontSize = 7.sp, maxLines = 1)
            Text(value, color = valueColor, fontSize = 12.sp, fontWeight = FontWeight.Black, maxLines = 1)
        }
    }
}

@Composable
private fun MatrixMetric(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = MatrixColors.Text) {
    Column(modifier) {
        Text(label.uppercase(), color = MatrixColors.TextFaint, fontSize = 8.sp, letterSpacing = 0.5.sp)
        Text(value, color = valueColor, fontSize = 12.sp, fontWeight = FontWeight.Bold, maxLines = 1)
    }
}

@Composable
private fun MatrixMicroChip(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = MatrixColors.SurfaceRaised,
        shape = RoundedCornerShape(7.dp),
        border = BorderStroke(1.dp, MatrixColors.BorderSoft),
    ) {
        Column(Modifier.padding(7.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, color = MatrixColors.TextFaint, fontSize = 7.sp)
            Text(value, color = MatrixColors.Neon, fontSize = 10.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun MatrixKeyValue(label: String, value: String, valueColor: Color = MatrixColors.Text) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, color = MatrixColors.TextMuted, fontSize = 11.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 11.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.End)
    }
}

@Composable
private fun MatrixSectionTitle(title: String) {
    Text(title, color = MatrixColors.Neon, fontSize = 16.sp, fontWeight = FontWeight.Black, letterSpacing = 1.2.sp)
}

private fun money(value: Double?): String = value?.let { "$%.2f".format(it) } ?: "—"
private fun price(value: Double?): String = value?.let { "%.2f".format(it) } ?: "—"
private fun signedMoney(value: Double?): String = value?.let { (if (it >= 0) "+" else "") + "$%.2f".format(it) } ?: "—"
private fun percent(value: Double): String = "%.0f%%".format(value.coerceIn(0.0, 1.0) * 100.0)
private fun trimNumber(value: Double): String = if (value % 1.0 == 0.0) value.toInt().toString() else "%.1f".format(value)
private fun pnlColor(value: Double?): Color = when {
    value == null -> MatrixColors.Text
    value > 0 -> MatrixColors.Neon
    value < 0 -> MatrixColors.Red
    else -> MatrixColors.Text
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
