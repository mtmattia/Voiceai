"""Registro globale "persone": collega gli speaker di ogni blocco alle stesse persone nel tempo.

Ogni blocco ha etichette locali (SPEAKER_00...). Le confrontiamo con le impronte di tutte le persone:
- match sopra soglia -> stessa persona (e, se molto sicuro, l'impronta si aggiunge: il profilo si adatta)
- nessun match e abbastanza parlato -> nuova persona "Sconosciuto N"
- poco parlato -> nessuna persona (embedding troppo rumoroso)
Così il collegamento tra pezzi non dipende dall'overlap.
"""
from .speakers import similarity


def assign(db, embeddings, talk, cfg, source=""):
    p = cfg["people"]
    prints = db.prints()
    sims = similarity(embeddings, prints) if prints else {s: {} for s in embeddings}
    pairs = sorted(((v, s, pid) for s, d in sims.items() for pid, v in d.items()), reverse=True)
    out, used = {}, set()
    for v, s, pid in pairs:
        if v < p["threshold"]:
            break
        if s in out or pid in used:
            continue
        out[s] = pid
        used.add(pid)
    # stessa persona spezzata in due dalla diarizzazione: accetta il doppione solo se molto simile
    for s, d in sims.items():
        if s not in out and d:
            pid, v = max(d.items(), key=lambda x: x[1])
            if v >= p["threshold"] + p["oversplit_margin"]:
                out[s] = pid
    for s in embeddings:
        sec = talk.get(s, 0.0)
        if s in out:
            if sims[s][out[s]] >= p["threshold"] + p["update_margin"] and sec >= p["min_update_sec"]:
                db.add_print(out[s], embeddings[s], source, cap=p["max_prints"])
        elif sec >= p["min_new_person_sec"]:
            out[s] = db.new_person()
            db.add_print(out[s], embeddings[s], source, cap=p["max_prints"])
    return out, sims


def apply_name_hints(db, votes, local_to_pid, cfg):
    """Voti dal testo (per etichetta locale) -> voti persistenti per persona -> nome probabile."""
    for s, cnt in votes.pos.items():
        pid = local_to_pid.get(s)
        if pid is None:
            continue
        for name, n in cnt.items():
            if name not in votes.neg[s]:
                db.add_votes(pid, name, n)
    taken = {r["name"] for r in db.q("SELECT name FROM persons WHERE confirmed=1")}
    min_votes = cfg["people"]["min_name_votes"]
    for r in db.q("SELECT id FROM persons WHERE confirmed=0"):
        vs = db.q("SELECT name, votes FROM name_votes WHERE person_id=? ORDER BY votes DESC", (r["id"],))
        if not vs:
            continue
        top, total = vs[0], sum(v["votes"] for v in vs)
        if top["votes"] >= min_votes and top["votes"] >= 0.6 * total and top["name"] not in taken:
            db.update_person(r["id"], name=f"{top['name']}?")   # "?" = probabile, da confermare
