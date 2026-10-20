"""JSON-backed data shared by the apps: games and mods, Jarvis tasks, notes and settings.

Every function reads the file fresh and writes it atomically, so the Mod Manager and
Jarvis can both change the same data while running.
"""
import fcntl
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CONTROL_CENTER_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

MODS_FILE = DATA / "mods.json"
JARVIS_FILE = DATA / "jarvis.json"
COMMANDS_FILE = DATA / "commands.json"

DEFAULT_COMMANDS = [
    {"id": "disk", "label": "Disk usage", "cmd": "df -h -x tmpfs -x devtmpfs -x efivarfs"},
    {"id": "memory", "label": "Memory", "cmd": "free -h"},
    {"id": "uptime", "label": "Uptime", "cmd": "uptime -p"},
    {"id": "top", "label": "Top processes", "cmd": "ps -eo pid,comm,%cpu,%mem --sort=-%cpu | head -n 11"},
    {"id": "updates", "label": "Pending updates", "cmd": "checkupdates 2>/dev/null | wc -l | xargs -I{} echo '{} package updates available'"},
    {"id": "gpu", "label": "GPU status", "cmd": "nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv 2>/dev/null || echo 'No NVIDIA GPU found'"},
]

DEFAULT_SETTINGS = {
    "name": (os.environ.get("USER") or "Sir").title(),
    "model": "qwen3:8b",
    "ollama": "http://127.0.0.1:11434",
    "voice": True,
    "voiceFx": True,
    "lat": None,
    "lon": None,
    "allowShell": True,
    "sttModel": "small.en",
    "handsFree": False,
}


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


@contextmanager
def locked():
    """Cross-process lock so two apps never write the data files at once."""
    with open(DATA / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


if not COMMANDS_FILE.exists():
    save_json(COMMANDS_FILE, DEFAULT_COMMANDS)


# ---------------------------------------------------------------- mods

def _mods_db():
    return load_json(MODS_FILE, {"games": [], "meta": {}, "order": {}})


def _safe_name(name):
    if not name or "/" in name or name in (".", "..", ".disabled"):
        raise ValueError(f"Invalid mod name: {name!r}")
    return name


def _mod_files(path, limit=5000):
    if path.is_file():
        return {path.name}
    files = set()
    for dirpath, _, names in os.walk(path):
        for n in names:
            files.add(os.path.relpath(os.path.join(dirpath, n), path))
            if len(files) >= limit:
                return files
    return files


def _size(path):
    if path.is_file():
        return path.stat().st_size
    total = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                pass
    return total


def _scan(game, db):
    base = Path(game["modsDir"]).expanduser()
    if not base.is_dir():
        return {**game, "path": str(base), "error": f"Folder not found: {base}", "mods": [], "conflicts": []}
    entries = []
    for folder, enabled in ((base, True), (base / ".disabled", False)):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if p.name.startswith("."):
                continue
            meta = db["meta"].get(f"{game['id']}/{p.name}", {})
            entries.append({"name": p.name, "enabled": enabled, "isDir": p.is_dir(), "size": _size(p),
                            "modified": p.stat().st_mtime, **meta, "_path": p})
    order = db["order"].get(game["id"], [])
    rank = {n: i for i, n in enumerate(order)}
    entries.sort(key=lambda e: (rank.get(e["name"], len(order)), e["name"].lower()))

    owners = {}
    for e in entries:
        if e["enabled"] and e["isDir"]:
            for f in _mod_files(e["_path"]):
                owners.setdefault(f, []).append(e["name"])
    conflicts = [{"file": f, "mods": m} for f, m in sorted(owners.items()) if len(m) > 1]
    conflicted = {n for c in conflicts for n in c["mods"]}
    for e in entries:
        e["conflict"] = e["enabled"] and e["name"] in conflicted
        del e["_path"]
    return {**game, "path": str(base), "mods": entries, "conflicts": conflicts[:500]}


def list_games():
    db = _mods_db()
    return [_scan(g, db) for g in db["games"]]


def find_game(query):
    """Find a game by id or by a case-insensitive name match."""
    q = query.lower().strip()
    games = _mods_db()["games"]
    return next((g for g in games if g["id"] == q), None) or next((g for g in games if q in g["name"].lower()), None)


def add_game(name, mods_dir):
    name, mods_dir = name.strip(), mods_dir.strip()
    if not name or not mods_dir:
        raise ValueError("A name and a mods folder are both required")
    with locked():
        db = _mods_db()
        ids = {g["id"] for g in db["games"]}
        gid = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "game"
        while gid in ids:
            gid += "-2"
        db["games"].append({"id": gid, "name": name, "modsDir": mods_dir})
        save_json(MODS_FILE, db)
    return gid


def remove_game(game_id):
    with locked():
        db = _mods_db()
        db["games"] = [g for g in db["games"] if g["id"] != game_id]
        db["order"].pop(game_id, None)
        save_json(MODS_FILE, db)


def set_mod_enabled(game_id, mod, enable):
    game = next((g for g in _mods_db()["games"] if g["id"] == game_id), None)
    if not game:
        raise ValueError(f"Unknown game: {game_id}")
    name = _safe_name(mod)
    base = Path(game["modsDir"]).expanduser()
    on, off = base / name, base / ".disabled" / name
    src, dst = (off, on) if enable else (on, off)
    if dst.exists() and not src.exists():
        return False  # already in the requested state
    if not src.exists():
        raise ValueError(f"{name} not found in {game['name']}")
    if dst.exists():
        raise ValueError(f"Cannot move {name}: something with that name is already there")
    with locked():
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dst))
        os.utime(MODS_FILE if MODS_FILE.exists() else DATA, None)  # nudge file watchers
    return True


def set_order(game_id, names):
    with locked():
        db = _mods_db()
        db["order"][game_id] = [_safe_name(n) for n in names]
        save_json(MODS_FILE, db)


def set_meta(game_id, mod, **fields):
    with locked():
        db = _mods_db()
        meta = {k: str(fields.get(k, ""))[:2000] for k in ("notes", "version", "url")}
        db["meta"][f"{game_id}/{_safe_name(mod)}"] = {k: v for k, v in meta.items() if v}
        save_json(MODS_FILE, db)


# ---------------------------------------------------------------- jarvis

def jarvis_state():
    db = load_json(JARVIS_FILE, {})
    db.setdefault("todos", [])
    db.setdefault("notes", [])
    db["settings"] = {**DEFAULT_SETTINGS, **db.get("settings", {})}
    return db


def _update_jarvis(fn):
    with locked():
        db = jarvis_state()
        result = fn(db)
        save_json(JARVIS_FILE, db)
    return result


def add_task(text):
    text = text.strip()[:300]
    if not text:
        raise ValueError("Task text is empty")
    item = {"id": int(time.time() * 1000), "text": text, "done": False}
    _update_jarvis(lambda db: db["todos"].append(item))
    return item


def set_task_done(task_id, done=True):
    def fn(db):
        for t in db["todos"]:
            if t["id"] == task_id:
                t["done"] = done
                return t
        raise ValueError("No task with that id")
    return _update_jarvis(fn)


def remove_task(task_id):
    _update_jarvis(lambda db: db.__setitem__("todos", [t for t in db["todos"] if t["id"] != task_id]))


def clear_done_tasks():
    _update_jarvis(lambda db: db.__setitem__("todos", [t for t in db["todos"] if not t["done"]]))


def add_note(text):
    text = text.strip()[:2000]
    if not text:
        raise ValueError("Note text is empty")
    item = {"id": int(time.time() * 1000), "text": text, "at": time.time()}

    def fn(db):
        db["notes"].insert(0, item)
        del db["notes"][200:]
    _update_jarvis(fn)
    return item


def remove_note(note_id):
    _update_jarvis(lambda db: db.__setitem__("notes", [n for n in db["notes"] if n["id"] != note_id]))


def update_settings(**changes):
    def fn(db):
        db["settings"].update({k: v for k, v in changes.items() if k in DEFAULT_SETTINGS})
        return db["settings"]
    return _update_jarvis(fn)


def protocols():
    return load_json(COMMANDS_FILE, [])


def run_shell(cmd, timeout=60):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout + res.stderr).strip()[-6000:]
    except subprocess.TimeoutExpired:
        return -1, f"Timed out after {timeout} seconds."
