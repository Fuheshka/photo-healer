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
