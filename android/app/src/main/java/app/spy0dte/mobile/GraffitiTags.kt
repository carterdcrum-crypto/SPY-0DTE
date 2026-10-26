package app.spy0dte.mobile

import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.nativeCanvas
import kotlin.math.max
import kotlin.random.Random

/**
 * Non-interactive spray-paint lettering. This art never intercepts Auto Trade
 * switch clicks or modifies any trading/sizing/execution settings.
 *
 * The lettering/placement derives from the user-approved graffiti mockup;
 * splash geometry is drawn on-device so it scales without screenshot artifacts.
 */
@Composable
internal fun MoneyButtonGraffiti(modifier: Modifier = Modifier) {
    SprayTag(
        modifier = modifier,
        designWidth = 480f,
        designHeight = 142f,
        script = { canvas, paint ->
            paintPhrase(canvas, paint, "the", 34f, 49f, 56f,
                intArrayOf(0xFFFFF35A.toInt(),0xFFFFB700.toInt()))
            paintPhrase(canvas, paint, "money", 24f, 105f, 89f,
                intArrayOf(0xFFF9F926.toInt(),0xFF04E1AA.toInt(),0xFF05BAFA.toInt()))
            paintPhrase(canvas, paint, "button", 232f, 108f, 82f,
                intArrayOf(0xFFFFC52C.toInt(),0xFFFF4AA7.toInt(),0xFF8538EF.toInt()))
            paint.strokeWidth = 5f
            paint.style = Paint.Style.STROKE
            paint.shader = null
            paint.color = 0xFFFF42D7.toInt()
            canvas.drawLine(238f,119f,462f,129f,paint)
            paint.style = Paint.Style.FILL
        }
    )
}

@Composable
internal fun CarterSignatureGraffiti(modifier: Modifier = Modifier) {
    SprayTag(
        modifier = modifier,
        designWidth = 250f,
        designHeight = 128f,
        script = { canvas, paint ->
            paintPhrase(canvas, paint, "invented by", 6f, 38f, 35f,
                intArrayOf(0xFFFF9B21.toInt(),0xFFFFCD3A.toInt()))
            paintPhrase(canvas, paint, "Carter", 7f, 79f, 60f,
                intArrayOf(0xFF26E9F8.toInt(),0xFF277CFB.toInt(),0xFFD33CED.toInt()))
            paintPhrase(canvas, paint, "Crum", 97f, 118f, 54f,
                intArrayOf(0xFF35E7F8.toInt(),0xFF8454FC.toInt(),0xFFFF2BE2.toInt()))
        }
    )
}

@Composable
private fun SprayTag(
    modifier: Modifier,
    designWidth: Float,
    designHeight: Float,
    script: (android.graphics.Canvas, Paint) -> Unit,
) {
    Canvas(modifier) {
        val raw = drawContext.canvas.nativeCanvas
        raw.save()
        raw.scale(size.width / designWidth, size.height / designHeight)
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        val rng = Random(101)
        val palette = intArrayOf(
            0xFF01D9EF.toInt(),0xFFFF3BB6.toInt(),0xFFFCEC12.toInt(),
            0xFF98FF3D.toInt(),0xFF874EFF.toInt())
        // Deterministic light spray texture without obscuring legible text.
        repeat(130) {
            val x = rng.nextFloat() * designWidth
            val y = rng.nextFloat() * designHeight
            p.color = palette[it % palette.size]
            p.alpha = if (it % 5 == 0) 190 else 108
            val radius = if (it % 9 == 0) 3.5f else 1.1f
            raw.drawCircle(x,y,radius,p)
        }
        p.alpha = 255
        script(raw,p)
        raw.restore()
    }
}

private fun paintPhrase(
    canvas: android.graphics.Canvas,
    paint: Paint,
    word: String,
    x: Float,
    y: Float,
    size: Float,
    gradient: IntArray,
) {
    paint.typeface = Typeface.create("sans-serif-black", Typeface.BOLD_ITALIC)
    paint.textSize = size
    paint.textSkewX = -.105f
    paint.strokeJoin = Paint.Join.ROUND
    paint.shader = null
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = max(5f,size * .16f)
    paint.color = 0xFF07100D.toInt()
    canvas.drawText(word,x,y,paint)
    paint.strokeWidth = max(2f,size * .04f)
    paint.color = 0xFFFFF5FC.toInt()
    canvas.drawText(word,x,y,paint)
    paint.style = Paint.Style.FILL
    paint.shader = LinearGradient(
        x, y-size, x+size*2.6f, y+4f,
        gradient, null, Shader.TileMode.CLAMP
    )
    canvas.drawText(word,x,y,paint)
    paint.shader = null
    paint.style = Paint.Style.FILL
}
