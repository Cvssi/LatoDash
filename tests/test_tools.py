import asyncio
import json
from pathlib import Path

import pytest

from latodash.config import AppConfig, SSHConfig
from latodash.monitor import Monitor
from latodash.sshlink import RunResult
from latodash.tools import ToolError, Tools


class FakeLink:
    def __init__(self, result=RunResult(0, "", "")):
        self.calls = []
        self.result = result

    async def run(self, command, *, input=None, timeout=30):
        self.calls.append((command, input))
        return self.result.pop(0) if isinstance(self.result, list) else self.result


def make(tmp_path, result=RunResult(0, "", "")):
    cfg = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")), latochan_dir=tmp_path)
    link = FakeLink(result)
    return Tools(link, Monitor(cfg), cfg), link


def run(coro):
    return asyncio.run(coro)


def test_user_text_only_goes_over_stdin(tmp_path):
    tools, link = make(tmp_path, RunResult(0, "sent 1", ""))
    evil = '"; sudo reboot; echo "'
    run(tools.say(evil))
    cmd, stdin = link.calls[0]
    assert evil not in cmd and stdin == evil
    assert cmd == 'sudo -n lato-say --stdin'


def test_persona_upload_keeps_pc_copy_and_backup(tmp_path):
    (tmp_path / "persona.md").write_text("old")
    tools, link = make(tmp_path, [RunResult(0, json.dumps({'protocol': 2, 'path': '/etc/latochan/persona.md'}), ''),
                                  RunResult(0, json.dumps({'protocol': 2, 'path': '/etc/latochan/persona.md',
                                                         'backup': '/etc/latochan/persona.md.bak-2026-10-28-171500'}), '')])
    res = run(tools.persona_put("new persona"))
    assert res["backup"].endswith(".bak-2026-10-28-171500")
    assert (tmp_path / "persona.md").read_text() == "new persona"
    assert (tmp_path / "persona.md.pc-backup").read_text() == "old"
    cmd, stdin = link.calls[1]
    assert stdin == "new persona" and cmd == "sudo -n /usr/local/sbin/lato-persona-install"


def test_empty_persona_refused(tmp_path):
    tools, link = make(tmp_path)
    with pytest.raises(ToolError) as exc:
        run(tools.persona_put("   "))
    assert exc.value.status == 422 and not link.calls


def test_sudo_password_reported(tmp_path):
    tools, _ = make(tmp_path, RunResult(1, "", "sudo: a password is required"))
    with pytest.raises(ToolError) as exc:
        run(tools.restart_bot())
    assert exc.value.status == 403 and "sudoers" in exc.value.message


def test_thinking_parses_output(tmp_path):
    tools, link = make(tmp_path, RunResult(0, "thinking: [ON]  (slower)", ""))
    assert run(tools.thinking("on"))["thinking"] is True
    assert link.calls[0][0] == "lato-config thinking on"
    with pytest.raises(ToolError):
        run(tools.thinking("sideways"))


def test_memory_list_rejects_odd_names(tmp_path):
    tools, link = make(tmp_path, RunResult(0, "ok", ""))
    run(tools.memory_list("bob"))
    assert link.calls[0][0] == "lato-memory list bob"
    with pytest.raises(ToolError):
        run(tools.memory_list("x; rm -rf /"))


def test_switch_model_validates_and_quotes(tmp_path):
    tools, link = make(tmp_path, RunResult(0, "latochan now on latochan-alt", ""))
    run(tools.set_live_model("latochan-alt"))
    assert link.calls[0][0] == "sudo -n /usr/local/sbin/lato-set-model latochan-alt"
    with pytest.raises(ToolError):
        run(tools.set_live_model("x; reboot"))


# ---- v1.0.1: bot settings + gif library --------------------------------------------------------------
LATOCHAN = Path(__file__).resolve().parents[1] / "latodash" / "vendor"


def test_settings_apply_sends_json_over_stdin(tmp_path):
    out = json.dumps({"values": {"thinking": True}})
    tools, link = make(tmp_path, RunResult(0, out, ""))
    res = run(tools.bot_settings_apply({"thinking": True, "gif_chance": 40}))
    cmd, stdin = link.calls[0]
    assert cmd == "lato-config apply" and json.loads(stdin) == {"thinking": True, "gif_chance": 40}
    assert res["values"]["thinking"] is True
    assert tools.mon.settings["thinking"] is True


@pytest.mark.parametrize("bad", [{}, {"Bad Key": 1}, {"x; reboot": 1}, {"thinking": [1]}, {"thinking": "x" * 40}])
def test_settings_apply_rejects_odd_input(tmp_path, bad):
    tools, link = make(tmp_path)
    with pytest.raises(ToolError) as exc:
        run(tools.bot_settings_apply(bad))
    assert exc.value.status == 422 and not link.calls


def test_server_rejection_becomes_422(tmp_path):
    tools, _ = make(tmp_path, RunResult(1, "", "rejected: gif_chance: must be between 0 and 100\n"))
    with pytest.raises(ToolError) as exc:
        run(tools.bot_settings_apply({"gif_chance": 500}))
    assert exc.value.status == 422 and "between 0 and 100" in exc.value.message


def test_unexpected_server_output_is_502(tmp_path):
    tools, _ = make(tmp_path, RunResult(0, "not json", ""))
    with pytest.raises(ToolError) as exc:
        run(tools.bot_settings())
    assert exc.value.status == 502


def test_reset_quotes_keys(tmp_path):
    tools, link = make(tmp_path, RunResult(0, json.dumps({"values": {}}), ""))
    run(tools.bot_settings_reset(["gif_chance", "sass"]))
    assert link.calls[0][0] == "lato-config reset gif_chance sass"
    with pytest.raises(ToolError):
        run(tools.bot_settings_reset(["x; rm -rf /"]))


def test_gif_request_validation(tmp_path):
    tools, link = make(tmp_path, RunResult(0, json.dumps({"added": 1, "removed": 0, "moods": {}}), ""))
    run(tools.gifs_apply("add", {"happy": ["https://x.io/a.gif"]}))
    cmd, stdin = link.calls[0]
    assert cmd == "lato-gifs apply" and json.loads(stdin) == {"op": "add", "moods": {"happy": ["https://x.io/a.gif"]}}
    for op, moods in [("nuke", {"happy": []}), ("add", {}), ("add", {"../etc": ["https://a.b/c"]}),
                      ("add", {"Happy": ["https://a.b/c"]}), ("add", {"happy": ["h" * 600]}), ("add", {"happy": "https://a.b/c"})]:
        with pytest.raises(ToolError) as exc:
            run(tools.gifs_apply(op, moods))
        assert exc.value.status == 422
    assert len(link.calls) == 1


def test_pc_gif_folder_roundtrip(tmp_path):
    gifs = tmp_path / "gifs"
    (gifs / "happy").mkdir(parents=True)
    (gifs / "happy" / "links.txt").write_text("# comment\nhttps://a.io/1.gif\nhttps://a.io/1.gif\nhttp://bad.io/x\n\nhttps://a.io/2.gif\n")
    (gifs / "Not A Mood").mkdir()
    (gifs / "Not A Mood" / "links.txt").write_text("https://a.io/zzz\n")
    (gifs / "sad").mkdir()
    (gifs / "sad" / "links.txt").write_text("# nothing yet\n")
    reply = json.dumps({"added": 2, "removed": 0, "skipped": [], "moods": {}})
    tools, link = make(tmp_path, RunResult(0, reply, ""))
    pc = run(tools.gifs_pc())
    assert pc["moods"] == {"happy": ["https://a.io/1.gif", "https://a.io/2.gif"], "sad": []}
    run(tools.gifs_import_pc("add"))
    sent = json.loads(link.calls[-1][1])
    assert sent == {"op": "add", "moods": {"happy": ["https://a.io/1.gif", "https://a.io/2.gif"]}}   # empty moods never sent
    run(tools.gifs_import_pc("replace"))
    assert json.loads(link.calls[-1][1])["op"] == "replace"
    with pytest.raises(ToolError):
        run(tools.gifs_import_pc("merge"))


def test_pc_export_keeps_backup(tmp_path):
    (tmp_path / "gifs" / "happy").mkdir(parents=True)
    (tmp_path / "gifs" / "happy" / "links.txt").write_text("https://old.io/x\n")
    reply = json.dumps({"moods": {"happy": ["https://new.io/y"], "sad": [], "Bad Name": ["https://z"]}})
    tools, _ = make(tmp_path, RunResult(0, reply, ""))
    res = run(tools.gifs_export_pc())
    assert (tmp_path / "gifs" / "happy" / "links.txt").read_text() == "https://new.io/y\n"
    assert (tmp_path / "gifs" / "happy" / "links.txt.pc-backup").read_text() == "https://old.io/x\n"
    assert (tmp_path / "gifs" / "sad" / "links.txt").read_text() == ""
    assert not (tmp_path / "gifs" / "Bad Name").exists() and res["written"] == 2


def test_no_pc_folder_is_a_clean_error():
    cfg = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")))
    tools = Tools(FakeLink(), Monitor(cfg), cfg)
    with pytest.raises(ToolError) as exc:
        run(tools.gifs_pc())
    assert exc.value.status == 409


@pytest.mark.skipif(not (LATOCHAN / "botsettings.py").exists(), reason="vendored bot files missing")
def test_demo_bot_uses_real_rules():
    from latodash.demo import DemoBot
    cfg = AppConfig(ssh=SSHConfig(host="h", user="u", key_file=Path("k"), known_hosts=Path("kh")), latochan_dir=LATOCHAN)
    bot = DemoBot(cfg)
    assert bot.ok and bot.doc()["values"]["thinking"] is False
    bot.bs.write_patch({"thinking": True, "gif_chance": 35}, bot.settings_path)
    assert bot.values()["thinking"] is True and bot.values()["gif_chance"] == 35
    with pytest.raises(ToolError) as exc:
        bot.guard(bot.bs.write_patch, {"gif_chance": 999}, bot.settings_path)
    assert exc.value.status == 422
    assert "happy" in bot.gs.all_moods()
