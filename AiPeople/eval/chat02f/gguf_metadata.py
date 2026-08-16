from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO, Iterable


_SCALAR_FORMATS = {
    0: "<B",   # UINT8
    1: "<b",   # INT8
    2: "<H",   # UINT16
    3: "<h",   # INT16
    4: "<I",   # UINT32
    5: "<i",   # INT32
    6: "<f",   # FLOAT32
    7: "<?",   # BOOL
    10: "<Q",  # UINT64
    11: "<q",  # INT64
    12: "<d",  # FLOAT64
}


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("Unexpected end of GGUF metadata")
    return value


def _read_scalar(stream: BinaryIO, value_type: int) -> int | float | bool:
    try:
        fmt = _SCALAR_FORMATS[value_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported GGUF scalar type: {value_type}") from exc
    return struct.unpack(fmt, _read_exact(stream, struct.calcsize(fmt)))[0]


def _read_string(stream: BinaryIO) -> str:
    size = struct.unpack("<Q", _read_exact(stream, 8))[0]
    return _read_exact(stream, size).decode("utf-8")


def _read_value(stream: BinaryIO, value_type: int, *, retain: bool) -> object | None:
    if value_type in _SCALAR_FORMATS:
        value = _read_scalar(stream, value_type)
        return value if retain else None
    if value_type == 8:
        value = _read_string(stream)
        return value if retain else None
    if value_type == 9:
        element_type = struct.unpack("<I", _read_exact(stream, 4))[0]
        count = struct.unpack("<Q", _read_exact(stream, 8))[0]
        if retain:
            return [
                _read_value(stream, element_type, retain=True)
                for _ in range(count)
            ]
        for _ in range(count):
            _read_value(stream, element_type, retain=False)
        return None
    raise ValueError(f"Unsupported GGUF metadata type: {value_type}")


def read_gguf_metadata(path: str | Path, keys: Iterable[str]) -> dict[str, object]:
    """Read selected GGUF metadata without loading tensor data into memory."""
    requested = set(keys)
    selected: dict[str, object] = {}
    with Path(path).open("rb") as stream:
        if _read_exact(stream, 4) != b"GGUF":
            raise ValueError("Not a GGUF file")
        version = struct.unpack("<I", _read_exact(stream, 4))[0]
        if version not in (2, 3):
            raise ValueError(f"Unsupported GGUF version: {version}")
        tensor_count = struct.unpack("<Q", _read_exact(stream, 8))[0]
        metadata_count = struct.unpack("<Q", _read_exact(stream, 8))[0]
        for _ in range(metadata_count):
            key = _read_string(stream)
            value_type = struct.unpack("<I", _read_exact(stream, 4))[0]
            retain = key in requested
            value = _read_value(stream, value_type, retain=retain)
            if retain:
                selected[key] = value
    selected["__gguf_version__"] = version
    selected["__tensor_count__"] = tensor_count
    selected["__metadata_count__"] = metadata_count
    return selected
