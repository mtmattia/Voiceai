"""Correzione della trascrizione con l'LLM, in due passate: correggi, poi rileggi le correzioni.

1. L'LLM vede il blocco intero (contesto) e propone correzioni solo per le frasi sbagliate.
2. Rilegge ogni coppia originale -> corretta e tiene solo quelle sicure.
In più un filtro meccanico scarta le riscritture troppo libere. L'originale resta in utterances.raw.
"""
import difflib
import json
import logging

from . import llm, text

log = logging.getLogger(__name__)

SYS = ("Correggi trascrizioni automatiche in italiano. Rispondi SOLO con JSON valido. "
       "Non riassumere, non riformulare, non aggiungere informazioni.")

FIX = """Questa è la trascrizione automatica (Whisper) di una conversazione di lavoro, una frase per riga
con il suo numero. Contiene errori di riconoscimento: parole sentite male ("fastagliato" -> "frastagliato"),
parole spezzate o attaccate, nomi propri scritti male, punteggiatura sbagliata.
Correggi SOLO questi errori, mantenendo parole, ordine e registro di chi parla (anche dialetto e parolacce).
Se una frase è incomprensibile lasciala com'è. Non toccare le frasi già giuste.
{glossary}Formato: {{"correzioni": [{{"n": numero, "testo": "frase corretta"}}]}}  (lista vuota se è tutto giusto)

TRASCRIZIONE:
{body}"""

CHECK = """Rileggi queste correzioni di una trascrizione automatica in italiano. Per ciascuna rispondi se la
versione corretta è chiaramente ciò che è stato detto (errore di riconoscimento sistemato) oppure se cambia
il senso, inventa parole o riformula. Nel dubbio rifiuta.
Formato: {{"accetta": [numeri delle correzioni da tenere]}}

{pairs}"""


def _json(cfg, prompt):
    try:
        d = json.loads(llm.chat(cfg, SYS, prompt, json_mode=True))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        log.warning("correzione LLM: %s", e)
        return {}


def close_enough(a, b, min_ratio=0.6):
    """Correzione, non riscrittura: il testo deve restare simile all'originale."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= min_ratio


def glossary(cfg):
    terms = sorted(text.prompt_terms(cfg["asr"].get("initial_prompt")) | {n.lower() for n in cfg["speakers"]["known_names"]})
    terms = [t for t in terms if len(t.split()) <= 3]
    return f"Nomi e termini che ricorrono: {', '.join(terms)}.\n" if terms else ""


def propose(cfg, lines):
    """lines: [(n, testo)] -> {n: testo corretto} dopo correzione + rilettura."""
    body = "\n".join(f"{n}. {t}" for n, t in lines)
    d = _json(cfg, FIX.format(glossary=glossary(cfg), body=body))
    orig = dict(lines)
    fixes = {}
    for c in d.get("correzioni", []) if isinstance(d.get("correzioni"), list) else []:
        try:
            n, new = int(c["n"]), str(c["testo"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if n in orig and new and new != orig[n] and close_enough(orig[n], new):
            fixes[n] = new
    if not fixes:
        return {}
    pairs = "\n".join(f"{n}. ORIGINALE: {orig[n]}\n   CORRETTA: {t}" for n, t in fixes.items())
    ok = _json(cfg, CHECK.format(pairs=pairs)).get("accetta", [])
    keep = {int(n) for n in ok if str(n).lstrip("-").isdigit()} if isinstance(ok, list) else set()
    return {n: t for n, t in fixes.items() if n in keep}


def recording(db, cfg, rid, max_chars=6000):
    """Corregge un blocco a pezzi (contesto limitato del 7B). -> numero di frasi corrette."""
    rows = db.q("SELECT id, text FROM utterances WHERE recording_id=? ORDER BY start", (rid,))
    done, chunk, size = 0, [], 0
    for r in rows + [None]:
        if r is not None and size + len(r["text"]) < max_chars:
            chunk.append(r)
            size += len(r["text"]) + 6
            continue
        if chunk:
            fixes = propose(cfg, [(i + 1, u["text"]) for i, u in enumerate(chunk)])
            with db.tx() as c:
                for n, t in fixes.items():
                    c.execute("UPDATE utterances SET raw=COALESCE(raw, text), text=? WHERE id=?", (t, chunk[n - 1]["id"]))
            done += len(fixes)
        chunk, size = ([r], len(r["text"]) + 6) if r is not None else ([], 0)
    db.q("UPDATE recordings SET corrected=1 WHERE id=?", (rid,))
    log.info("blocco %s: %d frasi corrette dall'LLM", rid, done)
    return done
