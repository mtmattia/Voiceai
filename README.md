# VoiceAi — registrazione continua, archivio personale e verbali, tutto in locale

Due modi d'uso, stesso codice:

1. **Archivio personale (app Android + server)**: il telefono registra a blocchi da 5-10 min, li manda al PC,
   verifica lo SHA-256 e cancella la copia locale. Il PC trascrive, riconosce le persone dalla voce, estrae memo,
   parole chiave, riassunto e umore della giornata. Shell web sul PC e app Android sono **indipendenti**:
   l'app ha una **copia completa offline** (trascrizioni, giornate, persone, memo) e si sincronizza quando c'è rete.
2. **Riunione singola** (`voiceai processa` / `voiceai ascolta`): un file lungo → trascrizione con nomi + verbale.

## Modalità archivio personale

```
Telefono (app VoiceAi)                         PC (voiceai server, porta 8765 su Tailscale)
 rec a blocchi Ogg/Opus ── upload + SHA-256 ──▶ verifica, salva, risponde con lo SHA-256 ──▶ telefono cancella
                                                coda GPU: WhisperX + pyannote sul blocco
                                                registro "persone" globale: impronte → stessa persona tra blocchi
                                                nomi probabili dal parlato ("Marco?")
                                                a riposo, Ollama: memo → parole chiave → giornata (riassunto, umore, consiglio)
 copia locale SQLite ◀── sync incrementale ──▶  SQLite (rev + updated_at, vince la modifica più recente)
 ricerca offline (FTS), memo, persone            shell web http://PC:8765 (Chiedi, Memo, Giornate, Persone, Cerca)
 "Chiedi" ── domanda ──▶ ricerca + LLM ──▶ risposta con fonti
```

- **Collegamento speaker tra blocchi**: non serve l'overlap. Ogni voce del blocco si confronta con le impronte di
  tutte le persone note; sotto i 10 s di parlato non si crea nessuna persona nuova; se il match è molto sicuro
  l'impronta si aggiunge (il profilo si adatta a microfono e ambiente). Se la stessa persona compare due volte:
  "Unisci" dalla shell web o dall'app.
- **Memo**: "devo stampare alle 12" → scadenza **esplicita** (rossa). "Stasera prendo il gatto" senza ora →
  scadenza **ipotizzata** (tratteggiata, `~19:30`): prima da quando hai spuntato memo simili, poi dall'ora a cui di solito
  spunti i memo di quel momento (mattina/pranzo/pomeriggio/sera), infine da un orario di default. Spuntare un memo è
  ciò che insegna i tuoi ritmi. Il telefono notifica alla scadenza anche offline.
- **Sync**: ogni riga ha `updated_at` (ora della modifica, chi modifica più tardi vince) e un contatore `rev` del server
  (cosa è cambiato dall'ultimo sync). Le modifiche fatte offline restano in coda finché il PC non è raggiungibile.
  Serve che **PC e telefono abbiano l'ora sincronizzata** (lo sono di default).
- **GPU**: trascrizione e LLM non stanno insieme in 6 GB. I modelli ASR restano caricati mentre arrivano blocchi; dopo 2 min
  di calma vengono liberati e lavora Ollama. Una domanda dall'app aspetta la fine del blocco in corso.

### Avvio server

```bash
openssl rand -hex 24    # token per l'app
cat >> ~/.config/voiceai/env <<EOT
VOICEAI_TOKEN=<il token>
EOT
voiceai aggiungi-persona Mattia mia_voce.m4a --io    # tu: serve per l'umore della giornata
voiceai server                                       # http://<ip-tailscale>:8765
cp systemd/voiceai-server.service ~/.config/systemd/user/ && systemctl --user enable --now voiceai-server
```

In `config.yaml` metti `server.host` = IP Tailscale del PC (`tailscale ip -4`), così il server non è esposto in LAN.

### App Android: compilazione su CachyOS

Il codice è in `android/` (Kotlin, nessuna libreria UI, min Android 12). **Non è ancora stato compilato con l'SDK vero**:
qui ho potuto solo verificare che il Kotlin compili contro le API di Android 15 con stub di AndroidX.

```bash
sudo pacman -S jdk17-openjdk
yay -S android-sdk-cmdline-tools-latest       # oppure Android Studio (aur: android-studio)
export ANDROID_HOME=$HOME/Android/Sdk
sdkmanager --sdk_root=$ANDROID_HOME "platform-tools" "platforms;android-35" "build-tools;35.0.0"
cd android && ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk   # oppure copia l'APK sul telefono
```

Sull'app: ⚙ → server `http://<ip-tailscale-PC>:8765`, token, durata blocchi. Tailscale attivo sul telefono.
Su Xiaomi/HyperOS: Impostazioni app → Risparmio batteria → **Nessuna restrizione**, altrimenti il sistema può chiudere la registrazione.

Audio della riunione → trascrizione italiana con **nome di chi parla** → **verbale** (sintesi, punti, decisioni, azioni).
Pensato per CachyOS + RTX 2060 6 GB, telefono che carica l'audio in una cartella sincronizzata.

```
telefono → Syncthing → inbox/
  → WhisperX large-v3-turbo int8 (VAD interno, trascrizione)
  → allineamento parole (wav2vec2 IT)
  → pyannote community-1 sull'audio intero (diarizzazione + embedding speaker)
  → nomi: impronte vocali → vocativi/autopresentazioni → LLM (votazione) → "Ospite N"
  → verbale a blocchi da 20 min con Ollama
  → verbali/<data>_<nome>/{trascrizione.md, verbale.md, meeting.json}
```

I modelli girano **uno alla volta** e vengono liberati dalla VRAM; prima di iniziare la pipeline chiede a Ollama di scaricare i suoi modelli, così non vanno in conflitto sui 6 GB.

## Installazione (CachyOS)

```bash
sudo pacman -S ffmpeg uv
git clone https://github.com/mtmattia/Voiceai ~/Progetti/voiceai && cd ~/Progetti/voiceai
uv venv --python 3.12        # whisperx richiede Python < 3.14
uv pip install -e .
```

**Hugging Face** (una volta):
1. crea un token "read" su https://huggingface.co/settings/tokens
2. accetta i termini di `pyannote/speaker-diarization-community-1`
3. salvalo:
```bash
mkdir -p ~/.config/voiceai
echo 'HF_TOKEN=hf_xxx' > ~/.config/voiceai/env && chmod 600 ~/.config/voiceai/env
cp config.example.yaml ~/.config/voiceai/config.yaml   # poi modifica percorsi e nomi
```

**Ollama**: deve rispondere su `http://localhost:11434` (porta pubblicata dal container). Scarica il modello:
`docker exec -it ollama ollama pull qwen2.5:7b-instruct-q4_K_M`

## Uso

```bash
source .venv/bin/activate; set -a; . ~/.config/voiceai/env; set +a

voiceai processa riunione.m4a           # un file
voiceai ascolta                         # watcher sulla inbox (vedi servizio sotto)
voiceai registra-voce Marco marco.m4a   # impronta da clip di 20-30 s di sola voce
voiceai registra-voce Giulia riunione.m4a --inizio 120 --fine 150
voiceai impara ~/Sync/riunioni/verbali/2026-10-28_0930_riunione SPEAKER_03 Luca --verbale
voiceai verbale <cartella>              # rigenera il verbale (es. Ollama era spento)
voiceai voci / voiceai rimuovi-voce Marco
```

> **Il modo migliore per creare le impronte è `impara`**: dopo la prima riunione guardi la tabella in cima a `trascrizione.md`, correggi "Ospite 2 = SPEAKER_03 → Luca" e l'impronta viene presa **dalla riunione vera, con lo stesso microfono e la stessa stanza**. Funziona meglio di una clip registrata a parte. Più impronte per persona = match più robusto.

### Servizio automatico

```bash
mkdir -p ~/.config/systemd/user
cp systemd/voiceai.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now voiceai
loginctl enable-linger $USER     # resta attivo anche senza sessione aperta
journalctl --user -u voiceai -f
```

Il watcher considera un file pronto quando **non cambia da 2 minuti** (upload Syncthing finito), ignora i file temporanei (`.syncthing.*.tmp`, `~…`, `.part`). Dopo l'elaborazione l'audio va in `archivio/`; se fallisce va in `inbox/failed/` con un `.log` dell'errore.

## Taratura

- `speakers.threshold` (default 0.50): in `meeting.json` → `speakers.<SPK>.sims` trovi la similarità di ogni speaker con ogni impronta. Dopo 2-3 riunioni guarda i valori dei match giusti e di quelli sbagliati e metti la soglia in mezzo.
- `diarization.min_speakers/max_speakers`: se una persona viene spezzata in due, abbassa `max_speakers`; se due persone vengono fuse, alza `min_speakers`.
- `asr.initial_prompt`: metti nomi, sigle e termini tecnici ricorrenti.
- `asr.batch_size`: 8 di default; abbassa a 4 se va in **CUDA out of memory**.

## Decisioni prese (da confermare)

| Punto aperto | Scelta |
|---|---|
| Verbale a fine riunione o in diretta | **A fine riunione**, diarizzazione unica sull'audio intero: più precisa e niente collegamento speaker tra pezzi |
| Docker o script | **Script Python + servizio systemd utente** (accesso diretto alla GPU, niente CUDA in container) |
| Formato | **Markdown** (si converte con `pandoc verbale.md -o verbale.docx`) |

Lo split in pezzi con overlap non c'è: WhisperX taglia già sui silenzi con il suo VAD, e con la diarizzazione sull'audio intero l'overlap non serve.

## Stime e limiti

- Velocità sulla 2060 **non misurata**: aspettati nell'ordine di 10-15 min di elaborazione per 1 h di audio, più il verbale. Controlla i tempi nel log alla prima riunione.
- RAM: 4 h di audio ≈ 1 GB in memoria, ok con 16 GB.
- Voci sovrapposte o simili e un solo microfono restano il limite principale: **un microfono da conferenza al centro del tavolo** vale più di qualsiasi modello.
- La deduzione dei nomi dal testo è conservativa (servono almeno 2 indizi concordi): meglio "Ospite N" che un nome sbagliato.

## GDPR e privacy

- **Registrazione continua**: registrare conversazioni a cui partecipi per uso personale è in genere lecito in Italia,
  diffonderle no. Ma le **impronte vocali sono dati biometrici**: la modalità archivio crea impronte anche di chi non
  hai avvisato. Per uso domestico/personale il rischio è ridotto, al lavoro serve informare e avere il consenso.
  `Elimina` su una persona cancella le sue impronte.

- Informare tutti della registrazione; regola chiara e condivisa.
- **Impronte vocali = dato biometrico**: consenso esplicito di ciascuno. Sono in `~/.local/share/voiceai/voiceprints/` (un JSON per persona); `voiceai rimuovi-voce Nome` le cancella. Nota: `meeting.json` contiene gli embedding degli speaker di quella riunione.
- Nessun dato esce dal PC (unico download: i modelli al primo avvio).

## Test

```bash
uv pip install pytest httpx && python -m pytest -q   # 21 test: logica, API, sync, memo; senza GPU né modelli
```
