package app.spy0dte.mobile

import android.content.Intent
import android.net.Uri
import android.util.Xml
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.xmlpull.v1.XmlPullParser
import java.net.URI
import java.util.concurrent.TimeUnit

internal data class NewsSource(val name:String,val page:String,val feed:String?)
internal data class NewsHeadline(val source:String,val title:String,val url:String,val published:String)

internal val MARKET_NEWS_SOURCES=listOf(
  NewsSource("Reuters Markets","https://www.reuters.com/markets/us/",null),
  NewsSource("CNBC Markets","https://www.cnbc.com/markets/","https://www.cnbc.com/id/100003114/device/rss/rss.html"),
  NewsSource("MarketWatch","https://www.marketwatch.com/","https://feeds.marketwatch.com/marketwatch/topstories/"),
  NewsSource("Federal Reserve","https://www.federalreserve.gov/newsevents/pressreleases.htm",
    "https://www.federalreserve.gov/feeds/press_all.xml"),
  NewsSource("AP Business","https://apnews.com/hub/business",null)
)

private val newsClient=OkHttpClient.Builder().callTimeout(8,TimeUnit.SECONDS).build()

internal fun trustedNewsUrl(url:String):Boolean=try {
  val parsed=URI(url.trim())
  parsed.scheme.equals("https",true) && !parsed.host.isNullOrBlank()
} catch (_:Exception) { false }

internal fun parseMarketRss(xml:String,source:String):List<NewsHeadline>{
  val parser=Xml.newPullParser()
  parser.setInput(xml.reader())
  val stories=mutableListOf<NewsHeadline>()
  var inside=false
  var field=""
  var title=""
  var link=""
  var published=""
  var event=parser.eventType
  while(event!=XmlPullParser.END_DOCUMENT && stories.size<12){
    when(event){
      XmlPullParser.START_TAG->{
        val tag=parser.name.lowercase()
        if(tag=="item"||tag=="entry"){
          inside=true;field="";title="";link="";published=""
        }else if(inside){
          field=tag
          if(tag=="link"){
            parser.getAttributeValue(null,"href")?.let{link=it}
          }
        }
      }
      XmlPullParser.TEXT->if(inside){
        val value=parser.text.trim()
        when(field){
          "title"->title+=value
          "link"->if(link.isBlank())link=value
          "pubdate","published","updated"->published+=value
        }
      }
      XmlPullParser.END_TAG->{
        val tag=parser.name.lowercase()
        if(inside&&(tag=="item"||tag=="entry")){
          if(title.isNotBlank()&&trustedNewsUrl(link)){
            stories+=NewsHeadline(source,title.take(240),link,published.take(70))
          }
          inside=false
        }
        field=""
      }
    }
    event=parser.next()
  }
  return stories
}

internal suspend fun loadMarketNews():List<NewsHeadline>=withContext(Dispatchers.IO){
  MARKET_NEWS_SOURCES.filter{it.feed!=null}.flatMap{ source->
    try{
      val req=Request.Builder().url(requireNotNull(source.feed))
        .header("User-Agent","SPY-0DTE/0.4")
        .header("Accept","application/rss+xml,application/atom+xml,text/xml,application/xml")
        .build()
      newsClient.newCall(req).execute().use{ resp->
        if(!resp.isSuccessful)emptyList()
        else{
          val xml=resp.body?.string().orEmpty().take(512_000)
          if(!xml.trimStart().startsWith("<"))emptyList()
          else parseMarketRss(xml,source.name)
        }
      }
    }catch(_:Exception){emptyList()}
  }.distinctBy{it.url}.take(15)
}

@Composable
internal fun MarketNewsPanel(){
  val context=LocalContext.current
  val scope=rememberCoroutineScope()
  var articles by remember{mutableStateOf<List<NewsHeadline>>(emptyList())}
  var loading by remember{mutableStateOf(true)}
  var checked by remember{mutableStateOf(false)}
  fun openPage(link:String){
    if(trustedNewsUrl(link)){
      runCatching{context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(link)))}
    }
  }
  suspend fun refresh(){
    loading=true
    articles=loadMarketNews()
    checked=true
    loading=false
  }
  LaunchedEffect(Unit){refresh()}
  Column(modifier=Modifier.testTag("news_panel"),verticalArrangement=Arrangement.spacedBy(10.dp)){
    Card(colors=CardDefaults.cardColors(containerColor=GlassColors.White),
      border=BorderStroke(1.dp,GlassColors.Border),shape=RoundedCornerShape(22.dp)){
      Column(Modifier.fillMaxWidth().padding(16.dp),verticalArrangement=Arrangement.spacedBy(10.dp)){
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
          Text("TRUSTED MARKET NEWS",fontWeight=FontWeight.Bold,fontSize=12.sp,color=GlassColors.Text)
          OutlinedButton(onClick={scope.launch{refresh()}},enabled=!loading,
            modifier=Modifier.testTag("refresh_news")){
            Text(if(loading)"Loading" else "Refresh",fontSize=10.sp)
          }
        }
        Text("Headlines and primary-source releases. News does not feed the V4 algorithm.",
          color=GlassColors.TextMuted,fontSize=11.sp)
        MARKET_NEWS_SOURCES.forEach{source->
          OutlinedButton(onClick={openPage(source.page)},modifier=Modifier.fillMaxWidth()
            .testTag("news_source_"+source.name.lowercase().replace(" ","_"))){
            Text("Open "+source.name+"  ↗",fontSize=12.sp)
          }
        }
        if(checked&&articles.isEmpty()){
          Text("Headlines temporarily unavailable. Use the sources above for current coverage.",
            color=GlassColors.TextMuted,fontSize=11.sp)
        }
      }
    }
    if(articles.isNotEmpty()){
      Card(colors=CardDefaults.cardColors(containerColor=GlassColors.White),
        border=BorderStroke(1.dp,GlassColors.Border),shape=RoundedCornerShape(22.dp)){
        Column(Modifier.fillMaxWidth().padding(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
          Text("LATEST HEADLINES",color=GlassColors.Text,fontWeight=FontWeight.Bold,fontSize=12.sp)
          articles.forEach{article->
            Column(Modifier.fillMaxWidth().clickable{openPage(article.url)}.padding(vertical=5.dp)){
              Text(article.source,color=GlassColors.GreenDark,fontSize=10.sp)
              Spacer(Modifier.height(3.dp))
              Text(article.title,color=GlassColors.Text,fontSize=12.sp,fontWeight=FontWeight.Medium)
              if(article.published.isNotBlank())
                Text(article.published,color=GlassColors.TextMuted,fontSize=10.sp)
            }
          }
        }
      }
    }
  }
}
