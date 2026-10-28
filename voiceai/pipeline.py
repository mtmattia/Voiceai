"""Orchestrazione: audio -> trascrizione con nomi -> verbale."""
import datetime as dt
import json
import logging
from pathlib import Path

from . import asr, llm, names, summary, text
from .speakers import VoiceprintStore, match

log = logging.getLogger(__name__)


def identify(cfg, trns, embeddings, talk):
    """-> {spk: {"name", "how", "score"}} con impronte, poi regole, poi LLM, poi Ospite N."""
    scfg = cfg["speakers"]
    prints = VoiceprintStore(cfg["paths"]["voiceprints"]).load()
    by_print, sims = match(embeddings, prints, scfg["threshold"]) if prints else ({}, {})
    ident = {s: {"name": n, "how": "impronta", "score": round(sims[s][n], 3)} for s, n in by_print.items()}

    order = list(dict.fromkeys(t["speaker"] for t in trns))  # ordine di prima apparizione
    unknown = [s for s in order if s not in ident]
    if unknown:
        votes = names.rule_votes(trns, scfg["known_names"])
        if cfg["llm"]["enabled"] and cfg["llm"]["name_inference"]:
            lbl = lambda s: ident[s]["name"] if s in ident else s
            names.llm_votes(cfg, trns, set(unknown), lbl, votes)
        taken = {v["name"] for v in ident.values()}
        for s, n in votes.resolve(scfg["min_votes"], taken, skip=set(ident)).items():
            ident[s] = {"name": n, "how": "testo", "score": votes.pos[s][n]}

    g = 0
    for s in order:
        if s not in ident:
            g += 1
            ident[s] = {"name": f"{scfg['guest_label']} {g}", "how": "ospite", "score": None}
    for s, v in ident.items():
        v["talk_sec"] = round(talk.get(s, 0.0))
        v["sims"] = sims.get(s, {})
    return ident


def render_transcript(meta, trns, ident):
    label = _label(ident)
    lines = [f"# Trascrizione — {meta['title']}", "", f"Data: {meta['date']} · Durata: {text.ts(meta['duration'])}", "",
             "| Speaker | Etichetta | Metodo |", "|---|---|---|",
             *[f"| {v['name']} | {s} | {v['how']} |" for s, v in ident.items()], ""]
    for t in trns:
        lines += [f"**[{text.ts(t['start'])}] {label(t['speaker'])}:** {t['text']}", ""]
    return "\n".join(lines)


def render_minutes(meta, ident, body):
    rows = sorted(ident.values(), key=lambda v: -v["talk_sec"])
    parts = [f"- **{v['name']}** — {v['talk_sec'] // 60} min ({v['how']})" for v in rows if v["talk_sec"] >= 5]
    return "\n".join([f"# Verbale — {meta['title']}", "",
                      f"Data: {meta['date']} · Durata: {text.ts(meta['duration'])}", "",
                      "## Partecipanti", *parts, "", body, "",
                      "---", "_Verbale generato automaticamente in locale: verificare nomi e azioni._", ""])


def _label(ident):
    return lambda s: ident[s]["name"] if s in ident else s


def make_minutes(cfg, out_dir):
    """(Ri)genera trascrizione.md e verbale.md da meeting.json."""
    out_dir = Path(out_dir)
    m = json.loads((out_dir / "meeting.json").read_text())
    trns, ident = text.turns(m["segments"]), m["speakers"]
    (out_dir / "trascrizione.md").write_text(render_transcript(m, trns, ident))
    if not cfg["llm"]["enabled"]:
        return out_dir
    who = [v["name"] for v in sorted(ident.values(), key=lambda v: -v["talk_sec"]) if v["talk_sec"] >= 5]
    try:
        body = summary.write(cfg, trns, _label(ident), who)
    except Exception as e:
        log.error("verbale non generato: %s", e)
        body = f"> Verbale non generato ({e}). Rilancia con: `voiceai verbale {out_dir}`"
    (out_dir / "verbale.md").write_text(render_minutes(m, ident, body))
    return out_dir


def process(cfg, audio_path, token):
    audio_path = Path(audio_path)
    when = dt.datetime.fromtimestamp(audio_path.stat().st_mtime)
    out_dir = cfg["paths"]["output"] / f"{when:%Y-%m-%d_%H%M}_{audio_path.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)

    llm.unload_all(cfg)
    r = asr.transcribe(audio_path, cfg, token)
    segs = text.fill_speakers(text.drop_hallucinations(r["segments"], cfg["asr"].get("initial_prompt")))
    trns = text.turns(segs)
    ident = identify(cfg, trns, r["embeddings"], r["talk"])

    meta = {"title": audio_path.stem, "date": f"{when:%Y-%m-%d %H:%M}", "duration": r["duration"],
            "source": str(audio_path), "segments": segs, "speakers": ident, "embeddings": r["embeddings"]}
    (out_dir / "meeting.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log.info("speaker: %s", {s: v["name"] for s, v in ident.items()})
    return make_minutes(cfg, out_dir)


def learn(cfg, out_dir, spk, name, minutes=False):
    """Corregge il nome di uno speaker e salva la sua impronta da questa riunione."""
    out_dir = Path(out_dir)
    f = out_dir / "meeting.json"
    m = json.loads(f.read_text())
    if spk not in m["speakers"]:
        raise SystemExit(f"{spk} non presente. Speaker: {', '.join(m['speakers'])}")
    if spk in m["embeddings"]:
        n = VoiceprintStore(cfg["paths"]["voiceprints"]).add(name, m["embeddings"][spk], source=out_dir.name)
        log.info("impronta salvata per %s (%d totali)", name, n)
    m["speakers"][spk].update(name=name, how="manuale")
    f.write_text(json.dumps(m, ensure_ascii=False, indent=1))
    if minutes:
        return make_minutes(cfg, out_dir)
    (out_dir / "trascrizione.md").write_text(render_transcript(m, text.turns(m["segments"]), m["speakers"]))
    return out_dir
