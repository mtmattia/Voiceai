package app.voiceai

import android.app.Activity
import android.app.AlertDialog
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.Toast
import app.voiceai.Ui.add
import app.voiceai.Ui.bg
import app.voiceai.Ui.button
import app.voiceai.Ui.card
import app.voiceai.Ui.column
import app.voiceai.Ui.dp
import app.voiceai.Ui.iconButton
import app.voiceai.Ui.input
import app.voiceai.Ui.page
import app.voiceai.Ui.row
import app.voiceai.Ui.text
import org.json.JSONArray
import java.util.Calendar
import java.util.concurrent.Executors

/** recordings.feelings (JSON) -> [(persona, sensazione, perché)] */
private fun feelings(s: Any?): List<Triple<String, String, String>> = runCatching {
    val a = JSONArray(s as? String ?: "[]")
    (0 until a.length()).map { a.getJSONObject(it).let { o -> Triple(o.optString("persona"), o.optString("sensazione"), o.optString("perche")) } }
}.getOrDefault(emptyList())

private fun kws(s: Any?): String = runCatching {
    val a = JSONArray(s as? String ?: "[]"); (0 until a.length()).joinToString(" · ") { a.getString(it) }
}.getOrDefault("")

/** Checklist dei memo: estratti dal parlato o aggiunti a mano. Modifiche offline, sync appena c'è rete. */
class MemoActivity : Activity() {
    private lateinit var body: LinearLayout
    private val db by lazy { LocalDb.get(this) }
    private val kind by lazy { if (intent.getStringExtra("kind") == "agenda") "agenda" else "todo" }
    private val agenda get() = kind == "agenda"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        body = page(if (agenda) "Agenda" else "Da fare") { add(iconButton("↻") { SyncWorker.now(this@MemoActivity); render() }) }
        render()
    }

    override fun onResume() { super.onResume(); render() }

    private fun changed() { SyncWorker.now(this); MemoReceiver.scheduleAll(this); render() }

    private fun render() {
        body.removeAllViews()
        val form = card()
        val t = input(if (agenda) "Nuovo appuntamento… (es. cena da mamma)" else "Nuova cosa da fare… (es. prendere il gatto)")
        val dsc = input("Descrizione (facoltativa)")
        form.add(t)
        form.add(dsc.apply { (layoutParams as? LinearLayout.LayoutParams)?.topMargin = dp(8) })
        val btns = row().apply { setPadding(0, dp(10), 0, 0) }
        fun save(ts: Double?) { if (t.text.isNotBlank()) { db.addMemo(t.text.toString(), ts, dsc.text.toString(), kind); changed() } }
        btns.add(button(if (agenda) "📅 Data e ora" else "+ ora") { pickDue { ts -> save(ts) } }, marginEnd = 8)
        if (!agenda) btns.add(button("Aggiungi", primary = true) { save(null) })
        form.add(btns)
        body.add(form)

        val open = db.openMemos(kind)
        body.add(card().apply {
            add(text(if (agenda) "In programma" else "Da fare", 15f, bold = true))
            if (open.isEmpty()) add(text(if (agenda) "Nessun appuntamento." else "Niente in sospeso.", 14f, Ui.MUT))
            var last = ""
            open.forEach { m ->
                if (agenda) {   // raggruppata per giorno
                    val d = (m["due_ts"] as? Double)?.let {
                        java.text.SimpleDateFormat("EEEE d MMMM", java.util.Locale.ITALY).format(java.util.Date((it * 1000).toLong()))
                    } ?: "senza data"
                    if (d != last) { add(text(d.uppercase(), 11f, Ui.MUT, bold = true).apply { setPadding(0, dp(10), 0, 0) }); last = d }
                }
                add(memoRow(m))
            }
        })
        if (!agenda) body.add(row().apply {
            setPadding(0, dp(4), 0, 0)
            add(dueChip("ora", "esplicita", false), marginEnd = 6); add(text("detta esplicitamente   ", 12f, Ui.MUT))
            add(dueChip("~ora", "ipotizzata", false), marginEnd = 6); add(text("stimata dai tuoi ritmi", 12f, Ui.MUT))
        })
        val done = db.doneMemos(kind)
        if (done.isNotEmpty()) body.add(card().apply {
            (layoutParams as LinearLayout.LayoutParams).topMargin = dp(10)
            add(text(if (agenda) "Passati" else "Fatti di recente", 15f, bold = true))
            done.forEach { add(memoRow(it)) }
        })
    }

    private fun dueChip(s: String, kind: String?, late: Boolean) = text(s, 12f, Color.WHITE).apply {
        setPadding(dp(7), dp(1), dp(7), dp(1))
        when {
            late -> background = bg(Ui.LATE, 12f)
            kind == "ipotizzata" -> { setTextColor(Ui.WARN); background = bg(Color.TRANSPARENT, 12f, Ui.WARN, dashed = true) }
            else -> background = bg(Ui.ACC, 12f)
        }
    }

    private fun memoRow(m: Map<String, Any?>): View {
        val uid = m["uid"] as String
        val done = m["done"] == 1L
        val r = row().apply { setPadding(0, dp(6), 0, dp(6)) }
        r.add(CheckBox(this).apply {
            isChecked = done
            setOnCheckedChangeListener { _, v -> db.setMemoDone(uid, v); changed() }
        })
        val info = column(0)
        info.add(text(m["text"] as String, 15f, if (done) Ui.MUT else Ui.FG, bold = !done))
        (m["descr"] as? String)?.takeIf { it.isNotBlank() }?.let { info.add(text(it, 13f, Ui.MUT).apply { setPadding(0, dp(2), 0, dp(2)) }) }
        val due = m["due_ts"] as? Double
        if (due != null) {
            val guessed = m["due_kind"] == "ipotizzata"
            val chip = dueChip((if (guessed) "~" else "") + Ui.due(due), m["due_kind"] as? String, !done && due < Ui.now())
            info.add(LinearLayout(this).apply { addView(chip) })
        }
        r.add(info, weight = 1f)
        r.add(iconButton("🕑") { pickDue { ts -> db.setMemoDue(uid, ts); changed() } }, marginEnd = 6)
        r.add(iconButton("✕") {
            AlertDialog.Builder(this).setMessage("Eliminare il memo?")
                .setPositiveButton("Elimina") { _, _ -> db.deleteMemo(uid); changed() }
                .setNegativeButton("Annulla", null).show()
        })
        return r
    }

    private fun pickDue(done: (Double) -> Unit) {
        val c = Calendar.getInstance()
        DatePickerDialog(this, { _, y, mo, d ->
            TimePickerDialog(this, { _, h, mi ->
                c.set(y, mo, d, h, mi, 0)
                done(c.timeInMillis / 1000.0)
            }, c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE), true).show()
        }, c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show()
    }
}

/** Persone riconosciute dalla voce: rinomina, conferma, "sono io". Unione: richiede il PC. */
class PersonsActivity : Activity() {
    private lateinit var body: LinearLayout
    private val db by lazy { LocalDb.get(this) }
    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        body = page("Persone")
        render()
    }

    private fun changed() { SyncWorker.now(this); render() }

    private fun render() {
        body.removeAllViews()
        body.add(text("\"Nome?\" = nome probabile dedotto dal parlato. Rinominare conferma la persona.", 13f, Ui.MUT)
            .apply { setPadding(0, 0, 0, dp(10)) })
        val all = db.persons()
        if (all.isEmpty()) body.add(text("Nessuna persona ancora: arriveranno dopo le prime registrazioni.", 14f, Ui.MUT))
        all.forEach { p ->
            val id = p["id"] as Long
            val confirmed = p["confirmed"] == 1L
            val owner = p["is_owner"] == 1L
            body.add(card().apply {
                add(row().apply {
                    add(text(p["name"] as String, 17f, if (confirmed) Ui.FG else Ui.WARN, bold = true), weight = 1f)
                    if (owner) add(text("⭐ io", 13f))
                })
                val min = ((p["talk_sec"] as? Number)?.toDouble() ?: 0.0) / 60
                val seen = (p["last_seen"] as? Double)?.let { " · ultima volta " + Ui.dm(it) } ?: ""
                add(text("%.0f min di parlato%s".format(min, seen), 12f, Ui.MUT))
                // due righe di pulsanti a larghezza piena: niente testo a capo né pulsanti fuori schermo
                lateinit var play: android.widget.Button
                add(row().apply {
                    setPadding(0, dp(10), 0, 0)
                    play = button("▶ Ascolta", primary = true) { listen(id, play) }
                    add(play, weight = 1f, marginEnd = 8)
                    add(button("Rinomina") { rename(id, p["name"] as String) }, weight = 1f)
                })
                add(row().apply {
                    setPadding(0, dp(8), 0, 0)
                    if (!confirmed) add(button("Conferma") { db.editPerson(id, confirmed = true); changed() }, weight = 1f, marginEnd = 8)
                    if (!owner) add(button("Sono io") { db.editPerson(id, owner = true); changed() }, weight = 1f, marginEnd = 8)
                    add(button("Unisci…") { merge(id, all) }, weight = 1f)
                })
            })
        }
    }

    private var player: android.media.MediaPlayer? = null

    /** Fa sentire la voce della persona (dal PC) per riconoscerla. */
    private fun listen(id: Long, b: android.widget.Button) {
        if (player != null) { stopVoice(); return }
        b.text = "…"
        io.execute {
            val f = java.io.File(cacheDir, "voce_$id.ogg")
            val r = runCatching { Api(this).voice(id, f) }
            ui.post {
                r.onSuccess {
                    player = android.media.MediaPlayer().apply {
                        setDataSource(f.path); prepare(); start()
                        setOnCompletionListener { stopVoice(); b.text = "▶ Ascolta" }
                    }
                    b.text = "■ Stop"
                }.onFailure {
                    b.text = "▶ Ascolta"
                    Toast.makeText(this, "Non riesco a farla sentire: ${it.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun stopVoice() { player?.release(); player = null; render() }

    override fun onPause() { super.onPause(); player?.release(); player = null }

    private fun rename(id: Long, cur: String) {
        val e = input("Nome", cur.removeSuffix("?"), InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS)
        AlertDialog.Builder(this).setTitle("Nome").setView(e)
            .setPositiveButton("Salva") { _, _ -> if (e.text.isNotBlank()) { db.editPerson(id, name = e.text.toString()); changed() } }
            .setNegativeButton("Annulla", null).show()
    }

    private fun merge(id: Long, all: List<Map<String, Any?>>) {
        val others = all.filter { it["id"] != id }
        if (others.isEmpty()) return
        AlertDialog.Builder(this).setTitle("Stessa persona di…")
            .setItems(others.map { it["name"] as String }.toTypedArray()) { _, i ->
                val into = others[i]["id"] as Long
                io.execute {
                    val r = runCatching { Api(this).merge(id, into); Sync(this).run() }
                    ui.post {
                        if (r.isFailure) Toast.makeText(this, "Serve il PC raggiungibile per unire", Toast.LENGTH_LONG).show()
                        render()
                    }
                }
            }.show()
    }
}

/** Archivio offline: giornate -> registrazioni -> trascrizione. */
class ArchiveActivity : Activity() {
    private val db by lazy { LocalDb.get(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val rec = intent.getLongExtra("rec", -1)
        val day = intent.getStringExtra("day")
        when {
            rec >= 0 -> showTranscript(rec, intent.getLongExtra("hl", -1))
            day != null -> showDay(day)
            else -> showDays()
        }
    }

    private fun go(day: String? = null, rec: Long? = null) {
        val i = android.content.Intent(this, ArchiveActivity::class.java)
        day?.let { i.putExtra("day", it) }
        rec?.let { i.putExtra("rec", it) }
        startActivity(i)
    }

    private fun showDays() {
        val body = page("Archivio") { add(iconButton("↻") { SyncWorker.now(this@ArchiveActivity); recreate() }) }
        val ds = db.days()
        if (ds.isEmpty()) body.add(text("Ancora niente: le giornate arrivano dopo la sincronizzazione col PC.", 14f, Ui.MUT))
        ds.forEach { d ->
            body.add(card().apply {
                add(row().apply {
                    add(text(d["day"] as String, 16f, bold = true), weight = 1f)
                    val mood = (d["mood_score"] as? Long)?.toInt()?.let { Ui.MOODS[it] } ?: ""
                    add(text("$mood ${d["mood"] ?: ""}", 14f))
                })
                add(text("${d["n_rec"]} blocchi · " + kws(d["keywords"]), 12f, Ui.MUT))
                setOnClickListener { go(day = d["day"] as String) }
            })
        }
    }

    private fun showDay(day: String) {
        val body = page(day) {
            add(button("Cronologia") { startActivity(android.content.Intent(this@ArchiveActivity, TimelineActivity::class.java).putExtra("day", day)) })
        }
        db.day(day)?.let { d ->
            if (d["mood"] != null) body.add(card().apply {
                val mood = (d["mood_score"] as? Long)?.toInt()?.let { Ui.MOODS[it] } ?: ""
                add(text("$mood ${d["mood"]}", 20f, bold = true))
                add(text(d["mood_reason"] as? String ?: "", 13f, Ui.MUT))
                (d["tip"] as? String)?.let { add(text("💡 $it", 14f, Ui.OK).apply { setPadding(0, dp(8), 0, 0) }) }
            })
            (d["summary"] as? String)?.let { body.add(card().apply { add(text(it.replace("**", ""), 14f)) }) }
        }
        db.recordingsOf(day).forEach { r ->
            body.add(card().apply {
                add(text("${Ui.hm(r["started_at"] as Double)}  ${r["title"] ?: ""}", 15f, bold = true))
                add(text(kws(r["keywords"]).ifEmpty { r["status"] as? String ?: "" }, 12f, Ui.MUT))
                feelings(r["feelings"]).takeIf { it.isNotEmpty() }?.let { f ->
                    add(text(f.joinToString("  ·  ") { "${it.first} ${it.second}" }, 12f, Ui.WARN).apply { setPadding(0, dp(4), 0, 0) })
                }
                setOnClickListener { go(rec = r["id"] as Long) }
            })
        }
    }

    private fun showTranscript(rid: Long, hl: Long) {
        val r = db.recording(rid)
        val body = page(if (r != null) "${r["day"]} ${Ui.hm(r["started_at"] as Double)}" else "Trascrizione")
        feelings(r?.get("feelings")).takeIf { it.isNotEmpty() }?.let { f ->
            body.add(card().apply {
                add(text("Come sembravano sentirsi", 15f, bold = true))
                f.forEach { add(text("${it.first}: ${it.second} — ${it.third}", 14f).apply { setPadding(0, dp(4), 0, 0) }) }
            })
        }
        val c = card()
        var target: View? = null
        db.transcript(rid).forEach { u ->
            val t = text("${Ui.hm(u["ts"] as Double)}  ${u["speaker"]}: ${u["text"]}", 14f).apply { setPadding(0, dp(4), 0, dp(4)) }
            if (u["id"] == hl) { t.setBackgroundColor(0xFF5A3B10.toInt()); target = t }
            c.add(t)
        }
        body.add(c)
        target?.let { v -> v.post { (body.parent as? android.widget.ScrollView)?.smoothScrollTo(0, v.top) } }
    }
}

/** Server (IP Tailscale del PC), token, durata blocchi. */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = Prefs(this)
        val body = page("Impostazioni")
        val server = input("http://100.x.y.z:8765", p.server, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        val token = input("Token", p.token, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
            .apply { transformationMethod = android.text.method.PasswordTransformationMethod.getInstance() }
        val device = input("Nome dispositivo", p.device)
        val chunk = input("Minuti per blocco (5-10)", p.chunkMinutes.toString(), InputType.TYPE_CLASS_NUMBER)
        val black = input("Secondi prima dello schermo nero", p.blackAfterSec.toString(), InputType.TYPE_CLASS_NUMBER)
        listOf("Server" to server, "Token" to token, "Dispositivo" to device, "Blocco" to chunk, "Anti burn-in" to black)
            .forEach { (l, v) -> body.add(text(l, 13f, Ui.MUT).apply { setPadding(0, dp(10), 0, dp(4)) }); body.add(v) }
        val out = text("", 13f, Ui.MUT).apply { setPadding(0, dp(12), 0, 0) }
        body.add(row().apply {
            setPadding(0, dp(16), 0, 0)
            add(button("Salva", primary = true) {
                p.server = server.text.toString(); p.token = token.text.toString(); p.device = device.text.toString()
                p.chunkMinutes = chunk.text.toString().toIntOrNull() ?: 5
                p.blackAfterSec = black.text.toString().toIntOrNull() ?: 8
                out.text = "Salvato. La durata del blocco vale dalla prossima registrazione."
                UploadWorker.enqueue(this@SettingsActivity); SyncWorker.now(this@SettingsActivity)
            }, marginEnd = 8)
            add(button("Prova connessione") {
                out.text = "Provo…"
                Executors.newSingleThreadExecutor().execute {
                    val r = runCatching { Api(this@SettingsActivity).status() }
                    runOnUiThread { out.text = r.fold({ "OK: $it" }, { "Errore: ${it.message}" }) }
                }
            })
        })
        body.add(out)
        // abbinamento: evita di digitare il token (sul PC: voiceai abbina)
        val code = input("Codice di abbinamento (6 cifre)", "", InputType.TYPE_CLASS_NUMBER)
        body.add(text("\nAbbina col PC", 15f, bold = true))
        body.add(text("Scrivi l'indirizzo del server qui sopra, poi il codice che ti dà `voiceai abbina` sul PC.", 12f, Ui.MUT))
        body.add(row().apply {
            setPadding(0, dp(8), 0, 0)
            add(code, weight = 1f, marginEnd = 8)
            add(button("Abbina", primary = true) {
                p.server = server.text.toString(); p.device = device.text.toString()
                out.text = "Abbino…"
                val c = code.text.toString()
                Executors.newSingleThreadExecutor().execute {
                    val r = runCatching { Api(this@SettingsActivity).pair(c) }
                    runOnUiThread {
                        r.onSuccess { t ->
                            p.token = t; token.setText(t); code.setText("")
                            out.text = "Abbinato ✓ Il telefono ora è collegato al PC."
                            UploadWorker.enqueue(this@SettingsActivity); SyncWorker.now(this@SettingsActivity)
                        }.onFailure { out.text = "Errore: ${it.message}" }
                    }
                }
            })
        })
        body.add(text("\nIl telefono deve avere Tailscale attivo. I blocchi vengono cancellati dal telefono solo dopo " +
            "che il PC ha confermato lo stesso SHA-256.", 12f, Ui.MUT))
    }
}

/** Cronologia della giornata minuto per minuto ("08:23 Mattia parla con Marco"). Richiede il PC. */
class TimelineActivity : Activity() {
    private val io = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var day = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent.getStringExtra("day")?.let { day = it }
        render()
    }

    private fun pickDay() {
        val c = Calendar.getInstance().apply { time = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).parse(day)!! }
        DatePickerDialog(this, { _, y, m, d -> day = "%04d-%02d-%02d".format(y, m + 1, d); render() },
            c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show()
    }

    private fun render() {
        val body = page("Cronologia $day") { add(iconButton("📅") { pickDay() }) }
        val c = column(0).apply { add(text("Chiedo al PC…", 14f, Ui.MUT)) }
        body.add(c)
        io.execute {
            val r = runCatching { Api(this).timeline(day).getJSONArray("items") }
            ui.post {
                c.removeAllViews()
                r.onSuccess { a ->
                    if (a.length() == 0) c.add(text("Niente registrato in questo giorno.", 14f, Ui.MUT))
                    // linea del tempo: orario piccolo a sinistra, pallino sulla linea, testo a destra
                    for (i in 0 until a.length()) {
                        val e = a.getJSONObject(i)
                        val color = when (e.optString("kind")) { "ai" -> 0xFFB07CF0.toInt(); "memo" -> Ui.OK; else -> Ui.ACC }
                        val ora = e.optString("ora").split("–")
                        c.add(row().apply {
                            gravity = android.view.Gravity.TOP
                            add(column(0).apply {
                                add(text(ora[0], 11f, Ui.FG).apply { gravity = android.view.Gravity.END; setPadding(0, dp(3), 0, 0) })
                                if (ora.size > 1) add(text(ora[1], 10f, Ui.MUT).apply { gravity = android.view.Gravity.END })
                                layoutParams = LinearLayout.LayoutParams(dp(44), -2)
                            })
                            add(TimelineDot(this@TimelineActivity, color, i == 0, i == a.length() - 1).apply {
                                layoutParams = LinearLayout.LayoutParams(dp(28), -1)
                            })
                            add(text(e.optString("text"), 14f).apply { setPadding(0, dp(2), 0, dp(18)) }, weight = 1f)
                            if (e.has("recording_id")) setOnClickListener {
                                startActivity(android.content.Intent(this@TimelineActivity, ArchiveActivity::class.java)
                                    .putExtra("rec", e.getLong("recording_id")))
                            }
                        })
                    }
                }.onFailure {
                    c.add(text(if (Prefs(this).server.isBlank()) "Imposta server e token in ⚙ per vedere la cronologia."
                               else "PC non raggiungibile (${it.message}).", 14f, Ui.WARN))
                }
            }
        }
    }
}
