"""Локальное распознавание голосовых Telegram через faster-whisper (CPU).

Модель грузится один раз и живёт в памяти процесса: повторные голосовые
не читают диск и не создают новый движок. Платные API не используются.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from pathlib import Path

logger = logging.getLogger("pvl-pult-bot.voice")

MODEL_CACHE_HINT = (
    "Локальный faster-whisper не установлен. "
    "Установите: .venv/bin/pip install faster-whisper"
)


class VoiceError(RuntimeError):
    """Понятная человеку ошибка распознавания голоса."""


class Transcriber:
    """Ленивый кэш модели faster-whisper внутри процесса бота."""

    def __init__(
        self,
        *,
        model_name: str = "tiny",
        download_root: Path | str = "models",
        language: str = "ru",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_name = model_name
        self.download_root = Path(download_root)
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()
        self._loaded_once = False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - зависит от окружения
                raise VoiceError(MODEL_CACHE_HINT) from exc

            self.download_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Загрузка faster-whisper: model=%s device=%s compute=%s dir=%s",
                self.model_name,
                self.device,
                self.compute_type,
                self.download_root,
            )
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.download_root),
                cpu_threads=1,
                num_workers=1,
            )
            self._loaded_once = True
            logger.info("faster-whisper готов к работе")
            return self._model

    def _transcribe_sync(self, audio: bytes) -> str:
        if not audio:
            raise VoiceError("Пустое голосовое сообщение")
        model = self._load_model()
        try:
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=self.language,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
        except Exception as exc:  # битый ogg/opus, нет кодека и т.п.
            logger.exception("Ошибка распознавания голоса")
            raise VoiceError(f"Не удалось распознать голос: {exc}") from exc
        logger.info(
            "Голос распознан: %.1f c аудио, %s символов",
            getattr(info, "duration", 0.0) or 0.0,
            len(text),
        )
        return text

    async def transcribe_bytes(self, audio: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)


def preload_model(transcriber: Transcriber) -> None:
    """Прогрев модели на старте (используется тестом и деплоем)."""
    transcriber._load_model()
