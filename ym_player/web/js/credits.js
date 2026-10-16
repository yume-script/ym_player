// 설정 > Thanks to 탭에 보여 줄 내용 (views/settings.js renderThanks).
// 참고한 저장소와 쓰는 라이브러리, 그리고 변경 이력(PR마다 한 줄, 새것이 위). 새 PR을 머지하면 CHANGELOG 맨 위에 한 줄 더한다.
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
        "pr": 36,
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
