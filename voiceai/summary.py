"""Verbale: riassunto a blocchi (~20 min) -> riduzione -> verbale finale."""
import logging

from . import llm, text

log = logging.getLogger(__name__)

SYS = ("Sei il segretario che redige il verbale di una riunione di lavoro, in italiano. "
       "Usa solo informazioni presenti nel testo, non inventare nomi, date o decisioni. Sii conciso.")

BLOCK = """Estratto {i}/{n} ({t0}–{t1}).
Estrai in Markdown, con esattamente queste sezioni:
### Punti discussi
- argomento: sintesi in una riga
### Decisioni
- decisione presa (chi l'ha proposta, se chiaro)
### Azioni
- Chi | Cosa | Entro quando (scrivi "n.d." se non detto)
Scrivi "Nessuna" in una sezione vuota.

TRASCRIZIONE:
{body}"""

MERGE = """Queste sono note parziali, in ordine cronologico, della stessa riunione.
Uniscile in un'unica nota con le sezioni ### Punti discussi, ### Decisioni, ### Azioni.
Elimina i duplicati, conserva tutte le decisioni e le azioni.

{body}"""

FINAL = """Queste sono le note, in ordine cronologico, di una riunione con: {who}.
Scrivi il verbale finale in Markdown con esattamente queste sezioni:
## Sintesi
3-5 frasi.
## Punti discussi
elenco puntato, raggruppa gli argomenti ripresi più volte.
## Decisioni
elenco puntato.
## Azioni
tabella Markdown con colonne: Chi | Cosa | Scadenza ("n.d." se non detta).
Elimina i duplicati. Non aggiungere altre sezioni.

NOTE:
{body}"""


def _reduce(cfg, notes, max_chars):
    """Unisce note a gruppi finché il totale entra nel contesto del modello."""
    while len(notes) > 1 and sum(len(x) for x in notes) > max_chars:
        groups, cur = [], []
        for x in notes:
            if cur and sum(map(len, cur)) + len(x) > max_chars:
                groups.append(cur)
                cur = []
            cur.append(x)
        groups.append(cur)
        if len(groups) == len(notes):  # ogni nota da sola è già troppo grande: accoppia
            groups = [notes[i:i + 2] for i in range(0, len(notes), 2)]
        log.info("riduzione: %d note -> %d", len(notes), len(groups))
        notes = [g[0] if len(g) == 1 else llm.chat(cfg, SYS, MERGE.format(body="\n\n---\n\n".join(g)))
                 for g in groups]
    return notes


DAY_SYS = ("Riassumi la giornata di una persona (lavoro, casa, vita privata) dalle trascrizioni delle sue "
           "conversazioni, in italiano. Usa solo ciò che è nel testo, non inventare. Sii conciso e rispettoso.")

DAY_FINAL = """Queste sono le note, in ordine cronologico, della giornata. Persone presenti: {who}.
Scrivi in Markdown con esattamente queste sezioni:
## Sintesi
3-5 frasi sulla giornata.
## Cosa è successo
elenco puntato per momenti (mattina, pomeriggio, sera) e ambiti (lavoro, casa, persone).
## Decisioni e impegni
elenco puntato, chi/cosa/quando se detto.
Non aggiungere altre sezioni.

NOTE:
{body}"""


def write(cfg, trns, label, participants, day=False):
    c = cfg["llm"]
    blks = text.blocks(trns, label, c["block_minutes"], c["block_max_chars"])
    notes = []
    for i, (t0, t1, body) in enumerate(blks, 1):
        log.info("riassunto blocco %d/%d", i, len(blks))
        out = llm.chat(cfg, DAY_SYS if day else SYS, BLOCK.format(i=i, n=len(blks), t0=text.ts(t0), t1=text.ts(t1), body=body))
        notes.append(f"[{text.ts(t0)}–{text.ts(t1)}]\n{out.strip()}")
    notes = _reduce(cfg, notes, c["block_max_chars"])
    log.info("verbale finale")
    final = DAY_FINAL if day else FINAL
    return llm.chat(cfg, DAY_SYS if day else SYS,
                    final.format(who=", ".join(participants), body="\n\n---\n\n".join(notes))).strip()
