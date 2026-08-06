from __future__ import annotations

import wave
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.local_speech import LocalSpeechTranscriber


class _FakeStream:
    def __init__(self, text: str) -> None:
        self.result = SimpleNamespace(text=text)
        self.samples = None

    def accept_waveform(self, sample_rate: int, samples) -> None:
        assert sample_rate == 16_000
        self.samples = samples


class _FakeRecognizer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.stream: _FakeStream | None = None

    def create_stream(self) -> _FakeStream:
        self.stream = _FakeStream(self.text)
        return self.stream

    def decode_stream(self, stream: _FakeStream) -> None:
        assert stream.samples is not None


def _wav(*, seconds: float = 0.25, channels: int = 1, sample_rate: int = 16_000) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x00\x00" * int(seconds * sample_rate) * channels)
    return output.getvalue()


def test_local_speech_transcribes_in_memory_and_drops_sensevoice_tags(tmp_path: Path) -> None:
    recognizer = _FakeRecognizer("<|zh|><|NEUTRAL|><|Speech|>今天复查合成事项。")
    service = LocalSpeechTranscriber(
        tmp_path,
        model_dir=tmp_path / "missing-model-is-not-needed-for-fake",
        recognizer_factory=lambda _model_dir: recognizer,
    )

    assert service.capabilities()["available"] is True
    result = service.transcribe_wav(_wav())

    assert result == {
        "text": "今天复查合成事项。",
        "duration_seconds": 0.25,
        "engine": "sherpa-onnx-sensevoice-small-int8-2024-07-17",
        "audio_retained": False,
    }
    assert list(tmp_path.rglob("*.wav")) == []


def test_local_speech_configures_mandarin_cpu_int8(monkeypatch, tmp_path: Path) -> None:
    import sherpa_onnx

    captured: dict[str, object] = {}

    def fake_from_sense_voice(**options):
        captured.update(options)
        return object()

    monkeypatch.setattr(
        sherpa_onnx.OfflineRecognizer,
        "from_sense_voice",
        fake_from_sense_voice,
    )

    result = LocalSpeechTranscriber._create_sensevoice_recognizer(tmp_path)

    assert result is not None
    assert captured["language"] == "zh"
    assert captured["use_itn"] is True
    assert captured["provider"] == "cpu"
    assert captured["model"] == str(tmp_path / "model.int8.onnx")
    assert captured["tokens"] == str(tmp_path / "tokens.txt")


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"not-a-wave", "speech_audio_invalid"),
        (_wav(channels=2), "speech_audio_invalid"),
        (_wav(sample_rate=8_000), "speech_audio_invalid"),
        (_wav(seconds=60.1), "speech_audio_too_long"),
    ],
    ids=["not-wave", "stereo", "wrong-rate", "too-long"],
)
def test_local_speech_rejects_unsafe_or_unsupported_audio(
    tmp_path: Path,
    content: bytes,
    code: str,
) -> None:
    service = LocalSpeechTranscriber(
        tmp_path,
        recognizer_factory=lambda _model_dir: _FakeRecognizer("不会使用"),
    )
    with pytest.raises(VaultError) as captured:
        service.transcribe_wav(content)
    assert captured.value.code == code


def test_local_speech_reports_missing_local_engine_without_exposing_paths(tmp_path: Path) -> None:
    service = LocalSpeechTranscriber(tmp_path, model_dir=tmp_path / "missing")
    capability = service.capabilities()
    assert capability["available"] is False
    assert capability["status"] in {"dependency_missing", "model_missing"}
    assert str(tmp_path) not in str(capability)
