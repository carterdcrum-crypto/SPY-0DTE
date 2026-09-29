package app.spy0dte.mobile

internal data class RiskState(
    val armed: Boolean = false,
    val loss: Double? = null,
    val gain: Double? = null,
    val exposure: Double? = null,
    val contracts: Int? = null,
)

internal data class AiProviderState(
    val provider: String,
    val probabilityUp: Double? = null,
    val confidence: Double? = null,
)

internal data class AiDecisionState(
    val active: Boolean = false,
    val quantProbabilityUp: Double? = null,
    val aiProbabilityUp: Double? = null,
    val hybridProbabilityUp: Double? = null,
    val effectiveWeight: Double? = null,
    val consensusConfidence: Double? = null,
    val disagreement: Double? = null,
    val providers: List<AiProviderState> = emptyList(),
)

internal data class BrokerState(
    val connected: Boolean = false,
    val entryAllowed: Boolean = false,
    val cashAvailable: Double? = null,
    val totalEquity: Double? = null,
    val dailyPnl: Double? = null,
    val openPnl: Double? = null,
    val openPositions: Int = 0,
    val pendingOrders: Int = 0,
    val maxEntryDebit: Double? = null,
)

internal data class LiveAlert(
    val symbol: String,
    val right: String,
    val bid: Double?,
    val ask: Double?,
    val contracts: Int,
    val maxDebit: Double?,
)

internal data class PaperPositionState(
    val symbol: String,
    val quantity: Int,
    val averageCost: Double,
    val markValue: Double? = null,
    val unrealizedPnl: Double? = null,
    val openedAt: String? = null,
    val entrySpot: Double? = null,
    val strategy: String? = null,
)

internal data class PaperTradeState(
    val timestamp: String,
    val symbol: String,
    val side: String,
    val quantity: Int,
    val fillPrice: Double,
    val realizedPnl: Double,
    val strategy: String? = null,
    val reason: String? = null,
)

internal data class ScreenStatus(
    val mode: String = "PAPER",
    val connected: Boolean = false,
    val decision: String = "CONNECTING",
    val reason: String = "Waiting for Railway",
    val strategy: String = "—",
    val riskProfile: String = "—",
    val exitProfile: String = "—",
    val spot: Double? = null,
    val dataAge: Double? = null,
    val feedDelay: Double? = null,
    val liveReady: Boolean = false,
    val liveReasons: List<String> = emptyList(),
    val broker: String = "webull",
    val brokerConfigured: Boolean = false,
    val brokerConnected: Boolean = false,
    val risk: RiskState = RiskState(),
    val brokerState: BrokerState = BrokerState(),
    val ai: AiDecisionState = AiDecisionState(),
    val alert: LiveAlert? = null,
    val paperCash: Double? = null,
    val paperRealizedPnl: Double? = null,
    val paperUnrealizedPnl: Double? = null,
    val paperTotalPnl: Double? = null,
    val paperPositions: Int = 0,
    val paperTradeCount: Int = 0,
    val paperArmed: Boolean = false,
    val paperPositionDetails: List<PaperPositionState> = emptyList(),
    val recentPaperTrades: List<PaperTradeState> = emptyList(),
)

internal data class PreparedTrade(
    val ticket: String,
    val expiresSeconds: Int,
    val orderJson: String,
    val optionType: String,
    val strike: Double,
    val expiration: String,
    val quantity: Int,
    val limitPrice: Double,
    val maxDebit: Double,
)
