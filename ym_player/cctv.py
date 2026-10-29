"""국가교통정보센터(ITS) 도로 CCTV - 오픈 API로 고속도로·국도 CCTV 목록과 영상 주소를 받는다.

API: https://openapi.its.go.kr:9443/cctvInfo?apiKey=…&type=ex|its&cctvType=1&minX=&maxX=&minY=&maxY=&getType=json
    type      ex = 고속도로, its = 국도
    cctvType  1 = 실시간 스트리밍(HLS), 4 = 실시간 스트리밍(HLS, https 주소)
    min/maxX  경도 범위, min/maxY 위도 범위
응답: {"response": {"datacount": N, "data": [{"cctvname": "[경부선] 서울", "cctvurl": "http://…m3u8", "coordx": 127.0,
       "coordy": 37.4, "cctvformat": "HLS", "cctvtype": 1, ...}]}}  (한 개면 data가 목록이 아니라 dict로 오기도 한다)
인증키는 https://www.its.go.kr 회원가입 → 오픈데이터 → CCTV 정보 → 인증키 신청 (무료). 키는 브라우저로 내보내지 않는다.
영상 주소(cctvurl)에는 시간이 지나면 만료되는 값이 붙는 경우가 있어서 목록을 몇 분마다 새로 받고,
재생은 /api/cctv/{sid}/live/{id}가 그때그때 새 주소로 넘겨 준다 (서버 중계 /api/stream - https 사이트에서 http 영상이 막히지 않게).
"""
import hashlib
import re

API = "https://openapi.its.go.kr:9443/cctvInfo"
ROADS = {"ex": "고속도로", "its": "국도"}
STREAM_TYPES = ("1", "4")

# 지역 (경도 minX, maxX, 위도 minY, maxY) - 대략의 사각형이라 경계 근처는 이웃 지역 것도 섞인다
REGIONS = {
    "all": ("전국", (124.5, 132.0, 33.0, 38.7)),
    "seoul": ("서울", (126.76, 127.19, 37.41, 37.72)),
    "gyeonggi": ("경기·인천", (126.3, 127.9, 36.9, 38.3)),
    "gangwon": ("강원", (127.0, 129.4, 37.0, 38.6)),
    "chungcheong": ("충청·대전·세종", (125.9, 128.7, 35.9, 37.3)),
    "jeolla": ("전라·광주", (125.0, 127.9, 34.2, 36.2)),
    "gyeongsang": ("경상·부산·대구·울산", (127.6, 129.6, 34.6, 37.1)),
    "jeju": ("제주", (126.1, 127.0, 33.1, 33.6)),
}


def clean_settings(raw, old=None):
    """설정 칸 검사. 인증키가 비어 오면 기존 키를 그대로 (브라우저는 키를 모른다)"""
    raw = raw if isinstance(raw, dict) else {}
    old = old if isinstance(old, dict) else {}
    out = {}
    key = str(raw.get("api_key") or "").strip()[:200]
    if key or old.get("api_key"):
        out["api_key"] = key or old["api_key"]
    roads = [r for r in (raw.get("roads") or []) if r in ROADS] if isinstance(raw.get("roads"), list) else []
    out["roads"] = roads or ["ex", "its"]
    region = str(raw.get("region") or "all")
    out["region"] = region if region in REGIONS or region == "custom" else "all"
    if out["region"] == "custom":
        box = parse_box(raw.get("box"))
        if not box:
            raise ValueError("CCTV 직접 지정 범위는 '최소경도, 최대경도, 최소위도, 최대위도' 숫자 4개로 넣어 주세요 (예: 126.9, 127.1, 37.4, 37.6).")
        out["box"] = ", ".join(f"{v:g}" for v in box)
    stype = str(raw.get("cctv_type") or "1")
    out["cctv_type"] = stype if stype in STREAM_TYPES else "1"
    return out


def parse_box(value):
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(value or ""))
    if len(nums) != 4:
        return None
    x1, x2, y1, y2 = map(float, nums)
    if not (x1 < x2 and y1 < y2 and 120 <= x1 <= 135 and 120 <= x2 <= 135 and 30 <= y1 <= 45 and 30 <= y2 <= 45):
        return None
    return x1, x2, y1, y2


def box_of(src):
    if src.get("region") == "custom":
        return parse_box(src.get("box")) or REGIONS["all"][1]
    return REGIONS.get(src.get("region") or "all", REGIONS["all"])[1]


def request_params(src, road):
    x1, x2, y1, y2 = box_of(src)
    return {"apiKey": src.get("api_key", ""), "type": road, "cctvType": src.get("cctv_type") or "1",
            "minX": f"{x1:g}", "maxX": f"{x2:g}", "minY": f"{y1:g}", "maxY": f"{y2:g}", "getType": "json"}


def _rows(data):
    """응답에서 CCTV 항목(dict) 목록을 찾는다 - response.data가 목록이거나 dict 하나거나, 감싸는 모양이 달라도"""
    if isinstance(data, list):
        if any(isinstance(x, dict) and _get(x, "cctvurl") for x in data):
            return [x for x in data if isinstance(x, dict)]
        for x in data:
            got = _rows(x)
            if got:
                return got
        return []
    if isinstance(data, dict):
        if _get(data, "cctvurl"):
            return [data]
        for v in data.values():
            got = _rows(v)
            if got:
                return got
    return []


def _get(d, key):
    for k, v in d.items():
        if str(k).lower() == key:
            return v
    return None


def api_error(data):
    """키가 틀렸거나 한도를 넘었을 때의 응답에서 사람이 읽을 말을 찾는다 (없으면 "")"""
    if not isinstance(data, dict):
        return ""
    for key in ("resultMsg", "message", "msg", "errMsg", "error"):
        for d in (data, data.get("response") if isinstance(data.get("response"), dict) else {}, data.get("header") if isinstance(data.get("header"), dict) else {}):
            v = _get(d, key.lower()) if d else None
            if v and isinstance(v, str) and not re.search(r"^(ok|success|정상)", v, re.I):
                return v[:200]
    return ""


def road_group(name, road):
    """"[경부선] 서울TG" -> "경부선" (대괄호가 없으면 고속도로/국도)"""
    m = re.match(r"\s*[\[\(]([^\]\)]{1,30})[\]\)]", name or "")
    return m.group(1).strip() if m else ROADS.get(road, "")


def cam_id(name, x, y):
    return hashlib.sha1(f"{name}|{x:.4f}|{y:.4f}".encode("utf-8")).hexdigest()[:10]


def parse_cams(data, road):
    """API 응답 JSON -> [{"id", "name", "group", "road", "x", "y", "url", "format"}] (주소가 http(s)인 것만, 같은 id는 하나)"""
    out, seen = [], set()
    for r in _rows(data):
        url = str(_get(r, "cctvurl") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        name = re.sub(r"\s+", " ", str(_get(r, "cctvname") or "").strip())[:120] or "CCTV"
        try:
            x, y = float(_get(r, "coordx") or 0), float(_get(r, "coordy") or 0)
        except (TypeError, ValueError):
            x = y = 0.0
        cid = cam_id(name, x, y)
        if cid in seen:
            continue
        seen.add(cid)
        fmt = str(_get(r, "cctvformat") or "").strip()
        out.append({"id": cid, "name": name, "group": road_group(name, road), "road": road, "x": x, "y": y,
                    "url": url[:2000], "format": fmt[:20]})
    return out


def public_cam(c):
    """영상 주소는 내보내지 않는다 (재생은 /live/{id}로)"""
    return {k: c[k] for k in ("id", "name", "group", "road", "x", "y", "format")}
