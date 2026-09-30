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
    val paperStartingCash: Double? = null,
    val paperCash: Double? = null,
    val paperPnl: Double? = null,
    val paperPositions: Int = 0,
    val paperTrades: Int = 0,
    val paperArmed: Boolean = false,
    val paperBuys: Int = 0,
    val paperSells: Int = 0,
    val paperUnrealizedPnl: Double? = null,
    val liveEnabled: Boolean = false,
    val liveState: String = "STARTING",
    val liveReason: String = "Waiting for live worker",
    val livePositions: List<LivePosition> = emptyList(),
    val workerState: String = "STARTING",
)

internal data class LivePosition(val symbol: String, val quantity: Int, val averagePrice: Double?)
