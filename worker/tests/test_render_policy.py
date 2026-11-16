"""Render-engine policy: GPU workers must not silently fall back to the CPU renderer."""
import importlib
import platform

from clipper import config, fastrender


def test_encoder_matches_platform():
    assert fastrender.encoder() == ("h264_nvenc" if platform.system() == "Linux" else "h264_videotoolbox")


def test_cpu_fallback_default_off_on_linux(monkeypatch):
    monkeypatch.delenv("RENDER_CPU_FALLBACK", raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert importlib.reload(config).RENDER_CPU_FALLBACK is False
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert importlib.reload(config).RENDER_CPU_FALLBACK is True


def test_cpu_fallback_env_override(monkeypatch):
    monkeypatch.setenv("RENDER_CPU_FALLBACK", "1")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert importlib.reload(config).RENDER_CPU_FALLBACK is True
