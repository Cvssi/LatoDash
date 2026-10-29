from pathlib import Path

from latodash.config import AppConfig, SSHConfig
from latodash.monitor import Monitor

CFG = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")))
T0 = 1_793_200_000


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def e(ts, msg, unit="ollama.service", cursor=None):
    d = {"__REALTIME_TIMESTAMP": str(int(ts * 1e6)), "MESSAGE": msg, "PRIORITY": "6", "_SYSTEMD_UNIT": unit}
    if cursor:
        d["__CURSOR"] = cursor
    return d


def bot(ts, msg):
    return e(ts, f"2026-10-28 16:19:09,123 INFO {msg}", "latochan.service")


def conversation(mon, ts, task=7, gen_tokens=41, prompt_tokens=887):
    mon.ingest_entry(bot(ts, "IN   #general | owner: hi"))
    mon.ingest_entry(e(ts + .1, f"slot launch_slot_: id  0 | task {task} | processing task, is_child = 0"))
    mon.ingest_entry(e(ts + .2, f"slot   operator(): id  0 | task {task} | new prompt, n_ctx_slot = 8192, n_keep = 4, task.n_tokens = 3660"))
    mon.ingest_entry(e(ts + 5, f"slot print_timing: id  0 | task {task} | prompt processing, n_tokens =    375, progress = 0.50, t =   5.25 s / 71.46 tokens per second"))
    mon.ingest_entry(e(ts + 13, f"slot print_timing: id  0 | task {task} | prompt eval time =   11643.20 ms /   {prompt_tokens} tokens (   13.13 ms per token,    76.18 tokens per second)"))
    mon.ingest_entry(e(ts + 13, f"slot print_timing: id  0 | task {task} |        eval time =    2047.97 ms /    {gen_tokens} tokens (   51.20 ms per token,    19.53 tokens per second)"))
    mon.ingest_entry(e(ts + 13, f"slot print_timing: id  0 | task {task} |       total time =   13691.18 ms /   928 tokens"))
    mon.ingest_entry(e(ts + 13, f"slot      release: id  0 | task {task} | stop processing: n_tokens = 3700, truncated = 0"))
    mon.ingest_entry(e(ts + 13, '[GIN] 2026/10/28 - 16:19:09 | 200 |   13.78s |       127.0.0.1 | POST     "/api/chat"'))
    mon.ingest_entry(bot(ts + 13.1, "RAW  (13.8s) yo"))
    mon.ingest_entry(bot(ts + 13.5, "replied to owner in #general"))


def test_generation_assembled_and_stats():
    clock = Clock()
    mon = Monitor(CFG, clock=clock)
    clock.t = T0 + 20
    conversation(mon, T0)
    assert len(mon.generations) == 1
    g = mon.generations[0]
    assert g["prompt_tokens"] == 887 and g["gen_tokens"] == 41 and g["gen_tps"] == 19.53
    assert g["ctx"] == 8192 and g["ctx_used"] == 3700 and g["source"] == "bot"
    assert mon.inflight is None and mon.pending_reply is None
    s = mon.stats()
    assert s["llm"]["calls"] == 1 and s["llm"]["gen_tokens"] == 41
    assert s["bot"]["in"] == 1 and s["bot"]["replied"] == 1 and s["bot"]["reply_avg_s"] == 13.8
    assert s["api"]["by_service"] == {"ollama": 1, "discord": 1}
    assert sum(h["gen"] for h in s["hourly"]) == 41


def test_inflight_tracking():
    mon = Monitor(CFG, clock=Clock())
    mon.ingest_entry(e(T0, "slot launch_slot_: id  0 | task 9 | processing task, is_child = 0"))
    mon.ingest_entry(e(T0 + 1, "slot print_timing: id  0 | task 9 | prompt processing, n_tokens =    883, progress = 1.00, t =  11.30 s / 78.16 tokens per second"))
    assert mon.inflight["phase"] == "generating"


def test_cursor_dedupe():
    mon = Monitor(CFG, clock=Clock())
    line = '[GIN] 2026/10/28 - 16:19:09 | 200 |   13.78s |       127.0.0.1 | POST     "/api/chat"'
    assert mon.ingest_entry(e(T0, line, cursor="c1"))
    assert mon.ingest_entry(e(T0, line, cursor="c1")) is None
    assert len(mon.api_calls) == 1


def test_health_calls_counted_not_listed():
    mon = Monitor(CFG, clock=Clock())
    mon.ingest_entry(e(T0, '[GIN] 2026/10/28 - 16:33:39 | 200 | 64.964µs |       127.0.0.1 | GET      "/api/ps"'))
    assert mon.health["count"] == 1 and not mon.api_calls and not mon.logs


def test_cli_generation_tagged_either_order():
    clock = Clock()
    mon = Monitor(CFG, clock=clock)
    mon.record_cli_generation({"gen_tokens": 41, "prompt_tokens": 887})
    conversation(mon, T0)
    assert mon.generations[-1]["source"] == "cli"
    conversation(mon, T0 + 100, task=8, gen_tokens=12, prompt_tokens=100)
    assert mon.generations[-1]["source"] == "bot"
    mon.record_cli_generation({"gen_tokens": 12, "prompt_tokens": 100})
    assert mon.generations[-1]["source"] == "cli"


def test_alerts_pending_reply_and_hot_gpu():
    clock = Clock()
    mon = Monitor(CFG, clock=clock)
    mon.set_link("connected", None)
    mon.ingest_metrics({"t": T0, "gpu": {"temp_junction": 101}, "ollama": {"ok": True, "latency_ms": 2,
                        "loaded": [{"name": "latochan:latest"}]}})
    mon.ingest_entry(bot(T0, "IN   #general | owner: hi"))
    clock.t = T0 + 5
    ids = {a["id"]: a["level"] for a in mon.alerts()}
    assert ids.get("gpu-temp") == "crit" and "pending" not in ids
    clock.t = T0 + 10
    mon.ingest_metrics({"t": T0 + 130})  # server clock moves on; telemetry still fresh
    ids = {a["id"]: a["level"] for a in mon.alerts()}
    assert ids.get("pending") == "warn"


def test_alerts_link_down_short_circuits():
    mon = Monitor(CFG, clock=Clock())
    mon.set_link("error", "timed out")
    alerts = mon.alerts()
    assert len(alerts) == 1 and alerts[0]["id"] == "link"


def test_service_and_model_alerts():
    clock = Clock()
    mon = Monitor(CFG, clock=clock)
    mon.set_link("connected", None)
    mon.ingest_metrics({"t": T0, "services": {
        "latochan.service": {"active": "inactive", "result": "exit-code", "restarts": 0},
        "ollama.service": {"active": "active", "restarts": 2},
        "gpu-tune.service": {"active": "inactive", "result": "success"},
    }, "ollama": {"ok": True, "latency_ms": 1, "loaded": []}})
    ids = {a["id"]: a["level"] for a in mon.alerts()}
    assert ids["svc:latochan.service"] == "crit"
    assert ids["restart:ollama.service"] == "warn"
    assert "svc:gpu-tune.service" not in ids
    assert ids["model"] == "warn"


def test_publish_and_overflow():
    mon = Monitor(CFG, clock=Clock())
    sub = mon.subscribe()
    for i in range(2100):
        mon.add_event("info", "tool", f"x{i}")
    assert sub.overflowed and sub.queue.qsize() == 2000
    mon.unsubscribe(sub)


def test_one_crash_counts_as_one_error():
    mon = Monitor(CFG, clock=Clock())  # Clock() starts at T0, so these events are inside the 24 h window
    unit = "latochan.service"
    mon.ingest_entry(e(T0, "2026-10-28 16:19:09,123 ERROR respond failed", unit))
    mon.ingest_entry({**e(T0 + .01, "Traceback (most recent call last):", unit), "PRIORITY": "3"})
    mon.ingest_entry(e(T0 + .02, '  File "/opt/latochan/gateway.py", line 126, in <module>', unit))
    mon.ingest_entry(e(T0 + .03, "discord.errors.HTTPException: 503 Service Unavailable", unit))
    assert mon.stats()["bot"]["error"] == 1 and len(mon.error_events) == 1
    mon.ingest_entry(e(T0 + 60, "2026-10-28 16:20:09,123 ERROR LLM call failed", unit))
    assert mon.stats()["bot"]["error"] == 2


def test_head_404_not_an_api_error():
    mon = Monitor(CFG, clock=Clock())
    mon.ingest_entry(e(T0, '[GIN] 2026/10/28 - 02:55:03 | 404 |     97.25µs |       127.0.0.1 | HEAD     "/api/blobs/sha256:54f4"'))
    mon.ingest_entry(e(T0, '[GIN] 2026/10/28 - 02:55:03 | 500 |     2.88s |       127.0.0.1 | POST     "/api/chat"'))
    assert mon.stats()["api"]["errors"] == 1
