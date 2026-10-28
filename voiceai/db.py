"""Archivio SQLite: registrazioni, persone, impronte, frasi (ricerca FTS5), giornate."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS persons(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, confirmed INTEGER DEFAULT 0,
  is_owner INTEGER DEFAULT 0, notes TEXT DEFAULT '', created REAL, rev INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS prints(
  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
  emb TEXT NOT NULL, source TEXT, manual INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS name_votes(
  person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE, name TEXT, votes REAL,
  PRIMARY KEY(person_id, name));
CREATE TABLE IF NOT EXISTS recordings(
  id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE, device TEXT, session TEXT, idx INTEGER,
  started_at REAL, day TEXT, path TEXT, size INTEGER, duration REAL,
  status TEXT DEFAULT 'pending', error TEXT, title TEXT, keywords TEXT, memos_done INTEGER DEFAULT 0,
  received REAL, rev INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS utterances(
  id INTEGER PRIMARY KEY, recording_id INTEGER REFERENCES recordings(id) ON DELETE CASCADE,
  ts REAL, start REAL, end REAL, label TEXT, person_id INTEGER, text TEXT, rev INTEGER, updated_at REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS utt_fts USING fts5(text, content='utterances', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS utt_ai AFTER INSERT ON utterances BEGIN
  INSERT INTO utt_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS utt_ad AFTER DELETE ON utterances BEGIN
  INSERT INTO utt_fts(utt_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER IF NOT EXISTS utt_au AFTER UPDATE OF text ON utterances BEGIN
  INSERT INTO utt_fts(utt_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO utt_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TABLE IF NOT EXISTS days(
  day TEXT PRIMARY KEY, dirty INTEGER DEFAULT 1, last_activity REAL, keywords TEXT,
  summary TEXT, mood TEXT, mood_score INTEGER, mood_reason TEXT, tip TEXT, updated REAL,
  rev INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS memos(
  id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL,
  recording_id INTEGER REFERENCES recordings(id) ON DELETE SET NULL, text TEXT NOT NULL, due_ts REAL, due_kind TEXT, moment TEXT, done INTEGER DEFAULT 0, done_ts REAL,
  source TEXT DEFAULT 'auto', notified INTEGER DEFAULT 0, created REAL, rev INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS asks(id INTEGER PRIMARY KEY, ts REAL, question TEXT);
CREATE TABLE IF NOT EXISTS counter(v INTEGER);
INSERT INTO counter SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM counter);
CREATE TABLE IF NOT EXISTS tombstones(tbl TEXT, key TEXT, rev INTEGER);
CREATE INDEX IF NOT EXISTS utt_person ON utterances(person_id);
CREATE INDEX IF NOT EXISTS utt_ts ON utterances(ts);
CREATE INDEX IF NOT EXISTS rec_status ON recordings(status);
"""

# rev = contatore globale del server (cosa è cambiato da quando il telefono ha sincronizzato);
# updated_at = ora della modifica (chi ha modificato più di recente vince). Gestiti da trigger.
NOW = "((julianday('now') - 2440587.5) * 86400.0)"
SYNC_TABLES = {"persons": "id", "memos": "uid", "recordings": "id", "utterances": "id", "days": "day"}
for _t, _k in SYNC_TABLES.items():
    SCHEMA += f"""
CREATE INDEX IF NOT EXISTS {_t}_rev ON {_t}(rev);
CREATE TRIGGER IF NOT EXISTS {_t}_sync_i AFTER INSERT ON {_t} BEGIN
  UPDATE counter SET v=v+1;
  UPDATE {_t} SET rev=(SELECT v FROM counter), updated_at=COALESCE(NEW.updated_at, {NOW}) WHERE rowid=NEW.rowid; END;
CREATE TRIGGER IF NOT EXISTS {_t}_sync_u AFTER UPDATE ON {_t} WHEN NEW.rev IS OLD.rev BEGIN
  UPDATE counter SET v=v+1;
  UPDATE {_t} SET rev=(SELECT v FROM counter),
    updated_at=CASE WHEN NEW.updated_at IS OLD.updated_at THEN {NOW} ELSE NEW.updated_at END WHERE rowid=NEW.rowid; END;
CREATE TRIGGER IF NOT EXISTS {_t}_sync_d AFTER DELETE ON {_t} BEGIN
  UPDATE counter SET v=v+1;
  INSERT INTO tombstones VALUES('{_t}', OLD.{_k}, (SELECT v FROM counter)); END;
"""


MIGRATIONS = [("utterances", "raw", "TEXT"),                       # testo di Whisper prima della correzione LLM
              ("recordings", "corrected", "INTEGER DEFAULT 0"),
              ("recordings", "feelings", "TEXT"),                    # JSON: come sembrava sentirsi ogni interlocutore
              ("memos", "descr", "TEXT DEFAULT ''"),                 # text = titolo breve, descr = dettagli
              ("memos", "kind", "TEXT DEFAULT 'todo'")]              # todo | agenda (appuntamento con data e ora)


def _kind(m):
    return "agenda" if m.get("kind") == "agenda" else "todo"


class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.tx() as c:
            c.executescript(SCHEMA)
            # colonne aggiunte dopo: archivi già esistenti
            for tbl, col, decl in MIGRATIONS:
                if col not in {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")}:
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")

    @contextmanager
    def tx(self):
        with self.lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            try:
                yield c
                c.commit()
            finally:
                c.close()

    def q(self, sql, args=()):
        with self.tx() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    # --- persone ---
    def new_person(self, name=None, confirmed=False, owner=False):
        with self.tx() as c:
            cur = c.execute("INSERT INTO persons(name, confirmed, is_owner, created) VALUES(?,?,?,?)",
                            (name or "", int(confirmed), int(owner), time.time()))
            pid = cur.lastrowid
            if not name:
                c.execute("UPDATE persons SET name=? WHERE id=?", (f"Sconosciuto {pid}", pid))
            return pid

    def add_print(self, pid, emb, source="", manual=False, cap=20):
        with self.tx() as c:
            c.execute("INSERT INTO prints(person_id, emb, source, manual, created) VALUES(?,?,?,?,?)",
                      (pid, json.dumps([round(float(x), 6) for x in emb]), source, int(manual), time.time()))
            # tiene le impronte manuali + le cap automatiche più recenti
            c.execute("""DELETE FROM prints WHERE person_id=? AND manual=0 AND id NOT IN
                         (SELECT id FROM prints WHERE person_id=? AND manual=0 ORDER BY id DESC LIMIT ?)""",
                      (pid, pid, cap))

    def prints(self):
        out = {}
        for r in self.q("SELECT person_id, emb FROM prints"):
            out.setdefault(r["person_id"], []).append(json.loads(r["emb"]))
        return out

    def update_person(self, pid, **kw):
        allowed = {k: v for k, v in kw.items() if k in ("name", "confirmed", "is_owner", "notes")}
        if not allowed:
            return
        with self.tx() as c:
            if allowed.get("is_owner"):
                c.execute("UPDATE persons SET is_owner=0")
            c.execute(f"UPDATE persons SET {', '.join(k + '=?' for k in allowed)} WHERE id=?",
                      (*allowed.values(), pid))

    def merge_persons(self, src, dst):
        """Sposta impronte, frasi e voti di src su dst, poi cancella src."""
        with self.tx() as c:
            c.execute("UPDATE prints SET person_id=? WHERE person_id=?", (dst, src))
            c.execute("UPDATE utterances SET person_id=? WHERE person_id=?", (dst, src))
            for r in c.execute("SELECT name, votes FROM name_votes WHERE person_id=?", (src,)).fetchall():
                c.execute("""INSERT INTO name_votes VALUES(?,?,?) ON CONFLICT(person_id,name)
                             DO UPDATE SET votes=votes+excluded.votes""", (dst, r["name"], r["votes"]))
            c.execute("UPDATE persons SET is_owner=MAX(is_owner, (SELECT is_owner FROM persons WHERE id=?)) "
                      "WHERE id=?", (src, dst))
            c.execute("DELETE FROM persons WHERE id=?", (src,))

    def delete_person(self, pid):
        with self.tx() as c:
            c.execute("UPDATE utterances SET person_id=NULL WHERE person_id=?", (pid,))
            c.execute("DELETE FROM persons WHERE id=?", (pid,))

    def add_votes(self, pid, name, n):
        with self.tx() as c:
            c.execute("""INSERT INTO name_votes VALUES(?,?,?) ON CONFLICT(person_id,name)
                         DO UPDATE SET votes=votes+excluded.votes""", (pid, name, n))

    def persons(self):
        rows = self.q("""SELECT p.*, (SELECT COUNT(*) FROM prints WHERE person_id=p.id) AS n_prints,
                         (SELECT COALESCE(SUM(end-start),0) FROM utterances WHERE person_id=p.id) AS talk_sec,
                         (SELECT MAX(ts) FROM utterances WHERE person_id=p.id) AS last_seen
                         FROM persons p ORDER BY confirmed DESC, talk_sec DESC""")
        votes = {}
        for v in self.q("SELECT * FROM name_votes ORDER BY votes DESC"):
            votes.setdefault(v["person_id"], []).append({"name": v["name"], "votes": v["votes"]})
        for r in rows:
            r["name_votes"] = votes.get(r["id"], [])[:5]
        return rows

    def owner(self):
        return self.one("SELECT * FROM persons WHERE is_owner=1")

    # --- ricerca ---
    def search(self, query, limit=50):
        q = " ".join(f'"{w}"' for w in query.replace('"', " ").split())
        if not q:
            return []
        return self.q("""SELECT u.id, u.ts, u.text, u.recording_id, r.day, r.title,
                         COALESCE(p.name, u.label) AS speaker
                         FROM utt_fts f JOIN utterances u ON u.id=f.rowid
                         JOIN recordings r ON r.id=u.recording_id
                         LEFT JOIN persons p ON p.id=u.person_id
                         WHERE utt_fts MATCH ? ORDER BY u.ts DESC LIMIT ?""", (q, limit))

    # --- sync con il telefono ---
    PULL_COLS = {
        "persons": "id, name, confirmed, is_owner, notes, rev, updated_at",
        "memos": "uid, text, descr, kind, due_ts, due_kind, moment, done, done_ts, source, recording_id, created, rev, updated_at",
        "recordings": "id, day, started_at, duration, status, title, keywords, feelings, rev, updated_at",
        "utterances": "id, recording_id, ts, start, end, label, person_id, text, rev, updated_at",
        "days": "day, keywords, summary, mood, mood_score, mood_reason, tip, rev, updated_at",
    }

    KEYS = {"persons": "id", "memos": "uid", "recordings": "id", "utterances": "id", "days": "day"}

    def pull(self, since, limit=2000):
        rows = []
        for t, cols in self.PULL_COLS.items():
            rows += [("t", t, r) for r in self.q(f"SELECT {cols} FROM {t} WHERE rev > ? ORDER BY rev LIMIT ?",
                                                     (since, limit))]
        rows += [("d", r["tbl"], r) for r in self.q("SELECT * FROM tombstones WHERE rev > ? ORDER BY rev LIMIT ?",
                                                    (since, limit))]
        rows.sort(key=lambda x: x[2]["rev"])
        more = len(rows) > limit
        rows = rows[:limit]
        last = rows[-1][2]["rev"] if rows else since
        # una chiave cancellata e poi riusata (gli id interi di SQLite si riciclano) non va cancellata sul
        # telefono: nella stessa pagina la riga nuova vince sulla lapide più vecchia
        live = {(t, str(r[self.KEYS[t]])): r["rev"] for kind, t, r in rows if kind == "t"}
        rows = [x for x in rows if x[0] == "t" or live.get((x[1], str(x[2]["key"])), -1) < x[2]["rev"]]
        out = {t: [] for t in self.PULL_COLS} | {"deleted": []}
        for kind, t, r in rows:
            (out["deleted"] if kind == "d" else out[t]).append(r)
        out.update(rev=last, more=more)
        return out

    def push(self, data):
        """Modifiche dal telefono: si applicano solo se più recenti di quelle già presenti (last-write-wins)."""
        applied, stale = 0, 0
        with self.tx() as c:
            for p in data.get("persons", []):
                cur = c.execute("SELECT updated_at FROM persons WHERE id=?", (p.get("id"),)).fetchone()
                if not cur:
                    continue
                if float(p.get("updated_at") or 0) <= (cur["updated_at"] or 0):
                    stale += 1
                    continue
                if p.get("is_owner"):
                    c.execute("UPDATE persons SET is_owner=0 WHERE is_owner=1 AND id<>?", (p["id"],))
                c.execute("UPDATE persons SET name=?, confirmed=?, is_owner=?, notes=?, updated_at=? WHERE id=?",
                          (str(p.get("name") or "Senza nome")[:80], int(bool(p.get("confirmed"))),
                           int(bool(p.get("is_owner"))), str(p.get("notes") or "")[:2000],
                           float(p["updated_at"]), p["id"]))
                applied += 1
            for m in data.get("memos", []):
                uid, ts = str(m.get("uid") or ""), float(m.get("updated_at") or 0)
                if not uid:
                    continue
                cur = c.execute("SELECT updated_at FROM memos WHERE uid=?", (uid,)).fetchone()
                gone = c.execute("SELECT 1 FROM tombstones WHERE tbl='memos' AND key=?", (uid,)).fetchone()
                if (cur and ts <= (cur["updated_at"] or 0)) or (not cur and gone):
                    stale += 1
                    continue
                if m.get("deleted"):
                    c.execute("DELETE FROM memos WHERE uid=?", (uid,))
                elif cur:
                    c.execute("""UPDATE memos SET text=?, descr=?, kind=?, due_ts=?, due_kind=?, done=?, done_ts=?,
                                 updated_at=? WHERE uid=?""",
                              (str(m.get("text") or "")[:200], str(m.get("descr") or "")[:1000], _kind(m),
                               m.get("due_ts"), m.get("due_kind"), int(bool(m.get("done"))), m.get("done_ts"), ts, uid))
                else:
                    c.execute("""INSERT INTO memos(uid, text, descr, kind, due_ts, due_kind, moment, done, done_ts,
                                 source, created, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (uid, str(m.get("text") or "")[:200], str(m.get("descr") or "")[:1000], _kind(m),
                               m.get("due_ts"), m.get("due_kind"),
                               m.get("moment"), int(bool(m.get("done"))), m.get("done_ts"), "manuale",
                               float(m.get("created") or ts), ts))
                applied += 1
        # stato attuale delle righe toccate: il telefono lo applica così non resta con una versione scartata
        pids = [p.get("id") for p in data.get("persons", [])]
        uids = [str(m.get("uid") or "") for m in data.get("memos", [])]
        ph = lambda xs: ",".join("?" * len(xs))
        cur_p = self.q(f"SELECT {self.PULL_COLS['persons']} FROM persons WHERE id IN ({ph(pids)})", pids) if pids else []
        cur_m = self.q(f"SELECT {self.PULL_COLS['memos']} FROM memos WHERE uid IN ({ph(uids)})", uids) if uids else []
        gone = sorted(set(uids) - {m["uid"] for m in cur_m})
        return {"applied": applied, "stale": stale, "persons": cur_p, "memos": cur_m, "deleted_memos": gone}

    def search_any(self, words, limit=40):
        """OR delle parole, ordinate per rilevanza (bm25): per le domande in linguaggio naturale."""
        q = " OR ".join(f'"{w}"' for w in words)
        if not q:
            return []
        return self.q("""SELECT u.id, u.ts, u.text, u.recording_id, r.day, COALESCE(p.name, u.label) AS speaker
                         FROM utt_fts f JOIN utterances u ON u.id=f.rowid
                         JOIN recordings r ON r.id=u.recording_id
                         LEFT JOIN persons p ON p.id=u.person_id
                         WHERE utt_fts MATCH ? ORDER BY bm25(utt_fts) LIMIT ?""", (q, limit))

    def search_keywords(self, word, limit=50):
        like = f"%{word.lower()}%"
        return self.q("""SELECT id, day, started_at, title, keywords FROM recordings
                         WHERE lower(keywords) LIKE ? OR lower(title) LIKE ? ORDER BY started_at DESC LIMIT ?""",
                      (like, like, limit))
