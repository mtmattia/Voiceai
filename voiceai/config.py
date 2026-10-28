import copy
import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path("~/.config/voiceai/config.yaml").expanduser()

DEFAULTS = {
    "paths": {
        "inbox": "~/Sync/riunioni/inbox",
        "output": "~/Sync/riunioni/verbali",
        "archive": "~/Sync/riunioni/archivio",
        "voiceprints": "~/.local/share/voiceai/voiceprints",
    },
    "hf_token_env": "HF_TOKEN",
    "asr": {"model": "large-v3", "compute_type": "int8", "batch_size": 8,
            "language": "it", "initial_prompt": None,
            "audio_filter": "highpass=f=80,lowpass=f=7500,dynaudnorm=f=150:g=15:p=0.9:m=20"},
    "diarization": {"model": "pyannote/speaker-diarization-community-1",
                    "min_speakers": 2, "max_speakers": 12},
    "speakers": {"threshold": 0.50, "known_names": [], "min_votes": 2, "guest_label": "Ospite"},
    "llm": {"enabled": True, "url": "http://localhost:11434", "model": "qwen2.5:7b-instruct-q4_K_M",
            "num_ctx": 8192, "temperature": 0.2, "block_minutes": 20, "block_max_chars": 14000,
            "name_inference": True, "correct": True},
    "server": {"host": "0.0.0.0", "port": 8765, "token_env": "VOICEAI_TOKEN"},
    "storage": {"data_dir": "~/.local/share/voiceai", "keep_audio": True},
    "people": {"threshold": 0.50, "oversplit_margin": 0.10, "update_margin": 0.10, "min_update_sec": 15,
               "min_new_person_sec": 10, "max_prints": 20, "min_name_votes": 3},
    "worker": {"poll_seconds": 10, "llm_after_idle_sec": 120, "day_idle_minutes": 60},
    "watch": {"poll_seconds": 30, "stable_seconds": 120,
              "extensions": [".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac",
                             ".mp4", ".webm", ".amr", ".3gp"]},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path=None):
    p = Path(path).expanduser() if path else DEFAULT_PATH
    user = yaml.safe_load(p.read_text()) if p.exists() else {}
    cfg = _merge(DEFAULTS, user)
    for k, v in cfg["paths"].items():
        cfg["paths"][k] = Path(v).expanduser()
    cfg["storage"]["data_dir"] = Path(cfg["storage"]["data_dir"]).expanduser()
    return cfg


def hf_token(cfg):
    tok = os.environ.get(cfg["hf_token_env"])
    if not tok:
        raise SystemExit(f"Manca il token Hugging Face: export {cfg['hf_token_env']}=hf_...")
    return tok


def api_token(cfg):
    tok = os.environ.get(cfg["server"]["token_env"])
    if not tok or len(tok) < 16:
        raise SystemExit(f"Imposta un token di almeno 16 caratteri: export {cfg['server']['token_env']}=...  "
                         "(es. openssl rand -hex 24)")
    return tok
