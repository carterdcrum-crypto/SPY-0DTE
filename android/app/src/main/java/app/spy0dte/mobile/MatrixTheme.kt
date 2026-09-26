package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

internal object MatrixColors {
    val Background = Color(0xFF010705)
    val Surface = Color(0xFF04110D)
    val SurfaceRaised = Color(0xFF071A14)
    val SurfaceSoft = Color(0xFF0A2119)
    val Neon = Color(0xFF00FF88)
    val NeonBright = Color(0xFF46FFA6)
    val NeonDim = Color(0xFF0A9A61)
    val Border = Color(0xFF08784F)
    val BorderSoft = Color(0xFF0A3D2C)
    val Text = Color(0xFFF0FFF8)
    val TextMuted = Color(0xFF8CB7A4)
    val TextFaint = Color(0xFF547566)
    val Red = Color(0xFFFF5F68)
    val Amber = Color(0xFFFFCA62)
    val Black = Color(0xFF000000)
}

private val MatrixScheme = darkColorScheme(
    primary = MatrixColors.Neon,
    onPrimary = MatrixColors.Black,
    primaryContainer = MatrixColors.SurfaceRaised,
    onPrimaryContainer = MatrixColors.Text,
    secondary = MatrixColors.NeonBright,
    onSecondary = MatrixColors.Black,
    secondaryContainer = MatrixColors.SurfaceSoft,
    onSecondaryContainer = MatrixColors.Text,
    background = MatrixColors.Background,
    onBackground = MatrixColors.Text,
    surface = MatrixColors.Surface,
    onSurface = MatrixColors.Text,
    surfaceVariant = MatrixColors.SurfaceRaised,
    onSurfaceVariant = MatrixColors.TextMuted,
    outline = MatrixColors.Border,
    error = MatrixColors.Red,
)

@Composable
internal fun MatrixTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = MatrixScheme,
        content = content,
    )
}
