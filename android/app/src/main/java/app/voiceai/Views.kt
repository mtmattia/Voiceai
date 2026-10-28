package app.voiceai

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min

/** Onda che scorre da destra a sinistra; grigia a riposo, rossa in registrazione. */
class WaveformView(ctx: Context) : View(ctx) {
    private val samples = FloatArray(90)
    private var head = 0
    var recording = false
        set(v) { field = v; invalidate() }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    /** amp = MediaRecorder.getMaxAmplitude() (0..32767) */
    fun push(amp: Int) {
        val v = if (amp <= 0) 0f else (ln(1f + amp) / ln(32768f)).coerceIn(0f, 1f)
        samples[head] = v * v * 1.4f   // più contrasto tra voce e silenzio
        head = (head + 1) % samples.size
        invalidate()
    }

    fun idle() {
        // a riposo: linea piatta (puntini), l'onda si muove solo con la voce in registrazione
        push(0)
    }

    override fun onDraw(c: Canvas) {
        val n = samples.size
        val w = width.toFloat() / n
        val mid = height / 2f
        for (i in 0 until n) {
            val v = samples[(head + i) % n]
            val h = max(w * 0.6f, v * height * 0.9f)
            // dissolvenza ai bordi
            val edge = min(i, n - 1 - i) / (n * 0.15f)
            val alpha = (min(1f, edge) * 255).toInt()
            paint.color = if (recording) Color.argb(alpha, 0xFF, 0x4D, 0x5A) else Color.argb(alpha / 2, 0xE8, 0xE9, 0xEC)
            rect.set(i * w + w * 0.2f, mid - h / 2, (i + 1) * w - w * 0.2f, mid + h / 2)
            c.drawRoundRect(rect, w, w, paint)
        }
    }
}

/** Tasto centrale: anello + pallino rosso a riposo; cerchio rosso con alone e quadrato "stop" in registrazione. */
class RecordButton(ctx: Context) : View(ctx) {
    var recording = false
        set(v) { field = v; invalidate() }
    var level = 0f
        set(v) { field = field * 0.7f + v * 0.3f; invalidate() }
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)

    init { isClickable = true; contentDescription = "Registra" }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r = min(width, height) / 2f * 0.72f
        if (recording) {
            p.style = Paint.Style.FILL
            p.color = Color.argb((60 + level * 100).toInt(), 0xFF, 0x4D, 0x5A)
            c.drawCircle(cx, cy, r * (1.08f + level * 0.3f), p)
            p.color = Color.rgb(0xFF, 0x4D, 0x5A)
            c.drawCircle(cx, cy, r, p)
            p.color = Color.WHITE
            val s = r * 0.36f
            c.drawRoundRect(cx - s, cy - s, cx + s, cy + s, s * 0.3f, s * 0.3f, p)
        } else {
            p.style = Paint.Style.STROKE
            p.strokeWidth = r * 0.08f
            p.color = Color.rgb(0xE8, 0xE9, 0xEC)
            c.drawCircle(cx, cy, r, p)
            p.style = Paint.Style.FILL
            p.color = Color.rgb(0xFF, 0x4D, 0x5A)
            c.drawCircle(cx, cy, r * 0.78f, p)
        }
    }
}
