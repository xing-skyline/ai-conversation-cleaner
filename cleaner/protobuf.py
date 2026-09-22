"""Lossless protobuf-wire parser for Antigravity's local summary map only.

Conversation payloads are opaque and are never rewritten or decrypted.
Unknown wire fields are preserved byte-for-byte. Map entries are removed only
when their field-1 UTF-8 key is an exact selected conversation UUID.
"""
import base64

from .store import CleanupError, UUID


def fields(data):
    i = 0
    def varint():
        nonlocal i
        value = shift = 0
        while i < len(data) and shift < 70:
            x = data[i]; i += 1
            value |= (x & 127) << shift
            if x < 128:
                return value
            shift += 7
        raise CleanupError("Antigravity 索引的 protobuf 格式无法识别。")
    result = []
    while i < len(data):
        start = i
        key = varint(); number, wire = key >> 3, key & 7
        if number == 0:
            raise CleanupError("protobuf 字段号错误。")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if i + length > len(data):
                raise CleanupError("protobuf 字段超出边界。")
            value = data[i:i+length]; i += length
        else:
            raise CleanupError("暂不支持此 Antigravity protobuf 索引格式。")
        result.append((number, wire, value, data[start:i]))
    return result


def summary_map(encoded, delete_ids=frozenset()):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise CleanupError("Antigravity 摘要索引不是受支持的 Base64 数据。") from error
    rows, kept = {}, []
    for n, w, v, original in fields(raw):
        target = None
        entry = fields(v) if n == 1 and w == 2 else []
        for en, ew, ev, _ in entry:
            if en == 1 and ew == 2:
                try:
                    candidate = ev.decode("utf-8")
                    if UUID.fullmatch(candidate):
                        target = candidate
                except UnicodeError:
                    pass
        if target:
            title = ""
            # Current local format: map value -> field 1 base64 -> field 1 title.
            try:
                value = next(ev for en,ew,ev,_ in entry if en == 2 and ew == 2)
                nested = next(ev for en,ew,ev,_ in fields(value) if en == 1 and ew == 2)
                title = next(ev.decode("utf-8") for en,ew,ev,_ in fields(base64.b64decode(nested, validate=True)) if en == 1 and ew == 2)
            except (ValueError, StopIteration, UnicodeError, CleanupError):
                pass
            rows[target] = title
        if target not in delete_ids:
            kept.append(original)
    return rows, base64.b64encode(b"".join(kept)).decode("ascii")
