"""Domande in linguaggio naturale sull'archivio: ricerca full-text + riassunti -> risposta LLM."""
import time

from . import llm
from .memos import words

SYS = ("Rispondi in italiano a domande sull'archivio delle conversazioni dell'utente, usando SOLO gli estratti "
       "forniti. Cita giorno e ora (es. 28/10 alle 9:05). Se gli estratti non bastano, dillo. Sii breve.")


def answer(db, cfg, question, limit=40):
    hits = db.search_any(sorted(words(question)), limit)
    days = sorted({h["day"] for h in hits}, reverse=True)[:5]
    sums = db.q(f"SELECT day, summary FROM days WHERE day IN ({','.join('?' * len(days))}) AND summary IS NOT NULL",
                days) if days else []
    hits.sort(key=lambda h: h["ts"])
    ctx = "\n".join(f"[{time.strftime('%d/%m %H:%M', time.localtime(h['ts']))}] {h['speaker']}: {h['text']}"
                    for h in hits)
    ctx += "".join(f"\n\nRIASSUNTO {s['day']}:\n{s['summary'][:2500]}" for s in sums)
    if not ctx.strip():
        return {"risposta": "Non ho trovato niente nell'archivio su questo.", "fonti": []}
    text = llm.chat(cfg, SYS, f"DOMANDA: {question}\n\nESTRATTI:\n{ctx[: cfg['llm']['block_max_chars']]}")
    return {"risposta": text.strip(), "fonti": hits[-10:]}
