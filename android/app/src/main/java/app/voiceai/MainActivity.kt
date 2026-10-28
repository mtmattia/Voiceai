package app.voiceai

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Space
import android.widget.TextView
import android.widget.Toast
import app.voiceai.Ui.add
import app.voiceai.Ui.bg
import app.voiceai.Ui.button
import app.voiceai.Ui.card
import app.voiceai.Ui.column
import app.voiceai.Ui.dp
import app.voiceai.Ui.iconButton
import app.voiceai.Ui.input
import app.voiceai.Ui.insetSafe
import app.voiceai.Ui.row
import app.voiceai.Ui.text
import app.voiceai.Ui.tile
import org.json.JSONArray
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private lateinit var prefs: Prefs
    private lateinit var wave: WaveformView
    private lateinit var recBtn: RecordButton
    private lateinit var timer: TextView
    private lateinit var status: TextView
    private lateinit var results: LinearLayout
    private lateinit var resultsScroll: ScrollView
    private lateinit var topSpace: Space
    private lateinit var black: View
    private lateinit var query: android.widget.EditText
    private var lastTouch = System.currentTimeMillis()
    private var tickN = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val root = FrameLayout(this).apply { background = bgDrawable; insetSafe() }
        val col = column(16)

        // intestazione: nome + stato a sinistra, impostazioni a destra
        val head = row()
        val titles = column(0)
        titles.add(text("● VoiceAi", 22f, bold = true).apply {
            text = android.text.SpannableString("● VoiceAi").apply {
                setSpan(android.text.style.ForegroundColorSpan(Ui.ACC), 0, 1, 0)
            }
        })
        status = text("", 12f, 0xB3FFFFFF.toInt())
        titles.add(status)
        head.add(titles, weight = 1f)
        head.add(iconButton("👥") { open(PersonsActivity::class.java) }, marginEnd = 8)
        head.add(iconButton("⚙") { open(SettingsActivity::class.java) })
        col.add(head)

        val nav = row().apply { setPadding(0, dp(14), 0, 0) }
        nav.add(tile("📝", "Da fare", 0xFFE5B454.toInt()) { open(MemoActivity::class.java) }, weight = 1f, marginEnd = 8)
        nav.add(tile("📅", "Agenda", 0xFF4CC38A.toInt()) {
            startActivity(Intent(this, MemoActivity::class.java).putExtra("kind", "agenda"))
        }, weight = 1f, marginEnd = 8)
        nav.add(tile("🗂️", "Archivio", 0xFF5B8DEF.toInt()) { open(ArchiveActivity::class.java) }, weight = 1f, marginEnd = 8)
        nav.add(tile("🕑", "Cronologia", 0xFFB07CF0.toInt()) { open(TimelineActivity::class.java) }, weight = 1f)
        col.add(nav)

        // ricerca: funziona anche durante la registrazione (il servizio è indipendente dalla schermata)
        val sr = row().apply { setPadding(0, dp(14), 0, dp(8)) }
        val pill = row().apply {
            background = Ui.glass(dp(24).toFloat()); setPadding(dp(14), 0, dp(4), 0)
        }
        query = input("Cerca o fai una domanda…").apply {
            background = null; setPadding(0, dp(12), 0, dp(12)); setHintTextColor(0xB3FFFFFF.toInt())
            imeOptions = EditorInfo.IME_ACTION_SEARCH
            setOnEditorActionListener { _, _, _ -> search(); true }
        }
        pill.add(query, weight = 1f)
        pill.add(text("🔍", 18f).apply {
            setPadding(dp(10), dp(8), dp(10), dp(8)); isClickable = true; setOnClickListener { search() }
        })
        sr.add(pill, weight = 1f, marginEnd = 8)
        sr.add(button("✨ Chiedi", primary = true) { ask() })
        col.add(sr)

        topSpace = Space(this)
        col.add(topSpace, weight = 1f)
        results = column(0)
        resultsScroll = ScrollView(this).apply { addView(results); visibility = View.GONE }
        col.add(resultsScroll, weight = 1.4f)

        wave = WaveformView(this)
        col.add(wave.apply { layoutParams = LinearLayout.LayoutParams(-1, dp(130)).apply { topMargin = dp(8) } })
        recBtn = RecordButton(this).apply { setOnClickListener { toggle() } }
        val btnWrap = FrameLayout(this)
        btnWrap.addView(recBtn, FrameLayout.LayoutParams(dp(170), dp(170), Gravity.CENTER))
        col.add(btnWrap)
        timer = text("Tocca per registrare", 15f, Ui.MUT).apply { gravity = Gravity.CENTER }
        col.add(timer)
        col.add(Space(this), weight = 0.6f)
        root.addView(col)

        // velo nero anti burn-in: pixel OLED spenti, si tocca per tornare
        black = View(this).apply { setBackgroundColor(Color.BLACK); visibility = View.GONE; isClickable = true }
        root.addView(black, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)

        SyncWorker.schedulePeriodic(this)
    }

    override fun onResume() {
        super.onResume()
        RecorderService.recoverOrphans(this)
        UploadWorker.enqueue(this)
        SyncWorker.now(this)
        lastTouch = System.currentTimeMillis()
        applyScreenFlag()
        ui.post(tick)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(tick)
        setBlack(false)
    }

    private fun open(c: Class<*>) = startActivity(Intent(this, c))

    // ---------- registrazione ----------
    private fun toggle() {
        if (RecorderService.running) {
            startService(Intent(this, RecorderService::class.java).setAction(RecorderService.ACTION_STOP))
            ui.postDelayed({ applyScreenFlag() }, 300)
            return
        }
        if (!prefs.configured) {
            Toast.makeText(this, "Imposta prima server e token", Toast.LENGTH_LONG).show()
            open(SettingsActivity::class.java); return
        }
        val need = listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS)
            .filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (need.isNotEmpty()) { requestPermissions(need.toTypedArray(), 1); return }
        startForegroundService(Intent(this, RecorderService::class.java))
        lastTouch = System.currentTimeMillis()
        ui.postDelayed({ applyScreenFlag() }, 300)
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        super.onRequestPermissionsResult(code, perms, res)
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) toggle()
        else Toast.makeText(this, "Serve il permesso microfono", Toast.LENGTH_LONG).show()
    }

    private fun applyScreenFlag() {
        if (RecorderService.running) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun setBlack(on: Boolean) {
        if ((black.visibility == View.VISIBLE) == on) return
        black.visibility = if (on) View.VISIBLE else View.GONE
        window.attributes = window.attributes.apply {
            screenBrightness = if (on) 0.01f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        }
        window.insetsController?.let {
            if (on) {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            } else it.show(WindowInsets.Type.systemBars())
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        lastTouch = System.currentTimeMillis()
        if (black.visibility == View.VISIBLE) {
            if (ev.action == MotionEvent.ACTION_DOWN) setBlack(false)
            return true   // il tocco che sveglia non preme niente
        }
        return super.dispatchTouchEvent(ev)
    }

    private val tick = object : Runnable {
        override fun run() {
            val rec = RecorderService.running
            if (rec != wave.recording) fadeTo(rec)
            wave.recording = rec; recBtn.recording = rec
            if (rec) {
                val a = RecorderService.amplitude()
                wave.push(a); recBtn.level = (a / 12000f).coerceIn(0f, 1f)
                val s = (System.currentTimeMillis() - RecorderService.startedAtMs) / 1000
                timer.text = "● REC  %d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60)
                timer.setTextColor(Color.WHITE)
                if (System.currentTimeMillis() - lastTouch > prefs.blackAfterSec * 1000L) setBlack(true)
            } else {
                if (tickN % 3 == 0L) wave.idle()
                timer.text = "Tocca per registrare"; timer.setTextColor(0xCCFFFFFF.toInt())
                setBlack(false)
            }
            if (tickN % 40 == 0L) updateStatus()
            tickN++
            ui.postDelayed(this, 50)
        }
    }

    // sfondo: verde a riposo, rosso in registrazione (dissolvenza)
    private val IDLE = intArrayOf(0xFF06231A.toInt(), 0xFF0C4A35.toInt(), 0xFF12805A.toInt())
    private val REC = intArrayOf(0xFF2A060C.toInt(), 0xFF6E1222.toInt(), 0xFFC4263F.toInt())
    private val bgDrawable = android.graphics.drawable.GradientDrawable(
        android.graphics.drawable.GradientDrawable.Orientation.TOP_BOTTOM, IDLE.clone())

    private fun fadeTo(rec: Boolean) {
        val from = if (rec) IDLE else REC
        val to = if (rec) REC else IDLE
        val ev = android.animation.ArgbEvaluator()
        android.animation.ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 600
            addUpdateListener { a ->
                val f = a.animatedValue as Float
                bgDrawable.colors = IntArray(3) { ev.evaluate(f, from[it], to[it]) as Int }
            }
            start()
        }
    }

    private fun updateStatus() {
        val pend = UploadWorker.pendingCount(this)
        val ls = prefs.lastSync
        val sync = if (ls == 0L) "mai sincronizzato" else "sync " + Ui.hm(ls / 1000.0)
        status.text = (if (pend > 0) "$pend blocchi in coda · " else "") + sync
    }

    // ---------- ricerca e domande ----------
    private fun showResults(v: List<View>) {
        results.removeAllViews()
        v.forEach { results.add(it) }
        val any = v.isNotEmpty()
        resultsScroll.visibility = if (any) View.VISIBLE else View.GONE
        topSpace.visibility = if (any) View.GONE else View.VISIBLE
    }

    private fun closeRow() = row().apply {
        add(text("", 1f), weight = 1f)
        add(button("✕") { showResults(emptyList()); query.setText("") })
    }

    private fun search() {
        val q = query.text.toString().trim()
        if (q.isEmpty()) return
        io.execute {
            val db = LocalDb.get(this)
            val recs = db.searchKeywords(q)
            val hits = db.search(q)
            ui.post {
                val v = mutableListOf<View>(closeRow())
                if (recs.isNotEmpty()) v += card().apply {
                    add(text("Registrazioni per parola chiave", 13f, Ui.MUT, bold = true))
                    recs.forEach { r ->
                        val kws = runCatching { JSONArray(r["keywords"] as? String ?: "[]") }.getOrNull()
                        val k = kws?.let { a -> (0 until a.length()).joinToString(" · ") { a.getString(it) } } ?: ""
                        add(text("${r["day"]} ${Ui.hm(r["started_at"] as Double)}  ${r["title"] ?: ""}\n$k", 14f).apply {
                            setPadding(0, dp(6), 0, dp(6)); setOnClickListener { openRec(r["id"] as Long, null) }
                        })
                    }
                }
                v += card().apply {
                    add(text("Frasi (${hits.size}) — ricerca offline sul telefono", 13f, Ui.MUT, bold = true))
                    if (hits.isEmpty()) add(text("Nessun risultato.", 14f, Ui.MUT))
                    hits.forEach { h -> add(utt(h)) }
                }
                showResults(v)
            }
        }
    }

    private fun utt(h: Map<String, Any?>) = text("${h["day"] ?: ""} ${Ui.hm(h["ts"] as Double)}  ${h["speaker"]}: ${h["text"]}", 14f).apply {
        setPadding(0, dp(6), 0, dp(6))
        setOnClickListener { openRec(h["recording_id"] as Long, h["id"] as Long) }
    }

    private fun ask() {
        val q = query.text.toString().trim()
        if (q.isEmpty()) return
        showResults(listOf(closeRow(), card().apply { add(text("Chiedo al PC… (se sta trascrivendo aspetta la fine del blocco)", 14f, Ui.MUT)) }))
        io.execute {
            val r = runCatching { Api(this).ask(q) }
            ui.post {
                r.onSuccess { j ->
                    val v = mutableListOf<View>(closeRow(), card().apply { add(text(j.optString("risposta"), 15f)) })
                    val f = j.optJSONArray("fonti")
                    if (f != null && f.length() > 0) v += card().apply {
                        add(text("Fonti", 13f, Ui.MUT, bold = true))
                        for (i in 0 until f.length()) {
                            val o = f.getJSONObject(i)
                            add(utt(mapOf("day" to o.optString("day"), "ts" to o.optDouble("ts"), "speaker" to o.optString("speaker"),
                                "text" to o.optString("text"), "recording_id" to o.optLong("recording_id"), "id" to o.optLong("id"))))
                        }
                    }
                    showResults(v)
                }.onFailure {
                    showResults(listOf(closeRow(), card().apply {
                        add(text("PC non raggiungibile (${it.message}).\nLa ricerca per parole con \"Cerca\" funziona anche offline.", 14f, Ui.WARN))
                    }))
                }
            }
        }
    }

    private fun openRec(rid: Long, highlight: Long?) {
        startActivity(Intent(this, ArchiveActivity::class.java).putExtra("rec", rid).putExtra("hl", highlight ?: -1L))
    }
}
