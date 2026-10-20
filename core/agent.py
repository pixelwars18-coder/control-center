"""Jarvis's brain: a local LLM served by Ollama, with tools that control this computer.

The model never runs arbitrary code by itself. It can read system state, manage tasks, notes
and mods, run the protocols listed in data/commands.json, and propose a shell command,
which only runs after the person clicks "Run" in a confirmation dialog.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from core import store
from core.system import SYSINFO, sampler

ROOT = Path(__file__).resolve().parent.parent

WEATHER_CODES = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
                 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
                 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
                 77: "snow grains", 80: "rain showers", 81: "heavy showers", 82: "violent showers", 85: "snow showers",
                 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms"}


def _gb(n):
    return round(n / 1024 ** 3, 1)


# ---------------------------------------------------------------- tool implementations

def _sample():
    """Latest stats, waiting briefly for the first sample in a freshly started process."""
    for _ in range(30):
        s = sampler().get()
        if "cpu" in s:
            return s
        time.sleep(0.1)
    return s


def get_system_status():
    s = _sample()
    if "cpu" not in s:
        return {"note": "Still collecting the first sample; try again in a second."}
    m = s["mem"]
    out = {
        "cpu_percent": s["cpu"]["total"], "cpu_threads": s["cpu"]["count"], "load_average": s["load"],
        "memory": {"used_gb": _gb(m["used"]), "total_gb": _gb(m["total"]), "percent": round(100 * m["used"] / m["total"], 1)},
        "swap_used_gb": _gb(m["swapUsed"]),
        "network": {"download_kb_s": round(s["net"]["rx"] / 1024, 1), "upload_kb_s": round(s["net"]["tx"] / 1024, 1)},
        "disks": [{"mount": d["mount"], "used_gb": _gb(d["used"]), "total_gb": _gb(d["total"]),
                   "percent": round(100 * d["used"] / d["total"], 1)} for d in s.get("disks", [])],
        "temperatures_c": {t["label"]: t["c"] for t in s.get("temps", [])[:8]},
        "uptime_hours": round(s["uptime"] / 3600, 1),
        "process_count": s["procCount"],
        "machine": {"cpu": SYSINFO["cpuModel"], "os": SYSINFO["os"], "host": SYSINFO["hostname"]},
    }
    if s.get("gpus"):
        out["gpus"] = s["gpus"]
    if s.get("battery"):
        out["battery"] = s["battery"]
    return out


def get_top_processes(limit=8):
    s = _sample()
    return [{"pid": p["pid"], "name": p["name"], "cpu_percent": p["cpu"], "memory_mb": round(p["rss"] / 1024 ** 2)}
            for p in s.get("top", [])[:max(1, min(int(limit), 12))]]


def get_disk_space(path="~"):
    """Free space on the drive holding a path, plus the size of that folder's direct children."""
    import shutil
    target = os.path.expanduser(str(path))
    if not os.path.exists(target):
        return {"error": f"{target} does not exist"}
    u = shutil.disk_usage(target)
    out = {"path": target, "drive_free_gb": _gb(u.free), "drive_total_gb": _gb(u.total),
           "drive_used_percent": round(100 * u.used / u.total, 1)}
    try:
        res = subprocess.run(["du", "-xsb", "--", *[str(p) for p in Path(target).iterdir()]] if os.path.isdir(target) else ["du", "-sb", target],
                             capture_output=True, text=True, timeout=20)
        sizes = sorted(((int(l.split("\t")[0]), l.split("\t")[1]) for l in res.stdout.splitlines() if "\t" in l), reverse=True)
        out["largest_items"] = [{"path": p, "size_gb": round(b / 1024 ** 3, 2)} for b, p in sizes[:10]]
    except (OSError, subprocess.TimeoutExpired):
        out["largest_items"] = "timed out measuring folder sizes"
    return out


def get_weather():
    st = store.jarvis_state()["settings"]
    if st.get("lat") is None or st.get("lon") is None:
        return {"error": "No location set. Ask the person to add latitude and longitude in Jarvis Settings."}
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={st['lat']}&longitude={st['lon']}"
           "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=2")
    with urllib.request.urlopen(url, timeout=8) as r:
        w = json.load(r)
    c, d = w["current"], w["daily"]
    return {"now": {"temp_c": c["temperature_2m"], "feels_like_c": c["apparent_temperature"],
                    "conditions": WEATHER_CODES.get(c["weather_code"], "unknown"), "wind_kmh": c["wind_speed_10m"],
                    "humidity": c["relative_humidity_2m"]},
            "today": {"high_c": d["temperature_2m_max"][0], "low_c": d["temperature_2m_min"][0], "rain_chance": d["precipitation_probability_max"][0]},
            "tomorrow": {"high_c": d["temperature_2m_max"][1], "low_c": d["temperature_2m_min"][1], "rain_chance": d["precipitation_probability_max"][1]}}


def list_tasks():
    return [{"id": t["id"], "text": t["text"], "done": t["done"]} for t in store.jarvis_state()["todos"]]


def add_task(text):
    return store.add_task(text)


def _match_task(task):
    tasks = store.jarvis_state()["todos"]
    t = str(task).strip().lower()
    return (next((x for x in tasks if str(x["id"]) == t), None)
            or next((x for x in tasks if x["text"].lower() == t), None)
            or next((x for x in tasks if t in x["text"].lower()), None))


def complete_task(task):
    found = _match_task(task)
    if not found:
        return {"error": f"No task matching {task!r}"}
    return store.set_task_done(found["id"], True)


def remove_task(task):
    found = _match_task(task)
    if not found:
        return {"error": f"No task matching {task!r}"}
    store.remove_task(found["id"])
    return {"removed": found["text"]}


def add_note(text):
    return store.add_note(text)


def list_notes(limit=10):
    return [{"text": n["text"], "saved": time.strftime("%Y-%m-%d %H:%M", time.localtime(n["at"]))}
            for n in store.jarvis_state()["notes"][:int(limit)]]


def list_protocols():
    return [{"id": c["id"], "label": c["label"]} for c in store.protocols()]


def run_protocol(protocol_id):
    cmd = next((c for c in store.protocols() if c["id"] == protocol_id or c["label"].lower() == str(protocol_id).lower()), None)
    if not cmd:
        return {"error": f"Unknown protocol {protocol_id!r}", "available": list_protocols()}
    code, output = store.run_shell(cmd["cmd"])
    return {"protocol": cmd["label"], "exit_code": code, "output": output}


def list_mods(game=None):
    games = store.list_games()
    if game:
        g = store.find_game(game)
        games = [x for x in games if g and x["id"] == g["id"]]
        if not games:
            return {"error": f"No game matching {game!r}", "games": [x["name"] for x in store.list_games()]}
    return [{"game": g["name"], "game_id": g["id"], "error": g.get("error"),
             "mods": [{"name": m["name"], "enabled": m["enabled"], "version": m.get("version"), "overlap": m["conflict"]} for m in g["mods"]],
             "overlapping_files": len(g["conflicts"])} for g in games]


def set_mod_enabled(game, mod, enabled):
    g = store.find_game(game)
    if not g:
        return {"error": f"No game matching {game!r}"}
    names = [m["name"] for x in store.list_games() if x["id"] == g["id"] for m in x["mods"]]
    target = next((n for n in names if n.lower() == mod.lower()), None) or next((n for n in names if mod.lower() in n.lower()), None)
    if not target:
        return {"error": f"No mod matching {mod!r} in {g['name']}", "mods": names}
    changed = store.set_mod_enabled(g["id"], target, bool(enabled))
    return {"game": g["name"], "mod": target, "enabled": bool(enabled), "changed": changed}


APPS = {"monitor": "monitor_app.py", "mods": "mod_manager_app.py", "mod_manager": "mod_manager_app.py"}


def open_app(app):
    script = APPS.get(str(app).lower().replace(" ", "_"))
    if not script:
        return {"error": f"Unknown app {app!r}. Options: monitor, mods"}
    subprocess.Popen([sys.executable, str(ROOT / script)], cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": app}


def open_location(target):
    """Open a folder, file or web address with the desktop's default app."""
    target = str(target).strip()
    if not target.startswith(("http://", "https://")):
        target = os.path.expanduser(target)
        if not os.path.exists(target):
            return {"error": f"{target} does not exist"}
    subprocess.Popen(["xdg-open", target], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": target}


# ---------------------------------------------------------------- tool schemas

def _fn(name, description, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


S = {"type": "string"}
TOOLS = [
    _fn("get_system_status", "Current CPU, memory, disks, network, temperatures, GPU, battery and uptime of this computer."),
    _fn("get_top_processes", "The processes using the most CPU right now.", {"limit": {"type": "integer"}}),
    _fn("get_disk_space", "Free space on the drive holding a folder (default: home), and the biggest items inside that folder.", {"path": S}),
    _fn("get_weather", "Current weather plus today's and tomorrow's forecast for the person's saved location."),
    _fn("list_tasks", "The person's to-do list."),
    _fn("add_task", "Add an item to the to-do list.", {"text": S}, ["text"]),
    _fn("complete_task", "Mark a task done, by id or by (part of) its text.", {"task": S}, ["task"]),
    _fn("remove_task", "Delete a task, by id or by (part of) its text.", {"task": S}, ["task"]),
    _fn("add_note", "Save a note.", {"text": S}, ["text"]),
    _fn("list_notes", "Most recent saved notes.", {"limit": {"type": "integer"}}),
    _fn("list_protocols", "Predefined maintenance commands ('protocols') that can be run without confirmation."),
    _fn("run_protocol", "Run one predefined protocol by id or label and return its output.", {"protocol_id": S}, ["protocol_id"]),
    _fn("list_mods", "Games tracked by the Mod Manager and their mods, optionally for one game.", {"game": S}),
    _fn("set_mod_enabled", "Turn a game mod on or off in the Mod Manager.",
        {"game": S, "mod": S, "enabled": {"type": "boolean"}}, ["game", "mod", "enabled"]),
    _fn("open_app", "Open one of the companion desktop apps. Only when the person asks to open or show it.", {"app": {"type": "string", "enum": ["monitor", "mods"]}}, ["app"]),
    _fn("open_location", "Open a folder, file or https URL in its desktop app. Only when the person explicitly asks to open it; never to answer a question.", {"target": S}, ["target"]),
    _fn("run_shell_command", "Run a bash command on this Linux computer. The person sees the command and must approve it "
        "before it runs, so explain why in 'reason'. Use only when no other tool fits. Prefer read-only commands.",
        {"command": S, "reason": S}, ["command", "reason"]),
]

HANDLERS = {
    "get_system_status": get_system_status, "get_top_processes": get_top_processes, "get_disk_space": get_disk_space, "get_weather": get_weather,
    "list_tasks": list_tasks, "add_task": add_task, "complete_task": complete_task, "remove_task": remove_task,
    "add_note": add_note, "list_notes": list_notes, "list_protocols": list_protocols, "run_protocol": run_protocol,
    "list_mods": list_mods, "set_mod_enabled": set_mod_enabled, "open_app": open_app, "open_location": open_location,
}


def system_prompt(settings):
    now = time.strftime("%A %d %B %Y, %H:%M")
    return f"""You are J.A.R.V.I.S., the personal assistant running locally on {settings['name']}'s computer \
({SYSINFO['os']}, {SYSINFO['cpuModel']}). It is {now}. Their home folder is {Path.home()}.

Style: calm, capable and dryly witty, like a British butler. Address the person as {settings['name']} now and then. \
Keep replies short (one to four sentences) unless asked for detail, because replies may be read aloud. \
Use plain sentences; avoid tables and headings.

Rules:
- Answer questions yourself with the information tools; do not open windows, apps or folders unless asked to.
- Use tools for any fact about this computer, the weather, tasks, notes or mods. Never invent numbers.
- When asked to do something, do it with the tools, then confirm briefly what happened.
- Prefer run_protocol and the specific tools over run_shell_command. Every shell command needs the person's approval.
- If a tool returns an error, say so plainly and suggest the fix.
- Round numbers sensibly (e.g. "CPU at 12%").
- Do not end with a follow-up offer or question unless something needs a decision."""


# ---------------------------------------------------------------- chat loop

class OllamaError(Exception):
    pass


def ollama_models(base):
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except (OSError, ValueError):
        return None


def warm_up(base, model):
    """Load the model into memory so the first reply is quick."""
    req = urllib.request.Request(f"{base}/api/generate", data=json.dumps({"model": model, "keep_alive": "30m"}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).read()
    except OSError:
        pass


class Agent:
    """Holds the conversation and runs one turn at a time (call from a worker thread)."""

    MAX_HISTORY = 30
    MAX_ROUNDS = 8

    def __init__(self):
        self.history = []

    def reset(self):
        self.history = []

    def run(self, user_text, on_token, on_tool, confirm, cancelled):
        """Run one turn. Callbacks: on_token(str), on_tool(name, args, result), confirm(cmd, reason) -> bool,
        cancelled() -> bool. Returns the assistant's final text."""
        settings = store.jarvis_state()["settings"]
        self.history.append({"role": "user", "content": user_text})
        tools = [t for t in TOOLS if settings.get("allowShell") or t["function"]["name"] != "run_shell_command"]
        final = ""
        for _ in range(self.MAX_ROUNDS):
            msgs = [{"role": "system", "content": system_prompt(settings)}] + self.history[-self.MAX_HISTORY:]
            content, calls = self._stream(settings, msgs, tools, on_token, cancelled)
            self.history.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
            final = content
            if not calls or cancelled():
                break
            for call in calls:
                fn = call["function"]
                name, args = fn["name"], fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = self._execute(name, args, settings, confirm)
                on_tool(name, args, result)
                self.history.append({"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)[:6000]})
        return final

    def _execute(self, name, args, settings, confirm):
        try:
            if name == "run_shell_command":
                if not settings.get("allowShell"):
                    return {"error": "Shell commands are turned off in Settings."}
                cmd = str(args.get("command", "")).strip()
                if not cmd:
                    return {"error": "Empty command"}
                if not confirm(cmd, str(args.get("reason", ""))):
                    return {"declined": True, "note": "The person declined to run this command. Do not retry it."}
                code, output = store.run_shell(cmd)
                return {"exit_code": code, "output": output}
            if name not in HANDLERS:
                return {"error": f"Unknown tool {name}"}
            return HANDLERS[name](**args)
        except Exception as e:  # report every tool failure back to the model
            return {"error": f"{type(e).__name__}: {e}"}

    def _stream(self, settings, msgs, tools, on_token, cancelled):
        body = {"model": settings["model"], "messages": msgs, "tools": tools, "stream": True, "think": False,
                "keep_alive": "30m", "options": {"num_ctx": 8192, "temperature": 0.6}}
        req = urllib.request.Request(f"{settings['ollama']}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            raise OllamaError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')[:300]}")
        except OSError as e:
            raise OllamaError(f"Cannot reach Ollama at {settings['ollama']} ({e}). Is it running? Try: systemctl start ollama")
        content, calls = "", []
        with resp:
            for line in resp:
                if cancelled():
                    break
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                msg = chunk.get("message", {})
                if msg.get("content"):
                    content += msg["content"]
                    on_token(msg["content"])
                calls.extend(msg.get("tool_calls") or [])
                if chunk.get("done"):
                    break
        return content, calls
