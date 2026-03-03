"""
tests/test_voice_message.py - 语音消息服务测试
"""

import os
import time
import pytest
import threading
from unittest.mock import patch

from services.voice_message import (
    VoiceMessageService, VoiceConfig, VoiceCache,
    AudioConverter, AudioFormat,
    STTEngine, TTSEngine,
    STTResult, TTSResult,
)


# ──────────────── VoiceConfig ────────────────

class TestVoiceConfig:
    def test_defaults(self):
        cfg = VoiceConfig()
        assert cfg.stt_engine == STTEngine.MOCK
        assert cfg.tts_engine == TTSEngine.MOCK
        assert cfg.default_language == "zh"
        assert cfg.cache_enabled is True
        assert cfg.max_audio_duration == 300
        assert cfg.max_tts_length == 5000
        assert cfg.max_file_size_mb == 25

    def test_from_env(self):
        with patch.dict(os.environ, {
            "STT_ENGINE": "mock",
            "TTS_ENGINE": "mock",
            "VOICE_LANGUAGE": "en",
            "VOICE_CACHE": "false",
        }):
            cfg = VoiceConfig.from_env()
            assert cfg.default_language == "en"
            assert cfg.cache_enabled is False

    def test_custom_config(self):
        cfg = VoiceConfig(
            stt_engine=STTEngine.OPENAI_WHISPER,
            openai_api_key="sk-test",
            max_audio_duration=60,
        )
        assert cfg.stt_engine == STTEngine.OPENAI_WHISPER
        assert cfg.openai_api_key == "sk-test"
        assert cfg.max_audio_duration == 60


# ──────────────── AudioConverter ────────────────

class TestAudioConverter:
    def test_detect_ogg(self):
        data = b"OggS" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.OGG

    def test_detect_mp3_id3(self):
        data = b"ID3" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.MP3

    def test_detect_mp3_sync(self):
        data = bytes([0xFF, 0xE0]) + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.MP3

    def test_detect_wav(self):
        data = b"RIFF" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.WAV

    def test_detect_amr(self):
        data = b"#!AMR\n" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.AMR

    def test_detect_silk(self):
        data = b"#!SILK_V3" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.SILK

    def test_detect_silk_v2(self):
        data = b"\x02#!SILK_V3" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.SILK

    def test_detect_webm(self):
        data = b"\x1a\x45\xdf\xa3" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.WEBM

    def test_detect_m4a(self):
        data = b"\x00\x00\x00\x20ftyp" + b"\x00" * 100
        assert AudioConverter.detect_format(data) == AudioFormat.M4A

    def test_detect_unknown(self):
        data = b"\x00" * 100
        assert AudioConverter.detect_format(data) is None

    def test_detect_too_short(self):
        data = b"\x00" * 5
        assert AudioConverter.detect_format(data) is None


# ──────────────── VoiceCache ────────────────

class TestVoiceCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return VoiceCache(cache_dir=str(tmp_path / "cache"), ttl=3600)

    def test_stt_miss(self, cache):
        result = cache.get_stt(b"audio data")
        assert result is None

    def test_stt_hit(self, cache):
        audio = b"test audio data"
        stt_result = STTResult(
            text="你好世界",
            language="zh",
            confidence=0.98,
            duration_seconds=2.5,
            engine="mock",
        )
        cache.set_stt(audio, stt_result)
        cached = cache.get_stt(audio)

        assert cached is not None
        assert cached.text == "你好世界"
        assert cached.language == "zh"
        assert cached.cached is True

    def test_stt_different_audio(self, cache):
        cache.set_stt(b"audio1", STTResult(text="text1"))
        assert cache.get_stt(b"audio2") is None

    def test_tts_miss(self, cache):
        result = cache.get_tts("hello", "alloy", "openai")
        assert result is None

    def test_tts_hit(self, cache):
        # 创建真实文件
        audio_path = os.path.join(cache.cache_dir, "test.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake mp3 data")

        tts_result = TTSResult(
            audio_path=audio_path,
            format="mp3",
            duration_seconds=1.5,
            engine="mock",
            voice="alloy",
        )
        cache.set_tts("hello world", tts_result, "alloy", "mock")
        cached = cache.get_tts("hello world", "alloy", "mock")

        assert cached is not None
        assert cached.format == "mp3"
        assert cached.cached is True

    def test_tts_file_deleted(self, cache):
        """TTS缓存文件被删除时应返回None"""
        tts_result = TTSResult(
            audio_path="/nonexistent/path.mp3",
            format="mp3",
            engine="mock",
        )
        cache.set_tts("test", tts_result, "v", "e")
        assert cache.get_tts("test", "v", "e") is None

    def test_cleanup(self, cache):
        audio = b"old audio"
        cache.set_stt(audio, STTResult(text="old"))

        # 设置过期
        import sqlite3
        with sqlite3.connect(cache._db_path) as conn:
            conn.execute("UPDATE stt_cache SET created_at = ?", (time.time() - 7200,))

        result = cache.cleanup()
        assert result["stt"] >= 1

    def test_stats(self, cache):
        cache.set_stt(b"a1", STTResult(text="t1"))
        cache.set_stt(b"a2", STTResult(text="t2"))
        stats = cache.stats()
        assert stats["stt_entries"] == 2
        assert stats["tts_entries"] == 0

    def test_hash_audio(self):
        h1 = VoiceCache._hash_audio(b"audio1")
        h2 = VoiceCache._hash_audio(b"audio2")
        assert h1 != h2
        assert len(h1) == 32

    def test_hash_text(self):
        h1 = VoiceCache._hash_text("hello", "alloy", "openai")
        h2 = VoiceCache._hash_text("hello", "nova", "openai")
        assert h1 != h2


# ──────────────── VoiceMessageService ────────────────

class TestVoiceMessageService:
    @pytest.fixture
    def service(self, tmp_path):
        config = VoiceConfig(
            stt_engine=STTEngine.MOCK,
            tts_engine=TTSEngine.MOCK,
            cache_dir=str(tmp_path / "voice_cache"),
        )
        return VoiceMessageService(config)

    def test_stt_mock(self, service):
        audio = b"test audio " * 100
        result = service.speech_to_text(audio)
        assert result.text
        assert result.engine == "mock"
        assert result.language == "zh"

    def test_stt_with_language(self, service):
        result = service.speech_to_text(b"test" * 10, language="en")
        assert result.language == "en"

    def test_stt_cache_hit(self, service):
        audio = b"cached audio data"
        r1 = service.speech_to_text(audio)
        r2 = service.speech_to_text(audio)
        assert r2.cached is True
        assert r2.text == r1.text

    def test_stt_file_too_large(self, service):
        service.config.max_file_size_mb = 0.001  # ~1KB
        with pytest.raises(ValueError, match="too large"):
            service.speech_to_text(b"x" * 2000)

    def test_tts_mock(self, service):
        result = service.text_to_speech("你好世界")
        assert result.audio_path
        assert result.engine == "mock"
        assert result.duration_seconds > 0
        assert os.path.exists(result.audio_path)

    def test_tts_empty_text(self, service):
        with pytest.raises(ValueError, match="empty"):
            service.text_to_speech("")

    def test_tts_text_too_long(self, service):
        service.config.max_tts_length = 10
        with pytest.raises(ValueError, match="too long"):
            service.text_to_speech("x" * 20)

    def test_tts_cache_hit(self, service):
        r1 = service.text_to_speech("缓存测试")
        r2 = service.text_to_speech("缓存测试")
        assert r2.cached is True

    def test_stats(self, service):
        service.speech_to_text(b"test audio")
        service.text_to_speech("test text")
        stats = service.get_stats()
        assert stats["stt_requests"] == 1
        assert stats["stt_success"] == 1
        assert stats["tts_requests"] == 1
        assert stats["tts_success"] == 1

    def test_cleanup_cache(self, service):
        result = service.cleanup_cache()
        assert isinstance(result, dict)

    def test_no_cache(self, tmp_path):
        config = VoiceConfig(cache_enabled=False)
        svc = VoiceMessageService(config)
        assert svc.cache is None
        result = svc.cleanup_cache()
        assert result == {"stt": 0, "tts": 0, "files": 0}

    def test_stt_unknown_engine(self, service):
        with pytest.raises(ValueError, match="Unknown STT"):
            service._run_stt("/tmp/test.wav", "zh", "invalid_engine")

    def test_tts_unknown_engine(self, service):
        with pytest.raises(ValueError, match="Unknown TTS"):
            service._run_tts("test", "voice", "invalid_engine", "mp3", 1.0)

    def test_stats_after_errors(self, service):
        service.config.max_file_size_mb = 0.0001
        try:
            service.speech_to_text(b"x" * 1000)
        except ValueError:
            pass
        stats = service.get_stats()
        assert stats["stt_errors"] == 1

    def test_concurrent_access(self, service):
        """并发访问测试"""
        results = []

        def do_stt(i):
            r = service.speech_to_text(f"audio{i}".encode() * 10)
            results.append(r.text)

        threads = [threading.Thread(target=do_stt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10

    def test_detect_and_process_amr(self, service):
        """AMR格式检测"""
        amr_data = b"#!AMR\n" + b"\x00" * 500
        fmt = AudioConverter.detect_format(amr_data)
        assert fmt == AudioFormat.AMR
