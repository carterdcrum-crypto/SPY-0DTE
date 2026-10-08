package app.spy0dte.mobile

import android.graphics.Bitmap
import android.content.ContentValues
import android.os.Environment
import android.provider.MediaStore
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.Density
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** Render the production composables with isolated fixtures; never contact a broker. */
class GlassDashboardTest {
    @get:Rule val compose = createComposeRule()

    private val fixture = ScreenStatus(
        connected = true, mode = "PAPER", spot = 573.42, dataAge = 901.0, feedDelay = 900.0,
        paperStartingCash = 1000.0, paperCash = 986.25, paperPnl = -13.75,
        paperTrades = 18, paperBuys = 9, paperSells = 9, paperUnrealizedPnl = 0.0,
        decision = "WAITING", reason = "Watching for a qualified entry.",
        strategy = "Adaptive SPY", riskProfile = "Dynamic", exitProfile = "Adaptive",
        workerState = "RUNNING", liveState = "LIVE_LOCKED", liveReason = "Owner setup is required.",
        ai = AiDecisionState(active = true, quantProbabilityUp = .56, aiProbabilityUp = .54,
            hybridProbabilityUp = .55, providers = listOf(AiProviderState("openai", .54), AiProviderState("anthropic", .57))),
    )

    private fun actions(onToggle: suspend (Boolean) -> Result<String> = { error("Unexpected broker action") }) = GlassActions(
        saveCredentials = { _, _ -> error("Unexpected credentials action") },
        setLiveAutonomy = onToggle,
        setMode = { Result.success(it) },
        setPaperAutonomy = { Result.success("Saved") },
        resetPaperAccount = { error("Unexpected ledger reset") },
        armRisk = { _, _, _, _ -> error("Unexpected limit change") },
        disarmRisk = { error("Unexpected disarm") },
    )

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        // Gradle uninstalls the test app after the run. Save owned screenshots
        // through MediaStore so the CI runner can retrieve them afterwards.
        val resolver = instrumentation.targetContext.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, "$name.png")
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/spy0dte-ui")
        }
        val uri = checkNotNull(resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values))
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        checkNotNull(resolver.openOutputStream(uri)).use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }

    @Test fun navigationAndPreviewControlsRemainFunctional() {
        compose.setContent { GlassTheme { GlassDashboard(fixture, true, null, actions(), null) } }
        compose.onNodeWithTag("auto_trade_switch").assertIsNotEnabled().assertIsOff()
        screenshot("01-overview")
        compose.onNodeWithTag("nav_POSITIONS").performClick()
        compose.onNodeWithText("Your exposure, at a glance.").assertIsDisplayed()
        screenshot("02-positions")
        compose.onNodeWithTag("nav_ANALYTICS").performClick()
        compose.onNodeWithText("A clearer view of every decision.").assertIsDisplayed()
        screenshot("03-analytics")
        compose.onNodeWithTag("nav_SETTINGS").performClick()
        compose.onNodeWithText("Your account. Your controls.").assertIsDisplayed()
        screenshot("04-settings")
        compose.onNodeWithTag("nav_LIVE").performClick()
        compose.onNodeWithTag("autotrade_settings").performScrollTo().performClick()
        compose.onNodeWithText("Your account. Your controls.").assertIsDisplayed()
    }

    @Test fun ownerToggleUsesConfirmedStateAndSupportsOff() {
        val status = mutableStateOf(fixture.copy(liveState = "IDLE", liveReason = "Ready"))
        val calls = mutableListOf<Boolean>()
        compose.setContent { GlassTheme { GlassDashboard(status.value, false, null, actions { enabled ->
            calls.add(enabled)
            status.value = status.value.copy(liveEnabled = enabled)
            Result.success("Saved")
        }, null) } }
        compose.onNodeWithTag("auto_trade_switch").performScrollTo().performClick()
        compose.onNodeWithTag("auto_trade_switch").assertIsOn().performClick()
        compose.onNodeWithTag("auto_trade_switch").assertIsOff()
        assertEquals(listOf(true, false), calls)
    }

    @Test fun largeTextAndOfflineStateStayNavigable() {
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                GlassTheme { GlassDashboard(fixture.copy(connected = false), false,
                    "Connection interrupted. Reconnecting…", actions(), null) }
            }
        }
        compose.onNodeWithTag("auto_trade_switch").performScrollTo().assertIsNotEnabled()
        screenshot("05-large-text-offline")
        compose.onNodeWithTag("nav_SETTINGS").performClick()
        compose.onNodeWithText("Your account. Your controls.").assertIsDisplayed()
        screenshot("06-large-text-settings")
    }
}
