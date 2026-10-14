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
        ai = AiDecisionState(quantProbabilityUp = .56),
    )

    private fun actions(onToggle: suspend (Boolean) -> Result<String> = { error("Unexpected broker action") }) = GlassActions(
        listCashAccounts = { Result.success(emptyList()) },
        selectCashAccount = { error("Unexpected account selection") },
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

    @Test fun liveV5AnalyticsShowsSelectedEngineAndCorrectRiskNotLegacyV4() {
        val v5 = fixture.copy(
            mode = "LIVE",
            liveStrategyProfile = "v5_close_only_unrestricted_research",
            strategy = "v5_close_only_unrestricted_research",
            decision = "MARKET_CLOSED",
            riskProfile = "NO_OWNER_DAILY_GAIN_LOSS_CONTRACT_EXPOSURE_CAPS_cash_depth_only",
            exitProfile = "35pct_hard_25pct_trailing_close_reversal_18min",
        )
        assertEquals("V5 Close-Only · Unrestricted", engineDisplayName(v5))
        compose.setContent { GlassTheme { GlassDashboard(v5, false, null, actions(), null) } }
        compose.onNodeWithTag("nav_ANALYTICS").performClick()
        compose.onNodeWithText("QUANT ENGINE · V5 CLOSE-ONLY").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Dynamic · settled cash and displayed market depth").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("35% loss trigger / 25% trail / reversal / 18 min").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("V5 Close-Only · Unrestricted", substring = true).assertExists()
        compose.onNodeWithText("QUANT ENGINE · ORIGINAL V4 3X").assertDoesNotExist()
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

    @Test fun quantTabsNavigateWithoutAnyAiControl() {
        compose.setContent { GlassTheme { GlassDashboard(fixture, false, null, actions(), null) } }
        compose.onNodeWithText("QUANT ENGINE · V4 ADAPTIVE PAPER").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("insight_CHART").performScrollTo().performClick()
        compose.onNodeWithTag("insight_OPTIONS").performScrollTo().performClick()
        compose.onNodeWithTag("insight_QUANT").performScrollTo().performClick()
        compose.onNodeWithText("QUANT ENGINE · V4 ADAPTIVE PAPER").performScrollTo().assertIsDisplayed()
    }

    @Test fun settingsSaveAndPaperActivationCallTheirBackendActions() {
        var saved: List<Any>? = null
        var paperCalls = 0
        val actionSet = GlassActions(
            listCashAccounts = { Result.success(emptyList()) },
        selectCashAccount = { error("Unexpected account selection") },
        saveCredentials = { _, _ -> Result.success("Saved") },
            setLiveAutonomy = { Result.success("Saved") },
            setMode = { Result.success(it) },
            setPaperAutonomy = { paperCalls++; Result.success("Paper saved") },
            resetPaperAccount = { Result.success("Reset") },
            armRisk = { loss, gain, exposure, contracts ->
                saved = listOf(loss, gain, exposure, contracts)
                Result.success("Standing safety settings saved")
            },
            disarmRisk = { error("daily arming button must not exist") },
        )
        compose.setContent { GlassTheme { GlassDashboard(fixture, false, null, actionSet, null) } }
        compose.onNodeWithTag("nav_SETTINGS").performClick()
        compose.onNodeWithText("Save Safety Settings").performScrollTo().performClick()
        compose.waitForIdle()
        assertEquals(listOf(25.0, 40.0, .35, 3), saved)
        compose.onNodeWithText("Arm Autonomous Paper").performScrollTo().performClick()
        compose.waitForIdle()
        assertEquals(1, paperCalls)
    }


    @Test fun thirtyMinuteChartShowsVerifiedCandlesAndFormingCandle() {
        val start = "2026-10-14T14:00:00+00:00"
        val candles = (0 until 30).map { i ->
            SpyCandle(start, 600.0 + i, 601.0 + i,
                599.0 + i, 600.5 + i, 250L, forming = i == 29)
        }
        val chart = SpyChartState(state="LIVE_FORMING",asof="2026-10-14T14:05:00+00:00",
            ageSeconds=2.0,hasFormingCandle=true,candles=candles)
        compose.setContent { GlassTheme {
            GlassDashboard(fixture.copy(spyChart=chart), false, null, actions(), null)
        } }
        compose.onNodeWithTag("insight_CHART").performScrollTo().performClick()
        compose.onNodeWithTag("spy_5min_chart").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("SPY · LAST 30 MINUTES").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("30 MIN WINDOW · 1 MIN CANDLES").performScrollTo().assertIsDisplayed()
    }

    @Test fun newsTabDisplaysThreeRealPublishersAndRefresh() {
        compose.setContent { GlassTheme {
            GlassDashboard(fixture, false, null, actions(), null)
        } }
        compose.onNodeWithTag("insight_NEWS").performScrollTo().performClick()
        compose.onNodeWithText("LATEST HEADLINES").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("TRUSTED MARKET NEWS").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("news_source_cnbc_markets").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("news_source_reuters_markets").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("news_source_federal_reserve").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("refresh_news").assertExists()
    }

    @Test fun rssParserRejectsUntrustedUrlsAndDoesNotInventHeadlines() {
        val source = MarketNewsRepository.publishers[0]
        val fixtureRss = """
            <rss version="2.0"><channel>
              <item><title>SPY opens higher</title>
              <link>https://www.cnbc.com/markets/real-update.html</link>
              <pubDate>Wed, 14 Oct 2026 14:00:00 GMT</pubDate></item>
              <item><title>Forged link</title><link>https://malicious.example/article</link></item>
            </channel></rss>
        """.trimIndent()
        val rows = MarketNewsRepository.parseRss(fixtureRss, source)
        assertEquals(1, rows.size)
        assertEquals("SPY opens higher", rows.first().title)
        org.junit.Assert.assertFalse(MarketNewsRepository.safeUrl("javascript:alert(1)"))
        org.junit.Assert.assertFalse(MarketNewsRepository.safeUrl("https://cnbc.com.evil.example"))
    }

}
