"""자막 파일(SRT / SMI / ASS·SSA / VTT)을 브라우저가 띄울 수 있는 WebVTT로 바꾼다.

Plex의 외부 자막 파일(/library/streams/{id})을 서버가 받아서 이걸로 바꾼 뒤 <track>으로 넘긴다.
한국 자막은 .smi + cp949(EUC-KR)가 흔해서 인코딩을 여러 개 시도한다. 순수 함수라 테스트에서 바로 부른다.
"""
import html
import re

TEXT_CODECS = {"srt", "subrip", "smi", "sami", "ass", "ssa", "vtt", "webvtt"}

_TAG_KEEP = re.compile(r"</?(?:b|i|u)>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_SMI_SYNC = re.compile(r"<sync\b[^>]*?start\s*=\s*[\"']?(\d+)[^>]*>", re.I)
_SMI_P = re.compile(r"<p\b[^>]*?class\s*=\s*[\"']?([\w-]+)[^>]*>", re.I)


def decode(data: bytes) -> str:
    """BOM(UTF-8/UTF-16)을 먼저 보고, 없으면 UTF-8 -> CP949 순으로."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _ts(ms):
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _clean(markup):
    """자막 마크업 -> VTT 본문. b/i/u만 남기고 나머지 태그는 지운다 (VTT가 <font> 등을 모름).

    순서가 중요하다: 태그를 먼저 걷어내고 나서 &lt; 같은 엔티티를 풀어야, 원래 글자였던 "<끝>"이
    태그로 오인돼 지워지지 않는다. 푼 글자는 다시 VTT식으로 이스케이프한다.
    """
    markup = _ASS_OVERRIDE.sub("", markup)
    kept = []

    def stash(m):
        kept.append(m.group(0).lower())
        return f"\x00{len(kept) - 1}\x00"

    text = _TAG_KEEP.sub(stash, markup)
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)
    return "\n".join(ln.strip() for ln in text.split("\n") if ln.strip())


def _vtt(cues):
    out = ["WEBVTT", ""]
    for start, end, text in sorted(cues, key=lambda c: c[0]):
        if text and end > start:
            out += [f"{_ts(start)} --> {_ts(end)}", text, ""]
    return "\n".join(out)


def _ms(h, m, s, frac):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def srt_to_vtt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            if "-->" in ln:
                times = _SRT_TIME.findall(ln)
                if len(times) == 2:
                    body = _clean("\n".join(lines[i + 1:]))
                    cues.append((_ms(*times[0]), _ms(*times[1]), body))
                break
    return _vtt(cues)


def smi_to_vtt(text, prefer_class=None):
    """SAMI: <SYNC Start=ms><P Class=KRCC>대사<br>둘째 줄. 다음 SYNC가 앞 대사의 끝.

    언어(Class)가 여러 개면 prefer_class -> KRCC -> 처음 나온 것 순으로 하나만 쓴다.
    &nbsp; 만 있는 SYNC는 "화면 지우기"라 대사 없이 끝 시각으로만 쓰인다.
    """
    body = text.replace("\r\n", "\n")
    matches = list(_SMI_SYNC.finditer(body))
    blocks = []
    for i, m in enumerate(matches):
        start = int(m.group(1))
        chunk = body[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(body)]
        cls_m = _SMI_P.search(chunk)
        cls = cls_m.group(1).upper() if cls_m else ""
        # SAMI에서 줄바꿈은 <br>뿐이고 원본 파일의 개행은 그냥 띄어쓰기
        chunk = re.sub(r"<br\s*/?>", "\n", chunk.replace("\n", " "), flags=re.I)
        chunk = re.split(r"</body>", chunk, flags=re.I)[0]
        # 대사가 있는지(빈 &nbsp; 지우기 SYNC인지)는 정리한 결과로 판단하고, 마크업은 _clean에 그대로 넘긴다
        blocks.append((start, cls, _clean(chunk)))

    classes = [c for _, c, _ in blocks if c]
    pick = None
    for want in ([prefer_class.upper()] if prefer_class else []) + ["KRCC"]:
        if want in classes:
            pick = want
            break
    if pick is None and classes:
        pick = classes[0]
    mine = [(s, w) for s, c, w in blocks if not c or not pick or c == pick]

    cues = []
    for i, (start, words) in enumerate(mine):
        if not words:
            continue
        end = mine[i + 1][0] if i + 1 < len(mine) else start + 5000
        cues.append((start, end, words))
    return _vtt(cues)


def _ass_time(t):
    m = re.match(r"(\d+):(\d{2}):(\d{2})[.:](\d{1,3})", t.strip())
    if not m:
        return None
    h, mi, s, cs = m.groups()
    frac = int(cs) * (10 if len(cs) == 2 else (100 if len(cs) == 1 else 1))
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + frac


def ass_to_vtt(text):
    fmt = None
    cues = []
    in_events = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in line[7:].split(",")]
        elif line.lower().startswith("dialogue:") and fmt:
            parts = line[9:].split(",", len(fmt) - 1)
            if len(parts) < len(fmt):
                continue
            row = dict(zip(fmt, parts))
            start, end = _ass_time(row.get("start", "")), _ass_time(row.get("end", ""))
            if start is None or end is None:
                continue
            body = row.get("text", "").replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            cues.append((start, end, _clean(body)))
    return _vtt(cues)


def to_vtt(data: bytes, codec: str = "") -> str:
    """codec(Plex가 알려준 형식)을 우선하되, 내용을 보고도 판단한다."""
    text = decode(data)
    head = text.lstrip()[:400].lower()
    codec = (codec or "").lower()
    if head.startswith("webvtt") or codec in ("vtt", "webvtt"):
        return text if head.startswith("webvtt") else "WEBVTT\n\n" + text
    if "<sami" in head or "<sync" in head or codec in ("smi", "sami"):
        return smi_to_vtt(text)
    if "[script info]" in head or "[events]" in text.lower() or codec in ("ass", "ssa"):
        return ass_to_vtt(text)
    return srt_to_vtt(text)
