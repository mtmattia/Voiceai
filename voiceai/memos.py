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
Estrai due liste separate.

1. "todo": cose che {owner} deve fare: impegni che prende o richieste che altri gli fanno e che accetta.
Ignora chiacchiere, opinioni, cose già fatte, frasi generiche ("fare delle cose").
2. "agenda": appuntamenti e incontri con un giorno o un orario detto nel testo (riunioni, visite, consegne).
Usa SOLO ciò che c'è nella trascrizione: nessun esempio, nessuna voce inventata. Meglio zero voci che voci vaghe.

Per ogni voce:
"titolo": breve, max 6 parole, all'infinito per i todo, nome dell'evento per l'agenda;
"descrizione": 1-2 frasi con i dettagli utili presi dal testo: cosa esattamente, per chi, chi l'ha chiesto,
dove, materiali, perché. Se il testo non dà dettagli: stringa vuota;
"quando": data e ora ISO (AAAA-MM-GGTHH:MM) SOLO se c'è un riferimento esplicito ("alle 12", "domani alle 9",
"lunedì"), calcolato rispetto all'inizio registrazione; altrimenti null (per l'agenda è obbligatorio);
"momento" (solo todo): se non c'è orario, il momento più probabile: mattina, pranzo, pomeriggio, sera, o null.
Formato: {{"todo": [{{"titolo": "...", "descrizione": "...", "quando": null, "momento": "sera"}}],
"agenda": [{{"titolo": "...", "descrizione": "...", "quando": "2026-10-29T09:00"}}]}}
Liste vuote se non c'è niente.

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


def add(db, text, recording_id=None, due_ts=None, due_kind=None, moment=None, source="auto", descr="", kind="todo"):
    """Inserisce evitando doppioni (stesso tipo, ancora aperti, ultimi 3 giorni)."""
    for m in db.q("SELECT id, text, descr FROM memos WHERE done=0 AND kind=? AND created > ?",
                  (kind, time.time() - 3 * 86400)):
        if jaccard(m["text"], text) >= 0.6:
            if descr and not m["descr"]:
                db.q("UPDATE memos SET descr=? WHERE id=?", (descr, m["id"]))
            return None
    with db.tx() as c:
        return c.execute("""INSERT INTO memos(uid, recording_id, text, descr, kind, due_ts, due_kind, moment, source,
                            created) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (uuid.uuid4().hex, recording_id, text, descr, kind, due_ts, due_kind, moment, source,
                          time.time())).lastrowid


def extract(db, cfg, rid):
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
    owner = db.owner()
    rows = db.q("""SELECT u.start, u.text, COALESCE(p.name, u.label) AS who FROM utterances u
                   LEFT JOIN persons p ON p.id=u.person_id WHERE recording_id=? ORDER BY start""", (rid,))
    body = "\n".join(f"{r['who']}: {r['text']}" for r in rows)[: cfg["llm"]["block_max_chars"]]
    d = {}
    if body:
        when = dt.datetime.fromtimestamp(rec["started_at"])
        try:
            d = json.loads(llm.chat(cfg, SYS, PROMPT.format(
                owner=owner["name"] if owner else "l'utente", when=f"{when:%Y-%m-%d %H:%M}",
                weekday=WEEKDAYS[when.weekday()], body=body), json_mode=True))
            d = d if isinstance(d, dict) else {}
        except Exception as e:
            log.warning("memo blocco %s: %s", rid, e)
    for kind in ("todo", "agenda"):
        for m in d.get(kind) or [] if isinstance(d.get(kind), list) else []:
            title = str(m.get("titolo") or m.get("testo") or "").strip()[:120] if isinstance(m, dict) else ""
            if not title:
                continue
            descr = str(m.get("descrizione") or "").strip()[:1000]
            if re.match(r"(non specificat|nessun dettagli|nessuna descrizion|n/?a\b)", descr.lower()):
                descr = ""
            due = _parse_when(m.get("quando"))
            if due and due < rec["started_at"] - 3600:
                due = None
            if kind == "agenda":
                if due:   # un appuntamento senza data non è agenda: diventa un todo
                    add(db, title, rid, due, "esplicita", None, descr=descr, kind="agenda")
                    continue
            moment = m.get("momento") if m.get("momento") in MOMENT_HOURS else None
            if due:
                add(db, title, rid, due, "esplicita", moment, descr=descr)
            else:
                g = guess_due(db, title, moment, rec["started_at"])
                add(db, title, rid, g, "ipotizzata" if g else None, moment, descr=descr)
    db.q("UPDATE recordings SET memos_done=1 WHERE id=?", (rid,))
