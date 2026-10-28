"""Client minimale per Ollama."""
import logging

import requests

log = logging.getLogger(__name__)


def chat(cfg, system, user, json_mode=False):
    c = cfg["llm"]
    body = {
        "model": c["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "options": {"num_ctx": c["num_ctx"], "temperature": c["temperature"]},
    }
    if json_mode:
        body["format"] = "json"
    r = requests.post(c["url"].rstrip("/") + "/api/chat", json=body, timeout=900)
    r.raise_for_status()
    return r.json()["message"]["content"]


def unload_all(cfg):
    """Libera la VRAM occupata da Ollama prima di Whisper/pyannote. Best effort."""
    url = cfg["llm"]["url"].rstrip("/")
    try:
        models = requests.get(url + "/api/ps", timeout=5).json().get("models", [])
        for m in models:
            requests.post(url + "/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
            log.info("ollama: scaricato %s dalla VRAM", m["name"])
    except requests.RequestException as e:
        log.warning("ollama non raggiungibile (%s): proseguo", e)
