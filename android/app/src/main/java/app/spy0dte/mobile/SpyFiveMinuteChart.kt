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

private val CHART_GREEN=Color(0xFF13805A)
private val CHART_RED=Color(0xFFD35651)
private val CHART_GRID=Color(0xFFDDE5E0)
private val candleLabel=DateTimeFormatter.ofPattern("h:mm a").withZone(ZoneId.of("America/New_York"))

private fun asEt(iso:String)=runCatching{
    candleLabel.format(OffsetDateTime.parse(iso).toInstant())
}.getOrDefault("—")

/** Up to thirty authenticated 1m bars; last can be a visibly flagged forming bar. */
@Composable
internal fun SpyFiveMinuteChart(status:ScreenStatus){
    val bars=status.spyChart.candles
    val state=status.spyChart.state
    Column(modifier=Modifier.fillMaxWidth().testTag("spy_5min_chart"),
        verticalArrangement=Arrangement.spacedBy(8.dp)){
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
            Text("30 MIN WINDOW · 1 MIN CANDLES",fontSize=10.sp,
                color=GlassColors.TextMuted,fontWeight=FontWeight.SemiBold)
            Text(if(state=="LIVE_FORMING")"● LIVE · FORMING"
                else if(state=="WAITING_FOR_NEXT_BAR")"● LAST CLOSED"
                else if(state=="STALE")"● STALE" else "NO MARKET DATA",
                fontSize=9.sp,color=if(state=="LIVE_FORMING") CHART_GREEN else GlassColors.Amber)
        }
        if(bars.isNotEmpty()){
            ChartCanvas(bars)
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
                Text(asEt(bars.first().time)+" ET",color=GlassColors.TextMuted,fontSize=9.sp)
                Text("${bars.size} × 1m",color=GlassColors.TextMuted,fontSize=9.sp)
                Text(asEt(bars.last().time)+" ET",color=GlassColors.TextMuted,fontSize=9.sp)
            }
            val last=bars.last()
            Text((if(last.forming)"LIVE FORMING: O " else "Last completed: O ")+ "%.2f".format(last.open)+
                 " · H "+"%.2f".format(last.high)+
                 " · L "+"%.2f".format(last.low)+
                 " · C "+"%.2f".format(last.close)+
                 " · Vol "+last.volume,
                 color=GlassColors.TextMuted,fontSize=10.sp)
            if(state!="LIVE_FORMING")Text("Live minute unavailable. Showing verified bars; the quant uses only closed candles.",
                color=GlassColors.Amber,fontSize=10.sp)
        }else{
            Box(Modifier.fillMaxWidth().height(165.dp)
                .background(Color.White.copy(alpha=.55f),RoundedCornerShape(17.dp))
                .padding(15.dp)){
                Column(verticalArrangement=Arrangement.spacedBy(8.dp)){
                    Text("Waiting for authentic SPY minute candles",color=GlassColors.Text,
                        fontWeight=FontWeight.SemiBold,fontSize=12.sp)
                    Text(status.spyChart.reason?.take(160)
                        ?: "Authentic Webull 1-minute stock OHLCV required. No demo prices.",
                        color=GlassColors.TextMuted,fontSize=10.sp)
                }
            }
        }
    }
}

@Composable
private fun ChartCanvas(bars:List<SpyCandle>){
    val highest=bars.maxOf{it.high}
    val lowest=bars.minOf{it.low}
    val spread=(highest-lowest).coerceAtLeast(0.01)
    Canvas(modifier=Modifier.fillMaxWidth().height(200.dp)
        .background(Color.White.copy(alpha=.55f),RoundedCornerShape(17.dp))
        .testTag("spy_chart_candles")){
        val availableHeight=size.height-30.dp.toPx()
        val topPadding=15.dp.toPx()
        val paddedMin=lowest-spread*0.09
        val paddedRange=spread*1.18
        fun chartY(value:Double):Float=topPadding+
            ((paddedMin+paddedRange-value)/paddedRange).toFloat()*availableHeight
        for(index in 0..3){
            val y=topPadding+index*availableHeight/3f
            drawLine(CHART_GRID,Offset(0f,y),Offset(size.width,y),1.dp.toPx())
        }
        val width=size.width/bars.size.toFloat()
        bars.forEachIndexed{index,bar->
            val center=(index+0.5f)*width
            val color=if(bar.close>=bar.open)CHART_GREEN else CHART_RED
            val upper=chartY(maxOf(bar.open,bar.close))
            val lower=chartY(minOf(bar.open,bar.close))
            drawLine(color,Offset(center,chartY(bar.high)),Offset(center,chartY(bar.low)),
                strokeWidth=2.dp.toPx())
            drawRect(color,
                topLeft=Offset(center-width*0.23f,upper),
                size=Size(width*0.46f,(lower-upper).coerceAtLeast(2.dp.toPx())))
        }
    }
}
