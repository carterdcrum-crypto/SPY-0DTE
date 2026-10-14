package app.spy0dte.mobile

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val chartUp=Color(0xFF13805A)
private val chartDown=Color(0xFFD35651)
private val chartGrid=Color(0xFFDDE5E0)
private val priceLabelFormat=DateTimeFormatter.ofPattern("h:mm a").withZone(ZoneId.of("America/New_York"))

private fun candleEtTime(raw:String):String = runCatching{
    priceLabelFormat.format(OffsetDateTime.parse(raw).toInstant())
}.getOrElse { "—" }

/** Five *completed* real SPY one-minute candles; no seeded demo/mock prices. */
@Composable
internal fun SpyFiveMinuteChart(status:ScreenStatus){
    val bars=status.spyChart.candles
    Column(Modifier.fillMaxWidth().testTag("spy_5min_chart"),
        verticalArrangement=Arrangement.spacedBy(7.dp)){
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
            Text("5 MIN WINDOW · 1 MIN CANDLES",color=GlassColors.TextMuted,
                fontSize=10.sp,fontWeight=FontWeight.SemiBold)
            Text(when(status.spyChart.state){
                "CURRENT"->"●  LIVE DATA"
                "STALE"->"●  STALE"
                else->"NO MARKET DATA"
            },color=if(status.spyChartState=="CURRENT")chartUp else GlassColors.Amber,fontSize=9.sp)
        }
        if(bars.size!=5){
            Box(Modifier.fillMaxWidth().height(190.dp).background(Color.White.copy(alpha=.58f),
                RoundedCornerShape(17.dp)).padding(16.dp)){
                Column(verticalArrangement=Arrangement.spacedBy(8.dp)){
                    Text("Waiting for 5 real completed SPY candles",
                        color=GlassColors.Text,fontSize=12.sp,fontWeight=FontWeight.SemiBold)
                    Text(status.spyChart.reason?.take(140)
                        ?: "The broker must provide current SPY 1-minute OHLCV bars. No prices are simulated.",
                        color=GlassColors.TextMuted,fontSize=10.sp)
                }
            }
            return
        }
        val top=bars.maxOf{it.high}
        val bottom=bars.minOf{it.low}
        val span=(top-bottom).coerceAtLeast(.01)
        val padded=span*1.16
        val floor=bottom-(padded-span)/2.
        Canvas(Modifier.fillMaxWidth().height(210.dp)
            .background(Color.White.copy(alpha=.58f),RoundedCornerShape(17.dp))
            .testTag("spy_chart_candles")){
            val w=size.width
            val h=size.height
            val xStep=w/bars.size
            val padY=19.dp.toPx()
            val usableH=h-2*padY
            fun y(price:Double):Float=padY +
                (1.0-((price-floor)/padded).coerceIn(0.0,1.0)).toFloat()*usableH
            repeat(4){index->
                val gridY=padY+usableH*index/3f
                drawLine(chartGrid,Offset(0f,gridY),Offset(w,gridY),strokeWidth=1.dp.toPx())
            }
            bars.forEachIndexed{index,candle->
                val center=xStep*(index+.5f)
                val ink=if(candle.close>=candle.open)chartUp else chartDown
                drawLine(ink,Offset(center,y(candle.high)),Offset(center,y(candle.low)),
                    strokeWidth=2.dp.toPx())
                val topBody=minOf(y(candle.open),y(candle.close))
                val bodyHeight= kotlin.math.abs(y(candle.close)-y(candle.open))
                    .coerceAtLeast(2.dp.toPx())
                drawRect(ink,topLeft=Offset(center-xStep*.22f,topBody),
                    size=Size(xStep*.44f,bodyHeight))
            }
        }
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
            Text(candleEtTime(bars.first().time)+" ET",color=GlassColors.TextMuted,fontSize=10.sp)
            Text("Low "+ "%.2f".format(bottom)+"  ·  High "+"%.2f".format(top),
                color=GlassColors.TextMuted,fontSize=10.sp)
            Text(candleEtTime(bars.last().time)+" ET",color=GlassColors.TextMuted,fontSize=10.sp)
        }
        val end=bars.last()
        Text("Last completed candle: O "+ "%.2f".format(end.open)+
            "  H "+"%.2f".format(end.high)+
            "  L "+"%.2f".format(end.low)+
            "  C "+"%.2f".format(end.close)+
            "  Vol "+end.volume,
            color=GlassColors.TextMuted,fontSize=10.sp)
        if(status.spyChartState!="CURRENT"){
            Text("Market data is stale. Chart is informational only; no false live price is shown.",
                color=GlassColors.Amber,fontSize=10.sp)
        }
    }
}
