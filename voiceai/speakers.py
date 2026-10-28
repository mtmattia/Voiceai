"""Impronte vocali: salvataggio e match speaker -> nome (cosine, assegnazione univoca)."""
import json
import re
from pathlib import Path

import numpy as np


def _slug(name):
    return re.sub(r"[^\w-]+", "_", name.strip()).strip("_")


class VoiceprintStore:
    """Una cartella, un file JSON per persona con una o più impronte."""

    def __init__(self, root):
        self.root = Path(root)

    def add(self, name, emb, source=""):
        self.root.mkdir(parents=True, exist_ok=True)
        f = self.root / f"{_slug(name)}.json"
        data = json.loads(f.read_text()) if f.exists() else {"name": name, "prints": []}
        data["prints"].append({"emb": [float(x) for x in emb], "source": str(source)})
        f.write_text(json.dumps(data))
        return len(data["prints"])

    def load(self):
        out = {}
        if self.root.exists():
            for f in sorted(self.root.glob("*.json")):
                d = json.loads(f.read_text())
                out[d["name"]] = [np.asarray(p["emb"], dtype=np.float32) for p in d["prints"]]
        return out

    def remove(self, name):
        f = self.root / f"{_slug(name)}.json"
        if f.exists():
            f.unlink()
            return True
        return False


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def similarity(spk_emb, prints):
    """spk_emb {spk: vec}, prints {name: [vec,...]} -> {spk: {name: max cosine}}"""
    return {s: {n: float(max(_unit(e) @ _unit(p) for p in ps)) for n, ps in prints.items()}
            for s, e in spk_emb.items()}


def match(spk_emb, prints, threshold):
    """Assegnazione greedy univoca: coppia con cosine più alta prima, un nome per speaker."""
    sims = similarity(spk_emb, prints)
    pairs = sorted(((v, s, n) for s, d in sims.items() for n, v in d.items()), reverse=True)
    out, used = {}, set()
    for v, s, n in pairs:
        if v < threshold:
            break
        if s in out or n in used:
            continue
        out[s] = n
        used.add(n)
    return out, sims
