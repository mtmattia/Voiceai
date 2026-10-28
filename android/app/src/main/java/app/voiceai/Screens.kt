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
import app.voiceai.Ui.icon
import app.voiceai.Ui.iconButton
import app.voiceai.Ui.setIcon
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
        body = page(if (agenda) "Agenda" else "Da fare") { add(iconButton(R.drawable.ms_refresh) { SyncWorker.now(this@MemoActivity); render() }) }
        render()
    }

    override fun onResume() { super.onResume(); render() }

    private fun changed() { SyncWorker.now(this); MemoReceiver.scheduleAll(this); render() }

    private fun render() {
        body.removeAllViews()
        // aggiunta veloce: una riga, il resto lo compila l'app dalle registrazioni
        val t = input(if (agenda) "Aggiungi un appuntamento…" else "Aggiungi una cosa da fare…")
        fun save(ts: Double?) { if (t.text.isNotBlank()) { db.addMemo(t.text.toString(), ts, "", kind); changed() } }
        body.add(row().apply {
            setPadding(0, 0, 0, dp(12))
            add(t, weight = 1f, marginEnd = 8)
            add(iconButton(R.drawable.ms_schedule) { pickDue { ts -> save(ts) } }, marginEnd = if (agenda) 0 else 8)
            if (!agenda) add(iconButton(R.drawable.ms_checklist) { save(null) })
        })

        val open = db.openMemos(kind)
        if (open.isEmpty()) body.add(text(if (agenda) "Nessun appuntamento in programma: compaiono da soli quando ne parli."
                                           else "Niente in sospeso.", 14f, Ui.MUT).apply { setPadding(dp(4), 0, dp(4), 0) })
        if (agenda) {
            // per giorno: orario a sinistra, titolo e dettagli a destra
            open.groupBy { day(it["due_ts"] as? Double) }.forEach { (d, ms) ->
                body.add(section(d))
                body.add(card().apply { ms.forEach { add(agendaRow(it)) } })
            }
        } else {
            val now = Ui.now()
            val endToday = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, 23); set(Calendar.MINUTE, 59) }.timeInMillis / 1000.0
            val groups = listOf(
                "Oggi" to open.filter { (it["due_ts"] as? Double)?.let { d -> d <= endToday } == true },
                "Prossimi giorni" to open.filter { (it["due_ts"] as? Double)?.let { d -> d > endToday } == true },
                "Quando capita" to open.filter { it["due_ts"] == null })
            groups.filter { it.second.isNotEmpty() }.forEach { (title, ms) ->
                body.add(section(title))
                body.add(card().apply { ms.forEach { add(todoRow(it, now)) } })
            }
        }
        val done = db.doneMemos(kind)
        if (done.isNotEmpty()) {
            body.add(section(if (agenda) "Passati" else "Fatti di recente"))
            body.add(card().apply { done.take(10).forEach { add(if (agenda) agendaRow(it) else todoRow(it, Ui.now())) } })
        }
    }

    private fun day(ts: Double?) = ts?.let {
        java.text.SimpleDateFormat("EEEE d MMMM", java.util.Locale.ITALY).format(java.util.Date((it * 1000).toLong()))
            .replaceFirstChar { c -> c.uppercase() }
    } ?: "Senza data"

    private fun section(title: String) = text(title.uppercase(), 11f, Ui.MUT, bold = true).apply {
        letterSpacing = 0.08f; setPadding(dp(6), dp(10), 0, dp(6))
    }

    /** Tocco lungo: orario, elimina. Niente pulsanti sempre in vista. */
    private fun actions(v: View, uid: String) = v.setOnLongClickListener {
        android.widget.PopupMenu(this, v).apply {
            menu.add("Cambia orario").setOnMenuItemClickListener { pickDue { ts -> db.setMemoDue(uid, ts); changed() }; true }
            menu.add("Elimina").setOnMenuItemClickListener { db.deleteMemo(uid); changed(); true }
            show()
        }
        true
    }

    private fun todoRow(m: Map<String, Any?>, now: Double): View {
        val uid = m["uid"] as String
        val done = m["done"] == 1L
        val r = row().apply { setPadding(0, dp(6), 0, dp(6)); gravity = android.view.Gravity.TOP }
        r.add(CheckBox(this).apply {
            isChecked = done; buttonTintList = android.content.res.ColorStateList.valueOf(Color.WHITE)
            setOnCheckedChangeListener { _, v -> db.setMemoDone(uid, v); changed() }
        })
        val info = column(0).apply { setPadding(0, dp(4), 0, 0) }
        info.add(text(m["text"] as String, 15f, if (done) Ui.MUT else Ui.FG, bold = !done))
        (m["descr"] as? String)?.takeIf { it.isNotBlank() }?.let { info.add(text(it, 13f, Ui.MUT).apply { setPadding(0, dp(2), 0, 0) }) }
        r.add(info, weight = 1f)
        (m["due_ts"] as? Double)?.let { due ->
            val late = !done && due < now
            r.add(text((if (m["due_kind"] == "ipotizzata") "~" else "") + Ui.hm(due), 12f, if (late) Ui.ACC else Ui.MUT)
                .apply { setPadding(dp(8), dp(6), dp(4), 0) })
        }
        actions(r, uid)
        return r
    }

    private fun agendaRow(m: Map<String, Any?>): View {
        val uid = m["uid"] as String
        val r = row().apply { setPadding(0, dp(8), 0, dp(8)); gravity = android.view.Gravity.TOP }
        r.add(text((m["due_ts"] as? Double)?.let { Ui.hm(it) } ?: "—", 15f, Ui.NAME, bold = true)
            .apply { layoutParams = LinearLayout.LayoutParams(dp(58), -2) })
        val info = column(0)
        info.add(text(m["text"] as String, 15f, bold = true))
        (m["descr"] as? String)?.takeIf { it.isNotBlank() }?.let { info.add(text(it, 13f, Ui.MUT).apply { setPadding(0, dp(2), 0, 0) }) }
        r.add(info, weight = 1f)
        actions(r, uid)
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
        body = page("Persone") {
            add(iconButton(R.drawable.ms_mic) { startActivity(android.content.Intent(this@PersonsActivity, VoiceActivity::class.java)) })
        }
        render()
    }

    private fun changed() { SyncWorker.now(this); render() }

    private fun render() {
        body.removeAllViews()
        body.add(text("Tocca ▶ per sentire la voce, tocca il nome per rinominare. \"Nome?\" = nome dedotto dal parlato.",
            13f, Ui.MUT).apply { setPadding(dp(4), 0, dp(4), dp(12)) })
        val all = db.persons()
        if (all.isEmpty()) body.add(text("Nessuna persona ancora: arriveranno dopo le prime registrazioni.", 14f, Ui.MUT))
        all.forEach { p ->
            val id = p["id"] as Long
            val confirmed = p["confirmed"] == 1L
            val owner = p["is_owner"] == 1L
            val name = p["name"] as String
            // una riga per persona: ascolta | nome e dettagli | menu
            body.add(card().apply {
                setPadding(dp(10), dp(10), dp(6), dp(10))
                add(row().apply {
                    lateinit var play: android.widget.ImageView
                    play = iconButton(R.drawable.ms_play_arrow) { listen(id, play) }
                    add(play, marginEnd = 12)
                    add(column(0).apply {
                        add(text(name + if (owner) "  · io" else "", 17f, if (confirmed) Ui.NAME else Ui.WARN, bold = true))
                        val min = ((p["talk_sec"] as? Number)?.toDouble() ?: 0.0) / 60
                        val seen = (p["last_seen"] as? Double)?.let { " · " + Ui.dm(it) } ?: ""
                        add(text("%.0f min di parlato%s".format(min, seen), 12f, Ui.MUT))
                        isClickable = true; setOnClickListener { rename(id, name) }
                    }, weight = 1f)
                    lateinit var more: android.widget.ImageView
                    more = iconButton(R.drawable.ms_more_vert) { menu(more, id, name, confirmed, owner, all) }
                    more.background = null
                    add(more)
                })
            })
        }
    }

    private fun menu(anchor: View, id: Long, name: String, confirmed: Boolean, owner: Boolean, all: List<Map<String, Any?>>) {
        android.widget.PopupMenu(this, anchor).apply {
            menu.add("Rinomina").setOnMenuItemClickListener { rename(id, name); true }
            if (!confirmed) menu.add("Conferma il nome").setOnMenuItemClickListener { db.editPerson(id, confirmed = true); changed(); true }
            if (!owner) menu.add("Sono io").setOnMenuItemClickListener { db.editPerson(id, owner = true); changed(); true }
            menu.add("Stessa persona di…").setOnMenuItemClickListener { merge(id, all); true }
            show()
        }
    }

    private var player: android.media.MediaPlayer? = null

    /** Fa sentire la voce della persona (dal PC) per riconoscerla. */
    private fun listen(id: Long, b: android.widget.ImageView) {
        if (player != null) { stopVoice(); return }
        b.alpha = 0.5f
        io.execute {
            val f = java.io.File(cacheDir, "voce_$id.ogg")
            val r = runCatching { Api(this).voice(id, f) }
            ui.post {
                r.onSuccess {
                    player = android.media.MediaPlayer().apply {
                        setDataSource(f.path); prepare(); start()
                        setOnCompletionListener { stopVoice() }
                    }
                    b.alpha = 1f; b.setImageDrawable(icon(R.drawable.ms_stop))
                }.onFailure {
                    b.alpha = 1f
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
        val body = page("Archivio") { add(iconButton(R.drawable.ms_refresh) { SyncWorker.now(this@ArchiveActivity); recreate() }) }
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
            add(iconButton(R.drawable.ms_event_note) { startActivity(android.content.Intent(this@ArchiveActivity, ReportActivity::class.java).putExtra("day", day)) }, marginEnd = 8)
            add(iconButton(R.drawable.ms_history) { startActivity(android.content.Intent(this@ArchiveActivity, TimelineActivity::class.java).putExtra("day", day)) })
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
                    add(text(Ui.names(f.joinToString("  ·  ") { "${it.first} ${it.second}" }, f.map { it.first }), 12f).apply { setPadding(0, dp(4), 0, 0) })
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
                f.forEach { add(text(Ui.names("${it.first}: ${it.second} — ${it.third}", listOf(it.first)), 14f).apply { setPadding(0, dp(4), 0, 0) }) }
            })
        }
        val c = card()
        var target: View? = null
        val names = db.personNames()
        val owner = db.ownerName()
        db.transcript(rid).forEach { u ->
            val both = u["overlap"] == 1L   // parlavano in due: testo meno affidabile
            val t = text(Ui.line("${Ui.hm(u["ts"] as Double)}  " + if (both) "⇄ " else "", u["speaker"].toString(),
                u["text"].toString(), names, owner), 14f)
                .apply { setPadding(0, dp(4), 0, dp(4)); if (both) alpha = 0.7f }
            if (u["id"] == hl) { t.setBackgroundColor(0xFF5A3B10.toInt()); target = t }
            c.add(t)
        }
        if (db.transcript(rid).any { it["overlap"] == 1L })
            body.add(text("⇄ = parlavano in due insieme: il testo può essere incompleto o di entrambi.", 12f, Ui.MUT)
                .apply { setPadding(dp(4), 0, dp(4), dp(8)) })
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
        // microfono: si applica dal prossimo blocco
        body.add(text("Microfono", 13f, Ui.MUT).apply { setPadding(0, dp(10), 0, dp(4)) })
        val micRow = row()
        val avail = Mic.available(this)
        fun drawMic() {
            micRow.removeAllViews()
            listOf("telefono" to "Telefono", "bluetooth" to "Bluetooth", "usb" to "USB-C").forEachIndexed { i, (k, l) ->
                micRow.add(button(l, primary = p.mic == k) { p.mic = k; drawMic() }.apply { alpha = if (k in avail) 1f else 0.5f },
                    weight = 1f, marginEnd = if (i < 2) 8 else 0)
            }
        }
        drawMic()
        body.add(micRow)
        body.add(text("Bluetooth (cuffie, custodia con microfono) registra come in una chiamata: qualità più bassa e " +
            "pensata per la voce di chi lo indossa. USB-C con il filo dà la qualità piena. Se il microfono scelto non " +
            "è collegato si usa quello del telefono. Sbiadito = non collegato adesso.", 12f, Ui.MUT).apply { setPadding(0, dp(6), 0, 0) })
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
        val body = page("Cronologia $day") { add(iconButton(R.drawable.ms_calendar_month) { pickDay() }) }
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
                    c.add(text(if (Prefs(this).server.isBlank()) "Imposta server e token nelle impostazioni per vedere la cronologia."
                               else "PC non raggiungibile (${it.message}).", 14f, Ui.WARN))
                }
            }
        }
    }
}

/** "Riconosci la mia voce": l'utente legge qualche frase (~25 s), il PC ne ricava l'impronta e lo segna come "io". */
class VoiceActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private var rec: android.media.MediaRecorder? = null
    private val secs = 25

    private val phrases = listOf(
        "Buongiorno, sono io: questa è la mia voce normale, come quando parlo al lavoro.",
        "Oggi devo chiamare il fornitore, preparare i documenti e controllare la consegna di venerdì.",
        "Ventitré, quarantotto, centocinquanta: i numeri li dico così, senza fretta.",
        "Se c'è rumore intorno va bene lo stesso, basta che parli con il mio tono di sempre.")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val body = page("La mia voce")
        val db = LocalDb.get(this)
        body.add(text("Leggi ad alta voce queste frasi, con calma e con la tua voce di sempre. Servono circa $secs secondi: " +
            "il PC impara a riconoscerti e ti distingue meglio dagli altri.", 14f, Ui.MUT).apply { setPadding(dp(4), 0, dp(4), dp(12)) })
        body.add(card().apply { phrases.forEach { add(text("“$it”", 16f).apply { setPadding(0, dp(6), 0, dp(6)) }) } })
        val name = input("Il tuo nome", db.ownerName() ?: "", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_WORDS)
        body.add(name)
        val status = text("", 15f).apply { gravity = android.view.Gravity.CENTER; setPadding(0, dp(14), 0, dp(10)) }
        lateinit var go: android.widget.Button
        go = button("Inizia a leggere", primary = true, iconRes = R.drawable.ms_mic) {
            if (rec != null) return@button
            if (RecorderService.running) { status.text = "Ferma prima la registrazione in corso."; return@button }
            if (name.text.isBlank()) { status.text = "Scrivi il tuo nome."; return@button }
            start(java.io.File(cacheDir, "mia_voce.ogg"), status, go, name.text.toString().trim())
        }
        body.add(go.apply { layoutParams = LinearLayout.LayoutParams(-1, dp(52)).apply { topMargin = dp(16) } })
        body.add(status)
    }

    private fun start(f: java.io.File, status: android.widget.TextView, go: android.widget.Button, name: String) {
        rec = android.media.MediaRecorder(this).apply {
            setAudioSource(android.media.MediaRecorder.AudioSource.VOICE_RECOGNITION)
            setOutputFormat(android.media.MediaRecorder.OutputFormat.OGG)
            setAudioEncoder(android.media.MediaRecorder.AudioEncoder.OPUS)
            setAudioChannels(1); setAudioSamplingRate(48000); setAudioEncodingBitRate(48000)
            setOutputFile(f.path); prepare(); start()
        }
        go.isEnabled = false
        var left = secs
        ui.post(object : Runnable {
            override fun run() {
                if (left > 0) { status.text = "Sto ascoltando… $left s"; left--; ui.postDelayed(this, 1000); return }
                rec?.runCatching { stop() }; rec?.release(); rec = null
                status.text = "Invio al PC e riconosco la voce…"
                Executors.newSingleThreadExecutor().execute {
                    val r = runCatching { Api(this@VoiceActivity).myVoice(f, name) }
                    ui.post {
                        go.isEnabled = true
                        r.onSuccess { j ->
                            status.text = if (j.optBoolean("gia_presente")) "Fatto ✓ Eri già nell'archivio: ora sei \"${j.optString("name")}\" e le tue frasi passate sono tue."
                                          else "Fatto ✓ Ti riconoscerò dalle prossime registrazioni."
                            SyncWorker.now(this@VoiceActivity)
                        }.onFailure { status.text = "Non riuscito: ${it.message}" }
                    }
                }
            }
        })
    }

    override fun onPause() { super.onPause(); rec?.runCatching { stop() }; rec?.release(); rec = null }
}

/** Resoconto della giornata per situazioni: quando, con chi, in che contesto, cosa è successo. Offline. */
class ReportActivity : Activity() {
    private var day = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent.getStringExtra("day")?.let { day = it }
        render()
    }

    override fun onResume() { super.onResume(); render() }

    private fun pickDay() {
        val c = Calendar.getInstance().apply { time = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).parse(day)!! }
        DatePickerDialog(this, { _, y, m, d -> day = "%04d-%02d-%02d".format(y, m + 1, d); render() },
            c.get(Calendar.YEAR), c.get(Calendar.MONTH), c.get(Calendar.DAY_OF_MONTH)).show()
    }

    private fun render() {
        val db = LocalDb.get(this)
        val label = java.text.SimpleDateFormat("EEEE d MMMM", java.util.Locale.ITALY)
            .format(java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).parse(day)!!)
            .replaceFirstChar { it.uppercase() }
        val body = page(label) {
            add(iconButton(R.drawable.ms_history) {
                startActivity(android.content.Intent(this@ReportActivity, TimelineActivity::class.java).putExtra("day", day))
            }, marginEnd = 8)
            add(iconButton(R.drawable.ms_calendar_month) { pickDay() })
        }
        val names = db.personNames()
        val d = db.day(day)
        if (d?.get("mood") != null) body.add(card().apply {
            val mood = (d["mood_score"] as? Long)?.toInt()?.let { Ui.MOODS[it] } ?: ""
            add(text("$mood ${d["mood"]}", 18f, bold = true))
            add(text(d["mood_reason"] as? String ?: "", 13f, Ui.MUT))
            (d["tip"] as? String)?.let { add(text(it, 14f, Ui.OK).apply { setPadding(0, dp(8), 0, 0) }) }
        })
        val sits = runCatching { JSONArray(d?.get("situations") as? String ?: "[]") }.getOrDefault(JSONArray())
        if (sits.length() == 0) {
            body.add(text(if (db.recordingsOf(day).isEmpty()) "Nessuna registrazione in questo giorno."
                          else "Il resoconto si prepara una decina di minuti dopo l'ultimo blocco registrato.", 14f, Ui.MUT)
                .apply { setPadding(dp(4), dp(8), dp(4), 0) })
            return
        }
        for (i in 0 until sits.length()) {
            val s = sits.getJSONObject(i)
            body.add(card().apply {
                add(row().apply {
                    add(text("${s.optString("dalle")}–${s.optString("alle")}", 12f, Ui.MUT), weight = 1f)
                    s.optString("contesto").takeIf { it.isNotBlank() }?.let {
                        add(text(it, 11f, Color.WHITE).apply {
                            setPadding(dp(8), dp(2), dp(8), dp(2)); background = bg(0x33FFFFFF, dp(10).toFloat())
                        })
                    }
                })
                add(text(s.optString("titolo"), 17f, bold = true).apply { setPadding(0, dp(4), 0, dp(2)) })
                val who = s.optJSONArray("persone")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
                if (who.isNotEmpty()) add(text(Ui.names("con " + who.joinToString(", "), who), 13f))
                add(text(Ui.names(s.optString("sintesi"), names + who), 14f).apply { setPadding(0, dp(6), 0, 0) })
                setOnClickListener {
                    startActivity(android.content.Intent(this@ReportActivity, ArchiveActivity::class.java).putExtra("day", day))
                }
            })
        }
    }
}
