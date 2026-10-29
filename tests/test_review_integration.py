"""Contract checks for reviewed server helpers and snapshot-based bot operations."""
import asyncio
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from latodash.sshlink import RunResult
from latodash.tools import ToolError
from test_tools import make


def helper(name):
    path = Path(__file__).resolve().parents[1] / 'server' / name
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != 'posix', reason='Server helpers run on Linux')
def test_persona_reads_active_config_and_creates_private_backup(tmp_path):
    module = helper('lato-persona-install')
    persona = tmp_path / 'persona.md'
    persona.write_text('original persona')
    env = tmp_path / 'bot.env'
    env.write_text(f'PERSONA_PATH="{persona}"\n')
    env.chmod(0o600)
    with patch.multiple(module, ENV=env, ALLOWED={persona}, ROOT_UID=os.getuid()):
        assert module.target() == persona
        result = module.write(persona, b'new persona')
    assert persona.read_text() == 'new persona'
    backup = Path(result['backup'])
    assert backup.read_text() == 'original persona'
    assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != 'posix', reason='Server helpers run on Linux')
def test_persona_refuses_symlinks_and_writable_parent(tmp_path):
    module = helper('lato-persona-install')
    actual = tmp_path / 'actual.md'
    actual.write_text('do not overwrite')
    persona = tmp_path / 'persona.md'
    persona.symlink_to(actual)
    with pytest.raises(OSError):
        module.read(persona)
    env = tmp_path / 'bot.env'
    env.write_text(f'PERSONA_PATH="{persona}"\n')
    env.chmod(0o600)
    tmp_path.chmod(0o770)
    with patch.multiple(module, ENV=env, ALLOWED={persona}, ROOT_UID=os.getuid()), pytest.raises(ValueError, match='administrator-owned'):
        module.target()


@pytest.mark.skipif(os.name != 'posix', reason='Server helpers run on Linux')
def test_model_helper_parses_quoted_model_without_exposing_token(tmp_path):
    module = helper('lato-set-model')
    env = tmp_path / 'bot.env'
    env.write_text('DISCORD_BOT_TOKEN=dummy-secret\nLLM_MODEL="custom-model:latest"\n')
    env.chmod(0o600)
    with patch.multiple(module, ENV=env, ROOT_UID=os.getuid()):
        assert module.model_from(module.read_env()) == 'custom-model:latest'
        with patch.object(module.subprocess, 'run'):
            result = module.switch('fallback:latest')
        assert 'dummy-secret' not in result
    assert 'DISCORD_BOT_TOKEN=dummy-secret' in env.read_text()
    assert module.model_from(env.read_bytes()) == 'fallback:latest'
    assert env.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != 'posix', reason='Server helpers run on Linux')
def test_model_helper_rolls_back_after_restart_failure(tmp_path):
    module = helper('lato-set-model')
    env = tmp_path / 'bot.env'
    env.write_text('DISCORD_BOT_TOKEN=dummy\nLLM_MODEL="old:latest"\n')
    env.chmod(0o600)
    before = env.read_bytes()
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[1] == 'restart' and len(calls) == 2:
            raise subprocess.CalledProcessError(1, args)
    with patch.multiple(module, ENV=env, ROOT_UID=os.getuid()), patch.object(module.subprocess, 'run', side_effect=run):
        with pytest.raises(ValueError, match='restored'):
            module.switch('new:latest')
    assert env.read_bytes() == before


def test_stale_memory_becomes_conflict_without_overwriting_pc_copy(tmp_path):
    file = tmp_path / 'latochan-memory.txt'
    file.write_text('my existing local copy')
    tools, _ = make(tmp_path, RunResult(1, '', 'Notes changed since export. Nothing imported; export again.'))
    with pytest.raises(ToolError) as error:
        asyncio.run(tools.memory_put('#snapshot ' + 'a' * 64 + '\n[alice 111111111111111111]\nlikes tea\n'))
    assert error.value.status == 409
    assert file.read_text() == 'my existing local copy'


def test_old_memory_export_is_rejected_before_remote_call(tmp_path):
    tools, link = make(tmp_path)
    with pytest.raises(ToolError) as error:
        asyncio.run(tools.memory_put('#exported 100\nold notes'))
    assert error.value.status == 409 and not link.calls


def test_partial_gif_storage_failure_is_explicit_conflict(tmp_path):
    tools, _ = make(tmp_path, RunResult(1, '', "partial update: Updated ['happy']; failed at 'sad'. Earlier changes remain."))
    with pytest.raises(ToolError) as error:
        asyncio.run(tools.gifs_apply('replace', {'happy': ['https://example.com/a.gif']}))
    assert error.value.status == 409 and 'Refresh' in error.value.message


def test_old_persona_helper_cannot_receive_upload(tmp_path):
    tools, link = make(tmp_path, RunResult(0, json.dumps({'protocol': 1}), ''))
    with pytest.raises(ToolError) as error:
        asyncio.run(tools.persona_put('new persona'))
    assert error.value.status == 409
    assert len(link.calls) == 1 and link.calls[0][0].endswith('--info')


def test_demo_never_executes_python_from_local_copy_folder(tmp_path):
    from latodash.demo import DemoBot
    from latodash.config import AppConfig, SSHConfig
    (tmp_path / 'botsettings.py').write_text("raise RuntimeError('must not execute this local file')")
    cfg = AppConfig(ssh=SSHConfig(host='demo', user='demo', key_file=Path('unused'), known_hosts=Path('unused')),
                    latochan_dir=tmp_path)
    bot = DemoBot(cfg)
    try:
        assert bot.ok
        assert bot.bs.__name__ == 'latodash.vendor.botsettings'
    finally:
        bot.tmp.cleanup()
