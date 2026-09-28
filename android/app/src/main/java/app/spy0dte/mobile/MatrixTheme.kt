package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * Option #4 from the approved UI showcase: Glass.
 *
 * The legacy Matrix* identifiers are intentionally kept only at the theme boundary
 * so WebullTradeActivity does not duplicate app wiring. All visible treatment is
 * the light frosted-glass system from the selected design.
 */
internal object MatrixColors {
    val Background = Color(0xFFF2F8FB)
    val BackgroundBlue = Color(0xFFE7F6FA)
    val Surface = Color(0xCCFFFFFF)
    val SurfaceRaised = Color(0xE8FFFFFF)
    val SurfaceSoft = Color(0xB8F6FBFD)
    val Neon = Color(0xFF18C98A)
    val NeonBright = Color(0xFF38DFA2)
    val NeonDim = Color(0xFF8FE7C8)
    val Border = Color(0xFFCAE2E8)
    val BorderSoft = Color(0x99C9DFE5)
    val Text = Color(0xFF101A22)
    val TextMuted = Color(0xFF637681)
    val TextFaint = Color(0xFF9AA9B1)
    val Red = Color(0xFFFF625E)
    val Amber = Color(0xFFE39A21)
    val Black = Color(0xFF0A1116)
    val White = Color(0xFFFFFFFF)
    val Blue = Color(0xFF4EA9D8)
    val SoftBlue = Color(0xFFD8F0F8)
    val SoftGreen = Color(0xFFD9F7EB)
    val SoftRed = Color(0xFFFFE9E8)
}

private val GlassScheme = lightColorScheme(
    primary = MatrixColors.Neon,
    onPrimary = MatrixColors.White,
    primaryContainer = MatrixColors.SoftGreen,
    onPrimaryContainer = MatrixColors.Text,
    secondary = MatrixColors.Blue,
    onSecondary = MatrixColors.White,
    secondaryContainer = MatrixColors.SoftBlue,
    onSecondaryContainer = MatrixColors.Text,
    background = MatrixColors.Background,
    onBackground = MatrixColors.Text,
    surface = MatrixColors.SurfaceRaised,
    onSurface = MatrixColors.Text,
    surfaceVariant = MatrixColors.SurfaceSoft,
    onSurfaceVariant = MatrixColors.TextMuted,
    outline = MatrixColors.Border,
    error = MatrixColors.Red,
)

@Composable
internal fun MatrixTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = GlassScheme,
        content = content,
    )
}
