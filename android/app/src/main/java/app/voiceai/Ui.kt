package app.voiceai

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Interfaccia costruita da codice: niente layout XML, niente librerie UI. */
object Ui {
    const val BG = 0xFF000000.toInt()
    const val CARD = 0xFF181B22.toInt()
    const val LINE = 0xFF262A33.toInt()
    const val FG = 0xFFE8E9EC.toInt()
    const val MUT = 0xFF9AA0AB.toInt()
    const val ACC = 0xFFFF4D5A.toInt()
    const val OK = 0xFF4CC38A.toInt()
    const val WARN = 0xFFE5B454.toInt()
    const val LATE = 0xFF7A1D24.toInt()

    val MOODS = mapOf(-2 to "😣", -1 to "😕", 0 to "😐", 1 to "🙂", 2 to "😄")

    fun Context.dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    fun bg(color: Int, radius: Float = 24f, stroke: Int? = null, dashed: Boolean = false) = GradientDrawable().apply {
        setColor(color); cornerRadius = radius
        if (stroke != null) { if (dashed) setStroke(2, stroke, 8f, 6f) else setStroke(2, stroke) }
    }

    fun Context.text(s: CharSequence, size: Float = 15f, color: Int = FG, bold: Boolean = false) = TextView(this).apply {
        text = s; textSize = size; setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    /** Sfondo con effetto al tocco. */
    fun ripple(d: android.graphics.drawable.Drawable) =
        android.graphics.drawable.RippleDrawable(android.content.res.ColorStateList.valueOf(0x33FFFFFF), d, null)

    fun gradient(a: Int, b: Int, radius: Float) = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(a, b))
        .apply { cornerRadius = radius }

    /** Vetro: riempimento bianco traslucido, bordo chiaro, riflesso in alto (liquid glass + Material). */
    fun glass(radius: Float, tint: Int? = null, strong: Boolean = false): android.graphics.drawable.Drawable {
        val fill = GradientDrawable().apply {
            cornerRadius = radius
            setColor(tint?.let { Color.argb(if (strong) 150 else 70, Color.red(it), Color.green(it), Color.blue(it)) }
                ?: Color.argb(if (strong) 235 else 34, 255, 255, 255))
            setStroke(2, Color.argb(if (strong) 0 else 80, 255, 255, 255))
        }
        val shine = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
            intArrayOf(Color.argb(if (strong) 0 else 46, 255, 255, 255), Color.TRANSPARENT)).apply { cornerRadius = radius }
        return ripple(android.graphics.drawable.LayerDrawable(arrayOf(fill, shine)))
    }

    fun Context.button(s: String, primary: Boolean = false, onClick: () -> Unit) = Button(this).apply {
        text = s; isAllCaps = false; textSize = 14f; stateListAnimator = null; isSingleLine = true
        // primario: vetro pieno (bianco) con testo scuro, come un "filled button" Material
        setTextColor(if (primary) 0xFF101418.toInt() else FG)
        if (primary) setTypeface(typeface, Typeface.BOLD)
        background = glass(dp(22).toFloat(), strong = primary)
        elevation = if (primary) dp(3).toFloat() else 0f
        setPadding(dp(14), dp(6), dp(14), dp(6)); minHeight = 0; minimumHeight = 0
        setOnClickListener { onClick() }
    }

    /** Riquadro di navigazione: icona grande + etichetta, sfondo sfumato. */
    fun Context.tile(icon: String, label: String, tint: Int, onClick: () -> Unit) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
        setPadding(dp(4), dp(12), dp(4), dp(10))
        background = glass(dp(20).toFloat())
        isClickable = true; setOnClickListener { onClick() }
        addView(TextView(context).apply { text = icon; textSize = 22f; gravity = Gravity.CENTER })
        addView(TextView(context).apply {
            text = label; textSize = 12f; setTextColor(FG); gravity = Gravity.CENTER; isSingleLine = true
            setPadding(0, dp(4), 0, 0)
        })
    }

    /** Pulsante tondo con sola icona. */
    fun Context.iconButton(icon: String, onClick: () -> Unit) = TextView(this).apply {
        text = icon; textSize = 18f; gravity = Gravity.CENTER
        background = glass(dp(21).toFloat())
        isClickable = true; setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(dp(42), dp(42))
    }

    /** Colore mescolato col nero dello sfondo (f = quanto colore). */
    fun blend(c: Int, f: Float) = Color.rgb((Color.red(c) * f).toInt(), (Color.green(c) * f).toInt(), (Color.blue(c) * f).toInt())

    fun Context.input(hint: String, value: String = "", type: Int = InputType.TYPE_CLASS_TEXT) = EditText(this).apply {
        this.hint = hint; setText(value); inputType = type; textSize = 15f
        setTextColor(FG); setHintTextColor(MUT); isSingleLine = true
        background = bg(0xFF0B0D11.toInt(), 20f, LINE); setPadding(dp(12), dp(10), dp(12), dp(10))
    }

    fun Context.column(pad: Int = 16) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; setPadding(dp(pad), dp(pad), dp(pad), dp(pad))
    }

    fun Context.row() = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
    }

    fun Context.card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; background = bg(CARD, 28f, LINE)
        setPadding(dp(14), dp(12), dp(14), dp(12))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { bottomMargin = dp(10) }
    }

    fun LinearLayout.add(v: View, weight: Float = 0f, marginEnd: Int = 0): View {
        val own = v.layoutParams as? LinearLayout.LayoutParams
        val lp = if (own != null && weight == 0f) LinearLayout.LayoutParams(own.width, own.height)
        else if (orientation == LinearLayout.HORIZONTAL)
            LinearLayout.LayoutParams(if (weight > 0) 0 else ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, weight)
        else LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, if (weight > 0) 0 else ViewGroup.LayoutParams.WRAP_CONTENT, weight)
        lp.marginEnd = context.dp(marginEnd)
        if (v.layoutParams is LinearLayout.LayoutParams && weight == 0f && marginEnd == 0) addView(v) else addView(v, lp)
        return v
    }

    /** Android 15 disegna sotto le barre di sistema: margini pari a barra di stato, navigazione e tastiera. */
    fun View.insetSafe() = setOnApplyWindowInsetsListener { v, ins ->
        val b = ins.getInsets(android.view.WindowInsets.Type.systemBars() or android.view.WindowInsets.Type.ime()
            or android.view.WindowInsets.Type.displayCutout())
        v.setPadding(b.left, b.top, b.right, b.bottom)
        ins
    }

    /** Pagina scorrevole standard: titolo + contenuto. Ritorna il contenitore del contenuto. */
    fun Activity.page(title: String, header: LinearLayout.() -> Unit = {}): LinearLayout {
        val root = column(0).apply { setBackgroundColor(BG); insetSafe() }
        val top = row().apply { setPadding(dp(16), dp(12), dp(16), dp(8)) }
        top.add(iconButton("←") { finish() }, marginEnd = 10)
        top.add(text(title, 19f, bold = true), weight = 1f)
        top.header()
        root.add(top)
        val body = column(16)
        root.add(ScrollView(this).apply { addView(body); isFillViewport = true }, weight = 1f)
        setContentView(root)
        return body
    }

    private val hm = SimpleDateFormat("HH:mm", Locale.ITALY)
    private val dm = SimpleDateFormat("d/M HH:mm", Locale.ITALY)
    private val dayFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
    fun hm(ts: Double) = hm.format(Date((ts * 1000).toLong()))
    fun dm(ts: Double) = dm.format(Date((ts * 1000).toLong()))

    /** "oggi 12:00", "domani 19:30", "lun 3/11 9:00" */
    fun due(ts: Double): String {
        val d = Date((ts * 1000).toLong())
        val now = System.currentTimeMillis()
        val day = dayFmt.format(d)
        return when (day) {
            dayFmt.format(Date(now)) -> "oggi " + hm.format(d)
            dayFmt.format(Date(now + 86_400_000)) -> "domani " + hm.format(d)
            else -> SimpleDateFormat("EEE d/M HH:mm", Locale.ITALY).format(d)
        }
    }

    fun now() = System.currentTimeMillis() / 1000.0
}
