package app.spy0dte.mobile

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.selection.selectable
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.selection.toggleable
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.offset
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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.launch

internal data class GlassActions(
    val saveCredentials: suspend (String, String) -> Result<String>,
    val setLiveAutonomy: suspend (Boolean) -> Result<String>,
    val setMode: suspend (String) -> Result<String>,
    val setPaperAutonomy: suspend (Boolean) -> Result<String>,
    val resetPaperAccount: suspend (Double) -> Result<String>,
    val armRisk: suspend (Double, Double, Double, Int) -> Result<String>,
    val disarmRisk: suspend () -> Result<String>,
)

/**
 * Refined Glass dashboard: calm surfaces, clear hierarchy and explicit live controls.
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
    var bottomTab by rememberSaveable { mutableStateOf("LIVE") }
    var showNavMenu by remember { mutableStateOf(false) }
    var showModePicker by remember { mutableStateOf(false) }
    var headerMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun selectMode(mode: String) {
        showModePicker = false
        if (mode == "LIVE") {
            bottomTab = "LIVE"
            headerMessage = if (preview) {
                "LIVE view opened. Owner sign-in is required before real-money Auto trade can be enabled."
            } else {
                "LIVE view opened. Use Auto trade to enable or disable live automation."
            }
        } else {
            scope.launch {
                actions.setMode(mode)
                    .onSuccess { headerMessage = it }
                    .onFailure { headerMessage = it.message ?: "Could not switch to $mode." }
            }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(GlassColors.Background, Color(0xFFF7F9F7), GlassColors.BackgroundBlue),
                ),
            )
            .windowInsetsPadding(WindowInsets.safeDrawing),
    ) {
        GlassAmbientBackground()
        Column(
            modifier = Modifier.fillMaxSize().widthIn(max = 620.dp).align(Alignment.TopCenter),
        ) {
            GlassHeader(
                status = status,
                onMenuClick = {
                    showNavMenu = !showNavMenu
                    showModePicker = false
                },
                onModeClick = {
                    showModePicker = !showModePicker
                    showNavMenu = false
                },
            )
            if (showNavMenu) {
                GlassHeaderNavigation(bottomTab) {
                    bottomTab = it
                    showNavMenu = false
                }
            }
            if (showModePicker) GlassHeaderModePicker(status.mode, ::selectMode)
            headerMessage?.let { text ->
                Box(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 3.dp)) {
                    GlassNotice(
                        text,
                        danger = text.contains("required", true) || text.contains("locked", true) ||
                            text.contains("fail", true) || text.contains("could not", true),
                    )
                }
            }
            Box(Modifier.weight(1f)) {
                when (bottomTab) {
                    "POSITIONS" -> GlassPositionsScreen(status)
                    "ANALYTICS" -> GlassAnalyticsScreen(status)
                    "SETTINGS" -> GlassSettingsScreen(
                        status = status,
                        preview = preview,
                        actions = actions,
                        onSignOut = onSignOut,
                        onOpenLive = { bottomTab = "LIVE" },
                    )
                    else -> GlassLiveScreen(
                        status = status,
                        preview = preview,
                        connectionError = connectionError,
                        actions = actions,
                        onPositions = { bottomTab = "POSITIONS" },
                        onSettings = { bottomTab = "SETTINGS" },
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
        drawCircle(GlassColors.Mint.copy(alpha = 0.42f), size.width * 0.65f,
            Offset(size.width * 1.02f, size.height * 0.10f))
    }
}

@Composable
private fun GlassHeader(status: ScreenStatus, onMenuClick: () -> Unit, onModeClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Surface(modifier = Modifier.size(48.dp).clip(RoundedCornerShape(16.dp))
            .clickable(onClickLabel = "Open navigation", onClick = onMenuClick)
            .semantics { contentDescription = "Open navigation" },
            shape = RoundedCornerShape(16.dp), color = GlassColors.Ink) {
            Canvas(Modifier.padding(12.dp)) {
                val line = Path().apply {
                    moveTo(0f, size.height * .78f); lineTo(size.width * .27f, size.height * .5f)
                    lineTo(size.width * .50f, size.height * .65f); lineTo(size.width, size.height * .12f)
                }
                drawPath(line, GlassColors.Lime, style = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round))
                drawLine(GlassColors.Lime, Offset(size.width * .66f, size.height * .12f),
                    Offset(size.width, size.height * .12f), 2.5.dp.toPx(), StrokeCap.Round)
            }
        }
        Column(Modifier.weight(1f).padding(start = 12.dp)) {
            Text("SPY", color = GlassColors.Text, fontWeight = FontWeight.Bold, fontSize = 23.sp, letterSpacing = (-.7).sp)
            Text("0DTE  /  TRADING", color = GlassColors.TextMuted, fontWeight = FontWeight.Medium,
                fontSize = 9.sp, letterSpacing = 1.2.sp)
        }
        Surface(modifier = Modifier.heightIn(min = 48.dp).clip(RoundedCornerShape(99.dp))
            .clickable(onClickLabel = "Change trading mode", onClick = onModeClick)
            .testTag("mode_picker"),
            shape = RoundedCornerShape(99.dp), color = GlassColors.White,
            border = BorderStroke(1.dp, GlassColors.Border)) {
            Row(Modifier.padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                Box(Modifier.size(6.dp).background(if (status.mode == "LIVE") GlassColors.Green else GlassColors.Amber, CircleShape))
                Text(status.mode, color = GlassColors.Text, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                Text("⌄", color = GlassColors.TextMuted, fontSize = 14.sp)
            }
        }
    }
}

@Composable
private fun GlassHeaderNavigation(selected: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        listOf("LIVE", "POSITIONS", "ANALYTICS", "SETTINGS").forEach { destination ->
            val active = selected == destination
            OutlinedButton(
                onClick = { onSelect(destination) },
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(15.dp),
                colors = ButtonDefaults.outlinedButtonColors(
                    containerColor = if (active) GlassColors.Mint else Color.White.copy(alpha = 0.50f),
                    contentColor = if (active) GlassColors.GreenDark else GlassColors.TextMuted,
                ),
                border = BorderStroke(1.dp, if (active) Color(0xFFA8E7CF) else GlassColors.Border),
            ) {
                Text(destination.lowercase().replaceFirstChar { it.uppercase() }, fontSize = 8.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun GlassHeaderModePicker(selected: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        listOf("LIVE", "PAPER", "SHADOW").forEach { mode ->
            val active = selected == mode
            OutlinedButton(
                onClick = { onSelect(mode) },
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(15.dp),
                colors = ButtonDefaults.outlinedButtonColors(
                    containerColor = if (active) GlassColors.Mint else Color.White.copy(alpha = 0.50f),
                    contentColor = if (active) GlassColors.GreenDark else GlassColors.TextMuted,
                ),
                border = BorderStroke(1.dp, if (active) Color(0xFFA8E7CF) else GlassColors.Border),
            ) { Text(mode, fontSize = 9.sp, fontWeight = FontWeight.Bold) }
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
    onSettings: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var topTab by rememberSaveable { mutableStateOf("AI") }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        GlassPortfolioCard(status)
        GlassMarketStrip(status)
        GlassAutoTradeCard(status, preview, saving, onSettings) { enabled ->
            saving = true
            scope.launch {
                actions.setLiveAutonomy(enabled)
                    .onSuccess { message = it }
                    .onFailure { message = it.message ?: "Could not change Auto trade. Check connection and settings." }
                saving = false
            }
        }
        connectionError?.let { GlassNotice(it, danger = true) }
        message?.let { GlassNotice(it) }
        GlassTopTabs(topTab) { topTab = it }
        when (topTab) {
            "AI" -> {
                GlassAiConsensus(status.ai, status.alert)
                GlassSetupCard(status)
                GlassPositionsCard(status, onPositions)
                GlassTodayCard(status)
            }
            "CHART" -> GlassChartPanel(status)
            "OPTIONS" -> GlassSetupCard(status)
            else -> GlassNewsPanel()
        }
        Spacer(Modifier.height(4.dp))
    }
}

@Composable
private fun GlassAutoTradeCard(status: ScreenStatus, preview: Boolean, saving: Boolean,
    onSettings: () -> Unit, onChange: (Boolean) -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    GlassPanel(modifier = Modifier.animateContentSize()) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("LIVE AUTOMATION", color = GlassColors.TextMuted, fontWeight = FontWeight.Medium,
                    letterSpacing = 1.2.sp, fontSize = 9.sp)
                Spacer(Modifier.height(4.dp))
                Text("Auto trade", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 23.sp)
            }
            AutoTradeSwitch(status.liveEnabled, !preview && status.connected && !saving, onChange)
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(6.dp).background(if (status.liveEnabled) GlassColors.Green else GlassColors.TextFaint, CircleShape))
            Spacer(Modifier.width(7.dp))
            Text(when {
                saving -> "Saving your preference…"
                !status.connected -> "Offline · last confirmed state"
                preview -> "Sign in to enable live trading"
                status.liveEnabled -> "On · ${status.liveState.replace('_', ' ').lowercase()}"
                else -> "Off · no new live entries"
            }, color = GlassColors.TextMuted, fontSize = 11.sp, modifier = Modifier.weight(1f))
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (expanded) "Hide details  −" else "How it works  +", color = GlassColors.GreenDark,
                fontWeight = FontWeight.SemiBold, fontSize = 11.sp,
                modifier = Modifier.heightIn(min = 48.dp).weight(1f).clip(RoundedCornerShape(8.dp))
                    .clickable { expanded = !expanded }.padding(top = 16.dp).testTag("autotrade_details"))
            Text("Settings  ↗", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 11.sp,
                modifier = Modifier.heightIn(min = 48.dp).clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = onSettings).padding(start = 12.dp, top = 16.dp).testTag("autotrade_settings"))
        }
        if (expanded) {
            Text("On: automatically opens and closes qualified live trades, even with your phone closed. Off: cancels pending buys and closes positions opened by Auto trade when executable.",
                color = GlassColors.TextMuted, fontSize = 12.sp, lineHeight = 18.sp)
            Spacer(Modifier.height(8.dp))
            Text("Your saved limits repeat each trading day until you switch it off.", color = GlassColors.TextMuted, fontSize = 12.sp)
            if (status.connected) {
                Spacer(Modifier.height(8.dp))
                Text(status.liveReason, color = GlassColors.TextMuted, fontSize = 11.sp)
            }
            if (status.liveEnabled && status.liveReasons.isNotEmpty()) {
                Text(status.liveReasons.joinToString(" · ") { it.replace('_', ' ') }, color = GlassColors.Amber, fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun AutoTradeSwitch(checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    val thumbOffset by animateDpAsState(if (checked) 24.dp else 2.dp, label = "Auto trade thumb")
    Box(
        Modifier.size(width = 64.dp, height = 48.dp)
            .testTag("auto_trade_switch").semantics { contentDescription = "Auto trade" }
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        contentAlignment = Alignment.Center,
    ) {
        Box(Modifier.size(width = 54.dp, height = 32.dp).clip(RoundedCornerShape(99.dp))
            .background((if (checked) GlassColors.Green else Color(0xFFDCE3DF)).copy(alpha = if (enabled) 1f else 0.55f))) {
            Box(Modifier.align(Alignment.CenterStart).offset(x = thumbOffset).size(28.dp)
                .background(Color.White, CircleShape))
        }
    }
}

@Composable
private fun GlassMarketStrip(status: ScreenStatus) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 3.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(42.dp).background(GlassColors.White, RoundedCornerShape(14.dp)), contentAlignment = Alignment.Center) {
            Text("S", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 20.sp)
        }
        Column(Modifier.weight(1f).padding(start = 11.dp)) {
            Text("SPY", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
            Text("S&P 500 ETF", color = GlassColors.TextMuted, fontSize = 10.sp)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(status.spot?.let { money(it) } ?: "—", color = GlassColors.Text,
                fontSize = 22.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-.7).sp)
            val delayed = (status.feedDelay ?: 0.0) > 5 || (status.dataAge ?: 0.0) > 5
            Text(when {
                !status.connected -> "Connecting"
                status.feedDelay != null && status.feedDelay > 5 -> "Delayed · %.0fs".format(status.feedDelay)
                status.dataAge != null -> "Updated %.0fs ago".format(status.dataAge)
                else -> "Engine connected"
            }, color = if (delayed) GlassColors.Amber else GlassColors.TextMuted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun GlassPortfolioCard(status: ScreenStatus) {
    val paper = status.mode != "LIVE"
    val balance = if (paper) status.paperCash else status.brokerState.totalEquity ?: status.brokerState.cashAvailable
    val pnl = if (paper) status.paperPnl else status.brokerState.dailyPnl
    val lightPnl = if (pnl == null) GlassColors.White else if (pnl < 0) Color(0xFFFFB7B9) else GlassColors.Lime
    Surface(shape = RoundedCornerShape(28.dp), color = GlassColors.Ink, modifier = Modifier.fillMaxWidth()) {
        Box {
            Canvas(Modifier.matchParentSize()) {
                drawCircle(Color.White.copy(alpha = .025f), size.width * .5f, Offset(size.width * 1.03f, size.height * .1f))
                drawCircle(Color.White.copy(alpha = .035f), size.width * .34f, Offset(size.width * 1.03f, size.height * .1f))
            }
            Column(Modifier.fillMaxWidth().padding(22.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(if (paper) "PAPER ACCOUNT" else "WEBULL ACCOUNT", color = GlassColors.InkMuted,
                        fontSize = 10.sp, fontWeight = FontWeight.Medium, letterSpacing = 1.5.sp, modifier = Modifier.weight(1f))
                    Text(if (status.connected) "●  Connected" else "○  Connecting", color = GlassColors.InkMuted, fontSize = 9.sp)
                }
                Spacer(Modifier.height(17.dp))
                Text(if (paper) "Settled cash" else if (status.brokerState.totalEquity != null) "Account equity" else "Available cash",
                    color = GlassColors.InkMuted, fontSize = 12.sp)
                BoxWithConstraints(Modifier.fillMaxWidth()) {
                    val amount = money(balance)
                    val amountSize = (maxWidth.value / (amount.length.coerceAtLeast(1) * .62f) /
                        LocalDensity.current.fontScale).coerceIn(22f, 40f)
                    Text(amount, color = GlassColors.White, fontSize = amountSize.sp,
                        fontWeight = FontWeight.Medium, letterSpacing = (-1.5).sp, maxLines = 1)
                }
                Spacer(Modifier.height(18.dp))
                Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(alpha = .12f)))
                Spacer(Modifier.height(15.dp))
                Row {
                    Column(Modifier.weight(1.4f)) {
                        Text(if (paper) "Realized · since reset" else "Today's P&L", color = GlassColors.InkMuted, fontSize = 10.sp)
                        Spacer(Modifier.height(4.dp))
                        Text(signedMoney(pnl), color = lightPnl, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
                    }
                    Column(Modifier.weight(1f), horizontalAlignment = Alignment.End) {
                        Text("Open positions", color = GlassColors.InkMuted, fontSize = 10.sp)
                        Spacer(Modifier.height(4.dp))
                        Text((if (paper) status.paperPositions else status.brokerState.openPositions).toString(),
                            color = GlassColors.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
    }
}

@Composable
private fun GlassTopTabs(selected: String, onSelect: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Color(0xFFE8EEEA)).padding(4.dp)) {
        listOf("AI" to "Insights", "CHART" to "Chart", "OPTIONS" to "Options", "NEWS" to "News").forEach { (key, label) ->
            val active = selected == key
            val color by animateColorAsState(if (active) GlassColors.White else Color.Transparent, label = "Segment color")
            Box(Modifier.weight(1f).heightIn(min = 44.dp).clip(RoundedCornerShape(12.dp)).background(color)
                .selectable(active, role = Role.Tab, onClick = { onSelect(key) }).testTag("insight_$key"),
                contentAlignment = Alignment.Center) {
                Text(label, color = if (active) GlassColors.Text else GlassColors.TextMuted, fontSize = 12.sp,
                    fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal)
            }
        }
    }
}

@Composable
private fun GlassAiConsensus(ai: AiDecisionState, alert: LiveAlert?) {
    GlassPanel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("MODEL CONSENSUS", color = GlassColors.Text, fontWeight = FontWeight.SemiBold,
                fontSize = 11.sp, letterSpacing = .8.sp, modifier = Modifier.weight(1f))
            Text(if (ai.active) "Active" else "Waiting", color = GlassColors.TextMuted, fontSize = 10.sp)
        }
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
    val directionProbability = pUp
    val sweep = ((directionProbability ?: 0.0).coerceIn(0.0, 1.0) * 270.0).toFloat()

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 7.dp.toPx()
            val inset = 7.dp.toPx()
            drawArc(
                color = GlassColors.PanelSoft,
                startAngle = 135f,
                sweepAngle = 270f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
            drawArc(
                color = GlassColors.Green,
                startAngle = 135f,
                sweepAngle = sweep,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - inset * 2, size.height - inset * 2),
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                directionProbability?.let { percent(it) } ?: "—",
                color = GlassColors.Text,
                fontSize = 28.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "UPSIDE PROB.",
                color = GlassColors.TextMuted,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

@Composable
private fun GlassProbabilityRow(label: String, value: Double?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = GlassColors.TextMuted, fontSize = 12.sp, modifier = Modifier.width(62.dp))
        Box(
            Modifier
                .weight(1f)
                .height(6.dp)
                .clip(RoundedCornerShape(99.dp))
                .background(GlassColors.PanelSoft),
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
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.End,
            modifier = Modifier.width(42.dp),
        )
    }
}

@Composable
private fun GlassSetupCard(status: ScreenStatus) {
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
                Text("NEXT OPPORTUNITY", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                Spacer(Modifier.height(5.dp))
                Text(
                    alert?.let { optionLabel(it.symbol, it.right) } ?: "Watching for an entry",
                    color = GlassColors.Text,
                    fontWeight = FontWeight.SemiBold,
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
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
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

        Text(
            if (status.liveEnabled) "Auto trade manages qualified entries and exits."
            else "Turn on Auto trade to allow live entries and automatic exits.",
            color = GlassColors.TextMuted, fontSize = 11.sp,
        )
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
            Text(label, color = if (enabled) tint else GlassColors.TextFaint, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
            Text(caption, color = if (enabled) GlassColors.Text else GlassColors.TextFaint, fontSize = 9.sp)
        }
    }
}

@Composable
private fun GlassPositionsCard(status: ScreenStatus, onOpen: (() -> Unit)?) {
    val paper = status.mode != "LIVE"
    val count = if (paper) status.paperPositions else status.brokerState.openPositions
    val pnl = if (paper) status.paperUnrealizedPnl else status.brokerState.openPnl

    GlassPanel(modifier = if (onOpen != null) Modifier.clip(RoundedCornerShape(24.dp)).clickable(onClick = onOpen) else Modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("POSITIONS ($count)", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp, modifier = Modifier.weight(1f))
            if (onOpen != null) Text("↗", color = GlassColors.TextMuted, fontSize = 20.sp)
        }
        Spacer(Modifier.height(7.dp))
        if (count == 0) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(4.dp).height(38.dp).background(GlassColors.Green, RoundedCornerShape(99.dp)))
                Spacer(Modifier.width(9.dp))
                Column(Modifier.weight(1f)) {
                    Text("FLAT", color = GlassColors.Green, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
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
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp,
                    )
                    Text("$count open position${if (count == 1) "" else "s"}", color = GlassColors.TextMuted, fontSize = 10.sp)
                }
                Text(signedMoney(pnl), color = pnlColor(pnl), fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            }
        }
    }
}

@Composable
private fun GlassTodayCard(status: ScreenStatus) {
    val pnl = if (status.mode != "LIVE") status.paperPnl else status.brokerState.dailyPnl
    GlassPanel {
        Text(if (status.mode == "LIVE") "LIVE · TODAY" else "PAPER · SINCE RESET", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Row {
            GlassTodayMetric("Executions", if (status.mode != "LIVE") status.paperTrades.toString() else "—", Modifier.weight(1f))
            GlassDivider()
            GlassTodayMetric("Closed trades", if (status.mode != "LIVE") status.paperSells.toString() else "—", Modifier.weight(1f))
            GlassDivider()
            GlassTodayMetric("P&L", signedMoney(pnl), Modifier.weight(1f), pnlColor(pnl))
        }
    }
}

@Composable
private fun GlassTodayMetric(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = GlassColors.Text) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, color = GlassColors.TextMuted, fontSize = 9.sp)
        Text(value, color = valueColor, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
    }
}

@Composable
private fun GlassDivider() {
    Box(Modifier.width(1.dp).height(34.dp).background(GlassColors.Border))
}

@Composable
private fun GlassChartPanel(status: ScreenStatus) {
    GlassPanel {
        Text("PRICE HISTORY", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Text(
            status.spot?.let { "SPY  %.2f".format(it) } ?: "SPY  —",
            color = GlassColors.Text,
            fontSize = 26.sp,
            fontWeight = FontWeight.SemiBold,
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
                Text("Price history is not available yet", color = GlassColors.TextMuted, fontSize = 11.sp)
                Text("The latest SPY quote appears above.", color = GlassColors.TextFaint, fontSize = 9.sp)
            }
        }
    }
}

@Composable
private fun GlassNewsPanel() {
    GlassPanel {
        Text("NEWS", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        Text(
            "No news feed is connected yet. Insights currently reflect market data only.",
            color = GlassColors.TextMuted,
            fontSize = 11.sp,
        )
    }
}

@Composable
private fun GlassPositionsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        GlassSectionTitle("Positions")
        GlassPositionsCard(status, null)
        GlassPanel {
            val live = status.mode == "LIVE"
            GlassKeyValue("Mode", status.mode)
            GlassKeyValue("Open positions", if (live) status.brokerState.openPositions.toString() else status.paperPositions.toString())
            GlassKeyValue("Pending broker orders", if (live) status.brokerState.pendingOrders.toString() else "—")
            GlassKeyValue("Open P&L", signedMoney(if (live) status.brokerState.openPnl else status.paperUnrealizedPnl), pnlColor(if (live) status.brokerState.openPnl else status.paperUnrealizedPnl))
            GlassKeyValue(if (live) "Day P&L" else "Realized since reset", if (live) signedMoney(status.brokerState.dailyPnl) else signedMoney(status.paperPnl), pnlColor(if (live) status.brokerState.dailyPnl else status.paperPnl))
            GlassKeyValue("New entry eligible", if (status.brokerState.entryAllowed) "YES" else "NO", if (status.brokerState.entryAllowed) GlassColors.Green else GlassColors.Amber)
        }
        status.livePositions.forEach { position ->
            GlassPanel {
                Text(position.symbol, color = GlassColors.Text, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                GlassKeyValue("Contracts owned", position.quantity.toString())
                GlassKeyValue("Average broker fill", money(position.averagePrice))
            }
        }
    }
}

@Composable
private fun GlassAnalyticsScreen(status: ScreenStatus) {
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        GlassSectionTitle("Analytics")
        GlassAiConsensus(status.ai, status.alert)
        GlassPanel {
            Text("ENGINE", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            GlassKeyValue("Strategy", status.strategy)
            GlassKeyValue("Risk", status.riskProfile)
            GlassKeyValue("Exit", status.exitProfile)
            GlassKeyValue("Decision", status.decision)
            GlassKeyValue("Worker", status.workerState)
            Spacer(Modifier.height(6.dp))
            Text(status.reason, color = GlassColors.TextMuted, fontSize = 10.sp)
        }
        GlassPanel {
            Text("PAPER LEDGER", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row {
                GlassTodayMetric("Cash", money(status.paperCash), Modifier.weight(1f))
                GlassDivider()
                GlassTodayMetric("Realized P&L", signedMoney(status.paperPnl), Modifier.weight(1f), pnlColor(status.paperPnl))
                GlassDivider()
                GlassTodayMetric("Executions", status.paperTrades.toString(), Modifier.weight(1f))
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
    onOpenLive: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var loss by remember(status.risk.loss) { mutableStateOf(status.risk.loss?.toString() ?: "25") }
    var gain by remember(status.risk.gain) { mutableStateOf(status.risk.gain?.toString() ?: "40") }
    var exposure by remember(status.risk.exposure) { mutableStateOf(status.risk.exposure?.let { (it * 100).toString() } ?: "20") }
    var contracts by remember(status.risk.contracts) { mutableStateOf(status.risk.contracts?.toString() ?: "1") }
    var message by remember { mutableStateOf<String?>(null) }
    var modeMessage by remember { mutableStateOf<String?>(null) }
    var paperMessage by remember { mutableStateOf<String?>(null) }
    var paperLimit by remember(status.paperStartingCash) {
        mutableStateOf(status.paperStartingCash?.let(::trimNumber) ?: status.paperCash?.let(::trimNumber) ?: "1000")
    }
    var resettingPaper by remember { mutableStateOf(false) }
    var pendingPaperReset by remember { mutableStateOf<Double?>(null) }
    var appKey by remember { mutableStateOf("") }
    var appSecret by remember { mutableStateOf("") }
    var savingCredentials by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        GlassSectionTitle("Settings")
        GlassPanel {
            Text("TRADING MODE", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                listOf("LIVE", "PAPER", "SHADOW").forEach { mode ->
                    val active = status.mode == mode
                    OutlinedButton(
                        onClick = {
                            if (mode == "LIVE") {
                                onOpenLive()
                                modeMessage = if (preview) {
                                    "LIVE view opened. Owner sign-in is required before real-money Auto trade can be enabled."
                                } else {
                                    "LIVE view opened. Use Auto trade to enable or disable live automation."
                                }
                            } else {
                                scope.launch {
                                    actions.setMode(mode)
                                        .onSuccess { modeMessage = it }
                                        .onFailure { modeMessage = it.message ?: "Could not switch to $mode." }
                                }
                            }
                        },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(18.dp),
                        colors = ButtonDefaults.outlinedButtonColors(
                            containerColor = if (active) GlassColors.Mint else Color.White.copy(alpha = 0.35f),
                            contentColor = if (active) GlassColors.GreenDark else GlassColors.TextMuted,
                        ),
                        border = BorderStroke(1.dp, if (active) Color(0xFFA8E7CF) else GlassColors.Border),
                    ) { Text(mode, fontSize = 10.sp, fontWeight = FontWeight.Bold) }
                }
            }
            modeMessage?.let {
                Spacer(Modifier.height(7.dp))
                GlassNotice(it, danger = it.contains("required", true) || it.contains("locked", true) || it.contains("fail", true) || it.contains("could not", true))
            }
        }

        GlassPanel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("RISK CONTROLS", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp, modifier = Modifier.weight(1f))
                Text(if (status.risk.armed) "ARMED" else "NOT ARMED", color = if (status.risk.armed) GlassColors.Green else GlassColors.Amber, fontSize = 9.sp, fontWeight = FontWeight.SemiBold)
            }
            Spacer(Modifier.height(8.dp))
            Text("Standing limits repeat each trading day. Turn Auto trade off before changing them.", color = GlassColors.TextMuted, fontSize = 11.sp)
            GlassTextField(loss, { loss = it }, "Daily loss stop ($)")
            GlassTextField(gain, { gain = it }, "Daily gain stop ($)")
            GlassTextField(exposure, { exposure = it }, "Max account exposure (%)")
            GlassTextField(contracts, { contracts = it }, "Max contracts")
            Spacer(Modifier.height(7.dp))
            GlassPrimaryButton(
                text = "Save Live Limits",
                enabled = !preview && !status.liveEnabled,
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    val l = loss.toDoubleOrNull()
                    val g = gain.toDoubleOrNull()
                    val e = exposure.toDoubleOrNull()?.div(100.0)
                    val c = contracts.toIntOrNull()
                    if (l == null || g == null || e == null || c == null || !l.isFinite() || !g.isFinite() || !e.isFinite()) {
                        message = "Enter valid numeric limits"
                    } else {
                        scope.launch {
                            actions.armRisk(l, g, e, c).onSuccess { message = it }.onFailure { message = it.message }
                        }
                    }
                },
            )
            Spacer(Modifier.height(7.dp))
            GlassOutlineButton(
                text = "Disarm Live",
                onClick = { scope.launch { actions.disarmRisk().onSuccess { message = it }.onFailure { message = it.message } } },
                modifier = Modifier.fillMaxWidth(),
            )
        }

        GlassPanel {
            Text("AUTONOMOUS PAPER", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
            Spacer(Modifier.height(7.dp))
            GlassKeyValue("Starting cash", money(status.paperStartingCash))
            GlassKeyValue("Cash", money(status.paperCash))
            GlassKeyValue("Realized P&L", signedMoney(status.paperPnl), pnlColor(status.paperPnl))
            GlassKeyValue("Executions", status.paperTrades.toString())
            GlassKeyValue("Buys / sells", "${status.paperBuys} / ${status.paperSells}")
            GlassKeyValue("Unrealized P&L", signedMoney(status.paperUnrealizedPnl), pnlColor(status.paperUnrealizedPnl))
            Text("Each buy or sell counts once. Realized P&L changes when contracts are sold.", color = GlassColors.TextMuted, fontSize = 11.sp)
            Spacer(Modifier.height(8.dp))
            GlassOutlineButton(
                text = if (status.paperArmed) "Stop New Paper Entries" else "Arm Autonomous Paper",
                onClick = {
                    scope.launch {
                        actions.setPaperAutonomy(!status.paperArmed)
                            .onSuccess { paperMessage = it }
                            .onFailure { paperMessage = it.message }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(10.dp))
            Text("PAPER ACCOUNT SIZE", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 10.sp)
            GlassTextField(paperLimit, { paperLimit = it }, "Paper account size ($)")
            Text(
                "Changing this amount resets the paper ledger, including open paper positions, executions, and paper P&L. It never affects Webull.",
                color = GlassColors.TextMuted,
                fontSize = 10.sp,
            )
            Spacer(Modifier.height(7.dp))
            GlassPrimaryButton(
                text = if (resettingPaper) "Resetting…" else "Set Paper Account Size",
                enabled = !resettingPaper && status.mode != "LIVE",
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    val amount = paperLimit.toDoubleOrNull()
                    if (amount == null || !amount.isFinite() || amount <= 0.0 || amount > 1_000_000.0) {
                        paperMessage = "Enter a paper account size from $0.01 to $1,000,000."
                    } else pendingPaperReset = amount
                },
            )
            pendingPaperReset?.let { amount ->
                Spacer(Modifier.height(7.dp))
                GlassNotice("Confirm reset to ${money(amount)}. Existing paper-only history and positions will be cleared.", danger = true)
                Spacer(Modifier.height(7.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    GlassOutlineButton("Cancel", { pendingPaperReset = null }, Modifier.weight(1f))
                    GlassPrimaryButton(
                        text = "Confirm Reset",
                        enabled = !resettingPaper,
                        modifier = Modifier.weight(1f),
                        onClick = {
                            resettingPaper = true
                            scope.launch {
                                actions.resetPaperAccount(amount)
                                    .onSuccess {
                                        paperMessage = it
                                        paperLimit = trimNumber(amount)
                                        pendingPaperReset = null
                                    }
                                    .onFailure { paperMessage = it.message ?: "Paper account reset failed." }
                                resettingPaper = false
                            }
                        },
                    )
                }
            }
            paperMessage?.let {
                Spacer(Modifier.height(7.dp))
                GlassNotice(it, danger = it.contains("fail", true) || it.contains("invalid", true) || it.contains("cannot", true))
            }
        }

        GlassPanel {
            Text("ACCOUNT", color = GlassColors.Text, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
            Spacer(Modifier.height(7.dp))
            GlassKeyValue("Provider", status.broker.uppercase())
            GlassKeyValue("API configured", if (status.brokerConfigured) "YES" else "NO")
            Text("Webull production OpenAPI credentials", color = GlassColors.TextMuted, fontSize = 11.sp)
            OutlinedTextField(appKey, { appKey = it }, label = { Text("App key") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(appSecret, { appSecret = it }, label = { Text("App secret") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            GlassPrimaryButton(
                text = if (savingCredentials) "Saving…" else "Save Production Credentials",
                enabled = !preview && !status.liveEnabled && !savingCredentials && appKey.isNotBlank() && appSecret.isNotBlank(),
                onClick = {
                    savingCredentials = true
                    scope.launch {
                        actions.saveCredentials(appKey.trim(), appSecret.trim())
                            .onSuccess { message = it; appKey = ""; appSecret = "" }
                            .onFailure { message = it.message }
                        savingCredentials = false
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
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
    Surface(modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 10.dp),
        shape = RoundedCornerShape(26.dp), color = GlassColors.White,
        border = BorderStroke(1.dp, GlassColors.Border), shadowElevation = 5.dp) {
        Row(Modifier.fillMaxWidth().padding(6.dp)) {
            listOf("LIVE" to "Overview", "POSITIONS" to "Positions", "ANALYTICS" to "Analytics", "SETTINGS" to "Settings").forEach { (key, label) ->
                val active = selected == key
                val tint by animateColorAsState(if (active) GlassColors.GreenDark else GlassColors.TextMuted, label = "Navigation color")
                Column(Modifier.weight(1f).heightIn(min = 62.dp).clip(RoundedCornerShape(20.dp))
                    .background(if (active) GlassColors.Mint else Color.Transparent)
                    .selectable(active, role = Role.Tab, onClick = { onSelect(key) }).testTag("nav_$key")
                    .padding(vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    GlassNavIcon(key, tint)
                    Text(label, color = tint, fontSize = 10.sp, fontWeight = if (active) FontWeight.SemiBold else FontWeight.Medium,
                        maxLines = 1)
                }
            }
        }
    }
}

@Composable
private fun GlassNavIcon(destination: String, tint: Color) {
    Canvas(Modifier.size(22.dp)) {
        val w = size.width
        val h = size.height
        val stroke = 1.7.dp.toPx()
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) =
            drawLine(tint, Offset(w * x1, h * y1), Offset(w * x2, h * y2), stroke, StrokeCap.Round)
        when (destination) {
            "LIVE" -> {
                val path = Path().apply {
                    moveTo(w*.12f,h*.45f); lineTo(w*.5f,h*.12f); lineTo(w*.88f,h*.45f)
                    moveTo(w*.23f,h*.4f); lineTo(w*.23f,h*.87f); lineTo(w*.42f,h*.87f)
                    lineTo(w*.42f,h*.62f); lineTo(w*.60f,h*.62f); lineTo(w*.60f,h*.87f)
                    lineTo(w*.78f,h*.87f); lineTo(w*.78f,h*.4f)
                }; drawPath(path,tint,style=Stroke(stroke,cap=StrokeCap.Round))
            }
            "POSITIONS" -> {
                line(.18f,.32f,.82f,.32f); line(.18f,.32f,.18f,.83f); line(.18f,.83f,.82f,.83f)
                line(.82f,.83f,.82f,.32f); line(.34f,.32f,.34f,.16f); line(.34f,.16f,.66f,.16f)
                line(.66f,.16f,.66f,.32f); line(.19f,.53f,.81f,.53f)
            }
            "ANALYTICS" -> { line(.2f,.83f,.2f,.51f); line(.5f,.83f,.5f,.17f); line(.8f,.83f,.8f,.35f) }
            else -> {
                line(.12f,.27f,.88f,.27f); line(.12f,.72f,.88f,.72f)
                drawCircle(GlassColors.White,w*.12f,Offset(w*.36f,h*.27f))
                drawCircle(tint,w*.12f,Offset(w*.36f,h*.27f),style=Stroke(stroke))
                drawCircle(GlassColors.White,w*.12f,Offset(w*.68f,h*.72f))
                drawCircle(tint,w*.12f,Offset(w*.68f,h*.72f),style=Stroke(stroke))
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
        shape = RoundedCornerShape(24.dp),
        color = GlassColors.Panel,
        border = BorderStroke(1.dp, GlassColors.Border),
        shadowElevation = 0.dp,
    ) {
        Column(Modifier.fillMaxWidth().padding(18.dp), content = content)
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
            fontSize = 12.sp,
            lineHeight = 18.sp,
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
        modifier = modifier.heightIn(min = 52.dp),
        shape = RoundedCornerShape(15.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = GlassColors.Ink,
            contentColor = GlassColors.White,
            disabledContainerColor = Color(0xFFDCE5E8),
            disabledContentColor = GlassColors.TextFaint,
        ),
    ) {
        Text(text, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
    }
}

@Composable
private fun GlassOutlineButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.heightIn(min = 48.dp),
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
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, color = GlassColors.TextMuted, fontSize = 12.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 12.sp, fontWeight = FontWeight.Medium,
            textAlign = TextAlign.End, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun GlassSectionTitle(title: String) {
    Column(Modifier.padding(horizontal = 2.dp, vertical = 8.dp)) {
        Text(title, color = GlassColors.Text, fontSize = 30.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-1).sp)
        Text(when (title) {
            "Positions" -> "Your exposure, at a glance."
            "Analytics" -> "A clearer view of every decision."
            else -> "Your account. Your controls."
        }, color = GlassColors.TextMuted, fontSize = 12.sp)
    }
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

private fun money(value: Double?): String = value?.let { "$%,.2f".format(it) } ?: "—"
private fun price(value: Double?): String = value?.let { "%.2f".format(it) } ?: "—"
private fun signedMoney(value: Double?): String = value?.let { (if (it >= 0) "+" else "−") + "$%,.2f".format(kotlin.math.abs(it)) } ?: "—"
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
