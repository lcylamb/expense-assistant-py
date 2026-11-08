from __future__ import annotations

"""从可能还不完整的 JSON 文本里，抠出 conclusion 已解出的部分。

和 TS 版 `stream-parse.ts` 同一个思路：每次拿到新文本都从头重扫，
不做状态机。文本只有一句话长，重扫的 O(n²) 可以接受；状态机反而更容易
在“断在转义序列中间”时写错。
"""


KEY = '"conclusion"'


def partial_conclusion(text: str) -> str:
    key_at = text.find(KEY)
    if key_at == -1:
        return ""

    cursor = _skip_ws(text, key_at + len(KEY))
    if cursor >= len(text) or text[cursor] != ":":
        return ""
    cursor = _skip_ws(text, cursor + 1)
    if cursor >= len(text) or text[cursor] != '"':
        return ""
    return _decode_string(text, cursor + 1)


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _decode_string(text: str, start: int) -> str:
    out = []
    cursor = start
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            return "".join(out)
        if char != "\\":
            out.append(char)
            cursor += 1
            continue

        if cursor + 1 >= len(text):
            return "".join(out)
        nxt = text[cursor + 1]
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if nxt in mapping:
            out.append(mapping[nxt])
            cursor += 2
            continue

        if nxt == "u":
            if cursor + 6 > len(text):
                return "".join(out)
            hex_part = text[cursor + 2 : cursor + 6]
            try:
                code = int(hex_part, 16)
            except ValueError:
                # 非法转义：照字面收下，最终交给 parse_answer 判断。
                out.append(nxt)
                cursor += 2
                continue
            out.append(chr(code))
            cursor += 6
            continue

        # 非法转义：照字面收下，不在这里崩。
        out.append(nxt)
        cursor += 2

    return "".join(out)
