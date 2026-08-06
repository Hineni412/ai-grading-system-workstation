from __future__ import annotations

import importlib.util
import os
import re
import threading
import wave
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np

from .errors import VaultError


_SENSEVOICE_TAG = re.compile(r"<\|[^|>]+\|>")


class LocalSpeechTranscriber:
    """Decode short, in-memory PCM WAV recordings with local SenseVoice."""

    sample_rate = 16_000
    max_duration_seconds = 60
    max_audio_bytes = 2_100_000
    engine_name = "sherpa-onnx-sensevoice-small-int8-2024-07-17"

    def __init__(
        self,
        project_root: Path,
        *,
        model_dir: Path | None = None,
        recognizer_factory: Callable[[Path], object] | None = None,
    ) -> None:
        self.model_dir = model_dir or (
            Path(project_root)
            / "runtime"
            / "models"
            / "class-teacher"
            / "sensevoice-small-int8-2024-07-17"
        )
        self._recognizer_factory = recognizer_factory
        self._recognizer: object | None = None
        self._recognizer_lock = threading.Lock()
        self._decode_lock = threading.Lock()

    @property
    def model_path(self) -> Path:
        return self.model_dir / "model.int8.onnx"

    @property
    def tokens_path(self) -> Path:
        return self.model_dir / "tokens.txt"

    def capabilities(self) -> dict[str, object]:
        status = "ready"
        available = True
        if self._recognizer_factory is None and importlib.util.find_spec("sherpa_onnx") is None:
            available = False
            status = "dependency_missing"
        elif self._recognizer_factory is None and not (
            self.model_path.is_file() and self.tokens_path.is_file()
        ):
            available = False
            status = "model_missing"
        return {
            "available": available,
            "status": status,
            "engine": self.engine_name,
            "offline": True,
            "sample_rate": self.sample_rate,
            "max_duration_seconds": self.max_duration_seconds,
            "max_audio_bytes": self.max_audio_bytes,
            "accepted_content_type": "audio/wav",
        }

    def transcribe_wav(self, content: bytes) -> dict[str, object]:
        samples, duration_seconds = self._validated_pcm_wav(content)
        recognizer = self._get_recognizer()
        try:
            # The desktop app can receive overlapping HTTP requests even though the
            # composer disables repeat clicks. Keep the shared native recognizer
            # single-flight so one recording cannot corrupt another.
            with self._decode_lock:
                stream = recognizer.create_stream()
                stream.accept_waveform(self.sample_rate, samples)
                recognizer.decode_stream(stream)
                raw_text = str(stream.result.text or "")
        except VaultError:
            raise
        except Exception as exc:
            raise VaultError(
                "speech_transcription_failed",
                "本地语音转写没有完成，请保留现有文字后重试",
                status_code=503,
            ) from exc
        text = _SENSEVOICE_TAG.sub("", raw_text).strip()
        if not text:
            raise VaultError(
                "speech_no_text",
                "没有识别到清晰语音，请靠近麦克风后重试",
                status_code=422,
            )
        if len(text) > 4000:
            raise VaultError(
                "speech_text_too_long",
                "转写文字超过输入框上限，请分段录入",
                status_code=422,
            )
        return {
            "text": text,
            "duration_seconds": round(duration_seconds, 2),
            "engine": self.engine_name,
            "audio_retained": False,
        }

    def validate_wav(self, content: bytes) -> float:
        """Validate a browser recording without loading either speech engine."""

        _samples, duration_seconds = self._validated_pcm_wav(content)
        return round(duration_seconds, 2)

    def _validated_pcm_wav(self, content: bytes) -> tuple[np.ndarray, float]:
        if len(content) > self.max_audio_bytes:
            raise VaultError(
                "speech_audio_too_large",
                "录音超过本机语音输入允许的大小",
                status_code=413,
            )
        return self._read_pcm_wav(content)

    def _read_pcm_wav(self, content: bytes) -> tuple[np.ndarray, float]:
        if not content:
            raise VaultError(
                "speech_audio_empty",
                "录音内容为空，请重新录制",
                status_code=422,
            )
        try:
            with wave.open(BytesIO(content), "rb") as source:
                channels = source.getnchannels()
                sample_width = source.getsampwidth()
                sample_rate = source.getframerate()
                frame_count = source.getnframes()
                compression = source.getcomptype()
                frames = source.readframes(frame_count)
        except (EOFError, wave.Error) as exc:
            raise VaultError(
                "speech_audio_invalid",
                "录音格式无法识别，请重新录制",
                status_code=422,
            ) from exc
        if (
            channels != 1
            or sample_width != 2
            or sample_rate != self.sample_rate
            or compression != "NONE"
        ):
            raise VaultError(
                "speech_audio_invalid",
                "录音必须是 16kHz 单声道 WAV",
                status_code=422,
            )
        if frame_count <= 0:
            raise VaultError(
                "speech_audio_empty",
                "录音内容为空，请重新录制",
                status_code=422,
            )
        duration_seconds = frame_count / self.sample_rate
        if duration_seconds > self.max_duration_seconds + 0.05:
            raise VaultError(
                "speech_audio_too_long",
                "单次语音输入不能超过 60 秒，请分段录入",
                status_code=422,
            )
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
        samples /= 32768.0
        return samples, duration_seconds

    def _get_recognizer(self) -> object:
        if self._recognizer is not None:
            return self._recognizer
        with self._recognizer_lock:
            if self._recognizer is not None:
                return self._recognizer
            capabilities = self.capabilities()
            if not capabilities["available"]:
                message = (
                    "本地语音组件尚未安装"
                    if capabilities["status"] == "dependency_missing"
                    else "本地语音模型尚未安装"
                )
                raise VaultError(
                    "speech_engine_unavailable",
                    message,
                    status_code=503,
                )
            try:
                factory = self._recognizer_factory or self._create_sensevoice_recognizer
                self._recognizer = factory(self.model_dir)
            except VaultError:
                raise
            except Exception as exc:
                raise VaultError(
                    "speech_engine_load_failed",
                    "本地语音组件没有成功启动",
                    status_code=503,
                ) from exc
        return self._recognizer

    @staticmethod
    def _create_sensevoice_recognizer(model_dir: Path) -> object:
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=max(1, min(4, os.cpu_count() or 1)),
            language="zh",
            use_itn=True,
            provider="cpu",
            debug=False,
        )


__all__ = ["LocalSpeechTranscriber"]
