package app.spy0dte.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import android.app.Activity
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.googlefonts.Font
import androidx.compose.ui.text.googlefonts.GoogleFont

internal object GlassColors {
    val Ink = Color(0xFF10231D)
    val Lime = Color(0xFF71E6B8)
    val InkMuted = Color(0xFF9EB3AB)
    val Background = Color(0xFF090D0C)
    val BackgroundBlue = Color(0xFF0B1210)
    val Panel = Color(0xFF111916)
    val PanelStrong = Color(0xFF15211C)
    val PanelSoft = Color(0xFF1A2621)
    val Border = Color(0xFF283A32)
    val BorderBright = Color(0xFF3F6855)
    val Green = Color(0xFF3EDFA7)
    val GreenDark = Color(0xFF71E6B8)
    val Mint = Color(0xFF173A2D)
    val Blue = Color(0xFF91B4C8)
    val Cyan = Blue
    val SoftBlue = Color(0xFF152630)
    val Red = Color(0xFFFF8C95)
    val SoftRed = Color(0xFF321C23)
    val Amber = Color(0xFFEBC47A)
    val Text = Color(0xFFF1F6F3)
    val TextMuted = Color(0xFFA6B7AE)
    val TextFaint = Color(0xFF7E9488)
    val White = Color(0xFFF7FBF8)
    val Black = Color(0xFF08140E)
}

private val GlassScheme = darkColorScheme(
    primary = GlassColors.Green,
    onPrimary = GlassColors.Black,
    primaryContainer = GlassColors.Mint,
    onPrimaryContainer = GlassColors.Text,
    secondary = GlassColors.Blue,
    onSecondary = GlassColors.Black,
    secondaryContainer = GlassColors.SoftBlue,
    onSecondaryContainer = GlassColors.Text,
    background = GlassColors.Background,
    onBackground = GlassColors.Text,
    surface = GlassColors.PanelStrong,
    onSurface = GlassColors.Text,
    surfaceVariant = GlassColors.PanelSoft,
    surfaceDim = GlassColors.Background,
    surfaceBright = GlassColors.PanelSoft,
    surfaceContainerLowest = GlassColors.Background,
    surfaceContainerLow = GlassColors.Panel,
    surfaceContainer = GlassColors.PanelStrong,
    surfaceContainerHigh = GlassColors.PanelSoft,
    surfaceContainerHighest = GlassColors.Border,
    onSurfaceVariant = GlassColors.TextMuted,
    outline = GlassColors.Border,
    error = GlassColors.Red,
    errorContainer = GlassColors.SoftRed,
    onErrorContainer = GlassColors.Red,
    surfaceTint = Color.Transparent,
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
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            (view.context as? Activity)?.window?.let { window ->
                WindowCompat.getInsetsController(window, view).apply {
                    isAppearanceLightStatusBars = false
                    isAppearanceLightNavigationBars = false
                }
            }
        }
    }
    MaterialTheme(
        colorScheme = GlassScheme,
        typography = GeistTypography,
        content = content,
    )
}
