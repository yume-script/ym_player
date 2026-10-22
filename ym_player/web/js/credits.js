// 설정 > Thanks to 탭에 보여 줄 내용 (views/settings.js renderThanks).
// 참고한 저장소와 쓰는 라이브러리, 그리고 변경 이력(PR마다 한 줄, 새것이 위). 새 PR을 머지하면 CHANGELOG 맨 위에 한 줄 더한다.
// PR마다 ym_player/__init__.py의 __version__ 셋째 자리를 하나 올리고 그 줄의 version에 같은 값을 적는다
// (version이 있는 줄은 아이콘 옆 [NEW]를 누르면 "새로워진 점"으로 나온다 - notice.js).
export const CREDITS = {
    "people": [
        {
            "name": "CRARES 님",
            "role": "북오아시스(BookOasis) 제작자",
            "note": "수많은 플러그인이 편하게 안주할 수 있는 터전, 북오아시스를 만들어 주셔서 고맙습니다. YMPlayer도 북오아시스의 M3U 플레이어와 텔레그램 동영상 플러그인에서 출발했습니다."
        }
    ],
    "sources": [
        {
            "name": "radio.bsod.kr",
            "url": "https://radio.bsod.kr/",
            "note": "한국 라디오 방송국(KBS·MBC·SBS 지역국, EBS, CBS, TBN, 공동체 라디오 등)을 지역별로 정리해 방송사 스트림으로 연결해 주는 곳. 한국 지역 라디오 목록이 이 사이트의 지역별 M3U에서 왔습니다."
        },
        {
            "name": "ym_m3u_player (북오아시스 M3U 플레이어 플러그인)",
            "url": "https://github.com/yume-script/ym_m3u_player",
            "note": "재생 엔진(hls.js·mpegts.js·네이티브 하이브리드), M3U·XMLTV 처리, 라이브 OSD를 옮겨 왔습니다."
        },
        {
            "name": "북오아시스 bookoasis_telegram_video 플러그인",
            "url": "",
            "note": "텔레그램 로그인·채널 색인·Range 스트림의 출발점 (파일로 받아 참고, 공개 저장소 주소 없음)."
        },
        {
            "name": "colaiuta77/jazzradio",
            "url": "https://github.com/colaiuta77/jazzradio",
            "note": "인터넷 라디오(방송국, 곡 정보, 앨범 표지, 시각화, LP 잡음) 아이디어를 참고했습니다. 코드는 쓰지 않고 새로 작성 (원본 AGPL-3.0)."
        },
        {
            "name": "ff_spotvnow (FlaskFarm 플러그인)",
            "url": "https://gitlab.com/sukajds/ff_spotvnow",
            "note": "IPTV 소스로 쓰는 M3U/EPG 출력 형식을 확인했습니다 (GitLab)."
        },
        {
            "name": "iptv-org/iptv",
            "url": "https://github.com/iptv-org/iptv",
            "note": "공개 IPTV 재생목록. 설정 화면의 M3U 예시 주소."
        }
    ],
    "libraries": [
        {
            "name": "hls.js",
            "url": "https://github.com/video-dev/hls.js",
            "note": "HLS 재생 (IPTV, Plex 변환, Safari용 텔레그램 변환)"
        },
        {
            "name": "mpegts.js",
            "url": "https://github.com/xqq/mpegts.js",
            "note": "MPEG-TS 라이브 재생"
        },
        {
            "name": "dash.js",
            "url": "https://github.com/Dash-Industry-Forum/dash.js",
            "note": "DASH(.mpd) 재생"
        },
        {
            "name": "Telethon",
            "url": "https://github.com/LonamiWebs/Telethon",
            "note": "텔레그램 접속"
        },
        {
            "name": "cryptg",
            "url": "https://github.com/cher-nov/cryptg",
            "note": "텔레그램 데이터 복호화 가속"
        },
        {
            "name": "aiohttp",
            "url": "https://github.com/aio-libs/aiohttp",
            "note": "웹 서버와 중계"
        },
        {
            "name": "FFmpeg",
            "url": "https://github.com/FFmpeg/FFmpeg",
            "note": "서버 변환(ffmpeg)과 형식 확인(ffprobe)"
        },
        {
            "name": "Font Awesome",
            "url": "https://github.com/FortAwesome/Font-Awesome",
            "note": "아이콘"
        },
        {
            "name": "Noto Sans KR",
            "url": "https://github.com/notofonts/noto-cjk",
            "note": "글꼴 (Google Fonts)"
        }
    ]
};

export const REPO = 'https://github.com/yume-script/ym_player';

export const CHANGELOG = [
    {
        "pr": 71,
        "version": "1.1.34",
        "date": "2026-10-22",
        "title": "웹툰 작품 정보: 다운로더 yaml(meta/search/files, Kavita 코드)을 제대로 읽기"
    },
    {
        "pr": 70,
        "version": "1.1.33",
        "date": "2026-10-22",
        "title": "웹툰: 작품 정보 파일(웹툰명.yaml)을 작품 화면에, 그림 받는 중 안내, 해시태그 작품 이름에서 #·작품 ID 빼기"
    },
    {
        "pr": 69,
        "version": "1.1.32",
        "date": "2026-10-22",
        "title": "웹툰 빨리 열기: 통째로 받지 않고 목차와 보는 쪽만, 여러 조각 동시에 받기, 다음 화 미리 받기"
    },
    {
        "pr": 68,
        "version": "1.1.31",
        "date": "2026-10-21",
        "title": "웹툰·시리즈 특별회차(특별편·외전·후기·공지·휴재)를 회차 목록 맨 밑으로, 이어 보기는 본편 다음에"
    },
    {
        "pr": 67,
        "version": "1.1.30",
        "date": "2026-10-21",
        "title": "설정 저장 요청을 작게 (바뀐 소스만 보냄) - 앞단 프록시에서 큰 요청이 막혀 저장이 안 되던 문제 피함"
    },
    {
        "pr": 66,
        "version": "1.1.29",
        "date": "2026-10-21",
        "title": "중간 프록시 오류(HTTP 500/413/502)를 알아보기 쉬운 안내로, 설정 저장 실패 원인을 화면에"
    },
    {
        "pr": 65,
        "version": "1.1.28",
        "date": "2026-10-21",
        "title": "사이드바 메뉴 순서 바꾸기 (소스·라이브러리, 사람마다 저장, 홈 줄 순서에도 적용)"
    },
    {
        "pr": 64,
        "version": "1.1.27",
        "date": "2026-10-21",
        "title": "라이브러리 보기 방식(그리드/세부/테이블), 재생기 연결 중 포스터와 라디오 표지도 포스터 크기 막대를 따름"
    },
    {
        "pr": 63,
        "version": "1.1.26",
        "date": "2026-10-21",
        "title": "위쪽에 포스터 크기 막대 - 카드와 상세 화면 포스터를 60~160%로"
    },
    {
        "pr": 62,
        "version": "1.1.25",
        "date": "2026-10-21",
        "title": "재생 빨리 시작: 다음 회차 미리 준비, 시작·탐색 직후 몰아 받기, 서버 변환 첫 화면 단축, 이어보기 시작 위치, 회차 화면 [재생]이 다른 회차를 틀던 문제"
    },
    {
        "pr": 61,
        "version": "1.1.24",
        "date": "2026-10-20",
        "title": "MCP 서버: Claude 같은 AI 앱에서 찾기·이어보기·최근 추가·라디오 곡 정보 (설정 > MCP에서 토큰)"
    },
    {
        "pr": 60,
        "version": "1.1.23",
        "date": "2026-10-20",
        "title": "텔레그램: 웹툰 채널 - zip/cbz 압축 파일을 세로 스크롤 웹툰 보기로 (이어 보기, 다음 화)"
    },
    {
        "pr": 59,
        "version": "1.1.22",
        "date": "2026-10-18",
        "title": "텔레그램: TMDB 작품 정보(포스터·배경·줄거리·장르·출연진·평점·회차 제목) 입히기, [정보 고치기]"
    },
    {
        "pr": 58,
        "version": "1.1.21",
        "date": "2026-10-18",
        "title": "설정 > 소스를 종류별 하위 탭(Plex / IPTV / 텔레그램 / 라디오)으로 나눔"
    },
    {
        "pr": 57,
        "version": "1.1.20",
        "date": "2026-10-18",
        "title": "텔레그램: 채널마다 시리즈 회차 목록 순서(1화부터 / 최신 회차부터) 설정"
    },
    {
        "pr": 56,
        "version": "1.1.19",
        "date": "2026-10-17",
        "title": "텔레그램 시리즈: 재생하면 HTTP 404 나던 문제 (시리즈를 영상처럼 열려고 함) - 다음 볼 회차로 바꿔서 재생, 시리즈 카드에도 [재생]"
    },
    {
        "pr": 55,
        "version": "1.1.18",
        "date": "2026-10-17",
        "title": "텔레그램 시리즈: S102E08처럼 시즌을 세 자리로 적은 회차도 인식 (킬러들의 쇼핑몰)"
    },
    {
        "pr": 54,
        "version": "1.1.17",
        "date": "2026-10-17",
        "title": "텔레그램: 회차 영상을 시리즈 카드로 묶기 (회차 목록, 다음 볼 회차 재생, 끝나면 다음 회차, 시리즈 전체 봤음)"
    },
    {
        "pr": 53,
        "version": "1.1.16",
        "date": "2026-10-17",
        "title": "텔레그램: 영상 옆에 올린 자막 파일(srt/smi/ass/vtt)을 그 영상의 자막으로 자동 연결, 한국어 자막 먼저 켜기"
    },
    {
        "pr": 52,
        "version": "1.1.15",
        "date": "2026-10-17",
        "title": "텔레그램: 홈에 채널들을 합친 \"새로 올라온 영상\" 줄, 지난번 방문 뒤 올라왔고 아직 안 본 영상에 NEW"
    },
    {
        "pr": 51,
        "version": "1.1.14",
        "date": "2026-10-17",
        "title": "한국 지역 라디오 194개(radio.bsod.kr 지역별 목록, 전국 + 9개 지역)를 설정에서 한 번에 넣기, 방송국 화면에서 지역별로 보기, HLS 방송 자동 판별"
    },
    {
        "pr": 50,
        "version": "1.1.13",
        "date": "2026-10-17",
        "title": "사이드바: IPTV 소스가 하나뿐이면 \"라이브 TV\"(합친 편성표) 메뉴를 빼고 그 소스의 채널 편성표만"
    },
    {
        "pr": 49,
        "version": "1.1.12",
        "date": "2026-10-17",
        "title": "작게 보기·PiP 자동 전환: 사이트 안에서는 작게 보기, 재생 중 다른 탭(Chrome)·홈 화면(아이패드)으로 가면 자동 PiP, 돌아오면 보던 화면으로"
    },
    {
        "pr": 48,
        "version": "1.1.11",
        "date": "2026-10-17",
        "title": "아이패드 라디오 PiP: 그림 영상을 미리 준비해 두고 누른 그 순간에 요청 (Safari의 user activation 조건)"
    },
    {
        "pr": 47,
        "version": "1.1.10",
        "date": "2026-10-17",
        "title": "라디오: 작게 보기(미니 막대)에 앨범 표지, 아이패드·아이폰 Safari의 화면 속 화면(webkitSetPresentationMode) 지원"
    },
    {
        "pr": 46,
        "version": "1.1.9",
        "date": "2026-10-17",
        "title": "라디오 PiP 보완: 브라우저 쪽에서 PiP를 열어도 그림 창으로 바꾸기, Chrome 미디어 조작의 PIP 버튼 연결, 실패하면 이유를 화면에"
    },
    {
        "pr": 45,
        "version": "1.1.8",
        "date": "2026-10-17",
        "title": "라디오 화면 속 화면(PiP): 검은 화면 대신 앨범 표지 + 시각화 + 방송국·곡 이름, PiP 창의 재생/일시정지도 동작"
    },
    {
        "pr": 44,
        "version": "1.1.7",
        "date": "2026-10-17",
        "title": "라디오: HLS(.m3u8) 방송국도 재생 (Chrome은 hls.js로, 서버 중계로 시각화도)"
    },
    {
        "pr": 43,
        "version": "1.1.6",
        "date": "2026-10-17",
        "title": "텔레그램 포스터: 우리말 제목 포스터와 원제 영상(성난 사람들 S02E03 / BEEF_S02E03)도 회차가 같고 바로 옆이면 짝짓기"
    },
    {
        "pr": 42,
        "version": "1.1.5",
        "date": "2026-10-16",
        "title": "텔레그램 포스터: 큰 파일은 포스터보다 한참 뒤에 올라와도 짝짓기(12시간까지), 포스터를 카드 썸네일로 먼저, 짝짓기 결과를 서버 로그에"
    },
    {
        "pr": 41,
        "version": "1.1.4",
        "date": "2026-10-16",
        "title": "텔레그램: 채널마다 영상만/사진만/둘 다 고르기, 영상 옆에 올린 포스터 사진을 그 영상의 썸네일·포스터로 (목록에서는 숨김)"
    },
    {
        "pr": 40,
        "version": "1.1.3",
        "date": "2026-10-16",
        "title": "GitHub 새 버전 알림 (관리자): 서버가 6시간마다 GitHub 버전을 확인, 아이콘 옆 [GitHub]에 바뀐 점과 업데이트 명령, 설정 > 상태에 [지금 확인]"
    },
    {
        "pr": 39,
        "version": "1.1.2",
        "date": "2026-10-16",
        "title": "새 버전 알림: 아이콘 옆 [NEW]를 누르면 새로워진 점, 열어 둔 화면에서 서버가 업데이트되면 [업데이트]로 새로고침 안내"
    },
    {
        "pr": 38,
        "version": "1.1.1",
        "date": "2026-10-16",
        "title": "랜덤 재생 (Plex·텔레그램): 홈/라이브러리의 [랜덤 재생], 끝나면 또 아무거나, 재생 화면의 [다른 랜덤 영상]"
    },
    {
        "pr": 37,
        "version": "1.1.1",
        "date": "2026-10-16",
        "title": "아이패드 라디오 백그라운드 재생 우선, 잠금 화면·제어 센터 조작(Media Session)"
    },
    {
        "pr": 36,
        "version": "1.1.1",
        "date": "2026-10-16",
        "title": "v1.1.1: 이름을 YMPlayer로, 버전 배지에 전체 버전, 기본 테마를 바꿔도 화면이 그대로인 경우 안내"
    },
    {
        "pr": 35,
        "date": "2026-10-16",
        "title": "Thanks to: 북오아시스를 만든 CRARES 님께 감사 인사"
    },
    {
        "pr": 34,
        "date": "2026-10-16",
        "title": "라디오: 일반 사용자에게도 방송국 목록, 사용자마다 볼 방송국 고르기"
    },
    {
        "pr": 33,
        "date": "2026-10-16",
        "title": "설정에 Thanks to 탭: 참고한 저장소와 변경 이력"
    },
    {
        "pr": 32,
        "date": "2026-10-16",
        "title": "라디오 2단계: 시각화 5종과 LP 잡음"
    },
    {
        "pr": 31,
        "date": "2026-10-16",
        "title": "인터넷 라디오 소스 (1단계: 방송국, 재생, 지금 나오는 곡, 앨범 표지)"
    },
    {
        "pr": 30,
        "date": "2026-10-15",
        "title": "텔레그램 채널 사진 뷰어"
    },
    {
        "pr": 29,
        "date": "2026-10-15",
        "title": "Safari: H.264 + AAC MP4만 원본으로, 나머지는 미리 서버 변환"
    },
    {
        "pr": 28,
        "date": "2026-10-15",
        "title": "업데이트 뒤 브라우저가 옛 화면 코드를 쓰지 않게: 화면 파일 주소에 지문"
    },
    {
        "pr": 27,
        "date": "2026-10-15",
        "title": "Safari(아이패드): 텔레그램 MP3 소리 영상과 서버 변환을 HLS로"
    },
    {
        "pr": 26,
        "date": "2026-10-15",
        "title": "아이패드 텔레그램 무음 시작 진단: Safari 구간 요청과 파일 음성 정보 기록"
    },
    {
        "pr": 25,
        "date": "2026-10-15",
        "title": "Plex 변환 재생: 처음 몇 초 소리가 안 나오던 문제 (copyts=1)"
    },
    {
        "pr": 24,
        "date": "2026-10-15",
        "title": "화면 속 화면(PiP): 홈으로 이동, IPTV에서도 창 크기 조절"
    },
    {
        "pr": 23,
        "date": "2026-10-15",
        "title": "새 기본 아이콘(초승달 재생)과 버전 배지"
    },
    {
        "pr": 22,
        "date": "2026-10-15",
        "title": "화면 테마: 다크/라이트/블랙/네이비/시스템 + 강조색 7가지"
    },
    {
        "pr": 21,
        "date": "2026-10-15",
        "title": "IPTV: 여러 M3U/EPG 주소를 하나의 소스로 합치기"
    },
    {
        "pr": 20,
        "date": "2026-10-15",
        "title": "텔레그램 나눠진 파일: 두 번째 이후 조각 재생 전에 확인"
    },
    {
        "pr": 19,
        "date": "2026-10-15",
        "title": "텔레그램 제목의 깨진 글자를 UTF-8로 고쳐서 보여주기"
    },
    {
        "pr": 18,
        "date": "2026-10-15",
        "title": "올린 아이콘이 잘려 보이던 것 고침"
    },
    {
        "pr": 17,
        "date": "2026-10-15",
        "title": "관리자가 화면 이름과 아이콘을 바꿀 수 있게 (설정 > 화면)"
    },
    {
        "pr": 16,
        "date": "2026-10-15",
        "title": "텔레그램 채널도 사용자마다 볼 수 있는 것만"
    },
    {
        "pr": 15,
        "date": "2026-10-15",
        "title": "동영상 캐시: 텔레그램·Plex 원본 재생 (디스크 캐시 + 앞쪽 미리 받기)"
    },
    {
        "pr": 14,
        "date": "2026-10-15",
        "title": "IPTV: DASH(.mpd) 재생"
    },
    {
        "pr": 13,
        "date": "2026-10-15",
        "title": "IPTV: 잘못된 로고 주소(None) 버리기, DASH(.mpd)는 분명한 메시지로"
    },
    {
        "pr": 12,
        "date": "2026-10-15",
        "title": "텔레그램 [재생] 멈칫함 줄이기"
    },
    {
        "pr": 11,
        "date": "2026-10-15",
        "title": "텔레그램 영상 서버 변환(ffmpeg): mkv·HEVC·AC-3도 재생"
    },
    {
        "pr": 10,
        "date": "2026-10-15",
        "title": "사용자별 볼 수 있는 범위, 통합 이어보기, Plex 음성·화질, 상태 점검·백업"
    },
    {
        "pr": 9,
        "date": "2026-10-15",
        "title": "로그인 화면과 다중 사용자: 관리자만 설정, 사용자는 보기만, 설정 메뉴는 사이드바로"
    },
    {
        "pr": 8,
        "date": "2026-10-15",
        "title": "텔레그램: 반응 속도 개선 (cryptg 기본 포함, 목록은 바로·새 영상은 뒤에서, 썸네일 재사용·미리 받기)"
    },
    {
        "pr": 7,
        "date": "2026-10-15",
        "title": "텔레그램 채널을 새 소스로: 로그인, 채널 고르기, 동영상 색인, Range 스트림, 이어보기"
    },
    {
        "pr": 6,
        "date": "2026-10-15",
        "title": "IPTV: 재생 중에 끊기면 자동으로 살리기 (직접 → 서버 중계, 중계는 3번까지 재연결)"
    },
    {
        "pr": 5,
        "date": "2026-10-14",
        "title": "설정: Plex 라이브러리마다 가져올지 체크"
    },
    {
        "pr": 4,
        "date": "2026-10-14",
        "title": "사이드바: 소스가 둘 이상이면 아코디언 (하나를 펼치면 나머지 접기)"
    },
    {
        "pr": 3,
        "date": "2026-10-14",
        "title": "Plex 자막: 재생 화면 자막 메뉴, 외부 자막을 브라우저 자막으로, Plex 선택과 동기화"
    },
    {
        "pr": 2,
        "date": "2026-10-14",
        "title": "systemd 서비스 파일과 venv 설치 안내 추가"
    },
    {
        "pr": 1,
        "date": "2026-10-14",
        "title": "YMPlayer: Plex/IPTV를 한 화면에서 보는 Plex 스타일 독립형 동영상 플레이어"
    }
];
