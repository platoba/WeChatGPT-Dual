"""
语音消息处理服务 - STT识别 + TTS合成 + 语音转文字 + 文字转语音

支持:
- 微信语音消息 (AMR/SILK) → 文字
- Telegram语音消息 (OGG/OPUS) → 文字
- 文字回复 → 语音回复 (TTS)
- 多引擎: OpenAI Whisper / 本地 Whisper / Azure STT
- 音频格式转换 (ffmpeg)
- 语音消息缓存 (避免重复转写)
"""

import os
import time
import hashlib
import logging
import tempfile
import json
import sqlite3
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class STTEngine(Enum):
    """语音识别引擎"""
    OPENAI_WHISPER = "openai_whisper"
    LOCAL_WHISPER = "local_whisper"
    AZURE_STT = "azure_stt"
    MOCK = "mock"


class TTSEngine(Enum):
    """语音合成引擎"""
    OPENAI_TTS = "openai_tts"
    AZURE_TTS = "azure_tts"
    EDGE_TTS = "edge_tts"
    MOCK = "mock"


class AudioFormat(Enum):
    """音频格式"""
    AMR = "amr"
    SILK = "silk"
    OGG = "ogg"
    OPUS = "opus"
    MP3 = "mp3"
    WAV = "wav"
    M4A = "m4a"
    WEBM = "webm"


@dataclass
class STTResult:
    """语音识别结果"""
    text: str
    language: str = "zh"
    confidence: float = 1.0
    duration_seconds: float = 0.0
    engine: str = ""
    cached: bool = False
    segments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TTSResult:
    """语音合成结果"""
    audio_path: str = ""
    audio_bytes: bytes = b""
    format: str = "mp3"
    duration_seconds: float = 0.0
    engine: str = ""
    voice: str = ""
    cached: bool = False


@dataclass
class VoiceConfig:
    """语音服务配置"""
    # STT
    stt_engine: STTEngine = STTEngine.MOCK
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    whisper_model: str = "whisper-1"
    azure_stt_key: str = ""
    azure_stt_region: str = "eastasia"
    default_language: str = "zh"

    # TTS
    tts_engine: TTSEngine = TTSEngine.MOCK
    openai_tts_voice: str = "alloy"
    openai_tts_model: str = "tts-1"
    azure_tts_voice: str = "zh-CN-XiaoxiaoNeural"
    edge_tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_speed: float = 1.0
    tts_format: str = "mp3"

    # Cache
    cache_enabled: bool = True
    cache_dir: str = ""
    cache_ttl: int = 86400  # 24h
    max_cache_size_mb: int = 500

    # Limits
    max_audio_duration: int = 300  # 5 min
    max_tts_length: int = 5000  # chars
    max_file_size_mb: int = 25

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        """从环境变量加载"""
        return cls(
            stt_engine=STTEngine(os.getenv("STT_ENGINE", "mock")),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            tts_engine=TTSEngine(os.getenv("TTS_ENGINE", "mock")),
            openai_tts_voice=os.getenv("TTS_VOICE", "alloy"),
            default_language=os.getenv("VOICE_LANGUAGE", "zh"),
            cache_enabled=os.getenv("VOICE_CACHE", "true").lower() == "true",
            cache_dir=os.getenv("VOICE_CACHE_DIR", ""),
        )


class VoiceCache:
    """语音缓存 (SQLite + 文件系统)"""

    def __init__(self, cache_dir: str = "", ttl: int = 86400, max_size_mb: int = 500):
        self.cache_dir = cache_dir or tempfile.mkdtemp(prefix="voice_cache_")
        self.ttl = ttl
        self.max_size_mb = max_size_mb
        self._lock = threading.Lock()

        os.makedirs(self.cache_dir, exist_ok=True)
        self._db_path = os.path.join(self.cache_dir, "voice_cache.db")
        self._init_db()

    def _init_db(self):
        """初始化缓存数据库"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stt_cache (
                    audio_hash TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    language TEXT DEFAULT 'zh',
                    confidence REAL DEFAULT 1.0,
                    duration REAL DEFAULT 0.0,
                    engine TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tts_cache (
                    text_hash TEXT PRIMARY KEY,
                    audio_path TEXT NOT NULL,
                    voice TEXT DEFAULT '',
                    format TEXT DEFAULT 'mp3',
                    duration REAL DEFAULT 0.0,
                    engine TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    access_count INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_stt_accessed ON stt_cache(accessed_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tts_accessed ON tts_cache(accessed_at)
            """)

    @staticmethod
    def _hash_audio(audio_data: bytes) -> str:
        """计算音频数据哈希"""
        return hashlib.sha256(audio_data).hexdigest()[:32]

    @staticmethod
    def _hash_text(text: str, voice: str = "", engine: str = "") -> str:
        """计算文本+声音哈希"""
        content = f"{text}:{voice}:{engine}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get_stt(self, audio_data: bytes) -> Optional[STTResult]:
        """查找STT缓存"""
        audio_hash = self._hash_audio(audio_data)
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM stt_cache WHERE audio_hash = ?", (audio_hash,)
                ).fetchone()
                if row and time.time() - row["created_at"] < self.ttl:
                    conn.execute(
                        "UPDATE stt_cache SET accessed_at = ?, access_count = access_count + 1 WHERE audio_hash = ?",
                        (time.time(), audio_hash),
                    )
                    return STTResult(
                        text=row["text"],
                        language=row["language"],
                        confidence=row["confidence"],
                        duration_seconds=row["duration"],
                        engine=row["engine"],
                        cached=True,
                    )
        return None

    def set_stt(self, audio_data: bytes, result: STTResult):
        """保存STT缓存"""
        audio_hash = self._hash_audio(audio_data)
        now = time.time()
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO stt_cache
                    (audio_hash, text, language, confidence, duration, engine, created_at, accessed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (audio_hash, result.text, result.language, result.confidence,
                     result.duration_seconds, result.engine, now, now),
                )

    def get_tts(self, text: str, voice: str = "", engine: str = "") -> Optional[TTSResult]:
        """查找TTS缓存"""
        text_hash = self._hash_text(text, voice, engine)
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM tts_cache WHERE text_hash = ?", (text_hash,)
                ).fetchone()
                if row and time.time() - row["created_at"] < self.ttl:
                    audio_path = row["audio_path"]
                    if os.path.exists(audio_path):
                        conn.execute(
                            "UPDATE tts_cache SET accessed_at = ?, access_count = access_count + 1 WHERE text_hash = ?",
                            (time.time(), text_hash),
                        )
                        return TTSResult(
                            audio_path=audio_path,
                            format=row["format"],
                            duration_seconds=row["duration"],
                            engine=row["engine"],
                            voice=row["voice"],
                            cached=True,
                        )
        return None

    def set_tts(self, text: str, result: TTSResult, voice: str = "", engine: str = ""):
        """保存TTS缓存"""
        text_hash = self._hash_text(text, voice, engine)
        now = time.time()
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO tts_cache
                    (text_hash, audio_path, voice, format, duration, engine, created_at, accessed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (text_hash, result.audio_path, result.voice, result.format,
                     result.duration_seconds, result.engine, now, now),
                )

    def cleanup(self) -> Dict[str, int]:
        """清理过期缓存"""
        cutoff = time.time() - self.ttl
        cleaned = {"stt": 0, "tts": 0, "files": 0}
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                # 获取要删除的TTS文件路径
                rows = conn.execute(
                    "SELECT audio_path FROM tts_cache WHERE created_at < ?", (cutoff,)
                ).fetchall()
                for row in rows:
                    try:
                        if os.path.exists(row[0]):
                            os.remove(row[0])
                            cleaned["files"] += 1
                    except OSError:
                        pass

                # 删除过期记录
                cursor = conn.execute("DELETE FROM stt_cache WHERE created_at < ?", (cutoff,))
                cleaned["stt"] = cursor.rowcount
                cursor = conn.execute("DELETE FROM tts_cache WHERE created_at < ?", (cutoff,))
                cleaned["tts"] = cursor.rowcount

        return cleaned

    def stats(self) -> Dict[str, Any]:
        """缓存统计"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                stt_count = conn.execute("SELECT COUNT(*) FROM stt_cache").fetchone()[0]
                tts_count = conn.execute("SELECT COUNT(*) FROM tts_cache").fetchone()[0]
                stt_hits = conn.execute(
                    "SELECT SUM(access_count) FROM stt_cache"
                ).fetchone()[0] or 0
                tts_hits = conn.execute(
                    "SELECT SUM(access_count) FROM tts_cache"
                ).fetchone()[0] or 0

        # 计算文件缓存大小
        total_size = 0
        if os.path.exists(self.cache_dir):
            for f in os.listdir(self.cache_dir):
                fp = os.path.join(self.cache_dir, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

        return {
            "stt_entries": stt_count,
            "tts_entries": tts_count,
            "stt_total_hits": stt_hits,
            "tts_total_hits": tts_hits,
            "cache_size_mb": round(total_size / 1024 / 1024, 2),
            "cache_dir": self.cache_dir,
        }


class AudioConverter:
    """音频格式转换器 (ffmpeg)"""

    SUPPORTED_INPUT = {f.value for f in AudioFormat}
    SUPPORTED_OUTPUT = {"mp3", "wav", "ogg", "m4a"}

    @staticmethod
    def detect_format(audio_data: bytes) -> Optional[AudioFormat]:
        """根据文件头检测音频格式"""
        if len(audio_data) < 12:
            return None

        # OGG (包含 Opus)
        if audio_data[:4] == b"OggS":
            return AudioFormat.OGG

        # MP3
        if audio_data[:3] == b"ID3" or (audio_data[0] == 0xFF and audio_data[1] & 0xE0 == 0xE0):
            return AudioFormat.MP3

        # WAV / RIFF
        if audio_data[:4] == b"RIFF":
            return AudioFormat.WAV

        # AMR
        if audio_data[:6] == b"#!AMR\n":
            return AudioFormat.AMR

        # SILK (WeChat)
        if audio_data[:7] == b"#!SILK_" or audio_data[:10] == b"\x02#!SILK_V3":
            return AudioFormat.SILK

        # WebM
        if audio_data[:4] == b"\x1a\x45\xdf\xa3":
            return AudioFormat.WEBM

        # M4A / MP4
        if len(audio_data) >= 8 and audio_data[4:8] == b"ftyp":
            return AudioFormat.M4A

        return None

    @staticmethod
    def get_duration(audio_path: str) -> float:
        """获取音频时长 (秒)"""
        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries",
                 "format=duration", "-of", "json", audio_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception:
            pass
        return 0.0

    @staticmethod
    def convert(input_path: str, output_path: str, output_format: str = "mp3",
                sample_rate: int = 16000, channels: int = 1) -> bool:
        """转换音频格式"""
        try:
            import subprocess
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-ar", str(sample_rate),
                "-ac", str(channels),
            ]
            if output_format == "mp3":
                cmd.extend(["-codec:a", "libmp3lame", "-q:a", "2"])
            elif output_format == "wav":
                cmd.extend(["-codec:a", "pcm_s16le"])
            elif output_format == "ogg":
                cmd.extend(["-codec:a", "libvorbis", "-q:a", "4"])

            cmd.append(output_path)

            result = subprocess.run(cmd, capture_output=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Audio conversion failed: {e}")
            return False

    @staticmethod
    def convert_silk_to_wav(silk_path: str, wav_path: str) -> bool:
        """微信SILK格式转WAV (需要 silk-v3-decoder)"""
        try:
            import subprocess
            # 尝试使用 silk_decoder
            result = subprocess.run(
                ["silk_decoder", silk_path, wav_path],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                return True
            # fallback: ffmpeg with silk support
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
                 "-i", silk_path, wav_path],
                capture_output=True, timeout=30
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"SILK conversion failed: {e}")
            return False


class VoiceMessageService:
    """
    语音消息处理主服务

    支持:
    - 语音→文字 (STT)
    - 文字→语音 (TTS)
    - 多引擎切换
    - 缓存加速
    - 格式转换
    """

    def __init__(self, config: Optional[VoiceConfig] = None):
        self.config = config or VoiceConfig()
        self.converter = AudioConverter()
        self.cache: Optional[VoiceCache] = None

        if self.config.cache_enabled:
            self.cache = VoiceCache(
                cache_dir=self.config.cache_dir,
                ttl=self.config.cache_ttl,
                max_size_mb=self.config.max_cache_size_mb,
            )

        # 统计
        self._stats = {
            "stt_requests": 0,
            "stt_success": 0,
            "stt_errors": 0,
            "stt_cache_hits": 0,
            "tts_requests": 0,
            "tts_success": 0,
            "tts_errors": 0,
            "tts_cache_hits": 0,
            "total_audio_seconds": 0.0,
        }
        self._lock = threading.Lock()

    def speech_to_text(
        self, audio_data: bytes,
        language: Optional[str] = None,
        engine: Optional[STTEngine] = None,
    ) -> STTResult:
        """
        语音转文字

        Args:
            audio_data: 音频二进制数据
            language: 语言代码 (zh, en, ja, ...)
            engine: 指定引擎 (默认使用配置)

        Returns:
            STTResult
        """
        with self._lock:
            self._stats["stt_requests"] += 1

        language = language or self.config.default_language
        engine = engine or self.config.stt_engine

        # 检查文件大小
        size_mb = len(audio_data) / (1024 * 1024)
        if size_mb > self.config.max_file_size_mb:
            self._stats["stt_errors"] += 1
            raise ValueError(f"Audio file too large: {size_mb:.1f}MB > {self.config.max_file_size_mb}MB")

        # 查缓存
        if self.cache:
            cached = self.cache.get_stt(audio_data)
            if cached:
                with self._lock:
                    self._stats["stt_cache_hits"] += 1
                    self._stats["stt_success"] += 1
                return cached

        try:
            # 检测格式
            fmt = self.converter.detect_format(audio_data)

            # 写入临时文件
            suffix = f".{fmt.value}" if fmt else ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name

            try:
                # SILK格式需要先转WAV
                process_path = tmp_path
                wav_path = None
                if fmt in (AudioFormat.SILK, AudioFormat.AMR):
                    wav_path = tmp_path + ".wav"
                    if fmt == AudioFormat.SILK:
                        self.converter.convert_silk_to_wav(tmp_path, wav_path)
                    else:
                        self.converter.convert(tmp_path, wav_path, "wav")
                    if os.path.exists(wav_path):
                        process_path = wav_path

                # 获取时长
                duration = self.converter.get_duration(process_path)
                if duration > self.config.max_audio_duration:
                    raise ValueError(
                        f"Audio too long: {duration:.0f}s > {self.config.max_audio_duration}s"
                    )

                # 调用识别引擎
                result = self._run_stt(process_path, language, engine)
                result.duration_seconds = duration

                # 保存缓存
                if self.cache:
                    self.cache.set_stt(audio_data, result)

                with self._lock:
                    self._stats["stt_success"] += 1
                    self._stats["total_audio_seconds"] += duration

                return result

            finally:
                # 清理临时文件
                for p in [tmp_path, wav_path]:
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass

        except Exception as e:
            with self._lock:
                self._stats["stt_errors"] += 1
            logger.error(f"STT failed: {e}")
            raise

    def _run_stt(self, audio_path: str, language: str, engine: STTEngine) -> STTResult:
        """执行语音识别"""
        if engine == STTEngine.OPENAI_WHISPER:
            return self._stt_openai(audio_path, language)
        elif engine == STTEngine.LOCAL_WHISPER:
            return self._stt_local_whisper(audio_path, language)
        elif engine == STTEngine.AZURE_STT:
            return self._stt_azure(audio_path, language)
        elif engine == STTEngine.MOCK:
            return self._stt_mock(audio_path, language)
        else:
            raise ValueError(f"Unknown STT engine: {engine}")

    def _stt_openai(self, audio_path: str, language: str) -> STTResult:
        """OpenAI Whisper API"""
        try:
            import httpx

            with open(audio_path, "rb") as f:
                response = httpx.post(
                    f"{self.config.openai_base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.config.openai_api_key}"},
                    files={"file": (os.path.basename(audio_path), f)},
                    data={
                        "model": self.config.whisper_model,
                        "language": language,
                        "response_format": "verbose_json",
                    },
                    timeout=60,
                )
                response.raise_for_status()

            data = response.json()
            segments = []
            for seg in data.get("segments", []):
                segments.append({
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                    "text": seg.get("text", ""),
                })

            return STTResult(
                text=data.get("text", ""),
                language=data.get("language", language),
                confidence=1.0,
                engine="openai_whisper",
                segments=segments,
            )
        except ImportError:
            raise RuntimeError("httpx required for OpenAI STT: pip install httpx")

    def _stt_local_whisper(self, audio_path: str, language: str) -> STTResult:
        """本地 Whisper 模型"""
        try:
            import subprocess
            result = subprocess.run(
                ["whisper", audio_path, "--language", language,
                 "--output_format", "json", "--output_dir", tempfile.gettempdir()],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Local whisper failed: {result.stderr}")

            json_path = os.path.splitext(audio_path)[0] + ".json"
            alt_json = os.path.join(
                tempfile.gettempdir(),
                os.path.splitext(os.path.basename(audio_path))[0] + ".json"
            )

            for jp in [json_path, alt_json]:
                if os.path.exists(jp):
                    with open(jp) as f:
                        data = json.load(f)
                    os.remove(jp)
                    return STTResult(
                        text=data.get("text", ""),
                        language=language,
                        engine="local_whisper",
                    )

            return STTResult(text=result.stdout.strip(), language=language, engine="local_whisper")
        except FileNotFoundError:
            raise RuntimeError("whisper CLI not found: pip install openai-whisper")

    def _stt_azure(self, audio_path: str, language: str) -> STTResult:
        """Azure Speech-to-Text"""
        try:
            import httpx

            lang_map = {"zh": "zh-CN", "en": "en-US", "ja": "ja-JP", "ko": "ko-KR"}
            azure_lang = lang_map.get(language, language)

            with open(audio_path, "rb") as f:
                audio_data = f.read()

            response = httpx.post(
                f"https://{self.config.azure_stt_region}.stt.speech.microsoft.com"
                f"/speech/recognition/conversation/cognitiveservices/v1"
                f"?language={azure_lang}",
                headers={
                    "Ocp-Apim-Subscription-Key": self.config.azure_stt_key,
                    "Content-Type": "audio/wav",
                },
                content=audio_data,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()

            return STTResult(
                text=data.get("DisplayText", ""),
                language=language,
                confidence=data.get("NBest", [{}])[0].get("Confidence", 0.0)
                if data.get("NBest") else 1.0,
                engine="azure_stt",
            )
        except ImportError:
            raise RuntimeError("httpx required for Azure STT")

    def _stt_mock(self, audio_path: str, language: str) -> STTResult:
        """Mock STT (测试用)"""
        file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        return STTResult(
            text=f"[mock transcription of {file_size} bytes audio]",
            language=language,
            confidence=0.95,
            duration_seconds=file_size / 16000,  # rough estimate
            engine="mock",
        )

    def text_to_speech(
        self, text: str,
        voice: Optional[str] = None,
        engine: Optional[TTSEngine] = None,
        output_format: Optional[str] = None,
        speed: Optional[float] = None,
    ) -> TTSResult:
        """
        文字转语音

        Args:
            text: 要合成的文本
            voice: 声音名称
            engine: TTS引擎
            output_format: 输出格式
            speed: 语速倍率

        Returns:
            TTSResult
        """
        with self._lock:
            self._stats["tts_requests"] += 1

        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        if len(text) > self.config.max_tts_length:
            raise ValueError(
                f"Text too long: {len(text)} > {self.config.max_tts_length} chars"
            )

        engine = engine or self.config.tts_engine
        voice = voice or self._default_voice(engine)
        output_format = output_format or self.config.tts_format
        speed = speed or self.config.tts_speed

        # 查缓存
        if self.cache:
            cached = self.cache.get_tts(text, voice, engine.value)
            if cached:
                with self._lock:
                    self._stats["tts_cache_hits"] += 1
                    self._stats["tts_success"] += 1
                return cached

        try:
            result = self._run_tts(text, voice, engine, output_format, speed)

            # 保存缓存
            if self.cache and result.audio_path:
                self.cache.set_tts(text, result, voice, engine.value)

            with self._lock:
                self._stats["tts_success"] += 1

            return result

        except Exception as e:
            with self._lock:
                self._stats["tts_errors"] += 1
            logger.error(f"TTS failed: {e}")
            raise

    def _default_voice(self, engine: TTSEngine) -> str:
        """获取引擎默认声音"""
        defaults = {
            TTSEngine.OPENAI_TTS: self.config.openai_tts_voice,
            TTSEngine.AZURE_TTS: self.config.azure_tts_voice,
            TTSEngine.EDGE_TTS: self.config.edge_tts_voice,
            TTSEngine.MOCK: "mock_voice",
        }
        return defaults.get(engine, "default")

    def _run_tts(self, text: str, voice: str, engine: TTSEngine,
                 output_format: str, speed: float) -> TTSResult:
        """执行语音合成"""
        if engine == TTSEngine.OPENAI_TTS:
            return self._tts_openai(text, voice, output_format, speed)
        elif engine == TTSEngine.EDGE_TTS:
            return self._tts_edge(text, voice, output_format, speed)
        elif engine == TTSEngine.AZURE_TTS:
            return self._tts_azure(text, voice, output_format, speed)
        elif engine == TTSEngine.MOCK:
            return self._tts_mock(text, voice, output_format)
        else:
            raise ValueError(f"Unknown TTS engine: {engine}")

    def _tts_openai(self, text: str, voice: str, fmt: str, speed: float) -> TTSResult:
        """OpenAI TTS API"""
        try:
            import httpx

            response = httpx.post(
                f"{self.config.openai_base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {self.config.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.openai_tts_model,
                    "input": text,
                    "voice": voice,
                    "speed": speed,
                    "response_format": fmt,
                },
                timeout=60,
            )
            response.raise_for_status()

            # 保存到临时文件
            suffix = f".{fmt}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=self._cache_dir()) as f:
                f.write(response.content)
                audio_path = f.name

            duration = self.converter.get_duration(audio_path)

            return TTSResult(
                audio_path=audio_path,
                audio_bytes=response.content,
                format=fmt,
                duration_seconds=duration,
                engine="openai_tts",
                voice=voice,
            )
        except ImportError:
            raise RuntimeError("httpx required for OpenAI TTS")

    def _tts_edge(self, text: str, voice: str, fmt: str, speed: float) -> TTSResult:
        """Edge TTS (免费, 微软)"""
        try:
            import subprocess

            suffix = f".{fmt}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=self._cache_dir()) as f:
                output_path = f.name

            rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"

            result = subprocess.run(
                ["edge-tts", "--voice", voice, "--rate", rate,
                 "--text", text, "--write-media", output_path],
                capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(f"edge-tts failed: {result.stderr.decode()}")

            duration = self.converter.get_duration(output_path)

            with open(output_path, "rb") as f:
                audio_bytes = f.read()

            return TTSResult(
                audio_path=output_path,
                audio_bytes=audio_bytes,
                format=fmt,
                duration_seconds=duration,
                engine="edge_tts",
                voice=voice,
            )
        except FileNotFoundError:
            raise RuntimeError("edge-tts not found: pip install edge-tts")

    def _tts_azure(self, text: str, voice: str, fmt: str, speed: float) -> TTSResult:
        """Azure TTS"""
        try:
            import httpx

            rate_percent = int((speed - 1) * 100)
            rate_str = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"

            ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>
                <voice name='{voice}'>
                    <prosody rate='{rate_str}'>{text}</prosody>
                </voice>
            </speak>"""

            response = httpx.post(
                f"https://{self.config.azure_stt_region}.tts.speech.microsoft.com"
                f"/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": self.config.azure_stt_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
                },
                content=ssml,
                timeout=60,
            )
            response.raise_for_status()

            suffix = f".{fmt}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=self._cache_dir()) as f:
                f.write(response.content)
                audio_path = f.name

            return TTSResult(
                audio_path=audio_path,
                audio_bytes=response.content,
                format=fmt,
                duration_seconds=self.converter.get_duration(audio_path),
                engine="azure_tts",
                voice=voice,
            )
        except ImportError:
            raise RuntimeError("httpx required for Azure TTS")

    def _tts_mock(self, text: str, voice: str, fmt: str) -> TTSResult:
        """Mock TTS (测试用)"""
        # 创建一个空的音频文件
        suffix = f".{fmt}"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=self._cache_dir()) as f:
            content = f"[mock audio: {len(text)} chars, voice={voice}]".encode()
            f.write(content)
            audio_path = f.name

        return TTSResult(
            audio_path=audio_path,
            audio_bytes=content,
            format=fmt,
            duration_seconds=len(text) * 0.08,  # ~80ms per char
            engine="mock",
            voice=voice,
        )

    def _cache_dir(self) -> Optional[str]:
        """获取缓存目录"""
        if self.cache:
            return self.cache.cache_dir
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取服务统计"""
        with self._lock:
            stats = dict(self._stats)

        if self.cache:
            stats["cache"] = self.cache.stats()

        stats["config"] = {
            "stt_engine": self.config.stt_engine.value,
            "tts_engine": self.config.tts_engine.value,
            "cache_enabled": self.config.cache_enabled,
        }

        return stats

    def cleanup_cache(self) -> Dict[str, int]:
        """清理过期缓存"""
        if self.cache:
            return self.cache.cleanup()
        return {"stt": 0, "tts": 0, "files": 0}
