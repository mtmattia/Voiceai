package app.voiceai

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Copia locale dell'archivio: trascrizioni, giornate, persone, memo. Consultabile offline.
 * persons/memos hanno dirty=1 finché la modifica locale non è stata inviata al PC.
 */
class LocalDb private constructor(ctx: Context) : SQLiteOpenHelper(ctx, "voiceai.db", null, 4) {
    companion object {
        @Volatile private var inst: LocalDb? = null
        fun get(ctx: Context) = inst ?: synchronized(this) { inst ?: LocalDb(ctx.applicationContext).also { inst = it } }
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""CREATE TABLE persons(id INTEGER PRIMARY KEY, name TEXT, confirmed INTEGER, is_owner INTEGER,
            notes TEXT, updated_at REAL, dirty INTEGER DEFAULT 0)""")
        db.execSQL("""CREATE TABLE memos(uid TEXT PRIMARY KEY, text TEXT, due_ts REAL, due_kind TEXT, moment TEXT,
            done INTEGER DEFAULT 0, done_ts REAL, source TEXT, recording_id INTEGER, created REAL, updated_at REAL,
            dirty INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, descr TEXT DEFAULT '', kind TEXT DEFAULT 'todo')""")
        db.execSQL("""CREATE TABLE recordings(id INTEGER PRIMARY KEY, day TEXT, started_at REAL, duration REAL,
            status TEXT, title TEXT, keywords TEXT, feelings TEXT, summary TEXT)""")
        db.execSQL("""CREATE TABLE utterances(id INTEGER PRIMARY KEY, recording_id INTEGER, ts REAL, start REAL,
            stop REAL, label TEXT, person_id INTEGER, text TEXT, overlap INTEGER DEFAULT 0)""")
        db.execSQL("""CREATE TABLE days(day TEXT PRIMARY KEY, keywords TEXT, summary TEXT, mood TEXT,
            mood_score INTEGER, mood_reason TEXT, tip TEXT, situations TEXT)""")
        db.execSQL("""CREATE VIRTUAL TABLE utt_fts USING fts4(text, tokenize=unicode61 "remove_diacritics=1")""")
        db.execSQL("CREATE INDEX utt_rec ON utterances(recording_id)")
        db.execSQL("CREATE INDEX utt_ts ON utterances(ts)")
    }

    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
        if (old < 2) {   // todo con descrizione, agenda, sensazioni degli interlocutori
            db.execSQL("ALTER TABLE memos ADD COLUMN descr TEXT DEFAULT ''")
            db.execSQL("ALTER TABLE memos ADD COLUMN kind TEXT DEFAULT 'todo'")
            db.execSQL("ALTER TABLE recordings ADD COLUMN feelings TEXT")
        }
        if (old < 3) {   // resoconto: sintesi per blocco e situazioni della giornata
            db.execSQL("ALTER TABLE recordings ADD COLUMN summary TEXT")
            db.execSQL("ALTER TABLE days ADD COLUMN situations TEXT")
        }
        if (old < 4) db.execSQL("ALTER TABLE utterances ADD COLUMN overlap INTEGER DEFAULT 0")   // due voci insieme
    }

    // ---------- helpers ----------
    private fun Cursor.rows(): List<Map<String, Any?>> = use {
        val out = mutableListOf<Map<String, Any?>>()
        while (moveToNext()) {
            out += (0 until columnCount).associate { i ->
                columnNames[i] to when (getType(i)) {
                    Cursor.FIELD_TYPE_INTEGER -> getLong(i)
                    Cursor.FIELD_TYPE_FLOAT -> getDouble(i)
                    Cursor.FIELD_TYPE_NULL -> null
                    else -> getString(i)
                }
            }
        }
        out
    }

    fun q(sql: String, vararg args: Any?): List<Map<String, Any?>> =
        readableDatabase.rawQuery(sql, args.map { it?.toString() }.toTypedArray()).rows()

    private fun JSONObject.cv(vararg cols: String) = ContentValues().apply {
        for (c in cols) {
            when (val v = opt(c)) {
                null, JSONObject.NULL -> putNull(c)
                is Int -> put(c, v.toLong())
                is Long -> put(c, v)
                is Double -> put(c, v)
                is Boolean -> put(c, if (v) 1L else 0L)
                else -> put(c, v.toString())
            }
        }
    }

    private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }

    // ---------- applicazione dei dati dal PC ----------
    /** Righe dal server. Per persons/memos: se ho una modifica locale più recente non ancora inviata, vince la mia. */
    fun applyPull(r: JSONObject) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            r.optJSONArray("persons")?.objects()?.forEach { applyPerson(db, it) }
            r.optJSONArray("memos")?.objects()?.forEach { applyMemo(db, it) }
            r.optJSONArray("recordings")?.objects()?.forEach {
                db.insertWithOnConflict("recordings", null,
                    it.cv("id", "day", "started_at", "duration", "status", "title", "keywords", "feelings", "summary"), SQLiteDatabase.CONFLICT_REPLACE)
            }
            r.optJSONArray("utterances")?.objects()?.forEach {
                val cv = it.cv("id", "recording_id", "ts", "start", "label", "person_id", "text", "overlap")
                cv.put("stop", it.optDouble("end", 0.0))
                db.insertWithOnConflict("utterances", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
                db.execSQL("DELETE FROM utt_fts WHERE docid=?", arrayOf(it.getLong("id")))
                db.execSQL("INSERT INTO utt_fts(docid, text) VALUES(?,?)", arrayOf(it.getLong("id"), it.optString("text")))
            }
            r.optJSONArray("days")?.objects()?.forEach {
                db.insertWithOnConflict("days", null,
                    it.cv("day", "keywords", "summary", "mood", "mood_score", "mood_reason", "tip", "situations"), SQLiteDatabase.CONFLICT_REPLACE)
            }
            r.optJSONArray("deleted")?.objects()?.forEach { d ->
                val key = d.getString("key")
                when (d.getString("tbl")) {
                    "persons" -> db.delete("persons", "id=?", arrayOf(key))
                    "memos" -> db.delete("memos", "uid=?", arrayOf(key))
                    "recordings" -> db.delete("recordings", "id=?", arrayOf(key))
                    "utterances" -> { db.delete("utterances", "id=?", arrayOf(key)); db.execSQL("DELETE FROM utt_fts WHERE docid=?", arrayOf(key)) }
                    "days" -> db.delete("days", "day=?", arrayOf(key))
                }
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    private fun localNewer(db: SQLiteDatabase, table: String, keyCol: String, key: String, remoteTs: Double): Boolean {
        db.rawQuery("SELECT updated_at FROM $table WHERE $keyCol=? AND dirty=1", arrayOf(key)).use {
            return it.moveToFirst() && it.getDouble(0) > remoteTs
        }
    }

    private fun applyPerson(db: SQLiteDatabase, p: JSONObject) {
        if (localNewer(db, "persons", "id", p.getLong("id").toString(), p.optDouble("updated_at", 0.0))) return
        val cv = p.cv("id", "name", "confirmed", "is_owner", "notes", "updated_at").apply { put("dirty", 0) }
        db.insertWithOnConflict("persons", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
    }

    private fun applyMemo(db: SQLiteDatabase, m: JSONObject) {
        if (localNewer(db, "memos", "uid", m.getString("uid"), m.optDouble("updated_at", 0.0))) return
        val cv = m.cv("uid", "text", "descr", "kind", "due_ts", "due_kind", "moment", "done", "done_ts", "source", "recording_id",
            "created", "updated_at").apply { put("dirty", 0); put("deleted", 0) }
        db.insertWithOnConflict("memos", null, cv, SQLiteDatabase.CONFLICT_REPLACE)
    }

    // ---------- modifiche locali (offline) ----------
    fun editPerson(id: Long, name: String? = null, confirmed: Boolean? = null, owner: Boolean? = null) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            if (owner == true) db.execSQL("UPDATE persons SET is_owner=0, dirty=1, updated_at=? WHERE is_owner=1 AND id<>?",
                arrayOf(Ui.now(), id))
            val cv = ContentValues().apply {
                name?.let { put("name", it.trim().take(80)); put("confirmed", 1) }
                confirmed?.let { put("confirmed", if (it) 1 else 0) }
                owner?.let { put("is_owner", if (it) 1 else 0) }
                put("updated_at", Ui.now()); put("dirty", 1)
            }
            db.update("persons", cv, "id=?", arrayOf(id.toString()))
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }

    fun addMemo(text: String, dueTs: Double?, descr: String = "", kind: String = "todo"): String {
        val uid = UUID.randomUUID().toString().replace("-", "")
        val now = Ui.now()
        writableDatabase.insert("memos", null, ContentValues().apply {
            put("uid", uid); put("text", text.trim().take(200)); put("source", "manuale")
            put("descr", descr.trim().take(1000)); put("kind", kind)
            if (dueTs != null) { put("due_ts", dueTs); put("due_kind", "esplicita") }
            put("created", now); put("updated_at", now); put("dirty", 1)
        })
        return uid
    }

    fun setMemoDone(uid: String, done: Boolean) {
        writableDatabase.update("memos", ContentValues().apply {
            put("done", if (done) 1 else 0)
            if (done) put("done_ts", Ui.now()) else putNull("done_ts")
            put("updated_at", Ui.now()); put("dirty", 1)
        }, "uid=?", arrayOf(uid))
    }

    fun setMemoDue(uid: String, dueTs: Double) {
        writableDatabase.update("memos", ContentValues().apply {
            put("due_ts", dueTs); put("due_kind", "esplicita"); put("updated_at", Ui.now()); put("dirty", 1)
        }, "uid=?", arrayOf(uid))
    }

    fun deleteMemo(uid: String) {
        writableDatabase.update("memos", ContentValues().apply {
            put("deleted", 1); put("updated_at", Ui.now()); put("dirty", 1)
        }, "uid=?", arrayOf(uid))
    }

    /** Modifiche da inviare. */
    fun dirty(): JSONObject {
        val persons = JSONArray()
        q("SELECT id, name, confirmed, is_owner, notes, updated_at FROM persons WHERE dirty=1").forEach { persons.put(JSONObject(it)) }
        val memos = JSONArray()
        q("""SELECT uid, text, descr, kind, due_ts, due_kind, moment, done, done_ts, created, updated_at, deleted
             FROM memos WHERE dirty=1""").forEach { m -> memos.put(JSONObject(m).put("deleted", m["deleted"] == 1L)) }
        return JSONObject().put("persons", persons).put("memos", memos)
    }

    /** Dopo il push: applico lo stato vincente restituito dal server. */
    fun applyPushResult(sent: JSONObject, r: JSONObject) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            // le righe inviate non sono più "mie" in sospeso, a meno che non le abbia ritoccate nel frattempo
            sent.getJSONArray("persons").objects().forEach {
                db.execSQL("UPDATE persons SET dirty=0 WHERE id=? AND updated_at=?", arrayOf(it.getLong("id"), it.getDouble("updated_at")))
            }
            sent.getJSONArray("memos").objects().forEach {
                db.execSQL("UPDATE memos SET dirty=0 WHERE uid=? AND updated_at=?", arrayOf(it.getString("uid"), it.getDouble("updated_at")))
            }
            r.optJSONArray("persons")?.objects()?.forEach { applyPerson(db, it) }
            r.optJSONArray("memos")?.objects()?.forEach { applyMemo(db, it) }
            r.optJSONArray("deleted_memos")?.let { a ->
                for (i in 0 until a.length()) db.delete("memos", "uid=? AND dirty=0", arrayOf(a.getString(i)))
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }

    // ---------- letture per l'interfaccia ----------
    /** Ricerca per parole (offline): "stampa pannello" -> frasi che contengono stampa* E pannello*. */
    fun search(query: String, limit: Int = 100): List<Map<String, Any?>> {
        val terms = query.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.length >= 2 }
        if (terms.isEmpty()) return emptyList()
        val match = terms.joinToString(" ") { "$it*" }
        return q("""SELECT u.id, u.ts, u.text, u.recording_id, r.day, COALESCE(p.name, u.label) AS speaker
                    FROM utt_fts f JOIN utterances u ON u.id=f.docid
                    LEFT JOIN recordings r ON r.id=u.recording_id
                    LEFT JOIN persons p ON p.id=u.person_id
                    WHERE utt_fts MATCH ? ORDER BY u.ts DESC LIMIT $limit""", match)
    }

    fun searchKeywords(query: String) = q("""SELECT id, day, started_at, title, keywords FROM recordings
        WHERE lower(keywords) LIKE ? OR lower(title) LIKE ? ORDER BY started_at DESC LIMIT 30""",
        "%${query.lowercase().trim()}%", "%${query.lowercase().trim()}%")

    fun openMemos(kind: String = "todo") =
        q("SELECT * FROM memos WHERE done=0 AND deleted=0 AND kind=? ORDER BY due_ts IS NULL, due_ts, created", kind)
    fun doneMemos(kind: String = "todo") =
        q("SELECT * FROM memos WHERE done=1 AND deleted=0 AND kind=? ORDER BY done_ts DESC LIMIT 20", kind)
    fun persons() = q("""SELECT p.*, (SELECT COALESCE(SUM(stop-start),0) FROM utterances WHERE person_id=p.id) AS talk_sec,
        (SELECT MAX(ts) FROM utterances WHERE person_id=p.id) AS last_seen FROM persons p ORDER BY confirmed DESC, talk_sec DESC""")
    fun days() = q("""SELECT d.day, d.mood, d.mood_score, d.keywords,
        (SELECT COUNT(*) FROM recordings r WHERE r.day=d.day) AS n_rec FROM days d ORDER BY d.day DESC""")
    fun day(day: String) = q("SELECT * FROM days WHERE day=?", day).firstOrNull()
    fun recordingsOf(day: String) = q("SELECT * FROM recordings WHERE day=? ORDER BY started_at", day)
    fun transcript(rid: Long) = q("""SELECT u.id, u.ts, u.text, u.overlap, COALESCE(p.name, u.label) AS speaker FROM utterances u
        LEFT JOIN persons p ON p.id=u.person_id WHERE recording_id=? ORDER BY start""", rid)
    /** Nomi delle persone (per evidenziarli) e nome dell'utente. */
    fun personNames() = q("SELECT name FROM persons").mapNotNull { (it["name"] as? String)?.takeUnless { n -> n.startsWith("Sconosciuto") } }
    fun ownerName() = q("SELECT name FROM persons WHERE is_owner=1").firstOrNull()?.get("name") as? String

    fun recording(rid: Long) = q("SELECT * FROM recordings WHERE id=?", rid).firstOrNull()
}

/** Un giro di sincronizzazione: push delle modifiche locali, poi pull incrementale a pagine. */
class Sync(private val ctx: Context) {
    fun run() {
        val db = LocalDb.get(ctx)
        val api = Api(ctx)
        val prefs = Prefs(ctx)
        val sent = db.dirty()
        if (sent.getJSONArray("persons").length() + sent.getJSONArray("memos").length() > 0) {
            db.applyPushResult(sent, api.push(sent))
        }
        var since = prefs.lastRev
        do {
            val r = api.pull(since)
            db.applyPull(r)
            since = r.getLong("rev")
            prefs.lastRev = since
        } while (r.optBoolean("more"))
        prefs.lastSync = System.currentTimeMillis()
    }
}
