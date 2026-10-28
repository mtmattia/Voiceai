import json

import numpy as np
import pytest

from voiceai import config, names, pipeline, summary, text, watcher
from voiceai.speakers import VoiceprintStore, match


def seg(s, e, spk, t):
    return {"start": s, "end": e, "speaker": spk, "text": t}


# --- impronte ---
def test_match_unique_and_threshold(tmp_path):
    rng = np.random.default_rng(0)
    marco, giulia = rng.normal(size=256), rng.normal(size=256)
    st = VoiceprintStore(tmp_path)
    st.add("Marco", marco)
    st.add("Giulia", giulia)
    st.add("Giulia", giulia + rng.normal(scale=0.1, size=256))
    emb = {"S0": marco + rng.normal(scale=0.3, size=256),
           "S1": giulia + rng.normal(scale=0.3, size=256),
           "S2": rng.normal(size=256),                       # sconosciuto
           "S3": marco + rng.normal(scale=0.5, size=256)}    # simile a Marco ma Marco è già preso
    m, sims = match(emb, st.load(), 0.5)
    assert m == {"S0": "Marco", "S1": "Giulia"}
    assert sims["S2"]["Marco"] < 0.5


# --- testo ---
def test_turns_and_blocks():
    segs = [seg(0, 5, "A", "ciao"), seg(5.5, 9, "A", "a tutti"), seg(10, 20, "B", "x" * 50),
            seg(700, 710, "A", "dopo")]
    t = text.turns(segs)
    assert [x["speaker"] for x in t] == ["A", "B", "A"] and t[0]["text"] == "ciao a tutti"
    b = text.blocks(t, str, minutes=10)
    assert len(b) == 2 and b[1][0] == 700
    assert len(text.blocks(t, str, minutes=60, max_chars=60)) == 3


def test_fill_speakers():
    s = text.fill_speakers([seg(0, 1, None, "a"), seg(1, 2, "B", "b"), seg(2, 3, None, "c")])
    assert [x["speaker"] for x in s] == ["SPEAKER_UNK", "B", "B"]


# --- nomi dal testo ---
def test_rule_votes():
    t = text.turns([
        seg(0, 3, "S1", "Buongiorno, sono Marco, iniziamo."),
        seg(4, 8, "S1", "Giulia, a che punto sei col report?"),
        seg(9, 15, "S2", "Quasi finito, lo mando domani."),
        seg(16, 18, "S1", "Perfetto. Luca, tu?"),
        seg(19, 25, "S3", "Io ho finito ieri."),
        seg(26, 27, "S1", "Grazie Luca."),
        seg(28, 30, "S1", "Giulia, mi mandi anche le slide?"),
        seg(31, 33, "S2", "Sì certo."),
    ])
    v = names.rule_votes(t, ["Marco", "Giulia", "Luca"])
    assert v.resolve(2) == {"S1": "Marco", "S2": "Giulia", "S3": "Luca"}
    assert "Giulia" in v.neg["S1"]


def test_resolve_needs_majority():
    v = names.Votes()
    v.add("S1", "Marco", 2)
    v.add("S1", "Luca", 2)
    assert v.resolve(2) == {}


def test_llm_votes(monkeypatch):
    cfg = config.load("/nonexistent")
    replies = iter(['{"SPEAKER_01": "Sara", "SPEAKER_09": "X"}', "non json", '{"SPEAKER_01": "Sara"}'])
    monkeypatch.setattr(names.llm, "chat", lambda *a, **k: next(replies))
    t = [seg(i * 600, i * 600 + 5, "SPEAKER_01", "bla") for i in range(5)]
    cfg["llm"]["block_minutes"] = 20
    v = names.llm_votes(cfg, text.turns(t, max_gap=0), {"SPEAKER_01"}, str)
    assert v.pos["SPEAKER_01"]["Sara"] == 2 and "SPEAKER_09" not in v.pos


# --- verbale ---
def test_summary_reduce(monkeypatch):
    cfg = config.load("/nonexistent")
    calls = []
    monkeypatch.setattr(summary.llm, "chat", lambda c, s, u, **k: calls.append(u) or "nota " * 20)
    cfg["llm"]["block_minutes"] = 1
    cfg["llm"]["block_max_chars"] = 300
    t = [seg(i * 70, i * 70 + 60, "A" if i % 2 else "B", "parole " * 10) for i in range(12)]
    out = summary.write(cfg, text.turns(t), str, ["A", "B"])
    assert out.startswith("nota")
    assert any("note parziali" in c for c in calls)
    assert "Scrivi il verbale finale" in calls[-1]


# --- watcher ---
def test_stability(tmp_path):
    now = [0.0]
    f = tmp_path / "r.m4a"
    f.write_bytes(b"abc")
    s = watcher.Stability(60, clock=lambda: now[0])
    assert not s.ready(f)
    now[0] = 30
    assert not s.ready(f)
    now[0] = 61
    assert s.ready(f)
    f.write_bytes(b"abcd")
    assert not s.ready(f)


def test_candidates(tmp_path):
    for n in ["a.m4a", ".syncthing.b.m4a.tmp", "~c.m4a", "d.txt", "e.M4A", "f.m4a.part"]:
        (tmp_path / n).write_bytes(b"x")
    assert [p.name for p in watcher.candidates(tmp_path, [".m4a"])] == ["a.m4a", "e.M4A"]


def test_watch_once_moves_and_fails(tmp_path):
    cfg = config.load("/nonexistent")
    cfg["paths"].update(inbox=tmp_path / "in", archive=tmp_path / "arc")
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "ok.m4a").write_bytes(b"x")
    (tmp_path / "in" / "ko.m4a").write_bytes(b"x")

    def handle(p):
        if p.name == "ko.m4a":
            raise RuntimeError("boom")
        return "out"
    watcher.run(cfg, handle, once=True)
    assert (tmp_path / "arc" / "ok.m4a").exists()
    assert (tmp_path / "in" / "failed" / "ko.m4a").exists()
    assert "boom" in (tmp_path / "in" / "failed" / "ko.m4a.log").read_text()


# --- pipeline end-to-end con ASR e LLM finti ---
def test_process_end_to_end(tmp_path, monkeypatch):
    cfg = config.load("/nonexistent")
    cfg["paths"].update(output=tmp_path / "out", voiceprints=tmp_path / "vp")
    cfg["speakers"]["known_names"] = ["Marco", "Giulia"]
    rng = np.random.default_rng(1)
    vm = rng.normal(size=64)
    VoiceprintStore(cfg["paths"]["voiceprints"]).add("Marco", vm)

    segs = [seg(0, 4, "SPEAKER_00", "Giulia, ci aggiorni?"), seg(5, 9, "SPEAKER_01", "Sì, fatto."),
            seg(10, 12, None, "bene"), seg(13, 15, "SPEAKER_00", "Giulia, e il cliente?"),
            seg(16, 30, "SPEAKER_01", "Lo chiamo domani."), seg(31, 40, "SPEAKER_02", "Io aggiungo una cosa.")]
    fake = {"segments": segs, "duration": 40.0, "talk": {"SPEAKER_00": 8, "SPEAKER_01": 18, "SPEAKER_02": 9},
            "embeddings": {"SPEAKER_00": list(vm + rng.normal(scale=0.2, size=64)),
                           "SPEAKER_01": list(rng.normal(size=64)), "SPEAKER_02": list(rng.normal(size=64))}}
    monkeypatch.setattr(pipeline.asr, "transcribe", lambda *a: fake)
    monkeypatch.setattr(pipeline.llm, "unload_all", lambda c: None)
    monkeypatch.setattr(names.llm, "chat", lambda *a, **k: "{}")
    monkeypatch.setattr(summary.llm, "chat", lambda *a, **k: "## Sintesi\nok")

    audio = tmp_path / "riunione.m4a"
    audio.write_bytes(b"x")
    out = pipeline.process(cfg, audio, "tok")
    m = json.loads((out / "meeting.json").read_text())
    got = {s: (v["name"], v["how"]) for s, v in m["speakers"].items()}
    assert got == {"SPEAKER_00": ("Marco", "impronta"), "SPEAKER_01": ("Giulia", "testo"),
                   "SPEAKER_02": ("Ospite 1", "ospite")}
    tr = (out / "trascrizione.md").read_text()
    assert "**[00:00:10] Giulia:** bene" not in tr  # turno unito a Giulia, non riga separata con speaker vuoto
    assert "Marco:** Giulia, ci aggiorni?" in tr
    vb = (out / "verbale.md").read_text()
    assert "**Giulia**" in vb and "## Sintesi" in vb

    pipeline.learn(cfg, out, "SPEAKER_02", "Luca")
    assert "Luca" in VoiceprintStore(cfg["paths"]["voiceprints"]).load()
    assert "Luca:** Io aggiungo" in (out / "trascrizione.md").read_text()
    with pytest.raises(SystemExit):
        pipeline.learn(cfg, out, "SPEAKER_99", "X")


def test_allucinazioni():
    segs = [{"text": "Grazie."}, {"text": "Grazie. Grazie a tutti."}, {"text": "Grazie, Marco, ci vediamo."},
            {"text": "Sottotitoli creati dalla comunità Amara.org"}, {"text": "Il preventivo è pronto."}]
    assert [s["text"] for s in text.drop_hallucinations(segs)] == ["Grazie, Marco, ci vediamo.", "Il preventivo è pronto."]
