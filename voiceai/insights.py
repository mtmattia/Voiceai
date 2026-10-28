"""LLM: parole chiave per blocco, riassunto della giornata, umore dell'utente principale, suggerimento."""
import json
import logging
import time

from . import llm, summary, text

log = logging.getLogger(__name__)

SYS = "Analizzi trascrizioni in italiano. Rispondi SOLO con JSON valido. Non inventare."

KW = """Dalla trascrizione estrai un titolo breve (max 8 parole) e 3-8 parole chiave utili per ritrovarla
(argomenti, progetti, luoghi, persone citate).
Poi, per ogni interlocutore{escluso} che parla abbastanza da capirlo, come sembrava sentirsi: "sensazione" in
una o due parole (es. tranquillo, preoccupato, irritato, entusiasta, stanco, di fretta) e "perche" in una frase
breve basata solo su ciò che dice. Se non si capisce, non includerlo. Usa i nomi come compaiono nel testo.
Infine "sintesi": 1-2 frasi su cosa succede: di cosa si parla, chi c'è, in che contesto (lavoro in laboratorio,
telefonata, cliente al banco, pausa...) se si capisce. Concreta, senza giudizi.
Formato: {{"titolo": "...", "parole_chiave": ["...", "..."], "sintesi": "...",
"sensazioni": [{{"persona": "...", "sensazione": "...", "perche": "..."}}]}}

TRASCRIZIONE:
{body}"""

MOOD = """Questo è il riassunto della giornata di {owner} e una selezione delle sue frasi.
Valuta lo stato d'animo di {owner} nella giornata, solo da ciò che emerge dal testo.
Poi dai UN piccolo suggerimento pratico, gentile e concreto per vivere la prossima giornata in modo più
sereno e meno stressato, legato a ciò che è successo (es. pause, delegare, chiudere un punto aperto).
Non dare consigli medici, non fare diagnosi, non moralizzare.
Formato: {{"umore": "una o due parole", "punteggio": intero da -2 (molto negativo) a 2 (molto positivo),
"motivazione": "una frase", "suggerimento": "due frasi al massimo"}}

RIASSUNTO:
{summary}

FRASI DI {owner}:
{lines}"""


def _json(cfg, prompt):
    try:
        d = json.loads(llm.chat(cfg, SYS, prompt, json_mode=True))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        log.warning("LLM JSON: %s", e)
        return {}


def recording_keywords(db, cfg, rid):
    rows = db.q("""SELECT u.start, u.end, u.text, COALESCE(p.name, u.label) AS who FROM utterances u
                   LEFT JOIN persons p ON p.id=u.person_id WHERE recording_id=? ORDER BY start""", (rid,))
    body = "\n".join(f"{r['who']}: {r['text']}" for r in rows)[: cfg["llm"]["block_max_chars"]]
    owner = db.owner()
    d = _json(cfg, KW.format(body=body, escluso=f" (escluso {owner['name']})" if owner else "")) if body else {}
    kws = [str(k).strip().lower() for k in d.get("parole_chiave", []) if str(k).strip()][:8]
    who = {r["who"] for r in rows}
    feel = [{"persona": str(f["persona"])[:80], "sensazione": str(f.get("sensazione", ""))[:40],
             "perche": str(f.get("perche", ""))[:300]}
            for f in d.get("sensazioni", []) if isinstance(d.get("sensazioni"), list)
            if isinstance(f, dict) and f.get("persona") in who and f.get("sensazione")
            and not str(f["persona"]).startswith("SPEAKER_")
            and not (owner and f.get("persona") == owner["name"])]
    with db.tx() as c:
        c.execute("UPDATE recordings SET title=?, keywords=?, feelings=?, summary=? WHERE id=?",
                  (str(d.get("titolo", ""))[:120], json.dumps(kws, ensure_ascii=False),
                   json.dumps(feel, ensure_ascii=False), str(d.get("sintesi") or "")[:600], rid))


SITUATIONS = """Questi sono i blocchi da 5 minuti registrati in un'ora della giornata di {owner}, in ordine: orario,
titolo, persone, sintesi. Scrivi il resoconto di quest'ora come elenco di situazioni: unisci SOLO i blocchi
consecutivi sullo stesso argomento concreto (lo stesso lavoro, lo stesso cliente, la stessa telefonata).
Una situazione dura di solito 5-30 minuti: in un'ora ce ne sono di solito 2-5.
Per ciascuna: "dalle" e "alle" (HH:MM, dai blocchi), "titolo" (max 6 parole, specifico: "Scritte per i carabinieri",
non "Discussione su progetti"), "persone" (nomi come nel testo, senza {owner}), "contesto" (2-4 parole: laboratorio,
telefonata, cliente al banco, pausa...), "sintesi" (1-3 frasi con i fatti concreti dei blocchi: oggetti, clienti,
problemi, decisioni, cose rimaste aperte). Vietate le frasi generiche ("discutono di progetti", "risolvono problemi").
Usa SOLO le informazioni dei blocchi.
Formato: {{"situazioni": [{{"dalle": "09:30", "alle": "09:45", "titolo": "...", "persone": ["..."],
"contesto": "...", "sintesi": "..."}}]}}

BLOCCHI:
{body}"""


def build_situations(db, cfg, day):
    """Resoconto per situazioni, un'ora alla volta (prompt piccoli: il 7B resta veloce e preciso).
    Le ore già fatte e senza blocchi nuovi si riusano da days.situations_hours."""
    recs = db.q("""SELECT r.id, r.started_at, r.duration, r.title, r.summary,
                   (SELECT GROUP_CONCAT(DISTINCT p.name) FROM utterances u JOIN persons p ON p.id=u.person_id
                    WHERE u.recording_id=r.id AND p.is_owner=0) AS who
                   FROM recordings r WHERE r.day=? AND r.status='done' AND r.keywords IS NOT NULL
                   ORDER BY r.started_at""", (day,))
    owner = db.owner()
    row = db.one("SELECT situations_hours FROM days WHERE day=?", (day,)) or {}
    try:
        cache = json.loads(row.get("situations_hours") or "{}")
    except ValueError:
        cache = {}
    hours = {}
    for r in recs:
        hours.setdefault(time.strftime("%H", time.localtime(r["started_at"])), []).append(r)
    out = {}
    for h, rs in sorted(hours.items()):
        key = ",".join(str(r["id"]) for r in rs)
        if cache.get(h, {}).get("key") == key:
            out[h] = cache[h]
            continue
        lines = [f"{time.strftime('%H:%M', time.localtime(r['started_at']))}-"
                 f"{time.strftime('%H:%M', time.localtime(r['started_at'] + (r['duration'] or 0)))} | {r['title'] or ''} | "
                 f"{r['who'] or '-'} | {r['summary'] or ''}" for r in rs]
        d = _json(cfg, SITUATIONS.format(owner=owner["name"] if owner else "l'utente", body="\n".join(lines)))
        sits = []
        for x in d.get("situazioni", []) if isinstance(d.get("situazioni"), list) else []:
            if isinstance(x, dict) and str(x.get("titolo", "")).strip():
                sits.append({k: str(x.get(k) or "")[:600] for k in ("dalle", "alle", "titolo", "contesto", "sintesi")})
                pers = x.get("persone") if isinstance(x.get("persone"), list) else []
                sits[-1]["persone"] = [str(p)[:60] for p in pers if not (owner and str(p) == owner["name"])][:8]
        out[h] = {"key": key, "sits": sits}
    flat = [x for h in sorted(out) for x in out[h]["sits"]]
    db.q("UPDATE days SET situations=?, situations_n=?, situations_hours=? WHERE day=?",
         (json.dumps(flat, ensure_ascii=False), len(recs), json.dumps(out, ensure_ascii=False), day))
    log.info("giornata %s: %d situazioni da %d blocchi", day, len(flat), len(recs))


def _sample(lines, max_chars):
    """Righe distribuite su tutta la giornata, non solo l'inizio."""
    if sum(len(x) + 1 for x in lines) <= max_chars:
        return lines
    step = max(1, int(len(lines) * (sum(map(len, lines)) / len(lines)) / max_chars) + 1)
    return lines[::step]


def build_day(db, cfg, day):
    started = time.time()
    rows = db.q("""SELECT u.ts, u.start, u.end, u.text, u.person_id, COALESCE(p.name, u.label) AS who
                   FROM utterances u JOIN recordings r ON r.id=u.recording_id
                   LEFT JOIN persons p ON p.id=u.person_id WHERE r.day=? ORDER BY u.ts""", (day,))
    if not rows:
        return
    # timestamp assoluti -> turni per i blocchi del riassunto
    t0 = rows[0]["ts"]
    trns = [{"start": r["ts"] - t0, "end": r["ts"] - t0 + (r["end"] - r["start"]), "speaker": r["who"],
             "text": r["text"]} for r in rows]
    who = sorted({r["who"] for r in rows})
    body = summary.write(cfg, trns, str, who, day=True)

    kws = {}
    for r in db.q("SELECT keywords FROM recordings WHERE day=? AND keywords IS NOT NULL", (day,)):
        for k in json.loads(r["keywords"]):
            kws[k] = kws.get(k, 0) + 1
    top = [k for k, _ in sorted(kws.items(), key=lambda x: -x[1])][:15]

    owner = db.owner()
    mood = {}
    if owner:
        lines = [f"[{time.strftime('%H:%M', time.localtime(r['ts']))}] {r['text']}"
                 for r in rows if r["person_id"] == owner["id"]]
        if lines:
            sample = "\n".join(_sample(lines, cfg["llm"]["block_max_chars"] // 2))
            mood = _json(cfg, MOOD.format(owner=owner["name"], summary=body[:6000], lines=sample))
    try:
        score = max(-2, min(2, int(mood.get("punteggio"))))
    except (TypeError, ValueError):
        score = None
    with db.tx() as c:
        c.execute("""UPDATE days SET dirty=(COALESCE(last_activity,0) > ?), keywords=?, summary=?, mood=?, mood_score=?, mood_reason=?,
                     tip=?, updated=? WHERE day=?""",
                  (started, json.dumps(top, ensure_ascii=False), body, mood.get("umore"), score,
                   mood.get("motivazione"), mood.get("suggerimento"), time.time(), day))
