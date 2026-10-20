# Control Center

Three Qt desktop apps that share one data folder:

| App | Start it | What it does |
|---|---|---|
| System Monitor | `./run.sh monitor` | Live CPU (per core), memory, network, disks, temperatures, NVIDIA GPU and top processes |
| Mod Manager | `./run.sh mods` | Turn mods on and off per game, drag to set load order, notes and versions, overlapping-file warnings |
| Jarvis | `./run.sh jarvis` | HUD assistant driven by a local LLM (Ollama) that can check and control this computer, with a spoken voice |

The first run creates `.venv` and installs PySide6 automatically. `./install-launchers.sh` adds all three
to your desktop's app menu (`--remove` takes them out again). Re-run it if you move this folder.

## Jarvis
- **Brain:** Ollama on `127.0.0.1:11434` with `qwen3:8b` (change both in Settings). Nothing leaves your machine
  except the weather lookup (Open-Meteo, only once you set a location).
- **What it can do:** read system status, top processes and disk space; check the weather; manage tasks and notes;
  list and toggle mods in the Mod Manager; run the protocols in `data/commands.json`; open the other apps, folders
  or links when asked.
- **Shell commands:** Jarvis can propose a bash command when nothing else fits. Each one opens a dialog showing the
  exact command, and it runs only if you click **Run**. You can turn this off in Settings.
- **Voice:** Piper neural speech (British male voice `en_GB-alan-medium`, in `voices/`) with a slower, slightly
  deeper delivery and a light shimmer-and-reverb effect. It runs fully offline. You can turn the effect or the voice
  off in Settings. Listen to `voice-sample.wav` for a preview.
- **Talking to Jarvis:** click **Mic** (or press Ctrl+Space), speak, and pause. It stops by itself and sends what
  you said. Turn on **Hands-free** to keep listening: say "Hey Jarvis" (or "OK Jarvis") with your request, or say
  it, wait for the chime, then speak. "Jarvis, …" without "hey" also works. The wake phrase is detected by
  openWakeWord's "hey jarvis" model, and requests are transcribed with Whisper (faster-whisper, `small.en` by default).
  All of it runs locally. If it hears speech without the wake word, it shows what it heard under the chat.
- **Look:** the window is styled as the inside of a helmet visor, with a boot sequence, compass and side tapes,
  and a targeting reticle whose arcs show CPU, memory and disk. Fonts: Orbitron and Rajdhani (SIL Open Font
  License, in `fonts/`).
- Ollama offline? Run `systemctl start ollama`. Missing model? Run `ollama pull qwen3:8b`.

## Data
- `data/commands.json`: Jarvis protocols as `{"id", "label", "cmd"}`. Each `cmd` runs as a shell command without
  asking, so only add commands you trust.
- `data/mods.json` and `data/jarvis.json`: games, tasks, notes and settings. Both apps watch these files, so a
  change made by Jarvis shows up in the Mod Manager straight away.
- A disabled mod is moved into `<mods folder>/.disabled/`. Nothing is ever deleted.

## Not in the repository

- `voices/`: the Piper voice (`en_GB-alan-medium.onnx` and its `.json`). Download them from the
  [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) collection into `voices/`.
- `data/jarvis.json`: your settings, notes and log; it is created on first run.
