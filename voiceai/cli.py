import argparse
import logging

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="voiceai", description="Trascrizione e verbale riunioni, in locale")
    ap.add_argument("--config", help="default ~/.config/voiceai/config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("processa", help="elabora un file audio")
    p.add_argument("audio")
    w = sub.add_parser("ascolta", help="watcher sulla cartella inbox")
    w.add_argument("--once", action="store_true", help="elabora quello che c'è ed esci")
    e = sub.add_parser("registra-voce", help="salva l'impronta vocale di una persona da una clip")
    e.add_argument("nome")
    e.add_argument("audio")
    e.add_argument("--inizio", type=float, help="secondi")
    e.add_argument("--fine", type=float, help="secondi")
    l = sub.add_parser("impara", help="correggi uno speaker di una riunione e salvane l'impronta")
    l.add_argument("cartella")
    l.add_argument("speaker", help="es. SPEAKER_03 (vedi tabella in trascrizione.md)")
    l.add_argument("nome")
    l.add_argument("--verbale", action="store_true", help="rigenera anche il verbale")
    v = sub.add_parser("verbale", help="rigenera trascrizione.md e verbale.md di una riunione")
    v.add_argument("cartella")
    sub.add_parser("voci", help="elenca le impronte salvate")
    r = sub.add_parser("rimuovi-voce", help="cancella le impronte di una persona")
    r.add_argument("nome")

    s = sub.add_parser("server", help="server per l'app Android + shell web + elaborazione a blocchi")
    s.add_argument("--senza-worker", action="store_true", help="solo API, nessuna elaborazione")
    n = sub.add_parser("aggiungi-persona", help="crea una persona nell'archivio da una clip della sua voce")
    n.add_argument("nome")
    n.add_argument("audio")
    n.add_argument("--io", action="store_true", help="è l'utente principale (umore del giorno)")
    n.add_argument("--inizio", type=float)
    n.add_argument("--fine", type=float)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load(a.config)

    from .speakers import VoiceprintStore
    store = VoiceprintStore(cfg["paths"]["voiceprints"])

    if a.cmd == "processa":
        from . import pipeline
        print(pipeline.process(cfg, a.audio, config.hf_token(cfg)))
    elif a.cmd == "ascolta":
        from . import pipeline, watcher
        tok = config.hf_token(cfg)
        watcher.run(cfg, lambda p: pipeline.process(cfg, p, tok), once=a.once)
    elif a.cmd == "registra-voce":
        from . import asr
        emb = asr.embed_clip(a.audio, cfg, config.hf_token(cfg), a.inizio, a.fine)
        print(f"{a.nome}: {store.add(a.nome, emb, source=a.audio)} impronte")
    elif a.cmd == "impara":
        from . import pipeline
        print(pipeline.learn(cfg, a.cartella, a.speaker, a.nome, minutes=a.verbale))
    elif a.cmd == "verbale":
        from . import pipeline
        print(pipeline.make_minutes(cfg, a.cartella))
    elif a.cmd == "server":
        from . import server
        server.serve(cfg, config.api_token(cfg), None if a.senza_worker else config.hf_token(cfg),
                     with_worker=not a.senza_worker)
    elif a.cmd == "aggiungi-persona":
        from . import asr
        from .db import DB
        emb = asr.embed_clip(a.audio, cfg, config.hf_token(cfg), a.inizio, a.fine)
        db = DB(cfg["storage"]["data_dir"] / "voiceai.db")
        pid = db.new_person(a.nome, confirmed=True, owner=a.io)
        if a.io:
            db.update_person(pid, is_owner=1)
        db.add_print(pid, emb, source=a.audio, manual=True)
        print(f"persona {pid}: {a.nome}")
    elif a.cmd == "voci":
        for n, ps in store.load().items():
            print(f"{n}: {len(ps)} impronte")
    elif a.cmd == "rimuovi-voce":
        print("rimossa" if store.remove(a.nome) else "non trovata")


if __name__ == "__main__":
    main()
