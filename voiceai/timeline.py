"""Cronologia della giornata: un log sintetico minuto per minuto ("8:23 Mattia parla con Marco").

Fonti: frasi trascritte (chi parla in quel minuto), domande fatte all'AI, memo creati.
I minuti consecutivi con le stesse persone diventano un intervallo (8:23–8:41).
"""
import datetime as dt
import time

GAP_MIN = 2   # minuti di silenzio che spezzano una conversazione


def _hm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _who(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


def _talk(owner, people):
    others = [p for p in people if p != owner]
    if owner in people:
        return f"{owner} parla con {_who(others)}" if others else f"{owner} parla da solo"
    return f"{_who(others)} {'parla' if len(others) == 1 else 'parlano'}"


def day_bounds(day):
    d = dt.datetime.strptime(day, "%Y-%m-%d")
    return d.timestamp(), (d + dt.timedelta(days=1)).timestamp()


def build(db, day):
    """-> [{ts, end, kind, text, recording_id?}] in ordine di tempo."""
    t0, t1 = day_bounds(day)
    own = db.owner()
    owner = own["name"] if own else "Io"
    rows = db.q("""SELECT u.ts, u.end - u.start AS dur, u.recording_id,
                          COALESCE(p.name, u.label, 'Sconosciuto') AS who, p.is_owner
                   FROM utterances u JOIN recordings r ON r.id = u.recording_id
                   LEFT JOIN persons p ON p.id = u.person_id
                   WHERE r.day = ? ORDER BY u.ts""", (day,))
    # minuto -> persone in ordine di prima apparizione
    minutes = {}
    for r in rows:
        name = owner if r["is_owner"] else r["who"]
        m = int(r["ts"] // 60)
        for k in range(m, int((r["ts"] + max(r["dur"] or 0, 0)) // 60) + 1):
            e = minutes.setdefault(k, {"people": [], "rec": r["recording_id"]})
            if name not in e["people"]:
                e["people"].append(name)

    out, cur = [], None
    for m in sorted(minutes):
        e = minutes[m]
        if cur and m - cur["last"] <= GAP_MIN and set(e["people"]) == set(cur["people"]):
            cur["last"] = m
            continue
        cur = {"first": m, "last": m, "people": e["people"], "rec": e["rec"]}
        out.append(cur)
    events = [{"ts": c["first"] * 60, "end": c["last"] * 60 + 59, "kind": "parlato",
               "text": _talk(owner, c["people"]), "recording_id": c["rec"]} for c in out]

    for a in db.q("SELECT ts, question FROM asks WHERE ts >= ? AND ts < ?", (t0, t1)):
        events.append({"ts": a["ts"], "end": a["ts"], "kind": "ai",
                       "text": f"{owner} parla con AI: «{a['question']}»"})
    for m in db.q("SELECT created, text FROM memos WHERE created >= ? AND created < ?", (t0, t1)):
        events.append({"ts": m["created"], "end": m["created"], "kind": "memo", "text": f"Memo: {m['text']}"})
    events.sort(key=lambda e: e["ts"])
    for e in events:
        e["ora"] = _hm(e["ts"]) + ("" if e["end"] - e["ts"] < 60 else "–" + _hm(e["end"]))
    return events
