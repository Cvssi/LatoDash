"""NVIDIA fallback in the remote collector (stdlib only; nvidia-smi is faked)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "latodash" / "remote" / "collector.py"


def load(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["collector.py"])
    spec = importlib.util.spec_from_file_location("collector_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_smi(mod, monkeypatch, stdout="", returncode=0, stderr=""):
    monkeypatch.setattr(mod, "NVIDIA_SMI", "/usr/bin/nvidia-smi")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr))


def test_single_gpu_maps_to_dashboard_keys(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "93, 41, 9421, 10240, 67, 247.31, 250.00, 62, 1710, 9501, NVIDIA Test GPU\n")
    g = mod.nvidia_section()
    assert g["busy"] == 93 and g["mem_busy"] == 41
    assert g["vram_used"] == 9421 * 1024 * 1024 and g["vram_total"] == 10240 * 1024 * 1024
    assert g["temp_edge"] == 67 and g["power_w"] == 247.31 and g["power_cap_w"] == 250
    assert g["fan_pct"] == 62 and g["sclk_mhz"] == 1710 and g["mclk_mhz"] == 9501
    assert g["name"] == "NVIDIA Test GPU" and len(g["gpus"]) == 1
    assert "temp_junction" not in g          # no hotspot sensor: the dashboard shows a dash


def test_na_fields_become_none(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "0, 0, 12, 10240, 33, [N/A], [N/A], [Not Supported], 210, 405, NVIDIA Test GPU\n")
    g = mod.nvidia_section()
    assert g["power_w"] is None and g["power_cap_w"] is None and g["fan_pct"] is None
    assert g["busy"] == 0 and g["temp_edge"] == 33


def test_two_gpus_first_on_top_all_listed(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "10, 5, 100, 10240, 40, 60, 250, 30, 210, 405, Test GPU\n"
                               "90, 50, 9000, 10240, 70, 240, 250, 60, 1700, 9501, Test GPU\n")
    g = mod.nvidia_section()
    assert g["index"] == 0 and g["busy"] == 10 and [x["index"] for x in g["gpus"]] == [0, 1]


def test_driver_missing_is_reported_not_swallowed(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "", returncode=9, stderr="NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n")
    with pytest.raises(RuntimeError, match="couldn't communicate"):
        mod.nvidia_section()


def test_gpu_section_none_when_no_gpu_at_all(monkeypatch):
    mod = load(monkeypatch)
    monkeypatch.setattr(mod, "GPU_DEV", None)
    monkeypatch.setattr(mod, "NVIDIA_SMI", None)
    assert mod.gpu_section() is None            # no GPU at all: section is simply absent


AMD = {"vendor": "amd", "name": "AMD Test GPU", "busy": 5, "vram_total": 8 << 30, "vram_used": 1 << 30, "temp_junction": 50}
SMI_OK = "30, 10, 5000, 10240, 60, 200, 250, 50, 1700, 9501, NVIDIA Test GPU\n"


def test_both_gpus_nvidia_is_primary_amd_listed(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, SMI_OK)
    monkeypatch.setattr(mod, "GPU_DEV", "/sys/fake")
    monkeypatch.setattr(mod, "amd_section", lambda: dict(AMD))
    g = mod.gpu_section()
    assert g["vendor"] == "nvidia" and g["busy"] == 30
    assert [(x["index"], x["vendor"]) for x in g["gpus"]] == [(0, "nvidia"), (1, "amd")]
    assert g["gpus"][1]["name"] == "AMD Test GPU"


def test_dead_nvidia_driver_keeps_amd_and_flags_error(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "", returncode=9, stderr="Unable to determine the device handle")
    monkeypatch.setattr(mod, "GPU_DEV", "/sys/fake")
    monkeypatch.setattr(mod, "amd_section", lambda: dict(AMD))
    g = mod.gpu_section()
    assert g["vendor"] == "amd" and len(g["gpus"]) == 1 and "device handle" in g["nvidia_error"]


def test_dead_nvidia_driver_alone_still_raises(monkeypatch):
    mod = load(monkeypatch)
    fake_smi(mod, monkeypatch, "", returncode=9, stderr="boom")
    monkeypatch.setattr(mod, "GPU_DEV", None)
    with pytest.raises(RuntimeError, match="boom"):
        mod.gpu_section()


def test_amd_only(monkeypatch):
    mod = load(monkeypatch)
    monkeypatch.setattr(mod, "NVIDIA_SMI", None)
    monkeypatch.setattr(mod, "GPU_DEV", "/sys/fake")
    monkeypatch.setattr(mod, "amd_section", lambda: dict(AMD))
    g = mod.gpu_section()
    assert g["vendor"] == "amd" and g["gpus"][0]["index"] == 0 and "nvidia_error" not in g
