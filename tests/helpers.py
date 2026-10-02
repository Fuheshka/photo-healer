"""JPEG test utilities and synthetic byte pattern builder."""

import struct


class JPEGTestKit:
    SOI = b"\xff\xd8"
    EOI = b"\xff\xd9"

    @staticmethod
    def dqt(table_id: int = 0, precision: int = 0, values: bytes = None) -> bytes:
        if values is None:
            values = bytes(range(1, 65))  # 64 bytes
        header_byte = ((precision & 0x0F) << 4) | (table_id & 0x0F)
        payload = bytes([header_byte]) + values
        length = len(payload) + 2
        return b"\xff\xdb" + struct.pack(">H", length) + payload

    @staticmethod
    def sof0(width: int = 640, height: int = 480, components: int = 3) -> bytes:
        # 8-bit precision, YCbCr 4:2:0 or Grayscale
        if components == 3:
            comp_data = b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
        else:
            comp_data = b"\x01\x11\x00"
        payload = struct.pack(">BHHB", 8, height, width, components) + comp_data
        length = len(payload) + 2
        return b"\xff\xc0" + struct.pack(">H", length) + payload

    @staticmethod
    def dht(table_class: int = 0, table_id: int = 0) -> bytes:
        header_byte = ((table_class & 0x01) << 4) | (table_id & 0x0F)
        counts = b"\x01" + b"\x00" * 15  # 16 bytes
        symbols = b"\x00"
        payload = bytes([header_byte]) + counts + symbols
        length = len(payload) + 2
        return b"\xff\xc4" + struct.pack(">H", length) + payload

    @staticmethod
    def sos(components: int = 3) -> bytes:
        if components == 3:
            comp_data = b"\x01\x00\x02\x11\x03\x11"
        else:
            comp_data = b"\x01\x00"
        payload = struct.pack(">B", components) + comp_data + b"\x00\x3f\x00"
        length = len(payload) + 2
        return b"\xff\xda" + struct.pack(">H", length) + payload

    @classmethod
    def minimal_donor_header(cls, width: int = 640, height: int = 480) -> bytes:
        """Returns standard donor header: SOI + DQT + SOF0 + DHT + SOS."""
        return (
            cls.SOI
            + cls.dqt(table_id=0)
            + cls.sof0(width=width, height=height)
            + cls.dht(table_class=0, table_id=0)
            + cls.sos()
        )

    @classmethod
    def app1_with_fake_sos(cls) -> bytes:
        """
        Creates an APP1 EXIF segment containing an embedded thumbnail
        with a genuine FF DA (SOS) marker inside.
        """
        fake_thumbnail = cls.SOI + cls.dqt(0) + cls.sos() + b"\x12\x34\x56" + cls.EOI
        exif_sig = b"Exif\x00\x00"
        tiff_header = b"II\x2a\x00\x08\x00\x00\x00"  # IFD0 at offset 8
        ifd_entry_count = struct.pack("<H", 1)
        tag_thumb_offset = struct.pack("<HHII", 0x0201, 4, 1, 26)
        next_ifd_offset = struct.pack("<I", 0)

        app1_payload = exif_sig + tiff_header + ifd_entry_count + tag_thumb_offset + next_ifd_offset + fake_thumbnail
        length = len(app1_payload) + 2
        return b"\xff\xe1" + struct.pack(">H", length) + app1_payload

    @staticmethod
    def dri(restart_interval: int = 4) -> bytes:
        """DRI marker (FF DD): 4 bytes length + 16-bit restart interval in MCUs."""
        return b"\xff\xdd\x00\x04" + struct.pack(">H", restart_interval)

    @staticmethod
    def rst_marker(index: int) -> bytes:
        """Restart marker RST0..RST7 (FF D0 .. FF D7) modulo 8."""
        return b"\xff" + bytes([0xD0 + (index % 8)])

    @staticmethod
    def stuffed_entropy_payload(
        raw_bytes: bytes | None = None,
        length: int = 64,
        inject_literal_ff: bool = True,
    ) -> bytes:
        """Generates byte-stuffed entropy payload where any 0xFF is followed by 0x00."""
        if raw_bytes is None:
            raw_bytes = bytes((i * 73 + 17) % 254 + 1 for i in range(length))

        stuffed = bytearray()
        for b in raw_bytes:
            stuffed.append(b)
            if b == 0xFF:
                stuffed.append(0x00)

        if inject_literal_ff and b"\xff\x00" not in stuffed:
            stuffed.extend(b"\x12\x34\xff\x00\x56\x78")

        return bytes(stuffed)

    @classmethod
    def build_restart_interval(cls, interval_index: int, payload_len: int = 64, with_rst: bool = True) -> bytes:
        """Builds one restart interval: [Stuffed Payload] + optional [RST(index % 8)]."""
        payload = cls.stuffed_entropy_payload(length=payload_len)
        if with_rst:
            return payload + cls.rst_marker(interval_index)
        return payload

    @classmethod
    def build_resync_bitstream(
        cls,
        restart_interval: int = 4,
        num_intervals: int = 16,
        interval_payload_len: int = 64,
        include_dri: bool = True,
        width: int = 640,
        height: int = 480,
        terminal_eoi: bool = True,
    ) -> bytes:
        """Builds a complete valid synthetic JPEG with DRI and RST0..RST7 marker chain."""
        header = (
            cls.SOI
            + cls.dqt(0)
            + (cls.dri(restart_interval) if include_dri else b"")
            + cls.sof0(width=width, height=height)
            + cls.dht(0, 0)
            + cls.sos()
        )
        scan = bytearray()
        for i in range(num_intervals):
            is_last = i == num_intervals - 1
            scan.extend(cls.build_restart_interval(i, payload_len=interval_payload_len, with_rst=not is_last))

        tail = cls.EOI if terminal_eoi else b""
        return header + bytes(scan) + tail

    @staticmethod
    def inject_trim_zeros_prefix(data: bytes, zeros_len: int = 65536) -> bytes:
        """Simulates TRIM erasure by zeroing beginning of file."""
        return (b"\x00" * zeros_len) + data[zeros_len:]

    @staticmethod
    def inject_noise_prefix(data: bytes, noise_len: int = 4096, inject_false_rst: bool = True) -> bytes:
        """Injects random noise before live entropy, optionally including isolated false RST markers."""
        noise = bytearray((i * 101 + 43) % 256 for i in range(noise_len))
        if inject_false_rst:
            noise[100:102] = b"\xff\xd5"  # Isolated RST5
            noise[500:502] = b"\xff\xd2"  # Isolated RST2
        return bytes(noise) + data

    @staticmethod
    def inject_truncated_tail(data: bytes, truncate_bytes: int = 50, drop_eoi: bool = True) -> bytes:
        """Truncates tail of file mid-interval, optionally stripping EOI."""
        end = len(data) - (2 if (drop_eoi and data.endswith(b"\xff\xd9")) else 0)
        return data[: max(0, end - truncate_bytes)]

