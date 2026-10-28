"""Turni di parola, timestamp e blocchi per l'LLM."""


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
        else:
            out.append(dict(start=s["start"], end=s["end"], speaker=s["speaker"], text=s["text"]))
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
