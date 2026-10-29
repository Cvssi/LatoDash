from latodash.journal import parse_entry, parse_go_duration_ms


def entry(msg, unit="ollama.service", prio=6, **extra):
    return {"__REALTIME_TIMESTAMP": "1793206081177186", "MESSAGE": msg, "PRIORITY": str(prio),
            "_SYSTEMD_UNIT": unit, "SYSLOG_IDENTIFIER": unit.split(".")[0], **extra}


def test_go_durations():
    assert parse_go_duration_ms("13.78s") == 13780
    assert parse_go_duration_ms("1m2.5s") == 62500
    assert abs(parse_go_duration_ms("91.876µs") - 0.091876) < 1e-9
    assert parse_go_duration_ms("1.5ms") == 1.5
    assert parse_go_duration_ms("garbage") is None


def test_gin_line():
    ev = parse_entry(entry('[GIN] 2026/10/28 - 16:19:09 | 200 |   13.78s |       127.0.0.1 | POST     "/api/chat"'))
    assert ev.kind == "gin" and ev.level == "info"
    assert ev.data == {"status": 200, "latency_ms": 13780.0, "client": "127.0.0.1", "method": "POST",
                       "path": "/api/chat", "health": False}


def test_gin_health_and_errors():
    ev = parse_entry(entry('[GIN] 2026/10/28 - 16:33:39 | 200 | 64.964µs |       127.0.0.1 | GET      "/api/ps"'))
    assert ev.data["health"] and ev.level == "debug"
    ev = parse_entry(entry('[GIN] 2026/10/28 - 16:33:39 | 500 |   2.1s |       127.0.0.1 | POST     "/api/chat"'))
    assert ev.level == "error"


def test_llama_timings():
    p = parse_entry(entry("slot print_timing: id  0 | task 12904 | prompt eval time =   11643.20 ms /   887 tokens (   13.13 ms per token,    76.18 tokens per second)"))
    e = parse_entry(entry("slot print_timing: id  0 | task 12904 |        eval time =    2047.97 ms /    41 tokens (   51.20 ms per token,    19.53 tokens per second)"))
    t = parse_entry(entry("slot print_timing: id  0 | task 12904 |       total time =   13691.18 ms /   928 tokens"))
    assert (p.kind, p.data["tokens"], p.data["tps"]) == ("t_prompt", 887, 76.18)
    assert (e.kind, e.data["tokens"], e.data["tps"]) == ("t_eval", 41, 19.53)
    assert (t.kind, t.data["tokens"], t.data["task"]) == ("t_total", 928, 12904)


def test_progress_and_context():
    ev = parse_entry(entry("slot print_timing: id  0 | task 12904 | prompt processing, n_tokens =    375, progress = 0.86, t =   5.25 s / 71.46 tokens per second"))
    assert ev.kind == "progress" and ev.data["progress"] == 0.86
    ev = parse_entry(entry("slot   operator(): id  0 | task 12904 | new prompt, n_ctx_slot = 8192, n_keep = 4, task.n_tokens = 3660"))
    assert ev.kind == "new_prompt" and ev.data == {"task": 12904, "ctx": 8192, "n": 3660}
    ev = parse_entry(entry("slot      release: id  0 | task 12904 | stop processing: n_tokens = 3700, truncated = 0"))
    assert ev.kind == "stop" and ev.data["n"] == 3700


def test_bot_lines():
    unit = "latochan.service"
    ev = parse_entry(entry("2026-10-28 16:19:09,123 INFO IN   #general | owner: hey lato / you up", unit))
    assert ev.source == "bot" and ev.kind == "in" and ev.data["who"] == "owner"
    assert ev.text.startswith("IN   #")
    ev = parse_entry(entry("2026-10-28 16:19:09,123 INFO RAW  (13.7s, thinking) nah im busy", unit))
    assert ev.kind == "raw" and ev.data == {"secs": 13.7, "thinking": True, "reply": "nah im busy"}
    ev = parse_entry(entry("2026-10-28 16:19:09,123 INFO action PIN blocked: his message didn't ask for it", unit))
    assert ev.kind == "action" and ev.level == "warn"
    ev = parse_entry(entry("Traceback (most recent call last):", unit, prio=3))
    assert ev.kind == "trace" and ev.level == "error"


def test_systemd_lines_attributed_to_unit():
    e = entry("latochan.service: Main process exited, code=killed, status=9/KILL", "init.scope", UNIT="latochan.service")
    e["SYSLOG_IDENTIFIER"] = "systemd"
    ev = parse_entry(e)
    assert ev.unit == "latochan.service" and ev.source == "bot" and ev.kind == "systemd" and ev.level == "error"


def test_kernel_and_binary_message():
    ev = parse_entry({"__REALTIME_TIMESTAMP": "1", "MESSAGE": list(b"amdgpu: ring gfx timeout"), "PRIORITY": "3",
                      "_TRANSPORT": "kernel"})
    assert ev.source == "kernel" and ev.kind == "gpu" and ev.level == "error"


def test_bad_entries_are_skipped():
    assert parse_entry({"MESSAGE": "x"}) is None
    assert parse_entry({"__REALTIME_TIMESTAMP": "1", "MESSAGE": ""}) is None
