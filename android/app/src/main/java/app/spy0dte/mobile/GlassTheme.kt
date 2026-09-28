package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

internal object GlassColors {
    val Background = Color(0xFFF4FAFC)
    val BackgroundBlue = Color(0xFFE8F6FA)
    val Panel = Color(0xBFFFFFFF)
    val PanelStrong = Color(0xE8FFFFFF)
    val PanelSoft = Color(0x9CF7FBFD)
    val Border = Color(0xFFC7DEE5)
    val BorderBright = Color(0xFFEAF4F7)
    val Green = Color(0xFF20C98A)
    val GreenDark = Color(0xFF0BA66F)
    val Mint = Color(0xFFD8F7EB)
    val Blue = Color(0xFF57A8D5)
    val Cyan = Blue
    val SoftBlue = Color(0xFFDDF1F8)
    val Red = Color(0xFFFF625E)
    val SoftRed = Color(0xFFFFE9E8)
    val Amber = Color(0xFFE39A21)
    val Text = Color(0xFF101A22)
    val TextMuted = Color(0xFF637681)
    val TextFaint = Color(0xFF99A8B0)
    val White = Color(0xFFFFFFFF)
    val Black = Color(0xFF0A1116)
}

private val GlassScheme = lightColorScheme(
    primary = GlassColors.Green,
    onPrimary = GlassColors.White,
    primaryContainer = GlassColors.Mint,
    onPrimaryContainer = GlassColors.Text,
    secondary = GlassColors.Blue,
    onSecondary = GlassColors.White,
    secondaryContainer = GlassColors.SoftBlue,
    onSecondaryContainer = GlassColors.Text,
    background = GlassColors.Background,
    onBackground = GlassColors.Text,
    surface = GlassColors.PanelStrong,
    onSurface = GlassColors.Text,
    surfaceVariant = GlassColors.PanelSoft,
    onSurfaceVariant = GlassColors.TextMuted,
    outline = GlassColors.Border,
    error = GlassColors.Red,
)

@Composable
internal fun GlassTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = GlassScheme, content = content)
}
