"""Turni di parola, timestamp e blocchi per l'LLM."""
import re

# Frasi che Whisper inventa nei silenzi e nel rumore (dai sottotitoli su cui è addestrato)
HALLUCINATIONS = {"grazie", "grazie a tutti", "grazie mille", "grazie per la visione", "grazie per l'attenzione",
                  "iscriviti al canale", "ciao a tutti"}
_SPLIT = re.compile(r"[.!?…]+")


def is_hallucination(t):
    if t.strip().lower().startswith("sottotitoli"):
        return True
    parts = [p.strip(" ,").lower() for p in _SPLIT.split(t) if p.strip(" ,")]
    return bool(parts) and all(p in HALLUCINATIONS for p in parts)


def prompt_terms(prompt):
    """Nomi e termini dell'initial_prompt: Whisper tende a ripeterli da soli nei silenzi."""
    return {p.strip().lower() for p in re.split(r"[.,:;!?]+", prompt or "") if p.strip()}


def is_prompt_echo(t, terms):
    parts = [p.strip().lower() for p in re.split(r"[.,:;!?…]+", t) if p.strip()]
    return bool(terms) and bool(parts) and all(p in terms for p in parts)


def drop_hallucinations(segs, prompt=None):
    terms = prompt_terms(prompt)
    return [s for s in segs if not (is_hallucination(s.get("text", "")) or is_prompt_echo(s.get("text", ""), terms))]


def ts(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fill_speakers(segs):
    """Segmenti senza speaker (nessun overlap con la diarizzazione) -> speaker precedente."""
    last = None
    for s in segs:
        if s.get("speaker"):
            last = s["speaker"]
        else:
            s["speaker"] = last or "SPEAKER_UNK"
    return segs


def turns(segs, max_gap=2.0):
    """Unisce segmenti consecutivi dello stesso speaker."""
    out = []
    for s in segs:
        if out and out[-1]["speaker"] == s["speaker"] and s["start"] - out[-1]["end"] <= max_gap:
            out[-1]["end"] = s["end"]
            out[-1]["text"] += " " + s["text"]
            out[-1]["overlap"] = out[-1]["overlap"] or bool(s.get("overlap"))
        else:
            out.append(dict(start=s["start"], end=s["end"], speaker=s["speaker"], text=s["text"],
                            overlap=bool(s.get("overlap"))))
    return out


def line(t, label):
    return f"[{ts(t['start'])}] {label(t['speaker'])}: {t['text']}"


def blocks(trns, label, minutes=20, max_chars=14000):
    """-> [(start, end, testo)] tagliati a ~minutes o max_chars, mai a metà turno."""
    out, cur, t0 = [], [], None
    size = 0
    for t in trns:
        ln = line(t, label)
        if cur and (t["start"] - t0 >= minutes * 60 or size + len(ln) > max_chars):
            out.append((t0, cur_end, "\n".join(cur)))
            cur, size = [], 0
        if not cur:
            t0 = t["start"]
        cur.append(ln)
        size += len(ln) + 1
        cur_end = t["end"]
    if cur:
        out.append((t0, cur_end, "\n".join(cur)))
    return out
