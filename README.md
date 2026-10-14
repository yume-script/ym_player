# 🎬 Yume Player — Plex 스타일 독립형 동영상 플레이어

Plex 서버와 IPTV(M3U + XMLTV 편성표)를 **한 화면에서** 보는 독립 실행형 웹 플레이어입니다.
북오아시스 `ym_m3u_player` 플러그인의 재생 엔진(hls.js / mpegts.js / 네이티브)과 M3U·EPG 처리를
그대로 옮겨 오고, 화면은 Plex 웹처럼 다시 만들었습니다. 북오아시스 없이 파이썬 하나로 돕니다.

## 실행

```bash
git clone https://github.com/yume-script/ym_player
cd ym_player
python3 -m venv .venv                 # Ubuntu/Debian은 시스템 pip 설치를 막아서 venv를 쓴다
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ym_player         # http://127.0.0.1:8899
```

`python3 -m venv`가 안 되면 먼저 `sudo apt install python3-venv`.
테스트: `.venv/bin/pip install pytest && .venv/bin/python -m pytest -q`

### 서버가 켜질 때 자동 실행 (systemd)

`deploy/ym_player.service`는 저장소가 `/mnt/ym_player`에 있다고 가정합니다. 다른 곳이면 파일 안의
`WorkingDirectory`/`ExecStart` 두 줄만 고치세요.

```bash
sudo cp deploy/ym_player.service /etc/systemd/system/
sudo cp deploy/ym_player.env.example /etc/ym_player.env
sudo chmod 600 /etc/ym_player.env
sudo nano /etc/ym_player.env          # MP_USER / MP_PASSWORD 바꾸기
sudo systemctl daemon-reload
sudo systemctl enable --now ym_player
```

상태와 로그: `systemctl status ym_player`, `journalctl -u ym_player -f`.
코드를 업데이트한 뒤에는 `git pull && sudo systemctl restart ym_player`.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `MP_HOST` / `MP_PORT` | `127.0.0.1` / `8899` | 집 안 TV·폰에서 보려면 `MP_HOST=0.0.0.0` |
| `MP_USER` / `MP_PASSWORD` | (없음) | 둘 다 넣으면 HTTP 기본 인증. **127.0.0.1 밖으로 열 땐 꼭 넣으세요** |
| `MP_DATA_DIR` | `ym_player/data` | 설정 파일(`config.json`) 위치. git에 안 올라감 |

처음 열면 소스가 없으니 오른쪽 위 ⚙️ **설정**에서 추가합니다.

- **Plex 계정으로 추가**: plex.tv 로그인 창이 뜨고, 허용하면 계정에 연결된 서버와 접속 주소(로컬/원격/릴레이)
  목록이 나옵니다. 하나 고르고 **[저장]**.
- **Plex 직접 입력**: 서버 주소(`http://192.168.0.10:32400`)와 `X-Plex-Token`.
- **가져올 라이브러리**: 저장된 Plex 소스에는 라이브러리 체크 목록이 나옵니다. 체크를 끈 라이브러리는 사이드바·홈·검색에서
  빠집니다. 끈 것만 기억하므로 나중에 Plex에 새로 만든 라이브러리는 기본으로 보입니다.
- **IPTV 추가**: M3U 주소 + (선택) XMLTV 편성표 주소. `.xml.gz`도 됩니다.

## 화면

| 화면 | 내용 |
|---|---|
| 사이드바 | 소스(Plex 서버·IPTV)별 메뉴. 소스가 둘 이상이면 하나를 펼치면 나머지는 접힘(펼친 상태 기억, 보고 있는 화면의 소스는 자동으로 펼침) |
| 홈 | 소스마다 허브를 가로 줄로: Plex의 이어보기 · 최근 추가, IPTV의 즐겨찾는 채널 · 지금 방송 중 |
| 라이브러리 | Plex 섹션별 **추천** 탭(섹션 허브) / **라이브러리** 탭(정렬, 안 본 것만, 스크롤하면 더 불러옴) |
| 상세 | 흐린 배경 그림 + 포스터, 정보, **재생 / 이어보기 / 처음부터 / 봤음 표시**, 시즌·에피소드 목록, 출연진 |
| 라이브 TV | Plex Live TV식 **편성표 격자**(8시간, 현재 시각 선), 그룹/검색/⭐즐겨찾기/안 나온 채널 숨기기 |
| 검색 | 모든 소스에 동시에: Plex 검색 + 채널명·지금 방송 제목 |
| 재생기 | 전체화면 재생, 자동으로 숨는 컨트롤, 탐색 막대, **자막 메뉴**, 라이브 OSD, 채널 목록 서랍, **뒤로 가면 미니 플레이어로 접힘** |

### 재생기 단축키

| 키 | 동작 |
|---|---|
| Space / K | 재생 · 일시정지 |
| ← / → | 10초 뒤로 / 30초 앞으로 (VOD) |
| ↑ / ↓ | 이전 / 다음 채널 (라이브) · 볼륨 (VOD) |
| F, 화면 두 번 클릭 | 전체화면 |
| M | 음소거 |
| I | 채널 정보(OSD) 다시 보기 |
| C | 자막 메뉴 (VOD) |
| Esc | 채널 서랍 닫기 → 미니 플레이어로 접기 |
| / | 검색창으로 (재생기 밖에서) |

## 구조

```
ym_player/                       저장소 루트
├── requirements.txt
├── tests/                       test_ym_player.py, test_subtitles.py
└── ym_player/                   파이썬 패키지 (python -m ym_player)
    ├── server.py                aiohttp 서버 - 정적 파일 + 아래 API
    ├── iptv.py                  M3U 파싱, XMLTV 파싱(iterparse, gzip), HLS 재생목록 주소 바꾸기 (순수 함수)
    ├── config_store.py          data/config.json 읽기/쓰기, 토큰 숨기기
    ├── subtitles.py             자막 SRT/SMI/ASS/VTT -> WebVTT 변환 (순수 함수)
    └── web/
        ├── index.html
        ├── css/app.css          Plex 다크 테마
        └── js/
            ├── app.js           사이드바 + 해시 라우터
            ├── player.js        재생 엔진 + 전체화면/미니 플레이어
            ├── cards.js         포스터·채널 카드, 가로 줄
            ├── providers/
            │   ├── index.js     소스 종류 등록소
            │   ├── plex.js      Plex 소스
            │   └── iptv.js      IPTV 소스 (채널, 편성표, 즐겨찾기)
            └── views/           home / library / detail / live / search / settings
```

### 서버 API

| 경로 | 하는 일 |
|---|---|
| `GET/PUT /api/config` | 소스 목록. Plex 토큰은 내려주지 않고 `has_token`만. 토큰 칸이 비면 기존 토큰 유지 |
| `GET /api/iptv/{id}/channels` | M3U를 받아 채널 JSON으로 (10분 캐시, `?refresh=1`) |
| `GET /api/iptv/{id}/epg` | XMLTV를 받아 **지금 −3시간 ~ +24시간**만 JSON으로 (30분 캐시) |
| `GET /api/stream?url=&ua=&ref=` | CORS·혼합콘텐츠로 막힌 스트림 중계. `.m3u8`이면 안의 주소(세그먼트·키·하위 목록)도 전부 중계 주소로 바꿈 |
| `GET /api/plex-sub/{id}?path=/library/streams/N&codec=` | Plex 외부 자막 파일을 받아 WebVTT로 바꿔 줌 (자막 스트림 경로만 허용) |
| `* /api/plex/{id}/...` | Plex API·이미지·스트림 중계. **토큰은 서버가 붙임** (브라우저에 안 보임) |
| `POST /api/plex-auth/pin`, `GET /api/plex-auth/pin/{id}` | plex.tv PIN 로그인 |

### 소스(provider) 모양

화면은 소스 종류를 모르고 아래 메서드만 부릅니다. 새 종류(Jellyfin, Emby 등)를 붙이려면 같은 모양의 클래스를
`web/js/providers/`에 만들어 `index.js`의 `TYPES`에 넣고, 서버 `config_store.SOURCE_TYPES`/`_clean_source`와
설정 화면 입력 칸만 추가하면 됩니다.

```
getLibraries()        -> [{id, title, type, icon}]          사이드바
getHomeHubs()         -> [{title, items, live?}]            홈 줄
search(q)             -> [{title, items}]
getDetail(id)         -> 상세 화면용 (Plex만)
getLibraryItems(...)  -> {items, total}                     (Plex만)
getPlayback(id|채널)  -> {url, kind: 'hls'|'ts'|'direct', live, proxyUrl?, startTime,
                          onProgress(state, sec), onStop(sec), getNext()}
```

## 재생 방식

- **IPTV**: 원래 플러그인과 같은 규칙. `.ts`/`output=ts` → mpegts.js, `.mp4/.webm…` → `<video>`, 나머지 → hls.js.
  원본 주소로 먼저 틀어 보고, 막히면(CORS 등) `/api/stream` 중계로 한 번 더. 그래도 안 되면 그 채널은 "안 나옴"으로
  표시(편성표·서랍에서 흐리게)하고, 망가진 MSE가 다음 채널까지 막지 않게 `<video>`를 새로 만듭니다.
  `#EXTVLCOPT:http-user-agent` / `http-referrer`가 있으면 중계할 때 그대로 실어 보냅니다.
- **Plex**: 브라우저가 그대로 틀 수 있는 파일(mp4+h264+aac, webm+vp9+opus, 음악 mp3/flac…)은 **원본 그대로(Direct Play)**,
  아니면 Plex 트랜스코더 **HLS**(`/video/:/transcode/universal/start.m3u8`, 자막은 영상에 입힘)로 받습니다.
  30초 넘게 보다 만 항목은 그 위치부터 이어보고, 10초마다 `/:/timeline`으로 진행 위치를 Plex에 알려서
  Plex 앱의 "이어보기"와 시청 기록이 그대로 맞습니다. 에피소드·곡이 끝나면 다음 화/곡을 이어서 틉니다.
- **자막 (Plex)**: 재생 화면 오른쪽 아래 **CC** 버튼(또는 `C`)으로 자막을 고르거나 끄고, 글자 크기(작게/보통/크게)를
  바꿉니다. 고른 자막은 Plex에도 저장돼서 Plex 앱과 같은 자막이 선택되고, 다음에 틀 때도 그대로 나옵니다.
  - **외부 텍스트 자막 파일** (`.srt`, `.smi`, `.ass/.ssa`, `.vtt`): 서버가 받아서 WebVTT로 바꾼 뒤 브라우저 자막으로
    띄웁니다. 원본 재생에서도 나오고, 영상을 다시 불러오지 않고 바로 켜고 끕니다. `.smi`는 한국어(KRCC)를 먼저 고르고,
    UTF-8/UTF-16/CP949(EUC-KR) 인코딩을 알아서 읽습니다.
  - **파일 안에 든 자막, 이미지 자막** (PGS·VobSub 등): 브라우저가 그릴 수 없어서 Plex가 변환하면서 영상에 입힙니다
    ("영상에 입힘" 표시). 이걸 고르거나 다시 끄면 그 위치부터 다시 불러옵니다.
- 편성표 매칭은 원래 플러그인과 같은 순서: `tvg-id` → 채널 표시명(공백·대소문자 무시) → 채널명 그대로.

## 주의할 점

- `/api/stream`과 `/api/plex/…`는 서버가 대신 접속하는 중계라, 아무나 접근할 수 있으면 남의 요청을 대신 보내주는
  통로가 됩니다. 집 안 네트워크(사설 IP) IPTV 서버도 틀 수 있게 일부러 사설 IP를 막지 않았으니,
  `127.0.0.1` 밖으로 열 때는 **`MP_USER`/`MP_PASSWORD`를 꼭** 설정하세요.
- 재생 엔진(hls.js 1.5.8, mpegts.js 1.7.3)과 아이콘은 CDN(jsDelivr, cdnjs)에서 받습니다. 인터넷이 안 되는 곳에서는
  이 파일들을 `web/` 아래에 두고 `index.html`의 주소를 바꾸세요.
- 브라우저가 모르는 코덱(HEVC, AC3/DTS 음성 등)은 Plex가 트랜스코딩해 주지만, IPTV 스트림은 서버가 변환하지 않으므로
  브라우저가 못 푸는 코덱의 채널은 안 나옵니다.
- 사진 라이브러리는 아직 지원하지 않습니다(사이드바에 안 보임).
