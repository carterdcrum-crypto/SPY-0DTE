package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.googlefonts.Font
import androidx.compose.ui.text.googlefonts.GoogleFont

internal object GlassColors {
    val Ink = Color(0xFF112D28)
    val Lime = Color(0xFFC1F3C7)
    val InkMuted = Color(0xFFA4BEB4)
    val Background = Color(0xFFF3F5F4)
    val BackgroundBlue = Color(0xFFEAF1EF)
    val Panel = Color(0xFFFFFFFF)
    val PanelStrong = Color(0xFFFFFFFF)
    val PanelSoft = Color(0xFFF1F5F3)
    val Border = Color(0xFFE0E7E4)
    val BorderBright = Color(0xFFF5F8F6)
    val Green = Color(0xFF008D68)
    val GreenDark = Color(0xFF00694E)
    val Mint = Color(0xFFE0F4EB)
    val Blue = Color(0xFF617E92)
    val Cyan = Blue
    val SoftBlue = Color(0xFFEAF0F5)
    val Red = Color(0xFFCB4C54)
    val SoftRed = Color(0xFFFCEBED)
    val Amber = Color(0xFF96600C)
    val Text = Color(0xFF152A27)
    val TextMuted = Color(0xFF60746E)
    val TextFaint = Color(0xFF83958E)
    val White = Color(0xFFFFFFFF)
    val Black = Color(0xFF112925)
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

private val GoogleFontsProvider = GoogleFont.Provider(
    providerAuthority = "com.google.android.gms.fonts",
    providerPackage = "com.google.android.gms",
    certificates = R.array.com_google_android_gms_fonts_certs,
)

private val GeistName = GoogleFont("Geist")

private val GeistFamily = FontFamily(
    Font(googleFont = GeistName, fontProvider = GoogleFontsProvider, weight = FontWeight.Normal),
    Font(googleFont = GeistName, fontProvider = GoogleFontsProvider, weight = FontWeight.Medium),
    Font(googleFont = GeistName, fontProvider = GoogleFontsProvider, weight = FontWeight.SemiBold),
    Font(googleFont = GeistName, fontProvider = GoogleFontsProvider, weight = FontWeight.Bold),
    Font(googleFont = GeistName, fontProvider = GoogleFontsProvider, weight = FontWeight.Black),
)

private val BaseTypography = Typography()

private val GeistTypography = Typography(
    displayLarge = BaseTypography.displayLarge.copy(fontFamily = GeistFamily),
    displayMedium = BaseTypography.displayMedium.copy(fontFamily = GeistFamily),
    displaySmall = BaseTypography.displaySmall.copy(fontFamily = GeistFamily),
    headlineLarge = BaseTypography.headlineLarge.copy(fontFamily = GeistFamily),
    headlineMedium = BaseTypography.headlineMedium.copy(fontFamily = GeistFamily),
    headlineSmall = BaseTypography.headlineSmall.copy(fontFamily = GeistFamily),
    titleLarge = BaseTypography.titleLarge.copy(fontFamily = GeistFamily),
    titleMedium = BaseTypography.titleMedium.copy(fontFamily = GeistFamily),
    titleSmall = BaseTypography.titleSmall.copy(fontFamily = GeistFamily),
    bodyLarge = BaseTypography.bodyLarge.copy(fontFamily = GeistFamily),
    bodyMedium = BaseTypography.bodyMedium.copy(fontFamily = GeistFamily),
    bodySmall = BaseTypography.bodySmall.copy(fontFamily = GeistFamily),
    labelLarge = BaseTypography.labelLarge.copy(fontFamily = GeistFamily),
    labelMedium = BaseTypography.labelMedium.copy(fontFamily = GeistFamily),
    labelSmall = BaseTypography.labelSmall.copy(fontFamily = GeistFamily),
)

@Composable
internal fun GlassTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = GlassScheme,
        typography = GeistTypography,
        content = content,
    )
}
