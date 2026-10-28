"""Fallback nomi dal testo: regole sui vocativi/autopresentazioni + LLM, con votazione su tutta la riunione."""
import json
import logging
import re
from collections import Counter, defaultdict

from . import llm, text

log = logging.getLogger(__name__)


class Votes:
    def __init__(self):
        self.pos = defaultdict(Counter)   # spk -> {nome: punti}
        self.neg = defaultdict(set)       # spk -> nomi che NON può essere (li chiama lui)

    def add(self, spk, name, w=1):
        self.pos[spk][name] += w

    def resolve(self, min_votes, taken_names=(), skip=()):
        """Greedy univoco; serve min_votes e >=60% dei voti dello speaker."""
        pairs = sorted(((c, s, n) for s, cnt in self.pos.items() if s not in skip
                        for n, c in cnt.items()), reverse=True)
        out, used = {}, set(taken_names)
        for c, s, n in pairs:
            if c < min_votes or s in out or n in used or n in self.neg[s]:
                continue
            if c < 0.6 * sum(self.pos[s].values()):
                continue
            out[s] = n
            used.add(n)
        return out


def rule_votes(trns, known_names, votes=None):
    votes = votes or Votes()
    if not known_names:
        return votes
    canon = {n.lower(): n for n in known_names}
    alt = "|".join(sorted(map(re.escape, known_names), key=len, reverse=True))
    self_re = re.compile(rf"\b(?:sono|mi chiamo|qui (?:è|e')|parla)\s+({alt})\b", re.I)
    start_re = re.compile(rf"^\W*(?:(?:allora|ok|okay|e|dai|senti|ciao)\s+)?({alt})\s*[,!?]", re.I)
    end_re = re.compile(rf",\s*({alt})\s*[?!.]*\s*$", re.I)
    thanks_re = re.compile(rf"\bgrazie\s+({alt})\b", re.I)

    def next_other(i):
        for t in trns[i + 1:i + 4]:
            if t["speaker"] != trns[i]["speaker"]:
                return t["speaker"]

    def prev_other(i):
        for t in reversed(trns[max(0, i - 3):i]):
            if t["speaker"] != trns[i]["speaker"]:
                return t["speaker"]

    for i, t in enumerate(trns):
        spk, txt = t["speaker"], t["text"]
        for m in self_re.finditer(txt):
            votes.add(spk, canon[m.group(1).lower()], 2)
        # chi risponde è il prossimo a parlare: conta solo il vocativo nelle ultime 2 frasi del turno
        tail = _SENT.split(txt.strip())[-2:]
        addressed = {m.group(1).lower() for snt in tail for r in (start_re, end_re) if (m := r.search(snt))}
        for n in addressed:
            n = canon[n.lower()]
            votes.neg[spk].add(n)
            if (o := next_other(i)):
                votes.add(o, n)
        for m in thanks_re.finditer(txt):
            n = canon[m.group(1).lower()]
            votes.neg[spk].add(n)
            if (o := prev_other(i)):
                votes.add(o, n)
    return votes


_SENT = re.compile(r"(?<=[.!?])\s+")
_NAME_OK = re.compile(r"^[A-ZÀ-Ý][\wÀ-ÿ'’-]{1,30}( [A-ZÀ-Ý][\wÀ-ÿ'’-]{1,30})?$")

SYS = ("Analizzi trascrizioni di riunioni in italiano. Rispondi SOLO con JSON valido. "
       "Non indovinare: indica un nome solo se il testo lo rende esplicito.")


def llm_votes(cfg, trns, unknown, label, votes=None):
    """Chiede all'LLM, blocco per blocco, il nome delle etichette ancora ignote."""
    votes = votes or Votes()
    c = cfg["llm"]
    for t0, t1, blk in text.blocks(trns, label, c["block_minutes"], c["block_max_chars"]):
        present = sorted({s for s in unknown if f"] {s}:" in blk})
        if not present:
            continue
        prompt = (f"Etichette da identificare: {', '.join(present)}.\n"
                  "Per ciascuna, se dal testo risulta chiaramente il nome (si presenta, viene chiamata "
                  "per nome e risponde, viene ringraziata per nome dopo aver parlato), indicalo. "
                  "Ometti le etichette incerte.\n"
                  'Formato: {"SPEAKER_01": "Marco"}\n\nTRASCRIZIONE:\n' + blk)
        try:
            data = json.loads(llm.chat(cfg, SYS, prompt, json_mode=True))
        except Exception as e:  # LLM giù o JSON rotto: il fallback è opzionale
            log.warning("name inference blocco %s: %s", text.ts(t0), e)
            continue
        for spk, name in (data.items() if isinstance(data, dict) else []):
            if spk in present and isinstance(name, str) and _NAME_OK.match(name.strip()):
                votes.add(spk, name.strip())
    return votes
