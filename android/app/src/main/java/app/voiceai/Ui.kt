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

    fun Context.button(s: String, primary: Boolean = false, onClick: () -> Unit) = Button(this).apply {
        text = s; isAllCaps = false; textSize = 14f
        setTextColor(if (primary) Color.WHITE else FG)
        background = bg(if (primary) ACC else CARD, 20f, if (primary) null else LINE)
        setPadding(dp(14), dp(6), dp(14), dp(6)); minHeight = 0; minimumHeight = 0
        setOnClickListener { onClick() }
    }

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
        val lp = if (orientation == LinearLayout.HORIZONTAL)
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
        top.add(button("←") { finish() }, marginEnd = 10)
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
