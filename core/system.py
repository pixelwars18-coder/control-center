"""Live system statistics read from Linux /proc and /sys (no dependencies)."""
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def cpu_times():
    """Return list of (total, idle) for the aggregate CPU followed by each core."""
    out = []
    for line in read("/proc/stat").splitlines():
        if not line.startswith("cpu"):
            break
        vals = [int(v) for v in line.split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        out.append((sum(vals[:8]), idle))
    return out


def meminfo():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            info[key] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    return {
        "total": total,
        "used": total - avail,
        "available": avail,
        "cached": info.get("Cached", 0) + info.get("Buffers", 0),
        "swapTotal": info.get("SwapTotal", 0),
        "swapUsed": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
    }


REAL_FS = {"ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "zfs", "bcachefs"}


def disks():
    seen, out = set(), []
    for line in read("/proc/mounts").splitlines():
        dev, mnt, fs = line.split()[:3]
        if fs not in REAL_FS or dev in seen:
            continue
        mnt = mnt.replace("\\040", " ")
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        seen.add(dev)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total:
            out.append({"mount": mnt, "device": dev, "fs": fs, "total": total, "used": total - free})
    return out


def net_counters():
    rx = tx = 0
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "docker", "br-", "virbr")):
            continue
        vals = rest.split()
        rx += int(vals[0])
        tx += int(vals[8])
    return rx, tx


def temperatures():
    temps = []
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        name = read(hw / "name").strip()
        for inp in sorted(hw.glob("temp*_input")):
            try:
                val = int(read(inp).strip()) / 1000
            except ValueError:
                continue
            label = read(inp.with_name(inp.name.replace("_input", "_label"))).strip()
            if 0 < val < 150:
                temps.append({"sensor": name, "label": label or name, "c": round(val, 1)})
    return temps


def gpu_info():
    if not shutil.which("nvidia-smi"):
        return []
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in res.stdout.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 6:
            continue

        def num(x):
            try:
                return float(x)
            except ValueError:
                return None
        gpus.append({"name": p[0], "util": num(p[1]), "memUsed": num(p[2]), "memTotal": num(p[3]),
                     "temp": num(p[4]), "power": num(p[5])})
    return gpus


def battery():
    for bat in Path("/sys/class/power_supply").glob("BAT*"):
        cap = read(bat / "capacity").strip()
        if cap.isdigit():
            return {"percent": int(cap), "status": read(bat / "status").strip()}
    return None


PAGE = os.sysconf("SC_PAGE_SIZE")


def process_samples():
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        stat = read(f"/proc/{d}/stat")
        if not stat:
            continue
        rp = stat.rfind(")")
        name = stat[stat.find("(") + 1:rp]
        fields = stat[rp + 2:].split()
        try:
            jiffies = int(fields[11]) + int(fields[12])
            rss = int(fields[21]) * PAGE
        except (IndexError, ValueError):
            continue
        procs[int(d)] = (name, jiffies, rss)
    return procs


class Sampler(threading.Thread):
    """Samples counters on an interval so every client sees the same rates."""

    def __init__(self, interval=1.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.lock = threading.Lock()
        self.snapshot = {}
        self.history = []
        self.slow = {"disks": [], "temps": [], "gpus": [], "battery": None}
        self.slow_at = 0

    def run(self):
        prev_cpu = cpu_times()
        prev_net = net_counters()
        prev_proc = process_samples()
        prev_t = time.time()
        ncpu = os.cpu_count() or 1
        while True:
            time.sleep(self.interval)
            now = time.time()
            dt = now - prev_t
            cur_cpu = cpu_times()
            usage = []
            for (t1, i1), (t0, i0) in zip(cur_cpu, prev_cpu):
                dtot = t1 - t0
                usage.append(round(100 * (1 - (i1 - i0) / dtot), 1) if dtot else 0.0)
            total_delta = cur_cpu[0][0] - prev_cpu[0][0] if cur_cpu else 0

            cur_net = net_counters()
            rx_rate = max(0, (cur_net[0] - prev_net[0]) / dt)
            tx_rate = max(0, (cur_net[1] - prev_net[1]) / dt)

            cur_proc = process_samples()
            top = []
            if total_delta:
                per_core = total_delta / ncpu
                for pid, (name, j, rss) in cur_proc.items():
                    old = prev_proc.get(pid)
                    cpu = 100 * (j - old[1]) / per_core if old else 0.0
                    top.append({"pid": pid, "name": name, "cpu": round(cpu, 1), "rss": rss})
                top.sort(key=lambda p: (p["cpu"], p["rss"]), reverse=True)

            if now - self.slow_at > 5:
                self.slow = {"disks": disks(), "temps": temperatures(), "gpus": gpu_info(), "battery": battery()}
                self.slow_at = now

            load = read("/proc/loadavg").split()
            mem = meminfo()
            snap = {
                "time": now,
                "cpu": {"total": usage[0] if usage else 0, "cores": usage[1:], "count": ncpu},
                "mem": mem,
                "net": {"rx": rx_rate, "tx": tx_rate, "rxTotal": cur_net[0], "txTotal": cur_net[1]},
                "load": [float(x) for x in load[:3]] if load else [0, 0, 0],
                "uptime": float(read("/proc/uptime", "0").split()[0]),
                "procCount": len(cur_proc),
                "top": top[:12],
                **self.slow,
            }
            with self.lock:
                self.snapshot = snap
                self.history.append({"t": now, "cpu": snap["cpu"]["total"],
                                     "mem": 100 * mem["used"] / mem["total"] if mem["total"] else 0,
                                     "rx": rx_rate, "tx": tx_rate})
                self.history = self.history[-120:]
            prev_cpu, prev_net, prev_proc, prev_t = cur_cpu, cur_net, cur_proc, now

    def get(self):
        with self.lock:
            return dict(self.snapshot, history=list(self.history))


SYSINFO = {
    "hostname": socket.gethostname(),
    "kernel": platform.release(),
    "os": (lambda: next((l.split("=", 1)[1].strip('"') for l in read("/etc/os-release").splitlines()
                         if l.startswith("PRETTY_NAME=")), platform.system()))(),
    "cpuModel": next((l.split(":", 1)[1].strip() for l in read("/proc/cpuinfo").splitlines()
                      if l.startswith("model name")), platform.processor()),
    "user": os.environ.get("USER", ""),
}



_sampler = None


def sampler():
    """Shared sampler, started on first use."""
    global _sampler
    if _sampler is None:
        _sampler = Sampler(interval=1.0)
        _sampler.start()
    return _sampler
