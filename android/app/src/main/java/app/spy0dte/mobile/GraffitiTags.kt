package app.spy0dte.mobile

import androidx.compose.foundation.Image
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource

/**
 * The exact approved graffiti picture cutouts, kept in native transparent
 * drawable resources. Purely decorative and never clickable, so they do not
 * interfere with the live Auto Trade switch, broker access, or equity values.
 */
@Composable
internal fun MoneyButtonGraffiti(modifier: Modifier = Modifier) {
    Image(
        painter = painterResource(id = R.drawable.the_money_button),
        contentDescription = null,
        modifier = modifier,
        contentScale = ContentScale.Fit,
    )
}

@Composable
internal fun CarterSignatureGraffiti(modifier: Modifier = Modifier) {
    Image(
        painter = painterResource(id = R.drawable.invented_by_carter_crum),
        contentDescription = null,
        modifier = modifier,
        contentScale = ContentScale.Fit,
    )
}
