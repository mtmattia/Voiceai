# Prompt per Claude Code sul PC (CachyOS)

## Prima di incollarlo: cose che deve fare Mattia

1. **Token Hugging Face** "read": https://huggingface.co/settings/tokens
2. **Accetta i termini** di https://huggingface.co/pyannote/speaker-diarization-community-1 (stesso account del token)
3. **Clip della tua voce** di 20-30 s (solo tu, niente rumore), es. `~/Progetti/voiceai/mia_voce.m4a`. Facoltativa per partire, serve per l'umore della giornata.
4. Tieni a portata la **password sudo**: Claude Code non può inserirla, ti chiederà di lanciare tu i comandi `sudo`/`yay` (nel prompt di Claude Code: `! comando`).

---

## Prompt (copia da qui in giù)

Sei sul mio PC CachyOS (RTX 2060 6 GB, Ryzen 7 3700X, 16 GB RAM). Il progetto VoiceAi è in `~/Progetti/voiceai` (già estratto e verificato, gradle-wrapper.jar controllato). Leggi prima `README.md`, `config.example.yaml`, `pyproject.toml`, `systemd/*.service`. Poi esegui l'installazione completa, un passo alla volta, verificando l'esito di ognuno prima di passare al successivo. Rispondimi in italiano, conciso.

Regole:
- Ogni comando che richiede `sudo` o `yay` NON eseguirlo: scrivimelo e aspetta che lo lanci io.
- Non mettere mai token o segreti nell'output o nei log; chiedimeli e scrivili solo in `~/.config/voiceai/env` (chmod 600).
- Non modificare il codice del progetto senza dirmelo prima; se un test o la build falliscono, mostrami l'errore e proponi la correzione.
- Il progetto sta in `~/Progetti/voiceai` (le unit systemd puntano già lì).

Passi:
1. **Controlli iniziali**: `nvidia-smi` (driver e CUDA ok?), `ffmpeg -version`, `uv --version`, `docker ps` (c'è il container Ollama? come si chiama? risponde `curl -s localhost:11434/api/tags`?), `tailscale ip -4`, `java -version`. Fammi un riepilogo di cosa manca.
2. **Pacchetti di sistema mancanti**: dammi il comando `sudo pacman -S ...` (ffmpeg, uv, jdk17-openjdk) da lanciare io.
3. **Ambiente Python**: in `~/Progetti/voiceai`: `uv venv --python 3.12` e `uv pip install -e ".[dev]"`. Verifica che torch veda la GPU: `.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"`. Se `cuda.is_available()` è False, fermati e dimmelo.
4. **Test**: `.venv/bin/python -m pytest -q` (attesi 21 test, senza GPU né modelli).
5. **Configurazione**: crea `~/.config/voiceai/`, copia `config.example.yaml` in `config.yaml`. Chiedimi: nomi dei colleghi abituali (per `known_names` e `initial_prompt`), percorsi inbox/output/archivio (proponi default). Metti `server.host` = IP Tailscale del PC.
6. **Segreti**: chiedimi il token HF; genera il token app con `openssl rand -hex 24`. Scrivi in `~/.config/voiceai/env` le righe `HF_TOKEN=...` e `VOICEAI_TOKEN=...`, `chmod 600`. Mostrami il VOICEAI_TOKEN una volta sola, mi serve per l'app.
7. **Ollama**: scarica `qwen2.5:7b-instruct-q4_K_M` nel container (`docker exec <nome> ollama pull ...`) e fai una prova breve via API.
8. **Prima prova con modelli reali**: genera con ffmpeg o chiedimi un audio di prova in italiano di 1-2 min, lancia `voiceai processa` (con `set -a; . ~/.config/voiceai/env; set +a`). Controlla che pyannote scarichi il modello (se 401/403: i termini HF non sono accettati, dimmelo). Riporta tempo di elaborazione rispetto alla durata e uso VRAM. Se va in CUDA OOM, abbassa `asr.batch_size` a 4.
9. **Mia impronta vocale**: se esiste `~/Progetti/voiceai/mia_voce.m4a`, lancia `voiceai aggiungi-persona Mattia mia_voce.m4a --io`; altrimenti ricordamelo alla fine.
10. **Servizio systemd utente**: copia `systemd/voiceai-server.service` (ed eventualmente `voiceai.service` per il watcher riunioni, chiedimi se lo voglio) in `~/.config/systemd/user/`, correggi `ExecStart` su `%h/Progetti/voiceai/.venv/bin/voiceai`, `daemon-reload`, `enable --now`. Dimmi di lanciare io `loginctl enable-linger $USER` se serve. Verifica con `systemctl --user status` e `curl http://<ip-tailscale>:8765`.
11. **Android SDK**: dammi i comandi `yay -S android-sdk-cmdline-tools-latest` da lanciare io. Poi `ANDROID_HOME=$HOME/Android/Sdk`, `sdkmanager --sdk_root=$ANDROID_HOME "platform-tools" "platforms;android-35" "build-tools;35.0.0"` (accetta licenze), crea `android/local.properties` con `sdk.dir`.
12. **Build APK**: `cd android && ./gradlew assembleDebug`. L'app non è mai stata compilata con l'SDK vero: aspettati errori; correggili uno alla volta spiegandomi cosa cambi. Alla fine dimmi il percorso dell'APK e come installarlo (`adb install` o copia sul telefono).
13. **Riepilogo finale**: cosa funziona, cosa no, URL della shell web, impostazioni da mettere nell'app (server `http://<ip-tailscale>:8765`, token), e ricordami: HyperOS → batteria app "Nessuna restrizione", Tailscale attivo sul telefono.
