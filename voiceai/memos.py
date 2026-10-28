"""Promemoria: estratti dal parlato, scadenza esplicita o ipotizzata dai ritmi dell'utente."""
import datetime as dt
import json
import logging
import re
import statistics
import time
import uuid

from . import llm

log = logging.getLogger(__name__)

SYS = "Estrai promemoria da trascrizioni in italiano. Rispondi SOLO con JSON valido. Non inventare."

PROMPT = """Registrazione di {owner}, iniziata {when} ({weekday}).
Elenca le cose che {owner} deve ricordarsi o fare: impegni che prende ("devo stampare alle 12",
"ricordami di prendere il gatto", "stasera mangio da mamma") o richieste che altri gli fanno e che accetta.
Ignora chiacchiere, opinioni e cose già fatte. Testo breve all'infinito ("Stampare i documenti").
"quando": data e ora ISO (AAAA-MM-GGTHH:MM) SOLO se nel testo c'è un riferimento esplicito
("alle 12", "domani alle 9", "lunedì"), calcolato rispetto all'inizio registrazione; altrimenti null.
"momento": se non c'è orario, il momento più probabile: mattina, pranzo, pomeriggio, sera, oppure null.
Formato: {{"memo": [{{"testo": "...", "quando": null, "momento": "sera"}}]}}
Se non c'è niente: {{"memo": []}}

TRASCRIZIONE:
{body}"""

MOMENT_HOURS = {"mattina": 9.5, "pranzo": 12.5, "pomeriggio": 16.0, "sera": 19.5}
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_W = re.compile(r"[a-zà-ù]{3,}")
_STOP = {"che", "per", "con", "del", "della", "dei", "alle", "allo", "una", "uno", "gli", "le", "non", "sono"}


def words(s):
    return {w for w in _W.findall(s.lower()) if w not in _STOP}


def jaccard(a, b):
    a, b = words(a), words(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def _hour(ts):
    t = time.localtime(ts)
    return t.tm_hour + t.tm_min / 60


def _next_at(base_ts, hour):
    """Prima occorrenza di 'hour' (ora del giorno) dopo base_ts."""
    base = dt.datetime.fromtimestamp(base_ts)
    cand = base.replace(hour=int(hour), minute=int(hour % 1 * 60), second=0, microsecond=0)
    if cand <= base:
        cand += dt.timedelta(days=1)
    return cand.timestamp()


def guess_due(db, text, moment, base_ts, min_samples=3):
    """Ritmi imparati: 1) quando hai spuntato memo simili, 2) a che ora spunti di solito quel 'momento',
    3) orario di default del momento."""
    done = db.q("SELECT text, moment, done_ts FROM memos WHERE done=1 AND done_ts IS NOT NULL")
    similar = [_hour(m["done_ts"]) for m in done if jaccard(m["text"], text) >= 0.4]
    if len(similar) >= 2:
        return _next_at(base_ts, statistics.median(similar))
    if moment in MOMENT_HOURS:
        same = [_hour(m["done_ts"]) for m in done if m["moment"] == moment]
        h = statistics.median(same) if len(same) >= min_samples else MOMENT_HOURS[moment]
        return _next_at(base_ts, h)
    return None


def _parse_when(s):
    try:
        return dt.datetime.fromisoformat(str(s)).timestamp()
    except (TypeError, ValueError):
        return None


def add(db, text, recording_id=None, due_ts=None, due_kind=None, moment=None, source="auto"):
    """Inserisce evitando doppioni di memo ancora aperti negli ultimi 3 giorni."""
    for m in db.q("SELECT id, text FROM memos WHERE done=0 AND created > ?", (time.time() - 3 * 86400,)):
        if jaccard(m["text"], text) >= 0.6:
            return None
    with db.tx() as c:
        return c.execute("""INSERT INTO memos(uid, recording_id, text, due_ts, due_kind, moment, source, created)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (uuid.uuid4().hex, recording_id, text, due_ts, due_kind, moment, source,
                          time.time())).lastrowid


def extract(db, cfg, rid):
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
    owner = db.owner()
    rows = db.q("""SELECT u.start, u.text, COALESCE(p.name, u.label) AS who FROM utterances u
                   LEFT JOIN persons p ON p.id=u.person_id WHERE recording_id=? ORDER BY start""", (rid,))
    body = "\n".join(f"{r['who']}: {r['text']}" for r in rows)[: cfg["llm"]["block_max_chars"]]
    found = []
    if body:
        when = dt.datetime.fromtimestamp(rec["started_at"])
        try:
            d = json.loads(llm.chat(cfg, SYS, PROMPT.format(
                owner=owner["name"] if owner else "l'utente", when=f"{when:%Y-%m-%d %H:%M}",
                weekday=WEEKDAYS[when.weekday()], body=body), json_mode=True))
            found = d.get("memo", []) if isinstance(d, dict) else []
        except Exception as e:
            log.warning("memo blocco %s: %s", rid, e)
    for m in found:
        if not isinstance(m, dict) or not str(m.get("testo", "")).strip():
            continue
        text = str(m["testo"]).strip()[:200]
        moment = m.get("momento") if m.get("momento") in MOMENT_HOURS else None
        due = _parse_when(m.get("quando"))
        if due and due > rec["started_at"] - 3600:
            add(db, text, rid, due, "esplicita", moment)
        else:
            g = guess_due(db, text, moment, rec["started_at"])
            add(db, text, rid, g, "ipotizzata" if g else None, moment)
    db.q("UPDATE recordings SET memos_done=1 WHERE id=?", (rid,))
