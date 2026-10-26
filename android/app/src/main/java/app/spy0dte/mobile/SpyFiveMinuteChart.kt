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
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val CHART_GREEN=GlassColors.Green
private val CHART_RED=GlassColors.Red
private val CHART_GRID=GlassColors.Border
private val candleLabel=DateTimeFormatter.ofPattern("h:mm a").withZone(ZoneId.of("America/New_York"))

private fun asEt(iso:String)=runCatching{
    candleLabel.format(OffsetDateTime.parse(iso).toInstant())
}.getOrDefault("—")

/** Up to thirty authenticated 1m bars; last can be a visibly flagged forming bar. */
@Composable
internal fun SpyFiveMinuteChart(status:ScreenStatus, compact:Boolean=false){
    val bars=status.spyChart.candles
    val state=status.spyChart.state
    Column(modifier=Modifier.fillMaxWidth().testTag(if(compact) "overview_spy_chart" else "spy_5min_chart"),
        verticalArrangement=Arrangement.spacedBy(8.dp)){
        if(!compact){
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
            Text("30 MIN WINDOW · 1 MIN CANDLES",fontSize=10.sp,
                color=GlassColors.TextMuted,fontWeight=FontWeight.SemiBold)
            Text(if(state=="LIVE_FORMING")"● LIVE · FORMING"
                else if(state=="WAITING_FOR_NEXT_BAR")"● LAST CLOSED"
                else if(state=="STALE")"● STALE" else "NO MARKET DATA",
                fontSize=9.sp,color=if(state=="LIVE_FORMING") CHART_GREEN else GlassColors.Amber)
        }
        }
        if(bars.isNotEmpty()){
            ChartCanvas(bars, compact)
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
                Text(asEt(bars.first().time)+" ET",color=GlassColors.TextMuted,fontSize=9.sp)
                Text("${bars.size} × 1m",color=GlassColors.TextMuted,fontSize=9.sp)
                Text(asEt(bars.last().time)+" ET",color=GlassColors.TextMuted,fontSize=9.sp)
            }
            if(!compact){
            val last=bars.last()
            Text((if(last.forming)"LIVE FORMING: O " else "Last completed: O ")+ "%.2f".format(last.open)+
                 " · H "+"%.2f".format(last.high)+
                 " · L "+"%.2f".format(last.low)+
                 " · C "+"%.2f".format(last.close)+
                 " · Vol "+last.volume,
                 color=GlassColors.TextMuted,fontSize=10.sp)
            if(state!="LIVE_FORMING")Text(status.spyChart.reason
                    ?: "The source has not supplied an in-progress SPY minute. The quant uses only completed candles.",
                color=GlassColors.Amber,fontSize=10.sp)
            }
        }else{
            Box(Modifier.fillMaxWidth().height(if(compact) 120.dp else 165.dp)
                .background(GlassColors.BackgroundBlue,RoundedCornerShape(12.dp))
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
private fun ChartCanvas(bars:List<SpyCandle>, compact:Boolean){
    val highest=bars.maxOf{it.high}
    val lowest=bars.minOf{it.low}
    val spread=(highest-lowest).coerceAtLeast(0.01)
    Canvas(modifier=Modifier.fillMaxWidth().height(if(compact) 165.dp else 220.dp)
        .background(GlassColors.BackgroundBlue,RoundedCornerShape(12.dp))
        .testTag(if(compact) "overview_chart_candles" else "spy_chart_candles")){
        val volumeHeight=22.dp.toPx()
        val availableHeight=size.height-38.dp.toPx()-volumeHeight
        val topPadding=15.dp.toPx()
        val paddedMin=lowest-spread*0.09
        val paddedRange=spread*1.18
        fun chartY(value:Double):Float=topPadding+
            ((paddedMin+paddedRange-value)/paddedRange).toFloat()*availableHeight
        val labelWidth=42.dp.toPx()
        val chartWidth=(size.width-labelWidth).coerceAtLeast(1f)
        val labelPaint=android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply{
            color=GlassColors.TextFaint.toArgb()
            textSize=9.sp.toPx()
        }
        for(index in 0..3){
            val y=topPadding+index*availableHeight/3f
            drawLine(CHART_GRID.copy(alpha=.65f),Offset(0f,y),Offset(chartWidth,y),.5.dp.toPx())
            val value=paddedMin+paddedRange-index*paddedRange/3
            drawIntoCanvas { canvas ->
                canvas.nativeCanvas.drawText("%.2f".format(value),chartWidth+6.dp.toPx(),y+3.dp.toPx(),labelPaint)
            }
        }
        val width=chartWidth/bars.size.toFloat()
        val maxVolume=bars.maxOf{it.volume}.coerceAtLeast(1L).toFloat()
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
            val volume=(bar.volume.toFloat()/maxVolume)*volumeHeight
            drawRect(color.copy(alpha=.20f),
                topLeft=Offset(center-width*.23f,size.height-volume-5.dp.toPx()),
                size=Size(width*.46f,volume))
        }
    }
}
