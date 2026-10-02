"""Tests for photo_healer.core.entropy EntropyAnalyzer."""

import io
import pytest
from photo_healer.core.entropy import EntropyAnalyzer


class TestEntropyAnalyzer:
    def test_shannon_entropy_pure_zeros(self):
        zeros = b"\x00" * 65536
        assert EntropyAnalyzer.shannon_entropy(zeros) == 0.0

    def test_shannon_entropy_empty(self):
        assert EntropyAnalyzer.shannon_entropy(b"") == 0.0

    def test_shannon_entropy_high_entropy_stream(self):
        # Pseudo-random byte distribution resembling compressed bitstream
        stream = bytes((i * 137 + 59) % 256 for i in range(65536))
        entropy = EntropyAnalyzer.shannon_entropy(stream)
        assert entropy > 7.9

    @pytest.mark.parametrize("zero_bytes", [
        512,         # 1 standard 512B sector
        4096,        # 1 4KB Advanced Format sector
        65536,       # 128 sectors (the SSD TRIM pattern seen in practice)
        1024,        # 2 sectors
    ])
    def test_detect_entropy_start_exact_sector_boundaries(self, zero_bytes):
        live_payload = bytes((i * 73 + 17) % 256 for i in range(16384))
        data = (b"\x00" * zero_bytes) + live_payload

        offset = EntropyAnalyzer.detect_entropy_start(data, block_size=512)
        assert offset == zero_bytes

    def test_detect_entropy_start_arbitrary_offset(self):
        zero_bytes = 1023
        live_payload = bytes((i * 73 + 17) % 256 for i in range(16384))
        data = (b"\x00" * zero_bytes) + live_payload

        # When searching with fine block size
        offset = EntropyAnalyzer.detect_entropy_start(data, block_size=1)
        assert offset == zero_bytes

    def test_detect_entropy_start_from_stream(self):
        zero_bytes = 4096
        live_payload = bytes((i * 73 + 17) % 256 for i in range(16384))
        data_stream = io.BytesIO((b"\x00" * zero_bytes) + live_payload)

        offset = EntropyAnalyzer.detect_entropy_start(data_stream, block_size=512)
        assert offset == zero_bytes

    def test_detect_entropy_start_ignores_stray_flash_noise(self):
        # Single isolated bit-flip in zero prefix should not trigger entropy detection
        prefix = bytearray(65536)
        prefix[128] = 0x01
        prefix[512] = 0x42
        live_payload = bytes((i * 137 + 11) % 256 for i in range(16384))
        data = bytes(prefix) + live_payload

        offset = EntropyAnalyzer.detect_entropy_start(data, block_size=512, entropy_threshold=5.0)
        assert offset == 65536

    def test_detect_entropy_start_pure_zeros_returns_none(self):
        pure_zeros = b"\x00" * 131072
        offset = EntropyAnalyzer.detect_entropy_start(pure_zeros, block_size=512)
        assert offset is None

    def test_detect_entropy_start_from_file_path(self, tmp_path):
        zero_bytes = 8192
        live_payload = bytes((i * 73 + 17) % 256 for i in range(16384))
        p = tmp_path / "damaged.bin"
        p.write_bytes((b"\x00" * zero_bytes) + live_payload)

        offset = EntropyAnalyzer.detect_entropy_start(p, block_size=4096)
        assert offset == zero_bytes

    def test_invalid_source_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Unsupported source type"):
            EntropyAnalyzer.detect_entropy_start(12345)

