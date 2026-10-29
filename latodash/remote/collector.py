"""Telemetry collector. Sent to the server over SSH on stdin and run as `python3 -u - '<json>'`.

Nothing is installed on the server. Standard library only; prints one JSON object per interval.
Every section is independent: a failure in one is reported under "errors" and the rest still go out.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ARGS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
INTERVAL = float(ARGS.get("interval", 2.0))
UNITS = list(ARGS.get("units", ["latochan.service", "ollama.service"]))
OLLAMA = ARGS.get("ollama", "http://127.0.0.1:11434")
SETTINGS = ARGS.get("settings", "/var/lib/latochan/settings.json")
SERVICE_EVERY = 5      # ticks (10 s at 2 s interval)
TAGS_EVERY = 60        # ticks (2 min)


def read(path, default=None):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    v = read(path)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


class Rate:
    """Turns ever-increasing counters into per-second rates."""

    def __init__(self):
        self.prev = {}

    def __call__(self, key, value, now):
        last = self.prev.get(key)
        self.prev[key] = (value, now)
        if last is None or now <= last[1] or value < last[0]:
            return None
        return (value - last[0]) / (now - last[1])


rate = Rate()
_cpu_prev = None


def cpu_section():
    global _cpu_prev
    rows = {}
    with open("/proc/stat") as fh:
        for line in fh:
            if not line.startswith("cpu"):
                break
            parts = line.split()
            vals = list(map(int, parts[1:]))
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            rows[parts[0]] = (sum(vals[:8]), idle)
    out = {"pct": None, "cores": []}
    if _cpu_prev:
        def pct(name):
            t0, i0 = _cpu_prev.get(name, (0, 0))
            t1, i1 = rows[name]
            dt = t1 - t0
            return round(100.0 * (1 - (i1 - i0) / dt), 1) if dt > 0 else 0.0
        out["pct"] = pct("cpu")
        out["cores"] = [pct(n) for n in rows if n != "cpu"]
    _cpu_prev = rows
    out["count"] = len(rows) - 1
    mhz = []
    try:
        with open("/proc/cpuinfo") as fh:
            mhz = [float(l.split(":")[1]) for l in fh if l.startswith("cpu MHz")]
    except OSError:
        pass
    out["mhz"] = round(sum(mhz) / len(mhz)) if mhz else None
    out["temp"] = None
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        if read(h + "/name") == "coretemp":
            temps = [read_int(f) for f in glob.glob(h + "/temp*_input")]
            label1 = read(h + "/temp1_label", "")
            if "Package" in label1:
                out["temp"] = read_int(h + "/temp1_input", 0) / 1000
            elif temps:
                out["temp"] = max(t for t in temps if t is not None) / 1000
    la = read("/proc/loadavg", "0 0 0").split()
    out["load"] = [float(x) for x in la[:3]]
    return out


def mem_section():
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    du = shutil.disk_usage("/")
    return {
        "total": info["MemTotal"], "avail": info["MemAvailable"],
        "used": info["MemTotal"] - info["MemAvailable"],
        "cached": info.get("Cached", 0) + info.get("Buffers", 0),
        "swap_total": info.get("SwapTotal", 0), "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
        "disk_total": du.total, "disk_used": du.used,
    }


def default_iface():
    try:
        with open("/proc/net/route") as fh:
            next(fh)
            for line in fh:
                p = line.split()
                if p[1] == "00000000":
                    return p[0]
    except (OSError, StopIteration):
        pass
    return None


IFACE = default_iface()


def net_section(now):
    if not IFACE:
        return None
    with open("/proc/net/dev") as fh:
        for line in fh:
            if line.strip().startswith(IFACE + ":"):
                p = line.split(":", 1)[1].split()
                rx, tx = int(p[0]), int(p[8])
                return {"iface": IFACE, "rx_bps": rate("rx", rx, now), "tx_bps": rate("tx", tx, now)}
    return None


def find_gpu():
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        if os.path.exists(dev + "/gpu_busy_percent"):
            hw = glob.glob(dev + "/hwmon/hwmon*")
            return dev, (hw[0] if hw else None)
    return None, None


GPU_DEV, GPU_HW = find_gpu()


def gpu_section():
    if not GPU_DEV:
        return None
    d, h = GPU_DEV, GPU_HW
    g = {
        "busy": read_int(d + "/gpu_busy_percent"),
        "mem_busy": read_int(d + "/mem_busy_percent"),
        "vram_used": read_int(d + "/mem_info_vram_used"),
        "vram_total": read_int(d + "/mem_info_vram_total"),
        "gtt_used": read_int(d + "/mem_info_gtt_used"),
    }
    if h:
        for f in glob.glob(h + "/temp*_label"):
            label = read(f, "")
            v = read_int(f.replace("_label", "_input"))
            if v is not None:
                g["temp_" + label] = v / 1000
        pw = read_int(h + "/power1_input") or read_int(h + "/power1_average")
        g["power_w"] = round(pw / 1e6, 1) if pw is not None else None
        cap = read_int(h + "/power1_cap")
        g["power_cap_w"] = round(cap / 1e6) if cap else None
        g["fan_rpm"] = read_int(h + "/fan1_input")
        g["fan_max"] = read_int(h + "/fan1_max")
        pwm, pwm_max = read_int(h + "/pwm1"), read_int(h + "/pwm1_max") or 255
        g["fan_pct"] = round(100 * pwm / pwm_max) if pwm is not None else None
        for i in (1, 2):
            lab, val = read(f"{h}/freq{i}_label"), read_int(f"{h}/freq{i}_input")
            if lab and val is not None:
                g[lab + "_mhz"] = round(val / 1e6)
        mv = read_int(h + "/in0_input")
        g["vddgfx_mv"] = mv
    return g


def cgroup_section(now):
    out = {}
    for unit in UNITS:
        if not unit.endswith(".service"):
            continue
        base = "/sys/fs/cgroup/system.slice/" + unit
        if not os.path.isdir(base):
            continue
        usage = None
        for line in (read(base + "/cpu.stat", "") or "").splitlines():
            if line.startswith("usage_usec"):
                usage = int(line.split()[1])
        anon = None
        for line in (read(base + "/memory.stat", "") or "").splitlines():
            if line.startswith("anon "):
                anon = int(line.split()[1])
                break
        r = rate("cg:" + unit, usage, now) if usage is not None else None
        out[unit] = {"cores": round(r / 1e6, 2) if r is not None else None, "rss": anon,
                     "tasks": read_int(base + "/pids.current")}
    return out


def services_section():
    cmd = ["systemctl", "show", *UNITS, "-p", "Id,ActiveState,SubState,NRestarts,ActiveEnterTimestamp,Result",
           "--timestamp=unix"]
    txt = subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
    out = {}
    for block in txt.strip().split("\n\n"):
        kv = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
        if "Id" not in kv:
            continue
        since = kv.get("ActiveEnterTimestamp", "")
        out[kv["Id"]] = {
            "active": kv.get("ActiveState"), "sub": kv.get("SubState"), "result": kv.get("Result"),
            "restarts": int(kv["NRestarts"]) if kv.get("NRestarts", "").isdigit() else None,
            "since": int(since[1:]) if since.startswith("@") and since[1:].isdigit() else None,
        }
    return out


def ollama_get(path, timeout=4):
    t0 = time.monotonic()
    with urllib.request.urlopen(OLLAMA + path, timeout=timeout) as r:
        data = json.load(r)
    return data, round((time.monotonic() - t0) * 1000, 1)


def ollama_section(with_tags):
    out = {"ok": False}
    try:
        ps, ms = ollama_get("/api/ps")
        out.update(ok=True, latency_ms=ms, loaded=[
            {"name": m.get("name"), "size": m.get("size"), "vram": m.get("size_vram"),
             "ctx": m.get("context_length"), "expires": m.get("expires_at"),
             "params": (m.get("details") or {}).get("parameter_size"),
             "quant": (m.get("details") or {}).get("quantization_level"),
             "family": (m.get("details") or {}).get("family")}
            for m in ps.get("models", [])])
    except Exception as e:  # noqa: BLE001 - reported to the dashboard
        out["error"] = f"{type(e).__name__}: {e}"
    if with_tags and out["ok"]:
        try:
            tags, _ = ollama_get("/api/tags")
            out["installed"] = [
                {"name": m.get("name"), "size": m.get("size"), "modified": m.get("modified_at"),
                 "params": (m.get("details") or {}).get("parameter_size"),
                 "quant": (m.get("details") or {}).get("quantization_level"),
                 "family": (m.get("details") or {}).get("family")}
                for m in tags.get("models", [])]
        except Exception as e:  # noqa: BLE001
            out["tags_error"] = str(e)
    return out


def settings_section():
    s = json.loads(read(SETTINGS, "{}") or "{}")
    try:  # LatoChan's model lives in the root-only env file; the helper prints it
        r = subprocess.run(["sudo", "-n", "/usr/local/sbin/lato-set-model", "--show"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            s["live_model"] = r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return s


def main():
    host = socket.gethostname()
    tick = 0
    while True:
        started = time.monotonic()
        now = time.time()
        sample = {"t": now, "host": host, "errors": {}}
        sections = {
            "cpu": cpu_section, "mem": mem_section, "gpu": gpu_section,
            "net": lambda now=now: net_section(now), "cgroups": lambda now=now: cgroup_section(now),
        }
        if tick % SERVICE_EVERY == 0:
            sections["services"] = services_section
            sections["ollama"] = lambda tick=tick: ollama_section(tick % TAGS_EVERY == 0)
            sections["settings"] = settings_section
        for name, fn in sections.items():
            try:
                sample[name] = fn()
            except Exception as e:  # noqa: BLE001
                sample["errors"][name] = f"{type(e).__name__}: {e}"
        try:
            sample["uptime"] = float(read("/proc/uptime", "0").split()[0])
        except ValueError:
            pass
        if not sample["errors"]:
            del sample["errors"]
        print(json.dumps(sample, separators=(",", ":")), flush=True)
        tick += 1
        time.sleep(max(0.2, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
