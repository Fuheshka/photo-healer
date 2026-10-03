"""Photo Healer Core - Modular JPEG parsing, entropy detection, splicing and validation."""

from photo_healer.core.parser import JpegParser, Marker
from photo_healer.core.entropy import EntropyAnalyzer
from photo_healer.core.splicer import HeaderSplicer, SpliceResult
from photo_healer.core.validator import JpegValidator, ValidationResult
from photo_healer.core.carver import ThumbnailCarver, CarvedPreview, RescueResult
from photo_healer.core.resync import StreamResync, RestartMarker, RestartCadence, ResyncResult

from photo_healer.core.donor_pool import DonorIndex, DonorEntry, DonorMatch, find_best_donor
from photo_healer.core.donor_discovery import DonorDiscovery, DonorFolderCandidate

__all__ = [
    "JpegParser",
    "Marker",
    "EntropyAnalyzer",
    "HeaderSplicer",
    "SpliceResult",
    "JpegValidator",
    "ValidationResult",
    "ThumbnailCarver",
    "CarvedPreview",
    "RescueResult",
    "StreamResync",
    "RestartMarker",
    "RestartCadence",
    "ResyncResult",
    "DonorIndex",
    "DonorEntry",
    "DonorMatch",
    "find_best_donor",
    "DonorDiscovery",
    "DonorFolderCandidate",
]


