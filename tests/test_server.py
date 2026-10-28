import hashlib
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voiceai import config, insights, names, people, server, summary, worker
from voiceai.db import DB

TOK = "x" * 24
H = {"Authorization": "Bearer " + TOK}
rng = np.random.default_rng(3)
VOICE = {n: rng.normal(size=128) for n in ["io", "marco", "giulia"]}


def noisy(v, s=0.15):
    return list(v + rng.normal(scale=s, size=v.shape))


class FakeEngine:
    """Ogni file audio contiene un JSON che descrive cosa 'si sente'."""
    loaded = False

    def run(self, path):
        self.loaded = True
        spec = json.loads(open(path).read())
        return {"segments": spec["segs"], "talk": spec["talk"], "duration": 300.0,
                "embeddings": {k: noisy(VOICE[v]) for k, v in spec["voices"].items()}}

    def unload(self):
        self.loaded = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = config.load("/nonexistent")
    cfg["storage"]["data_dir"] = tmp_path
    cfg["speakers"]["known_names"] = ["Marco", "Giulia"]
    cfg["worker"].update(llm_after_idle_sec=0, day_idle_minutes=0, situations_idle_minutes=0)
    db = DB(tmp_path / "v.db")
    w = worker.Worker(cfg, db, FakeEngine())
    monkeypatch.setattr(worker.llm, "unload_all", lambda c: None)
    return cfg, db, w, TestClient(server.create_app(cfg, db, TOK, w))


def up(client, spec, idx, t0, sha=None):
    data = json.dumps(spec).encode()
    return client.post("/api/upload", headers=H, files={"file": (f"b{idx}.m4a", data)},
                       data={"sha256": sha or hashlib.sha256(data).hexdigest(), "device": "poco",
                             "session": "s1", "idx": str(idx), "started_at": str(t0)})


def seg(s, e, spk, t):
    return {"start": s, "end": e, "speaker": spk, "text": t}


BLOCK1 = {"voices": {"SPEAKER_00": "io", "SPEAKER_01": "marco"}, "talk": {"SPEAKER_00": 60, "SPEAKER_01": 40},
          "segs": [seg(0, 5, "SPEAKER_00", "Marco, hai sentito il fornitore?"),
                   seg(6, 20, "SPEAKER_01", "Sì, la consegna slitta a venerdì."),
                   seg(21, 25, "SPEAKER_00", "Ok, sono un po' stanco oggi."),
                   seg(26, 30, "SPEAKER_00", "Marco, mandami il preventivo?"),
                   seg(31, 40, "SPEAKER_01", "Te lo mando stasera.")]}
# blocco successivo: etichette locali scambiate, la stessa persona deve essere riconosciuta
BLOCK2 = {"voices": {"SPEAKER_00": "marco", "SPEAKER_01": "io", "SPEAKER_02": "giulia"},
          "talk": {"SPEAKER_00": 30, "SPEAKER_01": 30, "SPEAKER_02": 3},
          "segs": [seg(0, 10, "SPEAKER_00", "Il preventivo del fornitore è pronto."),
                   seg(11, 20, "SPEAKER_01", "Perfetto. Marco, lo giri anche a Giulia?"),
                   seg(21, 25, "SPEAKER_00", "Certo."), seg(26, 28, "SPEAKER_02", "Grazie!")]}


def test_auth_and_checksum(env):
    cfg, db, w, c = env
    assert c.get("/api/persons").status_code == 401
    r = up(c, BLOCK1, 0, time.time(), sha="0" * 64)
    assert r.status_code == 422 and not r.json()["ok"]
    assert db.q("SELECT * FROM recordings") == []
    assert list((cfg["storage"]["data_dir"] / "audio" / ".incoming").iterdir()) == []


def test_full_flow(env, monkeypatch):
    cfg, db, w, c = env
    t0 = time.mktime((2026, 10, 28, 9, 0, 0, 0, 0, -1))
    r = up(c, BLOCK1, 0, t0)
    assert r.json()["ok"] and r.json()["sha256"] == hashlib.sha256(json.dumps(BLOCK1).encode()).hexdigest()
    assert up(c, BLOCK1, 0, t0).json()["duplicate"]          # ri-invio dopo risposta persa: nessun doppione
    assert c.get("/api/check/" + r.json()["sha256"], headers=H).json()["present"]
    assert up(c, BLOCK2, 1, t0 + 300).json()["ok"]

    assert w.step() and w.step()
    ps = {p["id"]: p for p in c.get("/api/persons", headers=H).json()}
    assert len(ps) == 2                       # Giulia ha parlato 3 s: nessuna persona creata
    utt = db.q("SELECT label, person_id, recording_id FROM utterances ORDER BY ts")
    me = utt[0]["person_id"]
    marco = utt[1]["person_id"]
    assert me != marco
    # stessa persona nei due blocchi nonostante etichette locali diverse
    assert {u["person_id"] for u in utt if u["recording_id"] == 2 and u["label"] == "SPEAKER_01"} == {me}
    assert ps[marco]["name"] == "Marco?" and not ps[marco]["confirmed"]   # nome probabile da 3 vocativi
    assert ps[me]["name"].startswith("Sconosciuto")

    # modifica dalla shell web / app
    assert c.patch(f"/api/persons/{me}", headers=H, json={"name": "Mattia", "is_owner": True}).json()["confirmed"]
    assert c.patch(f"/api/persons/{marco}", headers=H, json={"name": "Marco"}).json()["name"] == "Marco"

    # ricerca full-text (senza accenti, case-insensitive)
    res = c.get("/api/search", headers=H, params={"q": "consegna venerdi"}).json()
    assert res["frasi"][0]["speaker"] == "Marco"

    # fase LLM: parole chiave per blocco, poi giornata con umore e suggerimento
    calls = []

    def fake_chat(cfg_, sys, user, json_mode=False):
        calls.append(user)
        if "parole chiave" in user:
            assert "escluso Mattia" in user
            return json.dumps({"titolo": "Fornitore in ritardo", "parole_chiave": ["fornitore", "preventivo"],
                               "sintesi": "Ritardo della consegna.",
                               "sensazioni": [{"persona": "Marco", "sensazione": "tranquillo", "perche": "risponde sereno"},
                                              {"persona": "Mattia", "sensazione": "stanco", "perche": "lo dice"},
                                              {"persona": "Pippo", "sensazione": "felice", "perche": "inventato"}]})
        if "elenco di situazioni" in user:
            assert "Fornitore in ritardo" in user and "Ritardo della consegna" in user
            return json.dumps({"situazioni": [{"dalle": "09:00", "alle": "09:05", "titolo": "Fornitore",
                                               "persone": ["Marco"], "contesto": "telefonata", "sintesi": "Consegna slitta."}]})
        if "stato d'animo" in user:
            assert "Mattia" in user and "stanco" in user and "consegna slitta" not in user.split("FRASI DI")[1]
            return '{"umore": "affaticato", "punteggio": -1, "motivazione": "dice di essere stanco", "suggerimento": "Fai una pausa."}'
        return "## Sintesi\nRitardo fornitore."
    monkeypatch.setattr(insights.llm, "chat", fake_chat)
    monkeypatch.setattr(summary.llm, "chat", fake_chat)
    while w.step():
        pass
    d = c.get("/api/days/2026-10-28", headers=H).json()
    assert d["mood"] == "affaticato" and d["mood_score"] == -1 and d["tip"] == "Fai una pausa."
    assert "fornitore" in json.loads(d["keywords"]) and d["recordings"][0]["title"] == "Fornitore in ritardo"
    sits = json.loads(d["situations"])
    assert sits[0]["titolo"] == "Fornitore" and sits[0]["persone"] == ["Marco"]
    # sensazioni: solo interlocutori presenti nel testo, mai l'utente stesso
    assert json.loads(d["recordings"][0]["feelings"]) == [{"persona": "Marco", "sensazione": "tranquillo", "perche": "risponde sereno"}]
    assert c.get("/api/search", headers=H, params={"q": "preventivo"}).json()["registrazioni"]
    assert not w.step()                       # niente da fare

    # nuovo blocco quel giorno -> la giornata torna da rifare
    up(c, BLOCK2 | {"segs": BLOCK2["segs"][:1]}, 2, t0 + 900)
    w.step()
    assert db.one("SELECT dirty FROM days WHERE day='2026-10-28'")["dirty"] == 1


def test_merge_and_delete(env):
    cfg, db, w, c = env
    a, b = db.new_person(), db.new_person("Luca", confirmed=True)
    db.add_print(a, VOICE["marco"])
    db.add_votes(a, "Luca", 2)
    assert c.post(f"/api/persons/{a}/merge", headers=H, json={"into": a}).status_code == 400
    assert c.post(f"/api/persons/{a}/merge", headers=H, json={"into": b}).json()["ok"]
    ps = c.get("/api/persons", headers=H).json()
    assert len(ps) == 1 and ps[0]["n_prints"] == 1 and ps[0]["name_votes"][0]["votes"] == 2
    c.delete(f"/api/persons/{b}", headers=H)
    assert db.prints() == {}


def test_error_is_recorded_and_retry(env):
    cfg, db, w, c = env
    data = b"not json"
    c.post("/api/upload", headers=H, files={"file": ("x.m4a", data)},
           data={"sha256": hashlib.sha256(data).hexdigest(), "started_at": str(time.time())})
    w.step()
    r = db.one("SELECT * FROM recordings")
    assert r["status"] == "error" and r["error"]
    c.post(f"/api/recordings/{r['id']}/retry", headers=H)
    assert db.one("SELECT status FROM recordings")["status"] == "pending"


def test_print_cap(tmp_path):
    db = DB(tmp_path / "v.db")
    p = db.new_person()
    db.add_print(p, [1, 0], manual=True)
    for i in range(30):
        db.add_print(p, [1, i], cap=5)
    assert len(db.prints()[p]) == 6


# --- memo e domande ---
from voiceai import memos, ask


def test_memo_extract_explicit_guess_and_learn(env, monkeypatch):
    cfg, db, w, c = env
    t0 = time.mktime((2026, 10, 28, 9, 0, 0, 0, 0, -1))
    up(c, BLOCK1, 0, t0)
    w.step()
    reply = {"todo": [{"titolo": "Stampare i documenti", "descrizione": "Per Marco, due copie.", "quando": "2026-10-28T12:00", "momento": None},
                      {"titolo": "Prendere il gatto", "quando": None, "momento": "sera"},
                      {"titolo": "Stampare i documenti", "quando": None, "momento": None},   # doppione
                      {"titolo": "", "quando": None}, "spazzatura",
                      {"titolo": "Riunione con il fornitore", "quando": "2026-10-29T10:00"},    # incontro: va in agenda
                      {"titolo": "Cena da mamma", "quando": None}],                              # doppione dell'agenda
             "agenda": [{"titolo": "Cena da mamma", "descrizione": "Porta il vino.", "quando": "2026-10-28T20:00"},
                        {"titolo": "Chiamare il fornitore", "quando": None}]}            # senza data: diventa todo
    # llm è un unico modulo: una sola funzione finta che risponde in base al prompt
    monkeypatch.setattr(memos.llm, "chat", lambda c_, s_, u, **k: json.dumps(reply) if '"agenda"' in u else "{}")
    w.step()   # correzione del testo (nessuna correzione proposta)
    w.step()   # memo
    ms = {m["text"]: m for m in c.get("/api/memos", headers=H).json()}
    assert set(ms) == {"Stampare i documenti", "Prendere il gatto", "Chiamare il fornitore"}
    assert ms["Stampare i documenti"]["descr"] == "Per Marco, due copie."
    ag = c.get("/api/memos?kind=agenda", headers=H).json()
    assert [(a["text"], a["descr"], time.localtime(a["due_ts"]).tm_hour) for a in ag] == [
        ("Cena da mamma", "Porta il vino.", 20), ("Riunione con il fornitore", "", 10)]
    assert ms["Stampare i documenti"]["due_kind"] == "esplicita"
    assert time.localtime(ms["Stampare i documenti"]["due_ts"]).tm_hour == 12
    g = ms["Prendere il gatto"]
    assert g["due_kind"] == "ipotizzata" and time.localtime(g["due_ts"]).tm_hour == 19   # default "sera"

    # spunta + manuale
    assert c.patch(f"/api/memos/{g['id']}", headers=H, json={"done": True}).json()["done_ts"]
    assert c.post("/api/memos", headers=H, json={"text": "Mangiare da mamma"}).json()["source"] == "manuale"
    assert c.get("/api/memos?done=1", headers=H).json()[0]["text"] == "Prendere il gatto"


def test_guess_learns_rhythm(tmp_path):
    db = DB(tmp_path / "v.db")
    base = time.mktime((2026, 10, 20, 8, 0, 0, 0, 0, -1))
    for i in range(3):  # di solito "sera" la spunti verso le 21:30
        mid = memos.add(db, f"cosa diversa numero {i} xyz{i}", moment="sera")
        db.q("UPDATE memos SET done=1, done_ts=? WHERE id=?", (base + i * 86400 + 13.5 * 3600, mid))
    due = memos.guess_due(db, "Chiamare il commercialista", "sera", base + 10 * 86400)
    assert time.localtime(due).tm_hour == 21 and time.localtime(due).tm_min == 30
    for i in range(2):  # memo simili spuntati alle 7:15
        mid = memos.add(db, f"Portare fuori il cane {i}")
        db.q("UPDATE memos SET done=1, done_ts=? WHERE id=?", (base + i * 86400 - 45 * 60, mid))
    due = memos.guess_due(db, "portare il cane", None, base + 10 * 86400)
    assert time.localtime(due).tm_hour == 7
    assert memos.guess_due(db, "boh", None, base) is None


def test_ask(env, monkeypatch):
    cfg, db, w, c = env
    up(c, BLOCK1, 0, time.time())
    w.step()
    seen = {}

    def fake(cfg_, sys, user, json_mode=False):
        seen["u"] = user
        return "Marco ha detto che la consegna slitta a venerdì."
    monkeypatch.setattr(ask.llm, "chat", fake)
    r = c.post("/api/ask", headers=H, json={"q": "cosa ha detto Marco sulla consegna?"}).json()
    assert "venerdì" in r["risposta"] and "consegna slitta" in seen["u"]
    assert not w.engine.loaded            # VRAM liberata per l'LLM
    r = c.post("/api/ask", headers=H, json={"q": "zzz"}).json()
    assert "Non ho trovato" in r["risposta"]


# --- sync telefono <-> PC ---
def test_sync_pull_incremental_and_lww(env):
    cfg, db, w, c = env
    up(c, BLOCK1, 0, time.time())
    w.step()
    full = c.get("/api/sync/pull?since=0", headers=H).json()
    assert full["utterances"] and full["persons"] and not full["more"]
    rev = full["rev"]
    assert c.get(f"/api/sync/pull?since={rev}", headers=H).json()["persons"] == []

    pid = full["persons"][0]["id"]
    old = full["persons"][0]["updated_at"]
    # modifica dal telefono più recente -> vince
    r = c.post("/api/sync/push", headers=H, json={"persons": [
        {"id": pid, "name": "Mattia", "confirmed": 1, "is_owner": 1, "notes": "", "updated_at": time.time() + 1}]}).json()
    assert r["applied"] == 1
    # modifica vecchia (telefono rimasto offline) -> scartata
    r = c.post("/api/sync/push", headers=H, json={"persons": [
        {"id": pid, "name": "Vecchio", "confirmed": 1, "is_owner": 0, "updated_at": old}]}).json()
    assert r["stale"] == 1 and r["persons"][0]["name"] == "Mattia"   # il telefono riceve la versione vincente
    inc = c.get(f"/api/sync/pull?since={rev}", headers=H).json()
    assert [p["name"] for p in inc["persons"]] == ["Mattia"]

    # memo creato offline sul telefono, poi modificato sul PC, poi cancellato dal telefono
    t = time.time() - 10   # modifiche del telefono di 10 s fa, quella del PC è adesso
    c.post("/api/sync/push", headers=H, json={"memos": [{"uid": "abc", "text": "Prendere il gatto", "updated_at": t}]})
    mid = db.one("SELECT id FROM memos WHERE uid='abc'")["id"]
    c.patch(f"/api/memos/{mid}", headers=H, json={"done": True})
    c.post("/api/sync/push", headers=H, json={"memos": [{"uid": "abc", "text": "x", "done": 0, "updated_at": t + 0.5}]})
    assert db.one("SELECT done FROM memos WHERE uid='abc'")["done"] == 1      # PC più recente
    c.post("/api/sync/push", headers=H, json={"memos": [{"uid": "abc", "deleted": True, "updated_at": time.time() + 5}]})
    inc2 = c.get(f"/api/sync/pull?since={inc['rev']}", headers=H).json()
    assert {"tbl": "memos", "key": "abc"} == {k: inc2["deleted"][-1][k] for k in ("tbl", "key")}
    # ri-push del memo cancellato da un telefono non aggiornato: non resuscita
    c.post("/api/sync/push", headers=H, json={"memos": [{"uid": "abc", "text": "zombie", "updated_at": t + 1}]})
    assert db.one("SELECT * FROM memos WHERE uid='abc'") is None
    r = c.post("/api/sync/push", headers=H, json={"memos": [{"uid": "abc", "text": "zombie", "updated_at": t + 2}]}).json()
    assert r["deleted_memos"] == ["abc"]


def test_sync_pagination(env):
    cfg, db, w, c = env
    up(c, BLOCK1, 0, time.time())
    up(c, BLOCK2, 1, time.time() + 300)
    w.step(); w.step()
    since, seen = 0, set()
    for _ in range(50):
        r = c.get(f"/api/sync/pull?since={since}&limit=3", headers=H).json()
        seen |= {u["id"] for u in r["utterances"]}
        since = r["rev"]
        if not r["more"]:
            break
    assert seen == {u["id"] for u in db.q("SELECT id FROM utterances")}


def test_gpu_lock_tra_processi(env, tmp_path):
    """Il watcher chiede la GPU: il server a riposo scarica i modelli e la cede."""
    import subprocess, sys
    cfg, db, w, client = env
    w.shared.acquire()
    w.engine.loaded = True
    code = ("import sys, pathlib; from voiceai import gpu; "
            f"cfg={{'storage': {{'data_dir': pathlib.Path({str(tmp_path)!r})}}}}; "
            "l=gpu.GpuLock(cfg); l.acquire(); print('preso')")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    for _ in range(100):
        if w.shared.wanted():
            break
        time.sleep(0.05)
    assert w.shared.wanted()
    w.yield_gpu()
    assert p.communicate(timeout=10)[0].strip() == "preso"
    assert not w.engine.loaded and not w.shared.held


def test_cronologia(env, monkeypatch):
    cfg, db, w, c = env
    t0 = time.mktime((2026, 10, 28, 8, 23, 0, 0, 0, -1))
    up(c, BLOCK1, 0, t0)
    assert w.step()
    me, marco = [u["person_id"] for u in db.q("SELECT person_id FROM utterances ORDER BY ts")][:2]
    c.patch(f"/api/persons/{me}", headers=H, json={"name": "Mattia", "is_owner": True})
    c.patch(f"/api/persons/{marco}", headers=H, json={"name": "Marco"})
    db.q("INSERT INTO asks(ts, question) VALUES(?,?)", (t0 + 600, "cosa ha detto Marco?"))
    ev = c.get("/api/days/2026-10-28/timeline", headers=H).json()
    assert [(e["ora"], e["text"]) for e in ev] == [
        ("08:23", "Mattia parla con Marco"), ("08:33", "Mattia parla con AI: «cosa ha detto Marco?»")]
    assert ev[0]["recording_id"] == 1
    assert c.get("/api/days/28-10-2026/timeline", headers=H).status_code == 400


def test_abbinamento(env):
    cfg, db, w, c = env
    assert c.post("/api/pair", json={"code": "123456"}).status_code == 403
    f = cfg["storage"]["data_dir"] / "pair.json"
    f.write_text(json.dumps({"code": "123456", "expires": time.time() + 600, "attempts": 0}))
    assert c.post("/api/pair", json={"code": "000000"}).status_code == 403
    assert c.post("/api/pair", json={"code": "123456"}).json() == {"token": TOK}
    assert not f.exists()                                   # monouso
    f.write_text(json.dumps({"code": "123456", "expires": time.time() + 600, "attempts": 0}))
    for _ in range(5):
        c.post("/api/pair", json={"code": "999999"})
    assert c.post("/api/pair", json={"code": "123456"}).status_code == 403   # bloccato dopo 5 errori


def test_correzione_llm(env, monkeypatch):
    from voiceai import correct
    cfg, db, w, c = env
    up(c, BLOCK1, 0, time.time())
    assert w.step()
    calls = []

    def fake_chat(cfg_, sys, user, json_mode=False):
        calls.append(user)
        if "Rileggi" in user:
            assert "fornitore" in user and "Mandami" not in user   # la riscrittura è già scartata dal filtro
            return '{"accetta": [1]}'
        return json.dumps({"correzioni": [{"n": 1, "testo": "Marco, hai sentito il fornitore? "},   # uguale: ignorata
                                          {"n": 1, "testo": "Marco, hai sentito il fornitore."},
                                          {"n": 2, "testo": "Sì, la consegna slitta a venerdì prossimo."},
                                          {"n": 4, "testo": "Mandami tutto quello che hai, grazie mille davvero!"}]})
    monkeypatch.setattr(correct.llm, "chat", fake_chat)
    assert correct.recording(db, cfg, 1) == 1
    u = db.q("SELECT text, raw FROM utterances ORDER BY start")
    assert u[0] == {"text": "Marco, hai sentito il fornitore.", "raw": "Marco, hai sentito il fornitore?"}
    assert u[1]["raw"] is None                     # proposta ma non accettata alla rilettura
    assert db.search("fornitore")                 # indice di ricerca aggiornato
    assert db.one("SELECT corrected FROM recordings WHERE id=1")["corrected"] == 1


def test_pull_chiave_riusata(tmp_path):
    db = DB(tmp_path / "v.db")
    pid = db.new_person("Primo")
    db.delete_person(pid)
    assert db.new_person("Secondo") == pid           # SQLite ricicla l'id
    out = db.pull(0)
    assert [p["name"] for p in out["persons"]] == ["Secondo"] and out["deleted"] == []


def test_voce_persona(env, tmp_path):
    import subprocess
    cfg, db, w, c = env
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=20", str(wav)], check=True)
    pid = db.new_person("Marco")
    with db.tx() as x:
        x.execute("INSERT INTO recordings(id, sha256, started_at, day, path, status) VALUES(1,'x',0,'2026-10-28',?,'done')", (str(wav),))
        x.executemany("INSERT INTO utterances(recording_id, ts, start, end, person_id, text) VALUES(1,?,?,?,?,'ciao')",
                      [(0, 1, 5, pid), (0, 8, 9, pid)])                     # la seconda è troppo corta
    r = c.get(f"/api/persons/{pid}/voice", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content[:4] == b"OggS"
    assert c.get(f"/api/persons/{db.new_person('Muto')}/voice", headers=H).status_code == 404


def test_riconosci_la_mia_voce(env, monkeypatch):
    from voiceai import asr
    cfg, db, w, c = env
    up(c, BLOCK1, 0, time.time())
    assert w.step()
    me = db.q("SELECT person_id FROM utterances ORDER BY ts")[0]["person_id"]
    monkeypatch.setattr(asr, "embed_clip", lambda *a, **k: noisy(VOICE["io"]))
    r = c.post("/api/me/voice", headers=H, files={"file": ("io.ogg", b"x")}, data={"name": "Mattia"}).json()
    assert r["id"] == me and r["gia_presente"] and r["name"] == "Mattia"   # la voce era già nell'archivio
    p = db.one("SELECT * FROM persons WHERE id=?", (me,))
    assert p["is_owner"] == 1 and p["confirmed"] == 1
    assert db.one("SELECT COUNT(*) n FROM prints WHERE person_id=? AND manual=1", (me,))["n"] == 1


def test_resoconto_mentre_si_registra(env, monkeypatch):
    cfg, db, w, c = env
    cfg["worker"]["situations_idle_minutes"] = 60          # si sta ancora registrando
    cfg["worker"]["situations_every_blocks"] = 2
    t0 = time.mktime((2026, 10, 28, 9, 0, 0, 0, 0, -1))
    calls = []
    monkeypatch.setattr(insights, "build_situations", lambda db_, cfg_, day: calls.append(day) or
                        db_.q("UPDATE days SET situations_n=99 WHERE day=?", (day,)))
    for i in range(2):
        up(c, BLOCK1 | {"segs": BLOCK1["segs"][i:i + 1]}, i, t0 + 300 * i)
    db.q("UPDATE recordings SET status='done', corrected=1, memos_done=1, keywords='[]'")
    db.q("INSERT INTO days(day, dirty, last_activity) VALUES('2026-10-28', 0, ?)", (time.time(),))
    assert w.step() and calls == ["2026-10-28"]
