package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

internal object GlassColors {
    val Background = Color(0xFF071126)
    val BackgroundDeep = Color(0xFF040A18)
    val Navy = Color(0xFF0C1836)
    val Panel = Color(0xA6152348)
    val PanelStrong = Color(0xC51A2851)
    val PanelSoft = Color(0x80192A55)
    val Border = Color(0x668CA8FF)
    val BorderBright = Color(0x99A9BBFF)
    val Blue = Color(0xFF5C8CFF)
    val Cyan = Color(0xFF66E2FF)
    val Violet = Color(0xFFA56CFF)
    val VioletDeep = Color(0xFF6C4CFF)
    val Mint = Color(0xFF5EF0BD)
    val Red = Color(0xFFFF6B83)
    val Amber = Color(0xFFFFC96B)
    val Text = Color(0xFFF5F7FF)
    val TextMuted = Color(0xFFB5C3E7)
    val TextFaint = Color(0xFF7586AE)
    val Black = Color(0xFF050817)
}

private val GlassScheme = darkColorScheme(
    primary = GlassColors.Blue,
    onPrimary = GlassColors.Text,
    primaryContainer = GlassColors.PanelStrong,
    onPrimaryContainer = GlassColors.Text,
    secondary = GlassColors.Violet,
    onSecondary = GlassColors.Text,
    secondaryContainer = GlassColors.PanelSoft,
    onSecondaryContainer = GlassColors.Text,
    tertiary = GlassColors.Cyan,
    background = GlassColors.Background,
    onBackground = GlassColors.Text,
    surface = GlassColors.Panel,
    onSurface = GlassColors.Text,
    surfaceVariant = GlassColors.PanelStrong,
    onSurfaceVariant = GlassColors.TextMuted,
    outline = GlassColors.Border,
    error = GlassColors.Red,
)

@Composable
internal fun GlassTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = GlassScheme, content = content)
}
