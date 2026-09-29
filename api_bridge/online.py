"""Mixin para consumo de serviços online (Google Translate e Edge TTS) com circuit breakers."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from api_bridge.common import _split_text_chunks, _split_translation_chunks
from constants import (
    MAX_TRANSLATION_CHARACTERS,
    MAX_TTS_CHARACTERS,
    ONLINE_SERVICE_BACKOFF_SECONDS,
    ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
    ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
    ONLINE_SERVICE_MAX_ATTEMPTS,
    TRANSLATION_CHUNK_CHARACTERS,
    TRANSLATION_TIMEOUT_SECONDS,
    TTS_CHUNK_CHARACTERS,
    TTS_TIMEOUT_SECONDS,
)
from online_services import (
    CircuitBreaker,
    ServiceCircuitOpen,
    ServiceOperationTimeout,
    call_with_resilience,
)

logger = logging.getLogger(__name__)


class OnlineMixin:
    """Métodos de tradução neural e síntese de voz (TTS)."""

    def _online_breaker(self, service: str) -> CircuitBreaker:
        breakers = getattr(self, "_online_service_breakers", None)
        if breakers is None:
            breakers = {}
            self._online_service_breakers = breakers
        if service not in breakers:
            breakers[service] = CircuitBreaker(
                ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD,
                ONLINE_SERVICE_CIRCUIT_RESET_SECONDS,
            )
        return breakers[service]

    def _online_service_status(self, service: str) -> dict[str, Any]:
        timeout = TRANSLATION_TIMEOUT_SECONDS if service == "translation" else TTS_TIMEOUT_SECONDS
        return {
            "service": service,
            **self._online_breaker(service).status(),
            "timeout_seconds": timeout,
            "max_attempts": ONLINE_SERVICE_MAX_ATTEMPTS,
            "external_dependency": True,
            "contractual_availability_guarantee": False,
            "critical_use_recommended": False,
        }

    def get_online_services_status(self) -> dict[str, Any]:
        """Expõe a condição operacional sem realizar chamadas aos provedores."""
        return {
            "ok": True,
            "services": {
                name: self._online_service_status(name)
                for name in ("translation", "tts")
            },
        }

    def _online_failure(self, service: str, error: BaseException) -> dict[str, Any]:
        status = self._online_service_status(service)
        prefix = "Falha ao traduzir trecho: " if service == "translation" else "Falha na síntese de voz: "
        if isinstance(error, ServiceCircuitOpen):
            error_code = "circuit_open"
            message = (
                "Serviço temporariamente suspenso após falhas consecutivas. "
                f"Tente novamente em cerca de {max(1, round(error.retry_after_seconds))} segundos."
            )
        elif isinstance(error, ServiceOperationTimeout):
            error_code = "service_timeout"
            message = "O serviço online não respondeu dentro do tempo máximo. Tente novamente mais tarde."
        else:
            error_code = "service_unavailable"
            message = "O serviço online está indisponível no momento. Tente novamente mais tarde."
        return {
            "ok": False,
            "error": f"{prefix}{message}",
            "error_code": error_code,
            "retryable": True,
            "service_status": status,
        }

    def get_available_voices(self) -> dict[str, Any]:
        """Retorna as vozes neurais suportadas para leitura com alta fidelidade."""
        voices = [
            {"id": "pt-BR-FranciscaNeural", "name": "Francisca (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "pt-BR-AntonioNeural", "name": "Antônio (Português - Brasil)", "gender": "Masculina", "lang": "pt-BR"},
            {"id": "pt-BR-ThalitaNeural", "name": "Thalita (Português - Brasil)", "gender": "Feminina", "lang": "pt-BR"},
            {"id": "en-US-JennyNeural", "name": "Jenny (Inglês - EUA)", "gender": "Feminina", "lang": "en-US"},
            {"id": "en-US-GuyNeural", "name": "Guy (Inglês - EUA)", "gender": "Masculino", "lang": "en-US"},
            {"id": "es-ES-ElviraNeural", "name": "Elvira (Espanhol - Espanha)", "gender": "Feminina", "lang": "es-ES"},
            {"id": "es-ES-AlvaroNeural", "name": "Álvaro (Espanhol - Espanha)", "gender": "Masculino", "lang": "es-ES"},
            {"id": "fr-FR-DeniseNeural", "name": "Denise (Francês - França)", "gender": "Feminina", "lang": "fr-FR"},
            {"id": "it-IT-ElsaNeural", "name": "Elsa (Italiano - Itália)", "gender": "Feminina", "lang": "it-IT"},
            {"id": "de-DE-KatjaNeural", "name": "Katja (Alemão - Alemanha)", "gender": "Feminina", "lang": "de-DE"},
        ]
        return {"ok": True, "voices": voices, "service_status": self._online_service_status("tts")}

    def synthesize_speech(
        self,
        text: str,
        voice: str = "pt-BR-FranciscaNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> dict[str, Any]:
        import web_api

        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para síntese de voz."}
        if len(cleaned_text) > MAX_TTS_CHARACTERS:
            return {
                "ok": False,
                "error": f"O texto para voz excede o limite de {MAX_TTS_CHARACTERS} caracteres.",
            }
        text_chunks = _split_text_chunks(cleaned_text, TTS_CHUNK_CHARACTERS)

        async def _run_tts() -> list[bytes]:
            audio_segments: list[bytes] = []
            for text_chunk in text_chunks:
                communicate = web_api.edge_tts.Communicate(text_chunk, voice, rate=rate, pitch=pitch)
                segment_chunks: list[bytes] = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        segment_chunks.append(chunk["data"])
                segment = b"".join(segment_chunks)
                if not segment:
                    raise RuntimeError("O serviço não gerou um dos blocos de áudio.")
                audio_segments.append(segment)
            return audio_segments

        try:
            resilient = call_with_resilience(
                lambda: asyncio.run(_run_tts()),
                self._online_breaker("tts"),
                timeout_seconds=TTS_TIMEOUT_SECONDS,
                max_attempts=ONLINE_SERVICE_MAX_ATTEMPTS,
                backoff_seconds=ONLINE_SERVICE_BACKOFF_SECONDS,
            )
            audio_segments: list[bytes] = resilient.value

            if not audio_segments:
                return {"ok": False, "error": "Nenhum dado de áudio foi gerado."}

            data_uris = [
                f"data:audio/mp3;base64,{base64.b64encode(segment).decode('utf-8')}"
                for segment in audio_segments
            ]
            return {
                "ok": True,
                "audio_base64": data_uris[0],
                "audio_segments": data_uris,
                "voice": voice,
                "text_length": len(cleaned_text),
                "chunk_count": len(text_chunks),
                "attempts": resilient.attempts,
                "service_status": self._online_service_status("tts"),
            }
        except Exception as error:
            logger.error(f"Erro no Edge-TTS: {error}", exc_info=True)
            return self._online_failure("tts", error)

    def translate_text(self, text: str, target_lang: str = "pt", source_lang: str = "auto") -> dict[str, Any]:
        """Traduz texto online com Google Translator por meio de deep-translator."""
        import web_api

        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return {"ok": False, "error": "Nenhum texto informado para tradução."}
        if len(cleaned_text) > MAX_TRANSLATION_CHARACTERS:
            return {
                "ok": False,
                "error": f"O texto para tradução excede o limite de {MAX_TRANSLATION_CHARACTERS} caracteres.",
            }
        text_chunks = _split_translation_chunks(cleaned_text, TRANSLATION_CHUNK_CHARACTERS)

        def _translate_chunks(source: str) -> str:
            translator = web_api.GoogleTranslator(source=source, target=target_lang)
            translated_chunks: list[str] = []
            for chunk_index, (text_chunk, separator) in enumerate(text_chunks, start=1):
                translated = translator.translate(text_chunk)
                if translated is None:
                    raise RuntimeError(f"O serviço não retornou o bloco {chunk_index} da tradução.")
                translated_chunks.append(f"{translated}{separator}")
            return "".join(translated_chunks)

        def _translate_with_fallback() -> tuple[str, str]:
            try:
                return _translate_chunks(source_lang), source_lang
            except Exception:
                if source_lang == "auto":
                    raise
                return _translate_chunks("auto"), "auto"

        try:
            resilient = call_with_resilience(
                _translate_with_fallback,
                self._online_breaker("translation"),
                timeout_seconds=TRANSLATION_TIMEOUT_SECONDS,
                max_attempts=ONLINE_SERVICE_MAX_ATTEMPTS,
                backoff_seconds=ONLINE_SERVICE_BACKOFF_SECONDS,
            )
            translated, effective_source = resilient.value
            return {
                "ok": True,
                "original_text": cleaned_text,
                "translated_text": translated or "",
                "source_lang": effective_source,
                "target_lang": target_lang,
                "attempts": resilient.attempts,
                "service_status": self._online_service_status("translation"),
            }
        except Exception as error:
            logger.error(f"Erro na tradução: {error}", exc_info=True)
            return self._online_failure("translation", error)
