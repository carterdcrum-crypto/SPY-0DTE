package app.spy0dte.mobile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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

internal data class GlassActions(
    val prepareTrade: suspend () -> Result<PreparedTrade>,
    val submitTrade: suspend (PreparedTrade) -> Result<String>,
    val setMode: suspend (String) -> Result<String>,
    val setPaperAutonomy: suspend (Boolean) -> Result<String>,
    val armRisk: suspend (Double, Double, Double, Int) -> Result<String>,
    val disarmRisk: suspend () -> Result<String>,
)

/**
 * Approved showcase option #4: GLASS.
 *
 * Clean, minimal, light and frosted. Every financial value comes from the
 * Railway status stream. Missing values render as an em dash; sample values
 * from the concept art are never substituted into the running app.
 */
@Composable
internal fun GlassDashboard(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: GlassActions,
    onSignOut: (() -> Unit)?,
) {
    var bottomTab by remember { mutableStateOf("LIVE") }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(
                        Color(0xFFF9FDFE),
                        GlassColors.BackgroundBlue,
                        Color(0xFFF5FAFC),
                    ),
                ),
            )
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        GlassAmbientBackground()
        Column(
            modifier = Modifier
                .fillMaxSize()
                .widthIn(max = 620.dp)
                .align(Alignment.TopCenter),
        ) {
            GlassHeader(status)
            Box(Modifier.weight(1f)) {
                when (bottomTab) {
                    "POSITIONS" -> GlassPositionsScreen(status)
                    "ANALYTICS" -> GlassAnalyticsScreen(status)
                    "SETTINGS" -> GlassSettingsScreen(
                        status = status,
                        preview = preview,
                        actions = actions,
                        onSignOut = onSignOut,
                    )
                    else -> GlassLiveScreen(
                        status = status,
                        preview = preview,
                        connectionError = connectionError,
                        actions = actions,
                        onPositions = { bottomTab = "POSITIONS" },
                    )
                }
            }
            GlassBottomNav(bottomTab) { bottomTab = it }
        }
    }
}

@Composable
private fun GlassAmbientBackground() {
    Canvas(Modifier.fillMaxSize()) {
        drawCircle(
            color = Color(0xFFBFEFE4).copy(alpha = 0.28f),
            radius = size.minDimension * 0.42f,
            center = Offset(size.width * 0.84f, size.height * 0.08f),
        )
        drawCircle(
            color = Color(0xFFC8E9F7).copy(alpha = 0.34f),
            radius = size.minDimension * 0.48f,
            center = Offset(size.width * 0.06f, size.height * 0.34f),
        )
        drawCircle(
            color = Color.White.copy(alpha = 0.58f),
            radius = size.minDimension * 0.58f,
            center = Offset(size.width * 0.78f, size.height * 0.72f),
        )
    }
}

@Composable
private fun GlassHeader(status: ScreenStatus) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 17.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Surface(
            modifier = Modifier.size(38.dp),
            shape = RoundedCornerShape(14.dp),
            color = Color.White.copy(alpha = 0.72f),
            border = BorderStroke(1.dp, GlassColors.Border),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Text("☰", color = GlassColors.Text, fontSize = 18.sp)
            }
        }
        Text(
            "SPY 0DTE",
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
            color = GlassColors.Text,
            fontWeight = FontWeight.Black,
            fontSize = 16.sp,
        )
        Surface(
            shape = RoundedCornerShape(22.dp),
            color = GlassColors.Mint.copy(alpha = 0.92f),
            border = BorderStroke(1.dp, Color(0xFFA8E7CF)),
        ) {
            Text(
                "${status.mode}  ⌄",
                modifier = Modifier.padding(horizontal = 13.dp, vertical = 8.dp),
                color = GlassColors.GreenDark,
                fontWeight = FontWeight.Black,
                fontSize = 10.sp,
            )
        }
    }
}

@Composable
private fun GlassLiveScreen(
    status: ScreenStatus,
    preview: Boolean,
    connectionError: String?,
    actions: GlassActions,
    onPositions: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var prepared by remember { mutableStateOf<PreparedTrade?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }
    var topTab by remember { mutableStateOf("AI") }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        GlassMarketStrip(status)
        GlassTopTabs(topTab) { topTab = it }

        connectionError?.let { GlassNotice("$it · retrying", danger = true) }
        if (preview) {
            GlassNotice(
                "LIVE orders are locked until owner authentication is configured. Paper data and the complete Glass UI remain active.",
                danger = true,
            )
        }

        when (topTab) {
            "AI" -> {
                GlassAiConsensus(status.ai, status.alert)
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
                    onQuickCall = {
                        val right = status.alert?.right?.uppercase()
                        if (right == "CALL" || right == "C") {
                            scope.launch {
                                actions.prepareTrade()
                                    .onSuccess { prepared = it }
                                    .onFailure { message = it.message }
                            }
                        } else {
                            message = "No qualified CALL setup is active."
                        }
                    },
                    onQuickPut = {
                        val right = status.alert?.right?.uppercase()
                        if (right == "PUT" || right == "P") {
                            scope.launch {
                                actions.prepareTrade()
                                    .onSuccess { prepared = it }
                                    .onFailure { message = it.message }
                            }
                        } else {
                            message = "No qualified PUT setup is active."
                        }
                    },
                    onClose = onPositions,
                )
                GlassPositionsCard(status, onPositions)
                GlassTodayCard(status)
            }
            "CHART" -> GlassChartPanel(status)
            "OPTIONS" -> GlassSetupCard(
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
                onQuickCall = { message = "Railway chooses the exact CALL contract when a qualified CALL setup exists." },
                onQuickPut = { message = "Railway chooses the exact PUT contract when a qualified PUT setup exists." },
                onClose = onPositions,
            )
            else -> GlassNewsPanel()
        }

        message?.let {
            GlassNotice(
                it,
                danger = it.contains("fail", true) || it.contains("lock", true) || it.contains("no qualified", true),
            )
        }
        Spacer(Modifier.height(4.dp))
    }

    prepared?.let { trade ->
        AlertDialog(
            onDismissRequest = { if (!submitting) prepared = null },
            containerColor = Color(0xFFF9FCFD),
            titleContentColor = GlassColors.Text,
            textContentColor = GlassColors.Text,
            shape = RoundedCornerShape(26.dp),
            title = { Text("Confirm Webull trade", fontWeight = FontWeight.Black) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "SPY ${trade.optionType} ${trimNumber(trade.strike)}",
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Black,
                    )
                    GlassKeyValue("Expiration", trade.expiration)
                    GlassKeyValue("Quantity", trade.quantity.toString())
                    GlassKeyValue("Limit", money(trade.limitPrice))
                    GlassKeyValue("Maximum debit", money(trade.maxDebit))
                    GlassKeyValue("Authorization", "${trade.expiresSeconds}s")
                    Text(
                        "The authorization is single-use and applies only to this exact order.",
                        color = GlassColors.TextMuted,
                        fontSize = 12.sp,
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
                    Text("Cancel", color = GlassColors.TextMuted)
                }
            },
        )
    }
}

@Composable
private fun GlassMarketStrip(status: ScreenStatus) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 5.dp, vertical = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    status.spot?.let { "%.2f".format(it) } ?: "—",
                    color = GlassColors.Text,
                    fontSize = 34.sp,
                    fontWeight = FontWeight.Black,
                    letterSpacing = (-1).sp,
                )
                Text(
                    when {
                        !status.connected -> "Connecting to Railway"
                        status.dataAge != null -> "●  LIVE · data age %.1fs".format(status.dataAge)
                        else -> "●  ENGINE CONNECTED"
                    },
                    color = if (status.connected) GlassColors.Green else GlassColors.TextMuted,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            GlassSpotPulse(
                spot = status.spot,
                connected = status.connected,
                modifier = Modifier.width(132.dp).height(72.dp),
            )
        }
    }
}

@Composable
private fun GlassSpotPulse(spot: Double?, connected: Boolean, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val centerY = size.height * 0.58f
        drawLine(
            color = GlassColors.Border.copy(alpha = 0.72f),
            start = Offset(0f, centerY),
            end = Offset(size.width, centerY),
            strokeWidth = 1.dp.toPx(),
        )
        if (spot != null && connected) {
            val path = Path().apply {
                moveTo(size.width * 0.05f, centerY)
                cubicTo(
                    size.width * 0.23f, centerY,
                    size.width * 0.34f, centerY - 7.dp.toPx(),
                    size.width * 0.50f, centerY - 7.dp.toPx(),
                )
                cubicTo(
                    size.width * 0.65f, centerY - 7.dp.toPx(),
                    size.width * 0.73f, centerY,
                    size.width * 0.93f, centerY,
                )
            }
            drawPath(path, GlassColors.Green.copy(alpha = 0.60f), style = Stroke(2.dp.toPx()))
            drawCircle(
                color = GlassColors.Green,
                radius = 3.dp.toPx(),
                center = Offset(size.width * 0.93f, centerY),
            )
        }
    }
}

@Composable
private fun GlassTopTabs(selected: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        listOf("AI", "CHART", "OPTIONS", "NEWS").forEach { label ->
            val active = selected == label
            Surface(
                modifier = Modifier.weight(1f).clickable { onSelect(label) },
                shape = RoundedCornerShape(20.dp),
                color = if (active) Color.White.copy(alpha = 0.92f) else Color.Transparent,
                border = if (active) BorderStroke(1.dp, GlassColors.Border) else null,
                shadowElevation = if (active) 1.dp else 0.dp,
            ) {
                Text(
                    label,
                    modifier = Modifier.padding(vertical = 9.dp),
                    textAlign = TextAlign.Center,
                    color = if (active) GlassColors.Text else GlassColors.TextMuted,
                    fontSize = 10.sp,
                    fontWeight = if (active) FontWeight.Black else FontWeight.Medium,
                )
            }
        }
    }
}

@Composable
private fun GlassAiConsensus(ai: AiDecisionState, alert: LiveAlert?) {
    GlassPanel {
        Text("AI CONSENSUS", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            GlassConsensusGauge(ai, alert, Modifier.size(122.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                GlassProbabilityRow("Quant", ai.quantProbabilityUp)
                ai.providers.forEach { provider ->
                    GlassProbabilityRow(providerDisplayName(provider.provider), provider.probabilityUp)
                }
                if (ai.providers.isEmpty()) GlassProbabilityRow("AI", ai.aiProbabilityUp)
            }
        }
    }
}

@Composable
private fun GlassConsensusGauge(ai: AiDecisionState, alert: LiveAlert?, modifier: Modifier = Modifier) {
    val pUp = ai.hybridProbabilityUp ?: ai.aiProbabilityUp ?: ai.quantProbabilityUp
    val directionProbability = when {
        pUp == null -> null
        alert?.right.equals("PUT", true) || alert?.right.equals("P", true) -> 1.0 - pUp
        else -> pUp
    }
    val sweep = ((directionProbability ?: 0.0).coerceIn(0.0, 1.0) * 270.0).toFloat()
    val label = when {
        pUp == null -> "WAITING"
        pUp >= 0.53 -> "BULLISH"
        pUp <= 0.47 -> "BEARISH"
        else -> "NEUTRAL"
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 7.dp.toPx()
            val inset = 7.dp.toPx()
            drawArc(
                color = Color(0xFFDCE8EC),
                startAngle = 135f,
                sweepAngle = 270f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
            drawArc(
                color = GlassColors.Green,
                startAngle = 135f,
                sweepAngle = sweep,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke),
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                directionProbability?.let { percent(it) } ?: "—",
                color = GlassColors.Text,
                fontSize = 28.sp,
                fontWeight = FontWeight.Black,
            )
            Text(
                label,
                color = if (label == "BEARISH") GlassColors.Red else GlassColors.Green,
                fontSize = 10.sp,
                fontWeight = FontWeight.Black,
            )
        }
    }
}

@Composable
private fun GlassProbabilityRow(label: String, value: Double?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = GlassColors.TextMuted, fontSize = 10.sp, modifier = Modifier.width(62.dp))
        Box(
            Modifier
                .weight(1f)
                .height(6.dp)
                .clip(RoundedCornerShape(99.dp))
                .background(Color(0xFFE3ECEF)),
        ) {
            Box(
                Modifier
                    .fillMaxHeight()
                    .fillMaxWidth((value ?: 0.0).coerceIn(0.0, 1.0).toFloat())
                    .background(GlassColors.Green),
            )
        }
        Text(
            value?.let { percent(it) } ?: "—",
            color = GlassColors.Text,
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.End,
            modifier = Modifier.width(42.dp),
        )
    }
}

@Composable
private fun GlassSetupCard(
    status: ScreenStatus,
    preview: Boolean,
    busy: Boolean,
    onTrade: () -> Unit,
    onQuickCall: () -> Unit,
    onQuickPut: () -> Unit,
    onClose: () -> Unit,
) {
    val alert = status.alert
    val parsed = alert?.symbol?.let(::parseOccSymbol)
    val moneyness = if (parsed != null && status.spot != null) {
        when {
            kotlin.math.abs(status.spot - parsed.strike) < 0.25 -> "ATM"
            (parsed.right == "CALL" && status.spot > parsed.strike) ||
                (parsed.right == "PUT" && status.spot < parsed.strike) -> "ITM"
            else -> "OTM"
        }
    } else null
    val pUp = status.ai.hybridProbabilityUp ?: status.ai.aiProbabilityUp ?: status.ai.quantProbabilityUp
    val directionProbability = when {
        pUp == null || alert == null -> null
        alert.right.equals("PUT", true) || alert.right.equals("P", true) -> 1.0 - pUp
        else -> pUp
    }

    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("TOP SETUP", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
                Spacer(Modifier.height(5.dp))
                Text(
                    alert?.let { optionLabel(it.symbol, it.right) } ?: "WAITING FOR QUALIFIED SETUP",
                    color = GlassColors.Text,
                    fontWeight = FontWeight.Black,
                    fontSize = if (alert == null) 16.sp else 21.sp,
                )
            }
            moneyness?.let {
                Surface(
                    shape = RoundedCornerShape(99.dp),
                    color = GlassColors.Mint,
                    border = BorderStroke(1.dp, Color(0xFFA8E7CF)),
                ) {
                    Text(
                        it,
                        modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                        color = GlassColors.GreenDark,
                        fontSize = 8.sp,
                        fontWeight = FontWeight.Black,
                    )
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                GlassKeyValue("Direction Prob.", directionProbability?.let { percent(it) } ?: "—")
                GlassKeyValue("Est. Move", "—")
                GlassKeyValue("Risk/Reward", "—")
            }
            Box(Modifier.width(1.dp).height(62.dp).background(GlassColors.Border))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                GlassKeyValue("Limit", alert?.let { "${price(it.bid)} / ${price(it.ask)}" } ?: "—")
                GlassKeyValue("Contracts", alert?.contracts?.takeIf { it > 0 }?.toString() ?: "—")
                GlassKeyValue("Est. Cost", alert?.maxDebit?.let(::money) ?: "—")
            }
        }
        Spacer(Modifier.height(12.dp))

        val canTrade = !preview && status.liveReady && status.mode == "LIVE" && alert != null && !busy
        GlassPrimaryButton(
            text = if (preview) "TRADE · OWNER LOGIN REQUIRED" else "TRADE  →",
            enabled = canTrade,
            onClick = onTrade,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(9.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            GlassQuickButton(
                label = "↑",
                caption = "Buy Call",
                tint = GlassColors.Green,
                enabled = alert != null,
                onClick = onQuickCall,
                modifier = Modifier.weight(1f),
            )
            GlassQuickButton(
                label = "↓",
                caption = "Buy Put",
                tint = GlassColors.Red,
                enabled = alert != null,
                onClick = onQuickPut,
                modifier = Modifier.weight(1f),
            )
            val openCount = if (status.mode == "PAPER") status.paperPositions else status.brokerState.openPositions
            GlassQuickButton(
                label = "○",
                caption = "Close",
                tint = GlassColors.TextMuted,
                enabled = openCount > 0,
                onClick = onClose,
                modifier = Modifier.weight(1f),
            )
        }
        if (!status.liveReady && status.liveReasons.isNotEmpty()) {
            Spacer(Modifier.height(9.dp))
            Text(
                "LIVE LOCKED · ${status.liveReasons.joinToString(" · ").replace('_', ' ')}",
                color = GlassColors.Amber,
                fontSize = 9.sp,
            )
        }
    }
}

@Composable
private fun GlassQuickButton(
    label: String,
    caption: String,
    tint: Color,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier
            .height(65.dp)
            .clickable(enabled = enabled, onClick = onClick),
        shape = RoundedCornerShape(17.dp),
        color = Color.White.copy(alpha = if (enabled) 0.66f else 0.35f),
        border = BorderStroke(1.dp, GlassColors.Border),
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(label, color = if (enabled) tint else GlassColors.TextFaint, fontSize = 22.sp, fontWeight = FontWeight.Black)
            Text(caption, color = if (enabled) GlassColors.Text else GlassColors.TextFaint, fontSize = 9.sp)
        }
    }
}

@Composable
private fun GlassPositionsCard(status: ScreenStatus, onOpen: () -> Unit) {
    val paper = status.mode == "PAPER"
    val count = if (paper) status.paperPositions else status.brokerState.openPositions
    val pnl = if (paper) status.paperPnl else status.brokerState.openPnl

    GlassPanel(modifier = Modifier.clickable { onOpen() }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("POSITIONS ($count)", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp, modifier = Modifier.weight(1f))
            Text("›", color = GlassColors.TextMuted, fontSize = 22.sp)
        }
        Spacer(Modifier.height(7.dp))
        if (count == 0) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(4.dp).height(38.dp).background(GlassColors.Green, RoundedCornerShape(99.dp)))
                Spacer(Modifier.width(9.dp))
                Column(Modifier.weight(1f)) {
                    Text("FLAT", color = GlassColors.Green, fontWeight = FontWeight.Black, fontSize = 16.sp)
                    Text("No open positions", color = GlassColors.TextMuted, fontSize = 10.sp)
                }
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(4.dp).height(44.dp).background(if ((pnl ?: 0.0) >= 0) GlassColors.Green else GlassColors.Red, RoundedCornerShape(99.dp)))
                Spacer(Modifier.width(9.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        if (paper) "PAPER SPY 0DTE" else "WEBULL SPY 0DTE",
                        color = GlassColors.Text,
                        fontWeight = FontWeight.Black,
                        fontSize = 13.sp,
                    )
                    Text("$count open position${if (count == 1) "" else "s"}", color = GlassColors.TextMuted, fontSize = 10.sp)
                }
                Text(signedMoney(pnl), color = pnlColor(pnl), fontWeight = FontWeight.Black, fontSize = 15.sp)
            }
        }
    }
}

@Composable
private fun GlassTodayCard(status: ScreenStatus) {
    val pnl = if (status.mode == "PAPER") status.paperPnl else status.brokerState.dailyPnl
    GlassPanel {
        Text("TODAY", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Row {
            GlassTodayMetric("Trades", if (status.mode == "PAPER") status.paperTrades.toString() else "—", Modifier.weight(1f))
            GlassDivider()
            GlassTodayMetric("Win Rate", "—", Modifier.weight(1f))
            GlassDivider()
            GlassTodayMetric("P&L", signedMoney(pnl), Modifier.weight(1f), pnlColor(pnl))
        }
    }
}

@Composable
private fun GlassTodayMetric(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = GlassColors.Text) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, color = GlassColors.TextMuted, fontSize = 9.sp)
        Text(value, color = valueColor, fontWeight = FontWeight.Black, fontSize = 15.sp)
    }
}

@Composable
private fun GlassDivider() {
    Box(Modifier.width(1.dp).height(34.dp).background(GlassColors.Border))
}

@Composable
private fun GlassChartPanel(status: ScreenStatus) {
    GlassPanel {
        Text("LIVE CHART", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Text(
            status.spot?.let { "SPY  %.2f".format(it) } ?: "SPY  —",
            color = GlassColors.Text,
            fontSize = 26.sp,
            fontWeight = FontWeight.Black,
        )
        Spacer(Modifier.height(10.dp))
        Box(
            Modifier
                .fillMaxWidth()
                .height(170.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(Color.White.copy(alpha = 0.38f))
                .border(1.dp, GlassColors.Border, RoundedCornerShape(18.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Real-time chart series not published by Railway yet", color = GlassColors.TextMuted, fontSize = 11.sp)
                Text("No synthetic candles are drawn.", color = GlassColors.TextFaint, fontSize = 9.sp)
            }
        }
    }
}

@Composable
private fun GlassNewsPanel() {
    GlassPanel {
        Text("NEWS", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Text(
            "The AI decision feed is tape-only, so this screen stays empty until a timestamp-safe news source is connected.",
            color = GlassColors.TextMuted,
            fontSize = 11.sp,
        )
    }
}

@Composable
private fun GlassPositionsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        GlassSectionTitle("Positions")
        GlassPositionsCard(status) {}
        GlassPanel {
            val live = status.mode == "LIVE"
            GlassKeyValue("Mode", status.mode)
            GlassKeyValue("Open positions", if (live) status.brokerState.openPositions.toString() else status.paperPositions.toString())
            GlassKeyValue("Pending broker orders", if (live) status.brokerState.pendingOrders.toString() else "—")
            GlassKeyValue("Open P&L", if (live) signedMoney(status.brokerState.openPnl) else "—", pnlColor(status.brokerState.openPnl))
            GlassKeyValue("Day P&L", if (live) signedMoney(status.brokerState.dailyPnl) else signedMoney(status.paperPnl), pnlColor(if (live) status.brokerState.dailyPnl else status.paperPnl))
            GlassKeyValue("New entry eligible", if (status.brokerState.entryAllowed) "YES" else "NO", if (status.brokerState.entryAllowed) GlassColors.Green else GlassColors.Amber)
        }
        if (status.brokerState.openPositions > 0) {
            GlassNotice("The broker status stream currently exposes reconciled position count and P&L, not contract-level position details. This screen does not invent them.")
        }
    }
}

@Composable
private fun GlassAnalyticsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        GlassSectionTitle("Analytics")
        GlassAiConsensus(status.ai, status.alert)
        GlassPanel {
            Text("ENGINE", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            GlassKeyValue("Strategy", status.strategy)
            GlassKeyValue("Risk", status.riskProfile)
            GlassKeyValue("Exit", status.exitProfile)
            GlassKeyValue("Decision", status.decision)
            Spacer(Modifier.height(6.dp))
            Text(status.reason, color = GlassColors.TextMuted, fontSize = 10.sp)
        }
        GlassPanel {
            Text("PAPER LEDGER", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row {
                GlassTodayMetric("Cash", money(status.paperCash), Modifier.weight(1f))
                GlassDivider()
                GlassTodayMetric("P&L", signedMoney(status.paperPnl), Modifier.weight(1f), pnlColor(status.paperPnl))
                GlassDivider()
                GlassTodayMetric("Trades", status.paperTrades.toString(), Modifier.weight(1f))
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
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        GlassSectionTitle("Settings")
        GlassPanel {
            Text("TRADING MODE", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
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
                        shape = RoundedCornerShape(18.dp),
                        colors = ButtonDefaults.outlinedButtonColors(
                            containerColor = if (active) GlassColors.Mint else Color.White.copy(alpha = 0.35f),
                            contentColor = if (active) GlassColors.GreenDark else GlassColors.TextMuted,
                        ),
                        border = BorderStroke(1.dp, if (active) Color(0xFFA8E7CF) else GlassColors.Border),
                    ) {
                        Text(mode, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        GlassPanel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("RISK CONTROLS", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp, modifier = Modifier.weight(1f))
                Text(if (status.risk.armed) "ARMED" else "NOT ARMED", color = if (status.risk.armed) GlassColors.Green else GlassColors.Amber, fontSize = 9.sp, fontWeight = FontWeight.Black)
            }
            Spacer(Modifier.height(8.dp))
            GlassTextField(loss, { loss = it }, "Daily loss stop ($)")
            GlassTextField(gain, { gain = it }, "Daily gain stop ($)")
            GlassTextField(exposure, { exposure = it }, "Max account exposure (%)")
            GlassTextField(contracts, { contracts = it }, "Max contracts")
            Spacer(Modifier.height(7.dp))
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
            Text("AUTONOMOUS PAPER", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
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
            Text("ACCOUNT", color = GlassColors.Text, fontWeight = FontWeight.Black, fontSize = 12.sp)
            Spacer(Modifier.height(7.dp))
            GlassKeyValue("Provider", status.broker.uppercase())
            GlassKeyValue("API configured", if (status.brokerConfigured) "YES" else "NO")
            GlassKeyValue("Broker connected", if (status.brokerConnected) "YES" else "NO")
            GlassKeyValue("Cash", money(status.brokerState.cashAvailable))
            GlassKeyValue("Day P&L", signedMoney(status.brokerState.dailyPnl), pnlColor(status.brokerState.dailyPnl))
            GlassKeyValue("Owner auth", if (preview) "LOCKED" else "ACTIVE", if (preview) GlassColors.Amber else GlassColors.Green)
            onSignOut?.let {
                Spacer(Modifier.height(8.dp))
                GlassOutlineButton("Sign Out", it, Modifier.fillMaxWidth())
            }
        }
        if (preview) GlassNotice("Owner login is not configured in this APK, so Webull submission remains locked.", danger = true)
        message?.let { GlassNotice(it, danger = it.contains("fail", true) || it.contains("invalid", true)) }
        Spacer(Modifier.height(4.dp))
    }
}

@Composable
private fun GlassBottomNav(selected: String, onSelect: (String) -> Unit) {
    Surface(
        color = Color.White.copy(alpha = 0.82f),
        border = BorderStroke(1.dp, GlassColors.Border),
    ) {
        Row(Modifier.fillMaxWidth().height(68.dp).padding(horizontal = 8.dp)) {
            listOf(
                "LIVE" to "⌂",
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
                    Surface(
                        shape = CircleShape,
                        color = if (active) GlassColors.Green else Color.Transparent,
                    ) {
                        Text(
                            glyph,
                            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                            color = if (active) GlassColors.White else GlassColors.TextMuted,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Black,
                        )
                    }
                    Spacer(Modifier.height(2.dp))
                    Text(
                        label.lowercase().replaceFirstChar { it.uppercase() },
                        color = if (active) GlassColors.Green else GlassColors.TextMuted,
                        fontSize = 9.sp,
                        fontWeight = if (active) FontWeight.Bold else FontWeight.Normal,
                    )
                }
            }
        }
    }
}

@Composable
private fun GlassPanel(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        color = Color.White.copy(alpha = 0.67f),
        border = BorderStroke(1.dp, GlassColors.Border),
        shadowElevation = 3.dp,
    ) {
        Column(Modifier.fillMaxWidth().padding(14.dp), content = content)
    }
}

@Composable
private fun GlassNotice(text: String, danger: Boolean = false) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = if (danger) GlassColors.SoftRed.copy(alpha = 0.78f) else Color.White.copy(alpha = 0.55f),
        border = BorderStroke(1.dp, if (danger) GlassColors.Red.copy(alpha = 0.25f) else GlassColors.Border),
    ) {
        Text(
            text,
            modifier = Modifier.fillMaxWidth().padding(11.dp),
            color = if (danger) Color(0xFFC83C39) else GlassColors.TextMuted,
            fontSize = 10.sp,
        )
    }
}

@Composable
private fun GlassPrimaryButton(
    text: String,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(48.dp),
        shape = RoundedCornerShape(15.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = GlassColors.Green,
            contentColor = GlassColors.White,
            disabledContainerColor = Color(0xFFDCE5E8),
            disabledContentColor = GlassColors.TextFaint,
        ),
    ) {
        Text(text, fontWeight = FontWeight.Black, fontSize = 13.sp)
    }
}

@Composable
private fun GlassOutlineButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(15.dp),
        border = BorderStroke(1.dp, GlassColors.Border),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = Color.White.copy(alpha = 0.35f),
            contentColor = GlassColors.Text,
        ),
    ) {
        Text(text, fontWeight = FontWeight.Bold, fontSize = 11.sp)
    }
}

@Composable
private fun GlassTextField(value: String, onValueChange: (String) -> Unit, label: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
        singleLine = true,
        label = { Text(label) },
        shape = RoundedCornerShape(16.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = GlassColors.Green,
            unfocusedBorderColor = GlassColors.Border,
            focusedLabelColor = GlassColors.GreenDark,
            unfocusedLabelColor = GlassColors.TextMuted,
            focusedTextColor = GlassColors.Text,
            unfocusedTextColor = GlassColors.Text,
            cursorColor = GlassColors.Green,
            focusedContainerColor = Color.White.copy(alpha = 0.56f),
            unfocusedContainerColor = Color.White.copy(alpha = 0.42f),
        ),
    )
}

@Composable
private fun GlassKeyValue(label: String, value: String, valueColor: Color = GlassColors.Text) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = GlassColors.TextMuted, fontSize = 10.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 10.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.End)
    }
}

@Composable
private fun GlassSectionTitle(title: String) {
    Text(
        title.uppercase(),
        modifier = Modifier.padding(horizontal = 5.dp, vertical = 5.dp),
        color = GlassColors.Text,
        fontSize = 13.sp,
        fontWeight = FontWeight.Black,
        letterSpacing = 0.9.sp,
    )
}

private data class ParsedOcc(
    val strike: Double,
    val expiration: LocalDate?,
    val right: String,
)

private fun parseOccSymbol(symbol: String): ParsedOcc? {
    val match = Regex("^([A-Z]{1,6})(\\d{6})([CP])(\\d{8})$").matchEntire(symbol.uppercase()) ?: return null
    val yymmdd = match.groupValues[2]
    val right = if (match.groupValues[3] == "P") "PUT" else "CALL"
    val strike = match.groupValues[4].toIntOrNull()?.div(1000.0) ?: return null
    val expiration = runCatching {
        LocalDate.parse("20$yymmdd", DateTimeFormatter.ofPattern("yyyyMMdd"))
    }.getOrNull()
    return ParsedOcc(strike, expiration, right)
}

private fun money(value: Double?): String = value?.let { "$%.2f".format(it) } ?: "—"
private fun price(value: Double?): String = value?.let { "%.2f".format(it) } ?: "—"
private fun signedMoney(value: Double?): String = value?.let { (if (it >= 0) "+" else "") + "$%.2f".format(it) } ?: "—"
private fun percent(value: Double): String = "%.0f%%".format(value.coerceIn(0.0, 1.0) * 100.0)
private fun trimNumber(value: Double): String = if (value % 1.0 == 0.0) value.toInt().toString() else "%.1f".format(value)
private fun pnlColor(value: Double?): Color = when {
    value == null -> GlassColors.Text
    value > 0 -> GlassColors.Green
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
    val parsed = parseOccSymbol(symbol) ?: return symbol
    val dte = parsed.expiration?.let {
        java.time.temporal.ChronoUnit.DAYS.between(LocalDate.now(), it).coerceAtLeast(0)
    }
    val cp = if (right.equals("PUT", true) || right.equals("P", true) || parsed.right == "PUT") "P" else "C"
    return buildString {
        append("SPY ")
        append(trimNumber(parsed.strike))
        append(cp)
        dte?.let { append("  ${it}DTE") }
    }
}
