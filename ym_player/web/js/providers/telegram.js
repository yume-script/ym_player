// 텔레그램 채널 소스. 서버(/api/tg/{id}/...)가 Telethon으로 채널 영상을 색인하고 스트림을 이어 준다.
// 설정에서 고른 채널 하나하나가 라이브러리처럼 보인다. 항목 id는 "채널id:메시지id".
import { api, fmtDuration, h, modal, storeGet, storeSet, IS_SAFARI, dataSaver, toast, prepareMp4 } from '../util.js';
import { registry } from './index.js';
// 웹툰·책 보기는 열 때 받는다 (처음 화면을 가볍게)
const openReader = (...a) => import('../reader.js').then((m) => m.openReader(...a));
const openDocReader = (...a) => import('../docreader.js').then((m) => m.openDocReader(...a));

// "새로 올라온 영상"의 NEW: 지난번 방문(서버 설정 tg_seen) 뒤 올라왔고 아직 안 본 영상.
// 이번 방문 동안은 같은 기준을 쓰도록 처음 값을 세션에 붙잡아 둔다 (홈을 다시 열어도 NEW가 바로 사라지지 않게)
const SEEN_SESSION = 'yp_tg_seen_prev';
function seenBefore() {
    try {
        let v = sessionStorage.getItem(SEEN_SESSION);
        if (v === null) {
            v = (registry.prefs && registry.prefs.tg_seen) || '';
            sessionStorage.setItem(SEEN_SESSION, v);
        }
        return v;
    } catch (e) {
        return (registry.prefs && registry.prefs.tg_seen) || '';
    }
}

// 영상 옆에 올린 자막 파일 (서버가 짝지어 v.subs로 준다). 이름으로 언어를 짐작해 한국어를 먼저 켠다
const SUB_LANGS = [[/(^|[._ -])(ko|kor|korean|kr)([._ -]|$)|한글|한국어/i, 'ko', '한국어'], [/(^|[._ -])(en|eng|english)([._ -]|$)|영어/i, 'en', '영어'],
    [/(^|[._ -])(ja|jp|jpn)([._ -]|$)|일본어/i, 'ja', '일본어'], [/(^|[._ -])(zh|chs|cht|chi)([._ -]|$)|중국어/i, 'zh', '중국어']];
function subInfo(s) {
    const stem = (s.name || '').replace(/\.[^.]+$/, '');
    const hit = SUB_LANGS.find(([re]) => re.test(stem));
    const ext = ((s.name || '').match(/\.([^.]+)$/) || ['', ''])[1].toUpperCase();
    return { id: String(s.msg), msg: s.msg, text: true, lang: hit ? hit[1] : '', label: `${hit ? `${hit[2]} · ` : ''}${stem || '자막'}${ext ? ` (${ext})` : ''}` };
}

// 영상 안 자막 트랙 (ffprobe: {index, codec, lang, title, ko, forced}) -> 자막 메뉴 한 줄
const INNER_LANGS = { kor: '한국어', ko: '한국어', eng: '영어', en: '영어', jpn: '일본어', ja: '일본어', chi: '중국어', zho: '중국어', zh: '중국어' };
function innerSubInfo(s) {
    const lang = s.ko ? 'ko' : (s.lang || '');
    const name = s.ko ? '한국어' : (INNER_LANGS[s.lang] || (s.lang && s.lang !== 'und' ? s.lang : '언어 모름'));
    return { id: `i${s.index}`, inner: s.index, text: true, lang: lang === 'kor' ? 'ko' : lang.slice(0, 2), forced: !!s.forced,
        label: `${name}${s.title ? ` · ${s.title}` : ''}${s.forced ? ' (강제)' : ''} (영상 안 자막)` };
}

/** 이번에 본 가장 새 영상 시각을 서버에 (다음 방문의 NEW 기준) */
function markSeen(items) {
    const newest = items.reduce((m, v) => (v.date && v.date > m ? v.date : m), '');
    const prefs = registry.prefs || (registry.prefs = {});
    if (!newest || newest <= (prefs.tg_seen || '')) return;
    prefs.tg_seen = newest;
    api('/api/me/prefs', { method: 'PUT', json: { tg_seen: newest } }).catch(() => {});
}

const TRANSCODE_NOTE = { remux: '서버 변환(포장만)', audio: '서버 변환(음성)', transcode: '서버 변환(H.264)' };

// Safari는 길이를 모르는 변환 스트림을 못 열어서 서버 변환을 HLS로 받는다. 원본으로 보낼 형식도 좁게 본다 (util.js IS_SAFARI).

/** 서버가 "원본 그대로(direct)"라고 한 영상을 Safari에서 어떻게 볼지. 원본으로 못 믿을 조합은 미리 서버 변환으로 */
function safariMode(play, fileName = '') {
    if (play.mode !== 'direct' || !play.available) return play.mode;
    // 서버가 형식을 알아내지 못했으면(ffprobe 실패) mp4가 아닌 한 원본으로 시도하지 않고 바로 서버 변환으로
    // (Safari는 mkv 등을 받으면 "The operation is not supported"로 실패한다)
    if (!play.container) return /\.(mp4|m4v|mov)$/i.test(fileName) ? 'direct' : 'transcode';
    const video = String(play.video || '').split(' ')[0];
    if (play.container !== 'mp4' || video !== 'h264') return 'transcode';  // WebM, VP9, AV1 등
    if (play.audio && play.audio !== 'aac') return 'audio';                // MP3, Opus, FLAC 등 -> 소리만 AAC로
    return 'direct';
}

// 나눠 올린 파일의 조각 이름 ("이름_0001", "이름.002", "이름 part2", "이름 CD2"). 서버 telegram_src._PART_RE와 같은 규칙
const PART_RE = /^(.*?)[\s._-]*(?:(?:part|pt|cd|disc|disk)[\s._-]*0*(\d{1,3})|[._-](0\d{2,3}))(?:\.\w{2,4})?$/i;

function looksLikePart(v) {
    return [v.file_name, v.title].some((n) => {
        const m = PART_RE.exec((n || '').trim());
        return m && m[1].length >= 3;
    });
}

const ORDINAL = ['첫', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열'];
const nth = (n) => `${ORDINAL[n - 1] || n} 번째`;

/** 나눠 올린 영상에서 이어 볼 조각: 보다 만 조각 -> 다 보지 않은 첫 조각 -> 첫 조각 */
function resumePart(parts) {
    const unfinished = (p) => !p.watched && !(p.duration && p.position >= p.duration * 0.95);
    return parts.find((p) => p.position > 30 && unfinished(p)) || parts.find(unfinished) || parts[0];
}
// 조각 목록의 "(2/3) 재생"처럼 그 조각을 골라 틀 때 붙는 표시 - 이어 볼 조각으로 바꾸지 않는다
const EXACT = ':p';
const plainId = (id) => String(id).replace(/:p$/, '');

/** "pdf" / "epub" (파일 이름, 없으면 형식으로) */
function docFormat(v) {
    const name = String(v.file_name || '').toLowerCase();
    return name.endsWith('.epub') || v.mime === 'application/epub+zip' ? 'epub' : 'pdf';
}

function fmtSize(bytes) {
    if (!bytes) return '';
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? `${gb.toFixed(1)}GB` : `${Math.round(bytes / 1024 ** 2)}MB`;
}

/** 해시태그로 올린 웹툰 이름 다듬기: "#신입_공무원은_빌런_잡는다_856116" -> "신입 공무원은 빌런 잡는다" */
function cleanBookTitle(t) {
    const s = String(t || '');
    if (!s.startsWith('#')) return s;
    return s.replace(/^#+/, '').replace(/_/g, ' ').replace(/[\s-]*\(?\d{6,}\)?(?=\s|$)/g, '').replace(/\s{2,}/g, ' ').trim() || s;
}

/** 회차 이름: "3화" / "시즌 2 3화" / 특별회차는 "특별" (번호가 있으면 "특별 3") */
function epLabel(v, multiSeason) {
    if (v.special) return v.ep ? `특별 ${v.ep}` : '특별';
    return `${multiSeason ? `시즌 ${v.season} ` : ''}${v.ep}화`;
}

function epIndex(v, multiSeason) {
    if (v.special) return '특별';
    return multiSeason ? `S${v.season}·${v.ep}` : v.ep;
}

/** "최신 회차부터" 순서: 본편만 거꾸로, 특별회차는 그대로 맨 밑에 */
// 음악 캡션 ("🎵 곡" + "이름: 값" 줄 - BookOasis Assistant 음악 규칙, 서버 telegram_src.music_info와 같게)
const MUSIC_LABELS = { 아티스트: 'artist', 앨범: 'album', '앨범 아티스트': 'album_artist', 트랙: 'track', 디스크: 'disc', 장르: 'genre',
    작곡: 'composer', 작사: 'lyricist', 편곡: 'arranger', 앨범ID: 'album_id', 연도: 'year' };
function musicInfo(v) {
    const out = {};
    const rows = String((v && v.caption) || '').split('\n');
    if (rows[0] && rows[0].startsWith('🎵')) out.title = rows[0].slice(2).trim();
    for (const row of rows) {
        const i = row.indexOf(':');
        const f = i > 0 ? MUSIC_LABELS[row.slice(0, i).trim()] : null;
        if (f && row.slice(i + 1).trim()) out[f] = row.slice(i + 1).trim();
    }
    const m = /^(.*?)\s*\((1[89]\d{2}|20\d{2})\)\s*$/.exec(out.album || '');
    if (m) { out.album = m[1]; out.year = out.year || m[2]; }
    for (const f of ['track', 'disc']) {
        const t = /^\s*(\d{1,3})(?:\s*\/\s*(\d{1,3}))?/.exec(out[f] || '');
        if (t) { out[f] = Number(t[1]); if (t[2]) out[`${f}_total`] = Number(t[2]); } else delete out[f];
    }
    out.title = out.title || (v && v.title) || '';
    return out;
}
const fmtClockSec = (sec) => { sec = Math.round(sec || 0); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; };

function reverseRegular(children) {
    const regular = children.filter((c) => !c.raw.special).reverse();
    const special = children.filter((c) => c.raw.special);
    children.splice(0, children.length, ...regular, ...special);
}

export class TelegramProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'telegram';
        // kind: 관리자가 정한 채널 종류 ("video" 영상만 / "image" 사진만 / "webtoon" 웹툰 압축 파일 / "doc" 책(PDF·EPUB) / "" 영상+사진)
        // title: 설정에서 바꾼 이름(name)이 있으면 그것, 없으면 텔레그램 채널 이름
        // epOrder: 시리즈 회차 목록 순서 ("asc" 1화부터 / "desc" 최신 회차부터)
        this.channels = (src.channels || []).map((c) => ({ id: String(c.id), title: c.name || c.title, kind: c.kind || '', epOrder: c.ep_order || 'asc' }));
        this.base = `/api/tg/${encodeURIComponent(src.id)}`;
        this._warmed = new Set();
        this._seen = new Map(); // 항목 id -> 목록에서 받은 영상 정보 (조각 파일인지 미리 보려고)
    }

    channelTitle(id) {
        const c = this.channels.find((x) => x.id === String(id));
        return c ? c.title : '';
    }

    toItem(v) {
        const id = `${v.channel}:${v.msg}`;
        this._seen.set(id, v);
        // 영상 옆에 올린 포스터 사진(서버가 짝지음)이 있으면 카드 썸네일과 상세 화면 포스터로
        // (텔레그램 영상 썸네일은 검은 첫 화면이거나 없는 경우가 많아 포스터를 먼저)
        // Plex -> 텔레그램 자동 올리기로 올린 영상은 그때 받아 둔 Plex 포스터 (plex_poster)
        const posterImg = v.plex_poster || (v.poster ? `${this.base}/thumb/${v.channel}/${v.poster}` : '');
        const thumb = posterImg || (v.has_thumb ? `${this.base}/thumb/${v.channel}/${v.msg}` : '');
        if (v.kind === 'image') {
            // 사진: 누르면 뷰어로 (cards.js). 원본은 처음 열 때 서버가 텔레그램에서 받아 둔다
            const sub = [this.channelTitle(v.channel), (v.date || '').slice(0, 10), v.width ? `${v.width}×${v.height}` : '']
                .filter(Boolean).join(' · ');
            return {
                pid: this.id, id, type: 'photo', title: v.title, subtitle: sub,
                thumb, wideThumb: thumb, shape: 'wide',
                image: `${this.base}/image/${v.channel}/${v.msg}`,
                caption: (v.caption || '').split('\n')[0].trim() === v.title
                    ? (v.caption || '').split('\n').slice(1).join('\n').trim() : (v.caption || ''),
                width: v.width, height: v.height,
                raw: v,
            };
        }
        const book = v.kind === 'book';
        if (v.series && v.kind === 'audio') {
            // 앨범 카드 (같은 앨범의 곡 묶음 - 시리즈와 같은 구조). 그림은 곡의 앨범 아트(썸네일). 누르면 트랙 목록
            const sid = `series:${v.channel}:${v.series.key}`;
            this._seen.set(sid, v);
            const al = v.series.album || {};
            const cover = thumb || (v.series.cover ? `${this.base}/thumb/${v.channel}/${v.series.cover}` : '');
            return {
                pid: this.id, id: sid, type: 'show', playable: true, title: v.series.title,
                subtitle: [al.artist, `${v.series.count}곡`, al.year].filter(Boolean).join(' · '),
                thumb: cover, wideThumb: cover, shape: 'square',
                route: `#/item/${this.id}/${encodeURIComponent(sid)}`,
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore()),
                raw: v,
            };
        }
        if (v.series) {
            // 시리즈(회차 묶음) 카드: 가장 최근 회차가 대표. 누르면 회차 목록
            const sid = `series:${v.channel}:${v.series.key}`;
            this._seen.set(sid, v);
            // 작품 정보 파일(웹툰명.yaml)이 있으면 그 표지 (서버가 받아 둠), 없으면 회차 그림
            const plexCover = v.series.plex_poster || '';
            const cover = plexCover || (v.series.meta ? this.seriesCoverUrl(v.channel, v.series.key)
                : thumb || (v.series.cover ? `${this.base}/thumb/${v.channel}/${v.series.cover}` : ''));
            return {
                pid: this.id, id: sid, type: 'show', playable: true, title: v.series.title,
                subtitle: [this.channelTitle(v.channel), `${v.series.count}화`, (v.date || '').slice(0, 10)].filter(Boolean).join(' · '),
                thumb: cover, wideThumb: cover, shape: book || plexCover ? 'poster' : 'wide', cardClass: book ? 'card-book' : '',
                unwatched: v.series.unwatched, progress: 0,
                route: `#/item/${this.id}/${encodeURIComponent(sid)}`,
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
                raw: v,
            };
        }
        if (v.kind === 'doc') {
            // 책 (PDF / EPUB): 누르면 책 보기로 (docreader.js). duration = PDF 쪽 수 / EPUB 1000 (한 번 열어 본 뒤에 앎)
            return {
                pid: this.id, id, type: 'book', playable: true, title: cleanBookTitle(v.title),
                subtitle: [this.channelTitle(v.channel), docFormat(v).toUpperCase(), fmtSize(v.size)].filter(Boolean).join(' · '),
                thumb, wideThumb: thumb, shape: 'poster', cardClass: 'card-book card-doc',
                progress: v.duration && v.position ? Math.min(1, v.position / v.duration) : 0,
                watched: !!v.watched,
                onOpen: () => this.readDoc(id),
                fileName: v.file_name || '',
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
                raw: v,
            };
        }
        if (v.kind === 'audio') {
            // 곡 하나 (앨범이 없는 낱곡, 또는 앨범 화면의 트랙): 누르면 상세(재생)
            const mi = musicInfo(v);
            return {
                pid: this.id, id, type: 'movie', title: mi.title,
                subtitle: [mi.artist, v.duration ? fmtClockSec(v.duration) : ''].filter(Boolean).join(' · '),
                thumb, wideThumb: thumb, shape: 'square',
                route: `#/item/${this.id}/${id}`,
                ts: v.updated || 0,
                fileName: v.file_name || '',
                raw: v,
            };
        }
        if (book) {
            // 웹툰 한 화 (zip/cbz): 누르면 웹툰 보기로 (reader.js). duration = 쪽 수 (한 번 열어 본 뒤에 앎)
            return {
                pid: this.id, id, type: 'book', playable: true, title: cleanBookTitle(v.title),
                subtitle: [this.channelTitle(v.channel), (v.date || '').slice(0, 10), fmtSize(v.size)].filter(Boolean).join(' · '),
                thumb, wideThumb: thumb, shape: 'poster', cardClass: 'card-book',
                progress: v.duration && v.position ? Math.min(1, v.position / v.duration) : 0,
                watched: !!v.watched,
                onOpen: () => this.read(id),
                fileName: v.file_name || '',
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
                raw: v,
            };
        }
        // 나눠 올린 조각 묶음은 서버가 목록에서 하나로 합쳐 준다 (parts_count개 파일).
        // 둘째 줄은 길이·파일 수 (채널 위치는 이미 보고 있는 곳이라 뺌), 셋째 줄은 파일 이름 대신 등록일
        const sub = [v.duration ? fmtDuration(v.duration * 1000) : '', v.parts_count > 1 ? `${v.parts_count}개 파일` : ''].filter(Boolean).join(' · ');
        return {
            pid: this.id,
            id,
            type: 'movie',            // 카드/상세에서 "재생 가능한 단일 영상"으로 다루게
            title: v.meta_title || v.title,   // 영화는 찾아 둔 작품 제목(상세 화면과 같게 - "총알 탄 사나이"), 없으면 캡션·파일 이름
            subtitle: sub,
            thumb: v.plex_poster || thumb,
            wideThumb: thumb,
            shape: v.plex_poster ? 'poster' : 'wide',
            progress: v.duration && v.position ? v.position / v.duration : 0,
            watched: !!v.watched,
            route: `#/item/${this.id}/${id}`,
            ts: v.updated || 0,
            isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
            posterImg,
            fileName: (v.file_name || '') + (v.parts_count > 1 ? ` 외 ${v.parts_count - 1}개` : ''),
            extraLine: v.date ? `${v.date.slice(0, 10)} 등록` : '',
            raw: v,
        };
    }

    async getLibraries() {
        return this.channels.map((c) => ({ id: c.id, title: c.title, type: 'telegram', icon: 'paper-plane' }));
    }

    async getHomeHubs() {
        seenBefore(); // 이번 방문의 NEW 기준을 먼저 붙잡는다 (아래 markSeen이 서버 값을 바꾸기 전에)
        const data = await api(`${this.base}/home`);
        const hubs = [];
        if (data.continue && data.continue.length) {
            hubs.push({ title: '이어보기', source: this.name, continue: true, items: data.continue.map((v) => this.toItem(v)) });
        }
        // 채널들을 합친 새 영상 (채널이 여럿일 때 서버가 보내 줌)
        if (data.recent && data.recent.length) {
            hubs.push({ title: '새로 올라온 영상', source: this.name, items: data.recent.map((v) => this.toItem(v)) });
        }
        markSeen([...(data.recent || []), ...(data.channels || []).flatMap((r) => r.items)]);
        for (const row of data.channels || []) {
            if (!row.items.length) continue;
            const ck = (this.channels.find((c) => c.id === String(row.channel)) || {}).kind;
            hubs.push({
                title: `${this.channelTitle(row.channel) || '채널'} ${{ webtoon: '최신 웹툰', doc: '최신 책', music: '최신 음악' }[ck] || '최신 영상'}`, source: this.name,
                items: row.items.map((v) => this.toItem(v)), more: `#/library/${this.id}/${row.channel}`,
            });
        }
        return hubs;
    }

    /** 정렬 ([값, 이름, 기본 방향] - 같은 항목을 다시 누르면 방향이 바뀐다) */
    librarySorts() {
        return [['added', '추가된 날짜', 'desc'], ['title', '제목']];
    }

    librarySearchHint() { return '이 채널에서 찾기 (제목·캡션)'; }

    /** 라이브러리 화면의 보기 고르기 (영상/사진). 관리자가 채널 종류를 정해 두었으면 고를 것 없음(null) */
    libraryKinds(channel) {
        const c = this.channels.find((x) => x.id === String(channel));
        if (c && c.kind === 'music') return [['', '앨범'], ['artist', '아티스트'], ['track', '곡']];
        if (c && c.kind) return null;
        return [['', '전체'], ['video', '영상'], ['image', '사진'], ['doc', '책']];
    }

    /** 라이브러리(채널) 화면의 단추: [목록 새로 고침] - 채널의 새 영상을 지금 받아 오고(평소엔 몇 분마다) 목록을 다시 그린다.
     *  작품 정보(포스터)가 없는 영상은 서버가 뒤에서 TMDB에서 찾는다 */
    libraryTools(channel, reset) {
        const btn = h('button', { class: 'btn', type: 'button', title: '채널의 새 영상을 지금 받아 오고 목록을 다시 그립니다' },
            h('i', { class: 'fa-solid fa-rotate-right' }), ' 목록 새로 고침');
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.firstChild.classList.add('fa-spin');
            try {
                const r = await api(`${this.base}/refresh`, { method: 'POST', json: { channel } });
                reset();
                const t = r.tmdb || {};
                if (r.removed) toast(`텔레그램에서 지워진 ${r.removed}개를 목록에서 뺐습니다.`, 'info', 6000);
                if (t.error) toast(`작품 정보(TMDB): ${t.error}`, 'error', 8000);
                else if (t.tried) {
                    toast(`작품 정보 ${t.found}개를 찾았습니다${t.missing.length ? ` · 못 찾음 ${t.missing.length}개: ${t.missing.slice(0, 5).join(', ')}`
                        + `${t.missing.length > 5 ? ' …' : ''} ([정보 고치기]로 직접 고를 수 있음)` : ''}${t.left ? ` · 남은 ${t.left}개는 다시 누르면 이어서` : ''}`,
                    t.missing.length ? 'info' : 'success', 9000);
                }
            } catch (e) {
                toast(e.message, 'error');
            } finally {
                btn.disabled = false;
                btn.firstChild.classList.remove('fa-spin');
            }
        });
        return [btn];
    }

    async getLibraryItems(channel, { start = 0, size = 60, sort = 'latest', kind = '', q = '' } = {}) {
        if (this._isMusic(channel) && kind === 'artist') {   // 음악: 아티스트 (이름 순, 한 번에)
            if (start > 0) return { items: [], total: start };
            const data = await api(`${this.base}/artists/${channel}?${new URLSearchParams({ q })}`);
            const items = (data.items || []).map((a) => this._artistItem(channel, a));
            return { items, total: items.length };
        }
        const music = this._isMusic(channel);
        if (music && kind === 'track') kind = 'audio';   // 음악: 곡 하나하나 (앨범으로 묶지 않음)
        // (정렬 값: 추가된 날짜 added:desc/asc -> 서버의 latest/oldest, 제목 -> title. 예전 값도 그대로 받는다)
        sort = { 'added:desc': 'latest', 'added:asc': 'oldest', added: 'latest' }[sort] || sort;
        const p = new URLSearchParams({ channel, offset: start, limit: size, sort, kind, q, group: music && kind === 'audio' ? 0 : 1 }); // 회차는 시리즈로 묶어서
        const data = await api(`${this.base}/videos?${p}`);
        // 아직 훑지 않은 오래된 메시지가 남았으면 total보다 더 있을 수 있다 -> 한 칸 더 있다고 알려 계속 불러오게
        // 다음 쪽은 서버가 알려 준 위치부터 (나눠 올린 조각을 한 카드로 합쳐 카드 수가 읽은 수보다 적다 -
        // 카드 수로 넘기면 앞쪽 영화가 다시 나온다). total·nextStart는 그 위치 기준, 보이는 수는 shown/count
        const next = data.next_offset != null ? data.next_offset : start + data.items.length;
        const total = data.more ? next + 1 : next;
        return { items: data.items.map((v) => this.toItem(v)), total, nextStart: next, shown: data.total, count: data.total, approx: !!data.more };
    }

    seriesCoverUrl(channel, key) {
        return `${this.base}/series-cover/${channel}?${new URLSearchParams({ key })}`;
    }

    /** 사진/웹툰 채널에는 랜덤 재생할 영상이 없다 */
    canRandom(channel) {
        const c = this.channels.find((x) => x.id === String(channel));
        return !(c && (c.kind === 'image' || c.kind === 'webtoon' || c.kind === 'doc' || c.kind === 'music'));
    }

    /**
     * 랜덤 재생할 영상 하나 (채널 하나 또는 libId가 없으면 볼 수 있는 채널 전부, 사진 제외).
     * exclude: 이번 랜덤 재생에서 이미 튼 "pid/id"들. 반환 {pid, id, title} 또는 null
     */
    async randomPick(libId, { unwatched = false, exclude = new Set() } = {}) {
        const mine = [...exclude].filter((k) => k.startsWith(`${this.id}/`)).map((k) => k.slice(this.id.length + 1));
        const p = new URLSearchParams({ channel: libId || '', unwatched: unwatched ? 1 : 0, exclude: mine.slice(-40).join(',') });
        const data = await api(`${this.base}/random?${p}`);
        if (!data.item) return null;
        const it = this.toItem(data.item);
        return { pid: this.id, id: it.id, title: it.title };
    }

    async search(query) {
        const data = await api(`${this.base}/videos?${new URLSearchParams({ q: query, limit: 40 })}`);
        const items = data.items.map((v) => this.toItem(v));
        const kindOf = (it) => (it.raw && it.raw.kind) || '';
        const videos = items.filter((it) => it.type !== 'photo' && !['book', 'doc', 'audio'].includes(kindOf(it))), photos = items.filter((it) => it.type === 'photo');
        const books = items.filter((it) => kindOf(it) === 'book'), docs = items.filter((it) => kindOf(it) === 'doc');
        const songs = items.filter((it) => kindOf(it) === 'audio');
        return [
            videos.length ? { title: `영상 · ${this.name}`, items: videos } : null,
            songs.length ? { title: `음악 · ${this.name}`, items: songs } : null,
            books.length ? { title: `웹툰 · ${this.name}`, items: books } : null,
            docs.length ? { title: `책 · ${this.name}`, items: docs } : null,
            photos.length ? { title: `사진 · ${this.name}`, items: photos } : null,
        ].filter(Boolean);
    }

    async _video(id, probe = false) {
        const [channel, msg] = String(id).split(':');
        const data = await api(`${this.base}/video/${channel}/${msg}${probe ? '?probe=1' : ''}`);
        return data.item;
    }

    /**
     * 서버에 이 영상의 형식 확인(ffprobe)을 미리 시켜 둔다. 상세 화면을 열거나 재생 버튼에 마우스를 올리면 불러서,
     * [재생]을 누를 때는 이미 결과가 있게 한다 (텔레그램에서 파일 머리를 받느라 몇 초 걸리는 걸 숨김).
     */
    warm(id, head = false) {
        if (String(id).startsWith('series:') || this._warmed.has(head ? `${id}+` : id)) return;
        if (['book', 'doc'].includes((this._seen.get(String(id)) || {}).kind)) return;   // 영상만 (웹툰·책은 형식 확인 없음)
        this._warmed.add(id);
        if (head) this._warmed.add(`${id}+`);
        const [channel, msg] = String(id).split(':');
        // head: 앞부분 몇 MB도 받아 둔다 (곧 이어서 볼 다음 회차/조각)
        fetch(`${this.base}/probe/${channel}/${msg}${head ? '?head=1' : ''}`, { method: 'POST' }).catch(() => {});
    }

    // ----- 작품 정보 (TMDB, 서버가 찾아 저장) -----
    /** {meta, episodes: {시즌: {회차: {name, overview, still}}}, target} 또는 null (키가 없거나 못 찾음) */
    async _meta(channel, msg, seriesKey = null, seasons = []) {
        try {
            const path = seriesKey != null
                ? `${this.base}/meta/${channel}?${new URLSearchParams({ series: seriesKey, seasons: seasons.join(',') })}`
                : `${this.base}/meta/${channel}/${msg}`;
            return await api(path);
        } catch (e) {
            return null;
        }
    }

    /** 상세 모양에 작품 정보를 입힌다 */
    _withMeta(d, m) {
        if (!m) return d;
        return {
            ...d,
            title: m.title || d.title,
            poster: m.poster || d.poster,
            posterShape: m.poster ? 'poster' : d.posterShape,
            art: m.backdrop || d.art,
            summary: m.overview || d.summary,
            tagline: m.tagline || d.tagline,
            genres: m.genres || [],
            directors: m.directors || [],
            cast: m.cast || [],
            rating: m.rating || null,
            metaLine: [m.year, ...d.metaLine].filter(Boolean),
            // TMDB가 아닌 곳에서 온 정보는 출처를 (위키백과 글은 CC BY-SA - 출처 링크를 보여야 한다)
            metaSource: m.source === 'wikipedia' ? { label: '한국어 위키백과 (CC BY-SA 4.0)', url: m.url || '' }
                : m.source === 'anilist' ? { label: 'AniList', url: m.url || '' } : null,
        };
    }

    /** 관리자: [정보 고치기] - 다른 작품을 직접 찾아 고르거나 정보 없음으로 */
    _fixAction(target, query, kind) {
        if (!registry.isAdmin()) return [];
        return [{ icon: 'wand-magic-sparkles', label: '정보 고치기', title: 'TMDB에서 맞는 작품을 직접 고르기',
            onClick: (ctx) => this._openFix(target, query, kind, ctx) }];
    }

    _openFix(target, query, kind, ctx) {
        const kindSel = h('select', { class: 'select' },
            [['movie', '영화'], ['tv', 'TV 시리즈']].map(([v, label]) => h('option', { value: v, text: label, selected: v === kind })));
        const q = h('input', { class: 'input', value: query || '', placeholder: '작품 이름' });
        const list = h('div', { class: 'meta-results' });
        const choose = async (id, k) => {
            try {
                await api(`${this.base}/meta-fix`, { method: 'POST', json: { ...target, kind: k, tmdb_id: id } });
                m.close();
                ctx.rerender();
            } catch (e) { list.prepend(h('p', { class: 'muted error', text: e.message })); }
        };
        const search = async () => {
            list.replaceChildren(h('p', { class: 'muted', text: '찾는 중...' }));
            try {
                const { results } = await api(`${this.base}/meta-search?${new URLSearchParams({ kind: kindSel.value, q: q.value })}`);
                list.replaceChildren(...(results.length ? results.map((r) => h('button', {
                    class: 'meta-result', type: 'button', on: { click: () => choose(r.id, r.kind) },
                }, r.poster ? h('img', { src: r.poster, alt: '', loading: 'lazy' }) : h('span', { class: 'meta-noimg' }),
                h('span', { class: 'meta-text' },
                    h('b', { text: `${r.title}${r.year ? ` (${r.year})` : ''}` }),
                    r.original && r.original !== r.title ? h('small', { text: r.original }) : null,
                    h('span', { class: 'muted', text: (r.overview || '').slice(0, 120) })))) : [h('p', { class: 'muted', text: '찾은 작품이 없습니다.' })]));
            } catch (e) { list.replaceChildren(h('p', { class: 'muted error', text: e.message })); }
        };
        q.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
        const m = modal('작품 정보 고치기', h('div', { class: 'meta-fix' },
            h('div', { class: 'toolbar' }, kindSel, q, h('button', { class: 'btn btn-accent', on: { click: search } }, '찾기')), list), [
            h('button', { class: 'btn', on: { click: () => choose(0, kindSel.value) } }, '정보 없이 두기'),
            h('button', { class: 'btn', on: { click: () => m.close() } }, '닫기'),
        ]);
        search();
        q.focus();
    }

    /** 시리즈(회차 묶음) 상세: 회차 목록 + [다음 볼 회차] */
    async _seriesDetail(id) {
        const [, channel, ...rest] = String(id).split(':');
        const key = rest.join(':');
        const data = await api(`${this.base}/series/${channel}?${new URLSearchParams({ key })}`);
        const eps = data.episodes || [];
        if (eps.length && eps[0].kind === 'book') return this._bookSeriesDetail(id, channel, data);
        if (eps.length && eps[0].kind === 'audio') return this._albumDetail(id, channel, data);
        const multiSeason = new Set(eps.filter((x) => !x.special).map((x) => x.season)).size > 1;
        const children = eps.map((v) => {
            const it = this.toItem({ ...v, series: undefined });
            return {
                ...it,
                epLabel: epLabel(v, multiSeason),
                raw: { ...v, index: epIndex(v, multiSeason), title: v.title, originallyAvailableAt: (v.date || '').slice(0, 10), duration: (v.duration || 0) * 1000, summary: '' },
            };
        });
        // 다음에 볼 회차: 보던 회차 -> 안 본 첫 회차 -> 첫 회차
        const resumeIdx = eps.findIndex((v) => v.position > 30 && !v.watched);
        const nextIdx = resumeIdx >= 0 ? resumeIdx : Math.max(0, eps.findIndex((v) => !v.watched));
        const deck = children[nextIdx];
        const latest = eps[eps.length - 1] || {};
        const posterOf = (v) => (v.poster ? `${this.base}/thumb/${v.channel}/${v.poster}` : '');
        const poster = posterOf(latest) || [...eps].reverse().map(posterOf).find(Boolean) || '';
        const unwatched = eps.filter((v) => !v.watched).length;
        // 채널 설정이 "최신 회차부터"면 목록만 거꾸로 (다음 회차 이어 재생은 그대로 시간순)
        const ch = this.channels.find((c) => c.id === String(channel));
        if (ch && ch.epOrder === 'desc') reverseRegular(children);
        if (deck) this.warm(deck.id);   // [N화 재생]을 누르기 전에 형식 확인을 미리
        // 작품 정보: 회차 제목·줄거리·장면 사진도
        const mr = await this._meta(channel, null, key, [...new Set(eps.map((v) => v.season))]);
        if (mr && mr.meta) {
            for (const c of children) {
                const info = c.raw.special ? null : ((mr.episodes || {})[String(c.raw.season)] || {})[String(c.raw.ep)];
                if (!info) continue;
                c.raw = { ...c.raw, title: info.name || c.raw.title, summary: info.overview || '' };
                if (info.still) c.wideThumb = info.still;
            }
        }
        return this._withMeta({
            pid: this.id, id, type: 'show', title: data.title, subtitle: '',
            poster: poster || (deck && deck.thumb) || '', posterShape: poster ? 'poster' : 'wide', art: '',
            metaLine: [`${eps.length}화`, unwatched ? `안 본 회차 ${unwatched}` : '다 봄'],
            rating: null, summary: '', tagline: '', genres: [], directors: [], cast: [],
            playable: false, unwatched, watched: unwatched === 0,
            viewOffset: 0, duration: 0, progress: 0,
            parent: { label: this.channelTitle(channel), route: `#/library/${this.id}/${channel}` },
            children, childrenTitle: '회차', childrenStyle: 'episode',
            onDeck: deck ? { ...deck, subtitle: `${deck.epLabel} ${resumeIdx >= 0 ? '이어보기' : '재생'}` } : null,
            extraActions: this._fixAction({ channel: Number(channel), series: key }, data.title, 'tv'),
        }, mr && mr.meta);
    }

    /** 웹툰 작품(회차 묶음) 상세: 회차 목록 + [이어 보기 / 1화 보기] */
    _bookSeriesDetail(id, channel, data) {
        const eps = data.episodes;
        const multiSeason = new Set(eps.filter((x) => !x.special).map((x) => x.season)).size > 1;
        const children = eps.map((v) => {
            const it = this.toItem({ ...v, series: undefined });
            return {
                ...it,
                epLabel: epLabel(v, multiSeason),
                raw: { ...v, index: epIndex(v, multiSeason), title: v.title,
                    originallyAvailableAt: [(v.date || '').slice(0, 10), v.duration ? `${v.duration}쪽` : ''].filter(Boolean).join(' · '),
                    duration: 0, summary: '' },
            };
        });
        const resumeIdx = eps.findIndex((v) => v.position > 0 && !v.watched);
        const nextIdx = resumeIdx >= 0 ? resumeIdx : Math.max(0, eps.findIndex((v) => !v.watched));
        const deck = children[nextIdx];
        const unread = eps.filter((v) => !v.watched).length;
        const cover = [...children].reverse().map((c) => c.thumb).find(Boolean) || '';
        const ch = this.channels.find((c) => c.id === String(channel));
        if (ch && ch.epOrder === 'desc') reverseRegular(children);
        // 채널에 올린 작품 정보 파일(웹툰명.yaml): 작가·장르·연재처·상태·줄거리·표지 등
        const fm = data.file_meta && !data.file_meta.error ? data.file_meta : null;
        const metaCover = fm && fm.cover ? this.seriesCoverUrl(channel, data.key) : '';
        const facts = fm ? [
            ['작가', (fm.authors || []).join(', ')], ['연재처', fm.platform], ['상태', fm.status], ['연재 요일', fm.day],
            ['연령', fm.age], ['연재 시작', fm.year], ['언어', fm.language], ['태그', (fm.tags || []).join(', ')], ['링크', fm.url],
            ...(fm.extra || []),
        ].filter(([, v]) => v) : [];
        if (data.file_meta && data.file_meta.error) facts.push(['작품 정보 파일', data.file_meta.error]);
        return {
            pid: this.id, id, type: 'show', title: (fm && fm.title) || data.title, subtitle: '',
            poster: metaCover || cover, posterShape: 'poster', art: '',
            metaLine: ['웹툰', fm && fm.status, fm && fm.platform, `${eps.length}화`, unread ? `안 본 회차 ${unread}` : '다 봄'].filter(Boolean),
            rating: null, summary: (fm && fm.summary) || '', tagline: '', genres: (fm && fm.genres) || [], directors: [], cast: [],
            extraFacts: facts,
            playable: false, unwatched: unread, watched: unread === 0,
            viewOffset: 0, duration: 0, progress: 0,
            parent: { label: this.channelTitle(channel), route: `#/library/${this.id}/${channel}` },
            children, childrenTitle: '회차', childrenStyle: 'episode',
            onDeck: deck ? { ...deck, subtitle: `${deck.epLabel} ${resumeIdx >= 0 ? '이어 보기' : '보기'}` } : null,
        };
    }

    /** 앨범 상세: 트랙 목록(디스크·트랙 순) + [재생] (끝나면 다음 트랙). 정보는 곡 캡션에서 */
    _albumDetail(id, channel, data) {
        const eps = data.episodes;
        const infos = eps.map((v) => musicInfo(v));
        const multiDisc = new Set(eps.map((v) => v.season)).size > 1;
        const children = eps.map((v, i) => {
            const it = this.toItem({ ...v, series: undefined });
            const no = v.ep < 1000 ? v.ep : '';
            return { ...it, raw: { ...v, index: no ? (multiDisc ? `${v.season}-${no}` : no) : i + 1, title: infos[i].title,
                duration: (v.duration || 0) * 1000 } };
        });
        const first = infos.find((x) => x.album_artist || x.artist) || {};
        const artist = first.album_artist || first.artist || '';
        const pick = (f) => infos.map((x) => x[f]).find(Boolean) || '';
        const uniq = (f) => [...new Set(infos.map((x) => x[f]).filter(Boolean))].join(', ');
        const total = eps.reduce((a, v) => a + (v.duration || 0), 0);
        const cover = children.map((c) => c.thumb).find(Boolean) || '';
        return {
            pid: this.id, id, type: 'album', title: data.title, subtitle: artist,
            poster: cover, posterShape: 'square', art: '',
            metaLine: [artist, pick('year'), `${eps.length}곡`, total ? fmtDuration(total * 1000) : ''].filter(Boolean),
            rating: null, summary: '', tagline: '', genres: pick('genre') ? [pick('genre')] : [], directors: [], cast: [],
            extraFacts: [['작곡', uniq('composer')], ['작사', uniq('lyricist')], ['편곡', uniq('arranger')]].filter(([, v]) => v),
            playable: false, unwatched: 0, watched: false,
            viewOffset: 0, duration: 0, progress: 0,
            parent: { label: this.channelTitle(channel), route: `#/library/${this.id}/${channel}` },
            children, childrenTitle: '트랙', childrenStyle: 'track',
            onDeck: children[0] ? { ...children[0], subtitle: '재생' } : null,
            extraActions: [{ icon: 'shuffle', label: '섞어 듣기', title: '이 앨범을 섞어서', onClick: async () => {
                const queue = children.map((c) => ({ pid: this.id, id: c.id, title: c.raw.title }));
                if (!queue.length) return;
                const { player } = await import('../player.js');
                if (!player.music.shuffle) player.toggleMusicShuffle();
                player.playItem(this, queue[Math.floor(Math.random() * queue.length)].id, { queue, fromStart: true });
            } }, ...(artist ? [{ icon: 'user', label: artist, title: '이 아티스트의 앨범·곡', onClick: () => {
                location.hash = `#/item/${this.id}/${encodeURIComponent(`artist:${channel}:${artist}`)}`;
            } }] : [])],
        };
    }

    // ----- 웹툰 보기 (reader.js) -----
    /** 재생기 대신 이 소스가 직접 여는 항목이면 열고 true (웹툰 한 화 / 웹툰 작품 -> 다음 볼 회차) */
    async openItem(id) {
        const v = this._seen.get(String(id));
        if (v && v.kind === 'doc') { this.readDoc(id); return true; }
        if (!v || v.kind !== 'book') return false;
        const target = String(id).startsWith('series:') ? await this._seriesNext(id) : id;
        if (target) this.read(target);
        return true;
    }

    read(id) {
        openReader({
            load: (bid, onProgress, cancelled) => this._loadBook(bid, onProgress, cancelled),
            prepare: (bid) => {
                const [c, m] = String(bid).split(':');
                return fetch(`${this.base}/book/${c}/${m}?warm=1`);
            },
            save: (bid, position, total, done) => {
                const [channel, msg] = String(bid).split(':');
                const v = this._seen.get(String(bid));
                if (v) Object.assign(v, { position: done ? 0 : position, duration: total, watched: done || v.watched });
                return api(`${this.base}/progress`, { method: 'POST', json: { channel, msg, position, duration: total, ...(done ? { watched: true } : {}) } });
            },
            // 닫으면 회차 목록 화면의 진행 표시를 새로 (긴 목록은 스크롤 위치를 잃지 않게 그대로)
            onClose: () => { if (location.hash.startsWith('#/item/')) window.dispatchEvent(new HashChangeEvent('hashchange')); },
        }, id);
    }

    /** 책 (PDF / EPUB). 파일은 영상과 같은 주소에서 Range로 받는다 */
    readDoc(id) {
        const [channel, msg] = String(id).split(':');
        const v = this._seen.get(String(id)) || {};
        openDocReader({
            id: `${this.id}:${id}`,
            url: `${this.base}/stream/${channel}/${msg}?doc=1`,   // doc=1: 재생 현황(/api/activity)에서 빼게
            format: docFormat(v),
            title: cleanBookTitle(v.title || ''),
            sub: this.channelTitle(channel),
            position: v.watched ? 0 : (v.position || 0),
            save: (position, total, done) => {
                Object.assign(v, { position: done ? 0 : position, duration: total, watched: done || v.watched });
                return api(`${this.base}/progress`, { method: 'POST', json: { channel, msg, position, duration: total, ...(done ? { watched: true } : {}) } });
            },
            onClose: () => { if (location.hash.startsWith('#/item/') || location.hash.startsWith('#/library/')) window.dispatchEvent(new HashChangeEvent('hashchange')); },
        });
    }

    /** 압축 파일을 서버가 받을 때까지 기다렸다가 쪽 주소들 */
    async _loadBook(id, onProgress, cancelled) {
        const [channel, msg] = String(id).split(':');
        let data;
        for (;;) {
            data = await api(`${this.base}/book/${channel}/${msg}`);
            if (data.ready || cancelled()) break;
            onProgress(data.progress || 0);
            await new Promise((r) => setTimeout(r, 1000));
        }
        if (cancelled()) return null;
        const v = data.item;
        this._seen.set(String(id), v);
        const s = data.series;
        const ref = (x) => (x ? { id: `${channel}:${x.msg}`, title: x.title } : null);
        if (s) for (const x of [s.prev, s.next]) if (x) this._seen.set(`${channel}:${x.msg}`, { ...x, kind: 'book', channel: Number(channel) });
        return {
            id,
            title: v.title,
            sub: [s ? `${s.title} · ${s.special ? (s.ep ? `특별 ${s.ep}` : '특별') : `${s.ep}화`}` : this.channelTitle(channel), `${data.pages.length}쪽`].join(' · '),
            pages: data.pages.map((_n, i) => `${this.base}/book/${channel}/${msg}/${i}`),
            position: v.watched ? 0 : (v.position || 0),
            series: !!s,
            prev: ref(s && s.prev),
            next: ref(s && s.next),
        };
    }

    async _seriesNext(id) {
        const d = await this._seriesDetail(id);
        if (d.type === 'album') return (d.children[0] && d.children[0].id) || null;   // 앨범은 늘 첫 곡부터
        return (d.onDeck && d.onDeck.id) || (d.children[0] && d.children[0].id) || null;
    }

    /** 곡 재생: 원본 그대로 (브라우저가 mp3·flac·m4a·ogg를 직접), 오디오 화면(앨범 아트), 끝나면 앨범의 다음 곡.
     *  음악은 곡 중간부터 이어 듣지 않는다 (처음부터) */
    async _audioPlayback(id, startAt = null, opts = {}) {
        const v = await this._video(id);
        const [channel, msg] = String(id).split(':');
        const mi = musicInfo(v);
        // 곡 목록(섞어 듣기·반복·이전/다음 곡): 넘겨받은 목록(아티스트 전체 재생 등)에 이 곡이 있으면 그것, 아니면 이 곡의 앨범
        let queue = Array.isArray(opts.queue) && opts.queue.some((x) => x.id === id) ? opts.queue : null;
        if (!queue && v.series && v.series.key) {
            try {
                const al = await api(`${this.base}/series/${channel}?${new URLSearchParams({ key: v.series.key })}`);
                queue = (al.episodes || []).map((e) => { this._seen.set(`${e.channel}:${e.msg}`, e); return { pid: this.id, id: `${e.channel}:${e.msg}`, title: musicInfo(e).title }; });
            } catch (e) { queue = null; }
        }
        const report = (position, duration) => fetch(`${this.base}/progress`, {
            method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel, msg, position, duration: duration || v.duration }),
        }).catch(() => {});
        const n = v.series && v.series.next;
        return {
            live: false, audioOnly: true,
            title: mi.title, subtitle: [mi.artist, mi.album].filter(Boolean).join(' · '),
            poster: v.has_thumb ? `${this.base}/thumb/${channel}/${msg}` : '',
            durationHint: v.duration || 0,
            url: `${this.base}/stream/${channel}/${msg}`, kind: 'direct', startTime: startAt || 0,
            onProgress: (state, sec) => report(sec), onStop: (sec) => report(sec),
            getNext: n && !queue ? async () => ({ pid: this.id, id: `${channel}:${n.msg}`, title: n.title }) : null,
            queue: queue && queue.length ? queue : null, queueIndex: queue ? Math.max(0, queue.findIndex((x) => x.id === id)) : 0,
            explainError: () => api(`${this.base}/play-error/${channel}/${msg}`).then((j) => j.error || '').catch(() => ''),
        };
    }

    // ----- 음악 채널: 보기 (앨범 / 아티스트 / 곡) -----
    _isMusic(channel) {
        const c = this.channels.find((x) => x.id === String(channel));
        return !!(c && c.kind === 'music');
    }

    /** 아티스트 카드 (누르면 그 아티스트의 앨범·곡) */
    _artistItem(channel, a) {
        const id = `artist:${channel}:${a.name}`;
        const cover = a.cover ? `${this.base}/thumb/${channel}/${a.cover}` : '';
        return {
            pid: this.id, id, type: 'show', title: a.name,
            subtitle: [a.albums ? `앨범 ${a.albums}` : '', `${a.tracks}곡`].filter(Boolean).join(' · '),
            thumb: cover, wideThumb: cover, shape: 'square', cardClass: 'card-artist',
            route: `#/item/${this.id}/${encodeURIComponent(id)}`, raw: a,
        };
    }

    /** 아티스트 상세: 앨범 카드들 + [전체 재생]·[섞어 듣기] (곡 목록 = 그 아티스트의 모든 곡, 앨범·트랙 순) */
    async _artistDetail(id) {
        const [, channel, ...rest] = String(id).split(':');
        const name = rest.join(':');
        const data = await api(`${this.base}/artists/${channel}?${new URLSearchParams({ name })}`);
        const tracks = data.tracks || [];
        tracks.forEach((t) => this._seen.set(`${t.channel}:${t.msg}`, t));
        const queue = tracks.map((t) => ({ pid: this.id, id: `${t.channel}:${t.msg}`, title: musicInfo(t).title }));
        const albums = (data.albums || []).map((v) => this.toItem(v));
        const singles = tracks.filter((t) => !t.series && t.ep == null).map((t) => this.toItem(t));
        const cover = albums.map((a) => a.thumb).find(Boolean) || tracks.map((t) => (t.has_thumb ? `${this.base}/thumb/${t.channel}/${t.msg}` : '')).find(Boolean) || '';
        const total = tracks.reduce((a, t) => a + (t.duration || 0), 0);
        const play = (shuffle) => async () => {
            if (!queue.length) return;
            const { player } = await import('../player.js');
            if (!!player.music.shuffle !== shuffle) player.toggleMusicShuffle();
            const first = shuffle ? queue[Math.floor(Math.random() * queue.length)] : queue[0];
            player.playItem(this, first.id, { queue, fromStart: true });
        };
        return {
            pid: this.id, id, type: 'artist', title: data.name, subtitle: '',
            poster: cover, posterShape: 'square', art: '',
            metaLine: [albums.length ? `앨범 ${albums.length}` : '', `${tracks.length}곡`, total ? fmtDuration(total * 1000) : ''].filter(Boolean),
            rating: null, summary: '', tagline: '', genres: [], directors: [], cast: [],
            playable: false, unwatched: 0, watched: false, viewOffset: 0, duration: 0, progress: 0,
            parent: { label: this.channelTitle(channel), route: `#/library/${this.id}/${channel}` },
            children: [...albums, ...singles], childrenTitle: albums.length ? '앨범 · 곡' : '곡',
            extraActions: [
                { icon: 'play', label: '전체 재생', title: '이 아티스트의 모든 곡 (앨범·트랙 순)', onClick: play(false) },
                { icon: 'shuffle', label: '섞어 듣기', title: '이 아티스트의 모든 곡을 섞어서', onClick: play(true) },
            ],
        };
    }

    /** 곡 하나의 상세 (앨범이 있으면 위쪽 링크는 그 앨범) */
    async _trackDetail(id) {
        const v = await this._video(id);
        const mi = musicInfo(v);
        const it = this.toItem({ ...v, series: undefined });
        const facts = [['앨범', mi.album], ['앨범 아티스트', mi.album_artist !== mi.artist ? mi.album_artist : ''],
            ['트랙', mi.track ? `${mi.track}${mi.track_total ? `/${mi.track_total}` : ''}` : ''], ['작곡', mi.composer],
            ['작사', mi.lyricist], ['편곡', mi.arranger], ['파일', v.file_name]].filter(([, x]) => x);
        return {
            pid: this.id, id, type: 'movie', title: mi.title, subtitle: mi.artist || '',
            poster: it.thumb, posterShape: 'square', art: '',
            metaLine: [mi.artist, mi.year, v.duration ? fmtClockSec(v.duration) : '', fmtSize(v.size)].filter(Boolean),
            rating: null, summary: '', tagline: '', genres: mi.genre ? [mi.genre] : [], directors: [], cast: [], extraFacts: facts,
            playable: true, watched: !!v.watched, viewOffset: 0, duration: (v.duration || 0) * 1000, progress: 0,
            parent: v.series ? { label: v.series.title, route: `#/item/${this.id}/${encodeURIComponent(`series:${v.channel}:${v.series.key}`)}` }
                : { label: this.channelTitle(v.channel), route: `#/library/${this.id}/${v.channel}` },
            children: [],
        };
    }

    async getDetail(id) {
        if (String(id).startsWith('series:')) return this._seriesDetail(id);
        if (String(id).startsWith('artist:')) return this._artistDetail(id);
        const seenV = this._seen.get(String(id));
        if (seenV && seenV.kind === 'audio') return this._trackDetail(id);

        this.warm(id);
        const v = await this._video(id);
        // 회차라도 이 화면은 그 회차 하나 (series를 넘기면 시리즈 카드가 되어 [재생]이 다른 회차를 틀었다)
        const item = this.toItem({ ...v, series: undefined });
        const metaLine = [(v.date || '').slice(0, 10), v.duration ? fmtDuration(v.duration * 1000) : '',
            v.height ? `${v.height}p` : '', fmtSize(v.size), v.subs && v.subs.length ? `자막 ${v.subs.length}개` : ''].filter(Boolean);
        const facts = this.channelTitle(v.channel);
        const mr = await this._meta(v.channel, v.msg);
        const m = mr && mr.meta;
        // 시리즈 회차면 위쪽 링크는 그 시리즈로, 회차 제목·줄거리도
        const t = mr && mr.target;
        const epInfo = m && t && t.season != null ? ((mr.episodes || {})[String(t.season)] || {})[String(t.ep)] : null;
        const parent = v.series
            ? { label: v.series.title, route: `#/item/${this.id}/${encodeURIComponent(`series:${v.channel}:${v.series.key}`)}` }
            : (facts ? { label: facts, route: `#/library/${this.id}/${v.channel}` } : null);
        const base = {
            ...item,
            subtitle: '',
            poster: item.posterImg || item.thumb,
            posterShape: item.posterImg ? 'poster' : 'wide',
            art: '',
            metaLine,
            rating: null,
            // 제목이 캡션 첫 줄에서 왔으면 설명에서는 그 줄을 뺀다
            summary: (v.caption || '').split('\n')[0].trim() === v.title
                ? (v.caption || '').split('\n').slice(1).join('\n').trim() : (v.caption || ''),
            tagline: '',
            genres: [],
            directors: [],
            cast: [],
            playable: true,
            viewOffset: (v.position || 0) * 1000,
            duration: (v.duration || 0) * 1000,
            parent,
            children: [],
            onDeck: null,
            extraActions: this._fixAction({ channel: v.channel, msg: v.msg }, t && t.kind === 'tv' && v.series ? v.series.title : (m ? m.title : item.title),
                t ? t.kind : 'movie'),
        };
        if (!m) return this._withParts({ ...base, extraFacts: [['파일', v.file_name || v.title]] }, v);
        const d = this._withMeta(base, m);
        if (t && t.season != null) {
            d.metaLine = [`시즌 ${t.season} · ${t.ep}화${epInfo && epInfo.name ? ` ${epInfo.name}` : ''}`, ...d.metaLine];
            if (epInfo && epInfo.overview) d.summary = epInfo.overview;
        }
        d.extraFacts = [['파일', v.file_name || v.title]];
        return this._withParts(d, v);
    }

    /** 나눠 올린 영상: [재생]은 이어 볼 조각으로, 아래에 조각마다 "(1/3) 재생" 줄 */
    _withParts(d, v) {
        const parts = v.parts || [];
        if (parts.length < 2) return d;
        const n = parts.length;
        const cur = resumePart(parts);
        const ci = parts.indexOf(cur);
        d.id = `${v.channel}:${cur.msg}`;
        d.viewOffset = (cur.position || 0) * 1000;
        d.duration = (cur.duration || 0) * 1000;
        d.watched = parts.every((p) => p.watched);
        // 위 줄의 상영 시간·크기는 조각 하나 것 -> 전체 (조각을 모두 더한 값)
        const totDur = parts.reduce((a, p) => a + (p.duration || 0), 0);
        const totSize = parts.reduce((a, p) => a + (p.size || 0), 0);
        const one = { [v.duration ? fmtDuration(v.duration * 1000) : '-']: totDur ? `${fmtDuration(totDur * 1000)} (전체)` : '',
            [v.size ? fmtSize(v.size) : '-']: totSize ? fmtSize(totSize) : '' };
        d.metaLine = (d.metaLine || []).map((x) => (x in one && one[x] ? one[x] : x));
        d.metaLine = [...d.metaLine, `${n}개 파일 · 이어 볼 곳 (${ci + 1}/${n})`];
        d.childrenStyle = 'track';
        d.childrenTitle = `파일 ${n}개 - 골라서 재생`;
        d.children = parts.map((p, i) => ({
            id: `${v.channel}:${p.msg}${EXACT}`,
            title: `(${i + 1}/${n}) ${p.title || '재생'}`,
            raw: {
                index: `(${i + 1}/${n})`,
                title: `${p.title || '재생'}${p.watched ? ' · 다 봄' : p.position > 30 ? ` · ${fmtDuration(p.position * 1000)}까지 봄` : ''}${i === ci ? ' · 이어 볼 곳' : ''}`,
                duration: (p.duration || 0) * 1000,
            },
        }));
        d.extraFacts = [];   // 파일 이름은 아래 '골라서 재생' 목록에서 (두 곳에 같은 것을 보이지 않게)
        return d;
    }

    async setWatched(id, watched) {
        if (String(id).startsWith('series:')) {
            // 시리즈 전체를 봤음/안 봄으로
            const d = await this._seriesDetail(id);
            for (const ep of d.children) await this.setWatched(ep.id, watched);
            return;
        }
        const [channel, msg] = plainId(id).split(':');
        let msgs = [msg];
        try {   // 나눠 올린 영상은 조각 전부를
            const v = await this._video(plainId(id));
            if ((v.parts || []).length > 1) msgs = v.parts.map((p) => p.msg);
        } catch (e) { /* 그 조각만 */ }
        for (const m of msgs) await api(`${this.base}/progress`, { method: 'POST', json: { channel, msg: m, position: 0, duration: 0, watched } });
    }

    /**
     * 재생 직전 확인. 여러 파일로 나눠 올린 영상의 두 번째 이후 조각을 누르면 첫 조각부터 볼지 묻는다.
     * 반환: 재생할 항목 id (이 조각 또는 첫 조각), 취소하면 null.
     * 묻지 않는 경우: 조각 이름이 아님 / 다음 조각 자동 재생 / 이 조각을 보던 중 / 앞 조각을 다 봤음
     */
    async confirmPlay(id, opts = {}) {
        // 시리즈 카드의 [재생] 등 시리즈 아이디로 오면 다음 볼 회차(보던 회차 -> 안 본 첫 회차)로 바꿔서
        if (String(id).startsWith('series:')) return this._seriesNext(id);
        if (String(id).endsWith(EXACT)) return plainId(id);   // 조각 목록에서 고른 조각은 그대로
        if (opts.auto) return id;
        const seen = this._seen.get(id);
        if (seen && !looksLikePart(seen) && !(seen.parts_count > 1)) return id;
        let v;
        try { v = await this._video(id); } catch (e) { return id; } // 재생 단계에서 오류를 보여 준다
        const parts = v.parts || [];
        const idx = parts.findIndex((p) => p.msg === v.msg);
        // 목록 카드(첫 조각)의 [재생]: 보다 만 조각이나 다 보지 않은 첫 조각부터 (예전엔 첫 조각을 처음부터 다시 틀었다)
        if (idx === 0 && parts.length > 1 && !opts.fromStart) {
            const cur = resumePart(parts);
            if (cur.msg !== v.msg) return `${v.channel}:${cur.msg}`;
        }
        // [처음부터]: 나눠 올린 영상은 첫 조각의 처음부터
        if (opts.fromStart && idx > 0) return `${v.channel}:${parts[0].msg}`;
        if (idx <= 0) return id;
        const resumable = v.position > 30 && (!v.duration || v.position < v.duration * 0.95);
        if (resumable && !opts.fromStart) return id;
        if (parts[idx - 1].watched) return id;
        const first = parts[0];
        return new Promise((resolve) => {
            let answer = null;
            const pick = (val) => { answer = val; m.close(); };
            const body = h('div', { class: 'part-ask' },
                h('p', { text: `이 영상은 ${parts.length}개로 나눠진 파일 중 ${nth(idx + 1)} 파일입니다. 이 파일을 재생할까요?` }),
                h('ol', { class: 'part-list' }, parts.map((p, i) => h('li', { class: i === idx ? 'cur' : '' },
                    h('span', { text: p.title }),
                    p.watched ? h('small', { text: ' · 다 봄' }) : null))));
            const m = modal('나눠진 파일', body, [
                h('button', { class: 'btn', on: { click: () => pick(null) } }, '취소'),
                h('button', { class: 'btn', on: { click: () => pick(`${v.channel}:${first.msg}`) } }, '첫 번째 파일부터'),
                h('button', { class: 'btn btn-accent', on: { click: () => pick(id) } }, `${nth(idx + 1)} 파일 재생`),
            ]);
            // 바깥을 누르거나 X로 닫아도 끝나게 (닫힌 뒤 결과를 넘긴다)
            new MutationObserver((_, obs) => {
                if (!m.el.isConnected) { obs.disconnect(); resolve(answer); }
            }).observe(document.body, { childList: true });
            m.el.querySelector('.btn-accent').focus();
        });
    }

    /**
     * 서버가 ffprobe로 형식을 보고 정한 방법(v.play.mode)대로:
     *   direct  -> 원본 파일 Range 스트림 (탐색도 브라우저가). 그래도 못 열면 fallback으로 서버 변환
     *   그 밖   -> /transcode (ffmpeg가 만든 조각난 MP4). timeOffset초부터 시작하는 스트림이라
     *              탐색하면 재생기가 reloadAt(초)으로 그 위치부터 새로 받는다
     * opts.startAt  이 위치(초)부터 (탐색/다시 시도)
     */
    async getPlayback(id, { fromStart = false, startAt = null } = {}) {
        if (String(id).startsWith('series:')) id = await this._seriesNext(id);   // (confirmPlay를 거치지 않은 경우에도)
        id = plainId(id);
        const seenV = this._seen.get(id) || (String(id).includes(':') ? await this._video(id).catch(() => null) : null);
        if (seenV && seenV.kind === 'audio') return this._audioPlayback(id, startAt, arguments[1] || {});
        const v = await this._video(id, true);
        const [channel, msg] = String(id).split(':');
        const play = v.play || { mode: 'direct' };
        if (IS_SAFARI) play.mode = safariMode(play, v.file_name || '');
        const resumable = v.position > 30 && (!v.duration || v.position < v.duration * 0.95);
        const begin = startAt != null ? startAt : (!fromStart && resumable ? v.position : 0);
        const report = (position, duration) => fetch(`${this.base}/progress`, {
            method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel, msg, position, duration: duration || v.duration }),
        }).catch(() => {});
        const common = {
            live: false,
            title: v.title,
            subtitle: this.channelTitle(channel),
            poster: v.has_thumb ? `${this.base}/thumb/${channel}/${msg}` : '',
            durationHint: v.duration || 0,
            onProgress: (state, sec) => report(sec),
            onStop: (sec) => report(sec),
            getNext: null,
            // 재생 주소가 서버 오류로 끝나면(텔레그램에서 못 받음, 변환 실패 등) 그 이유를 물어본다
            explainError: () => api(`${this.base}/play-error/${channel}/${msg}`).then((j) => j.error || '').catch(() => ''),
        };
        // 자막 파일: 고른 것은 영상마다 기억 (처음엔 한국어, 없으면 첫 자막). 서버 변환 스트림은 요청 위치가 0초라
        // 자막 시각도 그만큼 당겨 받는다 (shift)
        // 영상 안 자막(소프트 자막, 서버가 ffprobe로 본 글자 자막 트랙)도 고를 수 있게 - 한국어 먼저.
        // 처음 켜는 자막: 한국어 자막 파일 > 영상 안 한국어 자막 > 첫 자막 파일 (영상 안 다른 언어는 골라야 켬)
        const files = (v.subs || []).map(subInfo);
        const vmsg = v.msg || msg;
        const inner = ((play.subs) || []).map(innerSubInfo);
        const subs = [...files, ...inner];
        const subKey = `yp_tg_sub_${channel}:${msg}`;
        let activeSub = storeGet(subKey, null);
        if (activeSub == null || (activeSub !== '0' && !subs.some((x) => x.id === activeSub))) {
            const first = files.find((x) => x.lang === 'ko') || inner.find((x) => x.lang === 'ko' && !x.forced) || inner.find((x) => x.lang === 'ko') || files[0];
            activeSub = first ? first.id : '0';
        }
        const trackFor = (shift) => {
            const st = subs.find((x) => x.id === activeSub);
            if (st && st.inner != null) {
                return { id: st.id, inner: true, src: `${this.base}/isub/${channel}/${vmsg}/${st.inner}`, shift: Math.floor(shift || 0), lang: st.lang, label: st.label };
            }
            return st ? { id: st.id, src: `${this.base}/sub/${channel}/${st.msg}?shift=${Math.floor(shift || 0)}`, lang: st.lang, label: st.label } : null;
        };
        const withSubs = (pb, shift) => (subs.length ? {
            ...pb,
            subtitles: subs,
            activeSubtitle: activeSub,
            textTrack: trackFor(shift),
            selectSubtitle: async (id) => {
                activeSub = id;
                storeSet(subKey, id);
                return { track: trackFor(shift) };
            },
        } : pb);
        // 나눠진 파일이면 끝난 뒤 다음 조각을 이어서 재생
        const parts = v.parts || [];
        const nextPart = parts[parts.findIndex((p) => p.msg === v.msg) + 1];
        if (parts.length && nextPart && parts.some((p) => p.msg === v.msg)) {
            common.getNext = async () => ({ pid: this.id, id: `${channel}:${nextPart.msg}`, title: nextPart.title });
            common.prepareNext = () => this.warm(`${channel}:${nextPart.msg}`, true);
            common.nextIsPart = true; // 랜덤 재생 중에도 다음 조각은 이어서
        } else if (v.series && v.series.next) {
            // 시리즈 회차면 끝난 뒤 다음 회차를
            const n = v.series.next;
            common.getNext = async () => ({ pid: this.id, id: `${channel}:${n.msg}`, title: n.title });
            common.prepareNext = () => this.warm(`${channel}:${n.msg}`, true);
        }
        const streamed = (at, mode) => withSubs({
            ...common,
            url: `${this.base}/transcode/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode, ...(dataSaver() ? { maxh: 720 } : {}) })}`,
            kind: 'direct',
            startTime: 0,
            timeOffset: Math.floor(at),
            engineNote: TRANSCODE_NOTE[mode] || '서버 변환',
            reloadAt: async (sec) => streamed(sec, mode),
        }, at);
        // Safari: 서버가 HLS 조각을 만들기 시작하고 첫 조각이 나오면 재생목록 주소를 준다
        const viaHls = async (at, mode) => {
            const j = await api(`${this.base}/hls/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode, ...(dataSaver() ? { maxh: 720 } : {}) })}`,
                { method: 'POST' });
            return withSubs({
                ...common,
                url: j.url,
                kind: 'hls',
                startTime: 0,
                timeOffset: Math.floor(at),
                engineNote: `${TRANSCODE_NOTE[j.mode] || '서버 변환'} · HLS`,
                reloadAt: async (sec) => viaHls(sec, mode),
            }, at);
        };
        const converted = (at, mode) => (IS_SAFARI ? viaHls(at, mode) : streamed(at, mode));
        // Safari(HLS로 보던 것): 그릇만 바꾸면 되는 영상(MKV 안의 H.264, HEVC 등)은 서버가 MP4 파일로 바꿔 두고 원본처럼 튼다 -
        // HLS보다 매끄럽고 탐색도 브라우저가 한다. 데이터 절약(720p로 줄이기) 중이면 예전처럼
        if (play.mode !== 'direct' && IS_SAFARI && !dataSaver()) {
            const mp4 = await prepareMp4(`/api/mp4/tg/${encodeURIComponent(this.id)}/${channel}/${msg}`);
            if (mp4) {
                return withSubs({
                    ...common, url: mp4.url, kind: 'direct', startTime: begin,
                    engineNote: `MP4로 바꿔 재생 (${{ remux: '포장만', audio: '음성 AAC', hevc: 'HEVC 그대로' }[mp4.mode] || '다시 압축 안 함'})`,
                    fallback: async (at) => viaHls(at || begin, play.mode),
                }, 0);
            }
        }
        if (play.mode !== 'direct') return converted(begin, play.mode);
        return withSubs({
            ...common,
            url: `${this.base}/stream/${channel}/${msg}`,
            kind: 'direct',
            startTime: begin,
            // 형식을 몰랐거나(ffprobe 실패) 판단이 틀려 브라우저가 못 열면 다시 압축해서
            fallback: play.available ? async (at) => converted(at || begin, 'transcode') : null,
        }, 0);
    }
}
