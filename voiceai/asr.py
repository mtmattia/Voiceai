"""Trascrizione + allineamento + diarizzazione (WhisperX 3.8 / pyannote 4).

Engine tiene i modelli in VRAM tra un blocco e l'altro (~3 GB in int8); unload() li libera
prima che Ollama carichi il suo modello.
"""
import gc
import logging

log = logging.getLogger(__name__)
SR = 16000


def _free():
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_audio(path, start=None, end=None, af=None):
    """-> float32 16 kHz mono. af = filtro ffmpeg (pulizia: alza le voci lontane, taglia rombi e sibili)."""
    import subprocess

    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "0", "-i", str(path)]
    cmd += (["-af", af] if af else []) + ["-f", "s16le", "-ac", "1", "-ar", str(SR), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    a = int(start * SR) if start else 0
    b = int(end * SR) if end else len(audio)
    return audio[a:b]


class Engine:
    def __init__(self, cfg, token):
        self.cfg, self.token, self.device = cfg, token, None
        self.model = self.align = self.meta = self.dia = None

    @property
    def loaded(self):
        return self.model is not None

    def load(self):
        if self.loaded:
            return
        import whisperx
        from whisperx.diarize import DiarizationPipeline
        a = self.cfg["asr"]
        self.device = _device()
        compute = a["compute_type"] if self.device == "cuda" else "int8"
        opts = {"initial_prompt": a["initial_prompt"]} if a.get("initial_prompt") else None
        log.info("carico modelli su %s", self.device)
        self.model = whisperx.load_model(a["model"], self.device, compute_type=compute,
                                         language=a["language"], asr_options=opts)
        self.align, self.meta = whisperx.load_align_model(a["language"], self.device)
        self.dia = DiarizationPipeline(model_name=self.cfg["diarization"]["model"], token=self.token,
                                       device=self.device)

    def unload(self):
        if not self.loaded:
            return
        self.model = self.align = self.meta = self.dia = None
        _free()
        log.info("modelli ASR scaricati dalla VRAM")

    def run(self, path, num_speakers=None):
        """-> dict(segments=[{start,end,text,speaker}], embeddings={spk: [..]}, talk={spk: sec}, duration)"""
        import whisperx
        self.load()
        a, d = self.cfg["asr"], self.cfg["diarization"]
        audio = load_audio(path, af=a.get("audio_filter"))
        duration = len(audio) / SR
        result = self.model.transcribe(audio, batch_size=a["batch_size"], language=a["language"])
        if not result["segments"]:
            return {"segments": [], "embeddings": {}, "talk": {}, "duration": duration}
        result = whisperx.align(result["segments"], self.align, self.meta, audio, self.device,
                                return_char_alignments=False)
        kw = {"num_speakers": num_speakers} if num_speakers else \
             {"min_speakers": d.get("min_speakers"), "max_speakers": d.get("max_speakers")}
        df, emb = self.dia(audio, return_embeddings=True, **kw)
        result = whisperx.assign_word_speakers(df, result)
        talk = {}
        for _, r in df.iterrows():
            talk[r["speaker"]] = talk.get(r["speaker"], 0.0) + float(r["end"] - r["start"])
        segs = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip(),
                 "speaker": s.get("speaker")} for s in result["segments"] if s.get("text", "").strip()]
        return {"segments": segs, "embeddings": emb or {}, "talk": talk, "duration": duration}


def transcribe(path, cfg, token):
    """Riunione intera in un colpo solo: carica, elabora, libera."""
    e = Engine(cfg, token)
    try:
        return e.run(path)
    finally:
        e.unload()


def embed_clip(path, cfg, token, start=None, end=None):
    """Impronta di una sola voce: stessa pipeline/stesso spazio di embedding delle registrazioni."""
    from whisperx.diarize import DiarizationPipeline
    audio = load_audio(path, start, end, af=cfg["asr"].get("audio_filter"))
    if len(audio) < 10 * SR:
        log.warning("clip di %.0fs: consigliati 20-30 s di parlato", len(audio) / SR)
    dia = DiarizationPipeline(model_name=cfg["diarization"]["model"], token=token, device=_device())
    _, emb = dia(audio, num_speakers=1, return_embeddings=True)
    del dia
    _free()
    if not emb:
        raise SystemExit("Nessuna voce rilevata nella clip")
    return next(iter(emb.values()))
