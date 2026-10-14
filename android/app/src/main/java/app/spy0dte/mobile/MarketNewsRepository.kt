package app.spy0dte.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import okhttp3.OkHttpClient
import okhttp3.Request
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.StringReader
import java.net.URI
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

/** Real, linked publisher RSS headlines. Informational ONLY; never fed to trading rules. */
internal data class MarketHeadline(
    val source: String,
    val title: String,
    val url: String,
    val published: String,
    val publishedAt: Long,
)

internal data class NewsFeedResult(
    val stories: List<MarketHeadline>,
    val failures: List<String>,
    val checkedAtMillis: Long,
)

internal data class PublisherFeed(val name: String, val url: String, val website: String)

internal object MarketNewsRepository {
    val publishers = listOf(
        PublisherFeed(
            "CNBC Markets",
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "https://www.cnbc.com/markets/",
        ),
        PublisherFeed(
            "NPR Business",
            "https://feeds.npr.org/1006/rss.xml",
            "https://www.npr.org/sections/business/",
        ),
        PublisherFeed(
            "Federal Reserve",
            "https://www.federalreserve.gov/feeds/press_monetary.xml",
            "https://www.federalreserve.gov/newsevents/pressreleases.htm",
        ),
    )
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(7, TimeUnit.SECONDS)
        .followRedirects(true).build()
    private const val CACHE_MILLIS = 3 * 60 * 1000L
    @Volatile private var cache: NewsFeedResult? = null

    /** A genuine HTTPS publisher URI only, never an arbitrary RSS redirect. */
    fun safeUrl(url: String): Boolean = runCatching {
        val uri = URI(url.trim())
        if (uri.scheme != "https" || uri.userInfo != null || uri.port !in listOf(-1, 443)) false
        else {
            val host = uri.host?.lowercase().orEmpty()
            listOf("cnbc.com", "npr.org", "federalreserve.gov").any {
                host == it || host.endsWith(".$it")
            }
        }
    }.getOrDefault(false)

    /** Pure parser supports deterministic offline tests without networking. */
    fun parseRss(xml: String, publisher: PublisherFeed): List<MarketHeadline> {
        val parser = XmlPullParserFactory.newInstance().newPullParser()
        parser.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES, false)
        parser.setInput(StringReader(xml))
        val result = mutableListOf<MarketHeadline>()
        var inside = false
        var title = ""
        var link = ""
        var date = ""
        var step = parser.eventType
        while (step != XmlPullParser.END_DOCUMENT && result.size < 10) {
            when (step) {
                XmlPullParser.START_TAG -> when (parser.name.lowercase()) {
                    "item" -> { inside = true; title = ""; link = ""; date = "" }
                    "title" -> if (inside) title = parser.nextText().trim()
                    "link" -> if (inside) link = parser.nextText().trim()
                    "pubdate" -> if (inside) date = parser.nextText().trim()
                }
                XmlPullParser.END_TAG -> if (parser.name.equals("item", true)) {
                    if (inside && title.isNotBlank() && safeUrl(link)) {
                        val timestamp = runCatching {
                            ZonedDateTime.parse(date, DateTimeFormatter.RFC_1123_DATE_TIME)
                                .toInstant().toEpochMilli()
                        }.getOrDefault(0L)
                        result += MarketHeadline(
                            source = publisher.name,
                            title = title.take(230),
                            url = link,
                            published = date.take(100),
                            publishedAt = timestamp,
                        )
                    }
                    inside = false
                }
            }
            step = parser.next()
        }
        return result
    }

    private fun fetchPublisher(publisher: PublisherFeed): List<MarketHeadline> {
        val request = Request.Builder().url(publisher.url)
            .header("User-Agent", "SPY-0DTE-News/1.0 (RSS Reader; Android)")
            .header("Accept", "application/rss+xml, application/xml, text/xml")
            .get().build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error(publisher.name + ": HTTP " + response.code)
            val body = response.body?.string().orEmpty()
            if (body.isBlank() || body.length > 1_000_000) error(publisher.name + ": invalid feed")
            return parseRss(body, publisher)
        }
    }

    suspend fun load(force: Boolean = false): NewsFeedResult {
        val now = System.currentTimeMillis()
        val existing = cache
        if (!force && existing != null && now - existing.checkedAtMillis < CACHE_MILLIS) {
            return existing
        }
        val fetched = coroutineScope {
            publishers.map { source ->
                async(Dispatchers.IO) { source to runCatching { fetchPublisher(source) } }
            }.awaitAll()
        }
        val stories = fetched.flatMap { it.second.getOrDefault(emptyList()) }
            .distinctBy { it.url }
            .sortedWith(compareByDescending<MarketHeadline> { it.publishedAt }.thenBy { it.source })
            .take(15)
        val failures = fetched.filter { it.second.isFailure }.map { it.first.name }
        val result = NewsFeedResult(stories, failures, now)
        cache = result
        return result
    }
}
