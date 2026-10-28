"""API per l'app Android + shell web. Da esporre solo su Tailscale."""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .db import DB

WEB = Path(__file__).parent / "web" / "index.html"
_SAFE = re.compile(r"[^\w.-]+")


class PersonPatch(BaseModel):
    name: Optional[str] = None
    confirmed: Optional[bool] = None
    is_owner: Optional[bool] = None
    notes: Optional[str] = None


class Merge(BaseModel):
    into: int


class Question(BaseModel):
    q: str


class MemoIn(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None
    due_ts: Optional[float] = None


def create_app(cfg, db: DB, token: str, worker=None):
    app = FastAPI(title="VoiceAi")
    audio_dir = cfg["storage"]["data_dir"] / "audio"
    incoming = audio_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    def auth(authorization: str = Header("")):
        if not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), token):
            raise HTTPException(401, "token non valido")

    A = [Depends(auth)]

    @app.get("/")
    def index():
        return FileResponse(WEB)

    # --- upload dal telefono ---
    @app.post("/api/upload", dependencies=A)
    def upload(file: UploadFile = File(...), sha256: str = Form(...), device: str = Form("phone"),
               session: str = Form(""), idx: int = Form(0), started_at: float = Form(...)):
        sha256 = sha256.lower().strip()
        tmp = incoming / f"{uuid.uuid4().hex}.part"
        h, size = hashlib.sha256(), 0
        with open(tmp, "wb") as f:
            while chunk := file.file.read(1 << 20):
                h.update(chunk)
                size += len(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        actual = h.hexdigest()
        if actual != sha256:
            tmp.unlink(missing_ok=True)
            return JSONResponse({"ok": False, "sha256": actual, "error": "checksum diverso"}, status_code=422)
        dup = db.one("SELECT id FROM recordings WHERE sha256=?", (actual,))
        if dup:
            tmp.unlink(missing_ok=True)
            return {"ok": True, "sha256": actual, "id": dup["id"], "duplicate": True}
        when = dt.datetime.fromtimestamp(started_at)
        ext = (Path(file.filename or "").suffix or ".m4a").lower()
        dest = audio_dir / f"{when:%Y-%m-%d}" / _SAFE.sub("_", f"{when:%H%M%S}_{device}_{session}_{idx}{ext}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(tmp, dest)
        with db.tx() as c:
            rid = c.execute("""INSERT INTO recordings(sha256, device, session, idx, started_at, day, path, size,
                               received) VALUES(?,?,?,?,?,?,?,?,strftime('%s','now'))""",
                            (actual, device, session, idx, started_at, f"{when:%Y-%m-%d}", str(dest), size)).lastrowid
        if worker:
            worker.wake.set()
        return {"ok": True, "sha256": actual, "id": rid}

    @app.get("/api/check/{sha256}", dependencies=A)
    def check(sha256: str):
        """Il telefono può chiedere se un file è già arrivato (es. risposta persa per rete)."""
        r = db.one("SELECT id, status FROM recordings WHERE sha256=?", (sha256.lower(),))
        return {"present": bool(r), **(r or {})}

    @app.get("/api/status", dependencies=A)
    def status():
        counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM recordings GROUP BY status")}
        return {"recordings": counts, "asr_loaded": bool(worker and worker.engine.loaded)}

    # --- persone ---
    @app.get("/api/persons", dependencies=A)
    def persons():
        return db.persons()

    @app.patch("/api/persons/{pid}", dependencies=A)
    def patch_person(pid: int, p: PersonPatch):
        if not db.one("SELECT id FROM persons WHERE id=?", (pid,)):
            raise HTTPException(404)
        kw = {k: (int(v) if isinstance(v, bool) else v) for k, v in p.model_dump().items() if v is not None}
        if "name" in kw:
            kw["name"] = kw["name"].strip()[:80] or "Senza nome"
            kw.setdefault("confirmed", 1)   # rinominare a mano = confermare
        db.update_person(pid, **kw)
        return db.one("SELECT * FROM persons WHERE id=?", (pid,))

    @app.post("/api/persons/{pid}/merge", dependencies=A)
    def merge(pid: int, m: Merge):
        if pid == m.into or not db.one("SELECT id FROM persons WHERE id=?", (m.into,)):
            raise HTTPException(400, "destinazione non valida")
        db.merge_persons(pid, m.into)
        return {"ok": True}

    @app.delete("/api/persons/{pid}", dependencies=A)
    def delete_person(pid: int):
        db.delete_person(pid)
        return {"ok": True}

    @app.get("/api/persons/{pid}/utterances", dependencies=A)
    def person_utt(pid: int, limit: int = 30):
        return db.q("""SELECT u.ts, u.text, u.recording_id, u.start FROM utterances u WHERE person_id=?
                       ORDER BY ts DESC LIMIT ?""", (pid, min(limit, 500)))

    # --- giornate, registrazioni, ricerca ---
    @app.get("/api/days", dependencies=A)
    def days():
        return db.q("""SELECT d.day, d.mood, d.mood_score, d.keywords, d.dirty,
                       (SELECT COUNT(*) FROM recordings r WHERE r.day=d.day) AS n_rec,
                       (SELECT COALESCE(SUM(duration),0) FROM recordings r WHERE r.day=d.day) AS seconds
                       FROM days d ORDER BY d.day DESC""")

    @app.get("/api/days/{day}", dependencies=A)
    def day(day: str):
        d = db.one("SELECT * FROM days WHERE day=?", (day,)) or {"day": day}
        d["recordings"] = db.q("""SELECT id, started_at, duration, status, error, title, keywords
                                  FROM recordings WHERE day=? ORDER BY started_at""", (day,))
        return d

    @app.get("/api/days/{day}/timeline", dependencies=A)
    def day_timeline(day: str):
        from . import timeline
        try:
            return timeline.build(db, day)
        except ValueError:
            raise HTTPException(400, "giorno non valido (AAAA-MM-GG)")

    @app.post("/api/days/{day}/rebuild", dependencies=A)
    def rebuild(day: str):
        db.q("UPDATE days SET dirty=1, last_activity=0 WHERE day=?", (day,))
        if worker:
            worker.last_asr = 0
            worker.wake.set()
        return {"ok": True}

    @app.get("/api/recordings/{rid}", dependencies=A)
    def recording(rid: int):
        r = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
        if not r:
            raise HTTPException(404)
        r["utterances"] = db.q("""SELECT u.id, u.ts, u.start, u.end, u.text, u.person_id,
                                  COALESCE(p.name, u.label) AS speaker FROM utterances u
                                  LEFT JOIN persons p ON p.id=u.person_id WHERE recording_id=? ORDER BY start""",
                               (rid,))
        return r

    @app.post("/api/recordings/{rid}/retry", dependencies=A)
    def retry(rid: int):
        db.q("UPDATE recordings SET status='pending', error=NULL WHERE id=?", (rid,))
        if worker:
            worker.wake.set()
        return {"ok": True}

    @app.get("/api/recordings/{rid}/audio", dependencies=A)
    def audio(rid: int):
        r = db.one("SELECT path FROM recordings WHERE id=?", (rid,))
        if not r or not Path(r["path"]).exists():
            raise HTTPException(404, "audio non disponibile")
        return FileResponse(r["path"])

    @app.get("/api/search", dependencies=A)
    def search(q: str, limit: int = 50):
        return {"frasi": db.search(q, min(limit, 200)), "registrazioni": db.search_keywords(q, 30)}

    @app.post("/api/ask", dependencies=A)
    def ask_q(body: Question):
        from . import ask
        q = body.q.strip()[:500]
        if not q:
            raise HTTPException(400, "domanda vuota")
        db.q("INSERT INTO asks(ts, question) VALUES(?,?)", (time.time(), q))
        try:
            return worker.ask(q) if worker else ask.answer(db, cfg, q)
        except Exception as e:
            raise HTTPException(503, f"LLM non disponibile: {e}")

    # --- sync con il telefono ---
    @app.get("/api/sync/pull", dependencies=A)
    def sync_pull(since: int = 0, limit: int = 2000):
        return db.pull(since, max(1, min(limit, 5000)))

    @app.post("/api/sync/push", dependencies=A)
    def sync_push(data: dict):
        return db.push(data) | {"server_time": time.time()}

    # --- memo ---
    @app.get("/api/memos", dependencies=A)
    def memo_list(done: int = 0, limit: int = 200):
        order = "done_ts DESC" if done else "due_ts IS NULL, due_ts, created"
        return db.q(f"SELECT * FROM memos WHERE done=? ORDER BY {order} LIMIT ?", (int(bool(done)), min(limit, 1000)))

    @app.post("/api/memos", dependencies=A)
    def memo_add(m: MemoIn):
        from . import memos
        if not (m.text or "").strip():
            raise HTTPException(400, "testo vuoto")
        mid = memos.add(db, m.text.strip()[:200], due_ts=m.due_ts,
                        due_kind="esplicita" if m.due_ts else None, source="manuale")
        return db.one("SELECT * FROM memos WHERE id=?", (mid,)) if mid else {"duplicate": True}

    @app.patch("/api/memos/{mid}", dependencies=A)
    def memo_patch(mid: int, m: MemoIn):
        if not db.one("SELECT id FROM memos WHERE id=?", (mid,)):
            raise HTTPException(404)
        with db.tx() as c:
            if m.text is not None:
                c.execute("UPDATE memos SET text=? WHERE id=?", (m.text.strip()[:200], mid))
            if m.due_ts is not None:
                c.execute("UPDATE memos SET due_ts=?, due_kind='esplicita', notified=0 WHERE id=?", (m.due_ts, mid))
            if m.done is not None:
                # done_ts = quando l'hai fatto davvero: è ciò che insegna i tuoi ritmi
                c.execute("UPDATE memos SET done=?, done_ts=? WHERE id=?",
                          (int(m.done), time.time() if m.done else None, mid))
        return db.one("SELECT * FROM memos WHERE id=?", (mid,))

    @app.delete("/api/memos/{mid}", dependencies=A)
    def memo_del(mid: int):
        db.q("DELETE FROM memos WHERE id=?", (mid,))
        return {"ok": True}

    return app


def serve(cfg, token, hf_token, with_worker=True):
    import uvicorn

    from . import asr
    from .worker import Worker
    db = DB(cfg["storage"]["data_dir"] / "voiceai.db")
    worker = None
    if with_worker:
        worker = Worker(cfg, db, asr.Engine(cfg, hf_token))
        worker.start()
    uvicorn.run(create_app(cfg, db, token, worker), host=cfg["server"]["host"], port=cfg["server"]["port"])
