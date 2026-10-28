"""Coda di lavoro: prima i blocchi audio (GPU per ASR), poi a riposo i lavori LLM (GPU per Ollama)."""
import logging
import os
import threading
import time
from pathlib import Path

from . import correct, gpu, insights, llm, memos, names, people, text

log = logging.getLogger(__name__)


def known_names(db, cfg):
    conf = [r["name"] for r in db.q("SELECT name FROM persons WHERE confirmed=1")]
    return sorted(set(conf) | set(cfg["speakers"]["known_names"]))


def process_recording(db, cfg, engine, rec):
    r = engine.run(rec["path"])
    segs = text.fill_speakers(text.drop_hallucinations(r["segments"], cfg["asr"].get("initial_prompt")))
    trns = text.turns(segs)
    pmap, _ = people.assign(db, r["embeddings"], r["talk"], cfg, source=f"rec{rec['id']}")
    votes = names.rule_votes(trns, known_names(db, cfg))
    people.apply_name_hints(db, votes, pmap, cfg)
    now = time.time()
    with db.tx() as c:
        c.execute("DELETE FROM utterances WHERE recording_id=?", (rec["id"],))
        c.executemany("INSERT INTO utterances(recording_id, ts, start, end, label, person_id, text, overlap) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      [(rec["id"], rec["started_at"] + t["start"], t["start"], t["end"], t["speaker"],
                        pmap.get(t["speaker"]), t["text"], int(t.get("overlap", False))) for t in trns])
        c.execute("UPDATE recordings SET status='done', error=NULL, corrected=0, duration=? WHERE id=?",
                  (r["duration"], rec["id"]))
        c.execute("""INSERT INTO days(day, dirty, last_activity) VALUES(?,1,?)
                     ON CONFLICT(day) DO UPDATE SET dirty=1, last_activity=excluded.last_activity""",
                  (rec["day"], now))
    if not cfg["storage"]["keep_audio"]:
        Path(rec["path"]).unlink(missing_ok=True)
    log.info("blocco %s: %d frasi, persone %s", rec["id"], len(trns), sorted(set(pmap.values())))


class Worker(threading.Thread):
    def __init__(self, cfg, db, engine):
        super().__init__(daemon=True, name="voiceai-worker")
        self.cfg, self.db, self.engine = cfg, db, engine
        self.last_asr = 0.0
        self.wake = threading.Event()
        self.stop_flag = False
        self.gpu = threading.Lock()   # una cosa alla volta sulla GPU (worker o domanda dall'app)
        self.shared = gpu.GpuLock(cfg)   # ... e rispetto agli altri processi (watcher, CLI)

    def step(self):
        with self.gpu:
            return self._step()

    def yield_gpu(self):
        """A riposo: se un altro processo aspetta la GPU, scarica i modelli e cede il lock."""
        with self.gpu:
            if self.shared.held and self.shared.wanted():
                self.engine.unload()
                self.shared.release()
                log.info("GPU ceduta a un altro processo")

    def _step(self):
        """Un'unità di lavoro. -> True se ha fatto qualcosa."""
        db, cfg, w = self.db, self.cfg, self.cfg["worker"]
        rec = db.one("SELECT * FROM recordings WHERE status='pending' ORDER BY started_at, idx LIMIT 1")
        if rec:
            self.shared.acquire()
            if not self.engine.loaded:
                llm.unload_all(cfg)
            try:
                process_recording(db, cfg, self.engine, rec)
            except Exception as e:
                log.exception("blocco %s fallito", rec["id"])
                if "CUDA" in str(e) or "cuda" in type(e).__name__.lower():
                    # dopo un errore CUDA (es. OOM) il contesto GPU del processo è rotto: il blocco resta
                    # in coda e il processo esce, systemd lo riavvia pulito
                    n = int(rec["error"][4:]) + 1 if (rec["error"] or "").startswith("gpu:") else 1
                    if n < 3:
                        db.q("UPDATE recordings SET error=? WHERE id=?", (f"gpu:{n}", rec["id"]))
                        log.error("errore GPU: riavvio il processo, il blocco %s resta in coda (%d/3)", rec["id"], n)
                        os._exit(75)
                db.q("UPDATE recordings SET status='error', error=? WHERE id=?", (str(e)[:2000], rec["id"]))
            self.last_asr = time.time()
            return True

        if not cfg["llm"]["enabled"] or time.time() - self.last_asr < w["llm_after_idle_sec"]:
            return False
        # priorità: correzione del testo (lo usano tutti gli altri) -> memo (hanno scadenze) -> parole chiave -> giornata
        fix = cfg["llm"].get("correct", True) and db.one(
            "SELECT id FROM recordings WHERE status='done' AND corrected=0 ORDER BY started_at LIMIT 1")
        if fix:
            self.shared.acquire()
            self.engine.unload()
            try:
                correct.recording(db, cfg, fix["id"])
            except Exception:
                log.exception("correzione LLM fallita")
                db.q("UPDATE recordings SET corrected=1 WHERE id=?", (fix["id"],))
            return True
        mem = db.one("SELECT id FROM recordings WHERE status='done' AND memos_done=0 ORDER BY started_at LIMIT 1")
        kw = db.one("SELECT id FROM recordings WHERE status='done' AND keywords IS NULL LIMIT 1")
        day = db.one("SELECT day FROM days WHERE dirty=1 AND last_activity < ? ORDER BY day LIMIT 1",
                     (time.time() - w["day_idle_minutes"] * 60,))
        # resoconto per situazioni: si aggiorna presto (10 min senza blocchi nuovi), è una chiamata leggera
        sit = None if (mem or kw) else db.one(
            """SELECT d.day FROM days d WHERE d.last_activity < ? AND
               (SELECT COUNT(*) FROM recordings r WHERE r.day=d.day AND r.status='done' AND r.keywords IS NOT NULL)
               > COALESCE(d.situations_n, 0) ORDER BY d.day LIMIT 1""",
            (time.time() - w.get("situations_idle_minutes", 10) * 60,))
        if not (mem or kw or day or sit):
            return False
        self.shared.acquire()
        self.engine.unload()   # libera VRAM per Ollama
        try:
            if mem:
                memos.extract(db, cfg, mem["id"])
            elif kw:
                insights.recording_keywords(db, cfg, kw["id"])
            elif sit:
                insights.build_situations(db, cfg, sit["day"])
            else:
                log.info("giornata %s: riassunto e umore", day["day"])
                insights.build_day(db, cfg, day["day"])
        except Exception:
            log.exception("lavoro LLM fallito")
            # non riprovare all'infinito lo stesso lavoro
            if mem:
                db.q("UPDATE recordings SET memos_done=1 WHERE id=?", (mem["id"],))
            elif kw:
                db.q("UPDATE recordings SET keywords='[]' WHERE id=?", (kw["id"],))
            elif sit:
                db.q("UPDATE days SET situations_n=999999 WHERE day=?", (sit["day"],))
            else:
                db.q("UPDATE days SET dirty=0 WHERE day=?", (day["day"],))
        return True

    def enroll(self, path, name=None):
        """Impronta della voce dell'utente dall'app: scarica Whisper (la VRAM serve a pyannote), poi riconosce."""
        from . import asr
        with self.gpu:
            self.shared.acquire()
            self.engine.unload()
            emb = asr.embed_clip(path, self.cfg, getattr(self.engine, "token", None))
            return people.enroll_owner(self.db, emb, self.cfg, name)

    def ask(self, question):
        """Domanda dall'app: aspetta il blocco in corso, libera la VRAM, risponde."""
        from . import ask
        with self.gpu:
            self.shared.acquire()
            self.engine.unload()
            return ask.answer(self.db, self.cfg, question)

    def run(self):
        while not self.stop_flag:
            if not self.step():
                self.yield_gpu()
                self.wake.wait(self.cfg["worker"]["poll_seconds"])
                self.wake.clear()
