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
import kotlin.math.sin

/** Onde morbide: sinusoidi sovrapposte e traslucide, sfumate ai bordi. Si muovono piano a riposo,
 *  si alzano con la voce in registrazione. */
class WaveformView(ctx: Context) : View(ctx) {
    var recording = false
    private var level = 0f          // 0..1, smussato
    private var target = 0f
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2f }
    private val path = android.graphics.Path()
    private val t0 = System.nanoTime()
    // (frequenza, velocità, fase, ampiezza relativa, opacità)
    private val layers = listOf(
        floatArrayOf(1.6f, 0.9f, 0.0f, 1.00f, 70f),
        floatArrayOf(2.3f, -1.3f, 1.7f, 0.75f, 60f),
        floatArrayOf(3.1f, 1.7f, 3.1f, 0.55f, 55f),
        floatArrayOf(4.2f, -2.2f, 4.4f, 0.40f, 80f))

    /** amp = MediaRecorder.getMaxAmplitude() (0..32767) */
    fun push(amp: Int) {
        target = if (amp <= 0) 0f else (ln(1f + amp) / ln(32768f)).coerceIn(0f, 1f).let { it * it * 1.3f }.coerceAtMost(1f)
    }

    fun idle() { target = 0f }

    override fun onDraw(c: Canvas) {
        level += (target - level) * 0.15f
        val w = width.toFloat()
        val mid = height / 2f
        val t = (System.nanoTime() - t0) / 1e9f
        val base = height * (if (recording) 0.14f + 0.36f * level else 0.20f)
        val n = 64
        for (l in layers) {
            path.reset()
            // contorno superiore da sinistra a destra, poi inferiore (più basso) a ritroso: forma chiusa
            for (i in 0..n) {
                val x = w * i / n
                val u = i.toFloat() / n
                val env = sin(Math.PI * u).toFloat().let { it * it }
                val y = mid - base * l[3] * env * (0.55f + 0.45f * sin(l[0] * 2 * Math.PI * u + l[2] + t * l[1]).toFloat())
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            for (i in n downTo 0) {
                val u = i.toFloat() / n
                val env = sin(Math.PI * u).toFloat().let { it * it }
                val y = mid + base * l[3] * 0.8f * env * (0.55f + 0.45f * sin(l[0] * 2 * Math.PI * u + l[2] + 1.3f + t * l[1] * 0.8f).toFloat())
                path.lineTo(w * i / n, y)
            }
            path.close()
            paint.color = Color.argb(l[4].toInt(), 255, 255, 255)
            c.drawPath(path, paint)
        }
        line.color = Color.argb(110, 255, 255, 255)
        c.drawLine(0f, mid, w, mid, line)
        postInvalidateOnAnimation()
    }
}

/** Tasto centrale: anello bianco + pallino rosso a riposo; cerchio bianco con alone e quadrato "stop" rosso in registrazione. */
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
            p.color = Color.argb((50 + level * 90).toInt(), 255, 255, 255)
            c.drawCircle(cx, cy, r * (1.08f + level * 0.3f), p)
            p.color = Color.WHITE
            c.drawCircle(cx, cy, r, p)
            p.color = Color.rgb(0xE5, 0x39, 0x4B)
            val s = r * 0.34f
            c.drawRoundRect(cx - s, cy - s, cx + s, cy + s, s * 0.3f, s * 0.3f, p)
        } else {
            p.style = Paint.Style.FILL
            p.color = Color.argb(45, 255, 255, 255)
            c.drawCircle(cx, cy, r, p)
            p.style = Paint.Style.STROKE
            p.strokeWidth = r * 0.07f
            p.color = Color.WHITE
            c.drawCircle(cx, cy, r, p)
            p.style = Paint.Style.FILL
            p.color = Color.rgb(0xFF, 0x4D, 0x5A)
            c.drawCircle(cx, cy, r * 0.74f, p)
        }
    }
}

/** Tratto di linea del tempo verticale con un pallino: la linea attraversa tutta la riga. */
class TimelineDot(ctx: Context, private val color: Int, private val first: Boolean, private val last: Boolean) : View(ctx) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = resources.displayMetrics.density * 11
        p.color = Color.argb(70, 255, 255, 255); p.strokeWidth = resources.displayMetrics.density * 1.5f
        c.drawLine(cx, if (first) cy else 0f, cx, if (last) cy else height.toFloat(), p)
        val r = resources.displayMetrics.density * 5
        p.color = color; c.drawCircle(cx, cy, r, p)
        p.style = Paint.Style.STROKE; p.color = Color.WHITE; p.strokeWidth = resources.displayMetrics.density * 1.5f
        c.drawCircle(cx, cy, r, p); p.style = Paint.Style.FILL
    }
}
