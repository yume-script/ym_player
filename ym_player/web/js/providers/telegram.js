// 텔레그램 채널 소스. 서버(/api/tg/{id}/...)가 Telethon으로 채널 영상을 색인하고 스트림을 이어 준다.
// 설정에서 고른 채널 하나하나가 라이브러리처럼 보인다. 항목 id는 "채널id:메시지id".
import { api, fmtDuration, h, modal, storeGet, storeSet, IS_SAFARI } from '../util.js';
import { registry } from './index.js';
import { openReader } from '../reader.js';

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
function safariMode(play) {
    if (play.mode !== 'direct' || !play.available || !play.container) return play.mode;
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

function fmtSize(bytes) {
    if (!bytes) return '';
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? `${gb.toFixed(1)}GB` : `${Math.round(bytes / 1024 ** 2)}MB`;
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
        // kind: 관리자가 정한 채널 종류 ("video" 영상만 / "image" 사진만 / "webtoon" 웹툰 압축 파일 / "" 영상+사진)
        // epOrder: 시리즈 회차 목록 순서 ("asc" 1화부터 / "desc" 최신 회차부터)
        this.channels = (src.channels || []).map((c) => ({ id: String(c.id), title: c.title, kind: c.kind || '', epOrder: c.ep_order || 'asc' }));
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
        const posterImg = v.poster ? `${this.base}/thumb/${v.channel}/${v.poster}` : '';
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
        if (v.series) {
            // 시리즈(회차 묶음) 카드: 가장 최근 회차가 대표. 누르면 회차 목록
            const sid = `series:${v.channel}:${v.series.key}`;
            this._seen.set(sid, v);
            const cover = thumb || (v.series.cover ? `${this.base}/thumb/${v.channel}/${v.series.cover}` : '');
            return {
                pid: this.id, id: sid, type: 'show', playable: true, title: v.series.title,
                subtitle: [this.channelTitle(v.channel), `${v.series.count}화`, (v.date || '').slice(0, 10)].filter(Boolean).join(' · '),
                thumb: cover, wideThumb: cover, shape: book ? 'poster' : 'wide', cardClass: book ? 'card-book' : '',
                unwatched: v.series.unwatched, progress: 0,
                route: `#/item/${this.id}/${encodeURIComponent(sid)}`,
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
                raw: v,
            };
        }
        if (book) {
            // 웹툰 한 화 (zip/cbz): 누르면 웹툰 보기로 (reader.js). duration = 쪽 수 (한 번 열어 본 뒤에 앎)
            return {
                pid: this.id, id, type: 'book', playable: true, title: v.title,
                subtitle: [this.channelTitle(v.channel), (v.date || '').slice(0, 10), fmtSize(v.size)].filter(Boolean).join(' · '),
                thumb, wideThumb: thumb, shape: 'poster', cardClass: 'card-book',
                progress: v.duration && v.position ? Math.min(1, v.position / v.duration) : 0,
                watched: !!v.watched,
                onOpen: () => this.read(id),
                ts: v.updated || 0,
                isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
                raw: v,
            };
        }
        const sub = [this.channelTitle(v.channel), (v.date || '').slice(0, 10), v.duration ? fmtDuration(v.duration * 1000) : '']
            .filter(Boolean).join(' · ');
        return {
            pid: this.id,
            id,
            type: 'movie',            // 카드/상세에서 "재생 가능한 단일 영상"으로 다루게
            title: v.title,
            subtitle: sub,
            thumb,
            wideThumb: thumb,
            shape: 'wide',
            progress: v.duration && v.position ? v.position / v.duration : 0,
            watched: !!v.watched,
            route: `#/item/${this.id}/${id}`,
            ts: v.updated || 0,
            isNew: !!(seenBefore() && v.date && v.date > seenBefore() && !v.watched && !(v.position > 0)),
            posterImg,
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
            const webtoon = (this.channels.find((c) => c.id === String(row.channel)) || {}).kind === 'webtoon';
            hubs.push({
                title: `${this.channelTitle(row.channel) || '채널'} ${webtoon ? '최신 웹툰' : '최신 영상'}`, source: this.name,
                items: row.items.map((v) => this.toItem(v)), more: `#/library/${this.id}/${row.channel}`,
            });
        }
        return hubs;
    }

    librarySorts() {
        return [['latest', '최신순'], ['oldest', '오래된 순'], ['title', '제목']];
    }

    /** 라이브러리 화면의 보기 고르기 (영상/사진). 관리자가 채널 종류를 정해 두었으면 고를 것 없음(null) */
    libraryKinds(channel) {
        const c = this.channels.find((x) => x.id === String(channel));
        if (c && c.kind) return null;
        return [['', '전체'], ['video', '영상'], ['image', '사진']];
    }

    async getLibraryItems(channel, { start = 0, size = 60, sort = 'latest', kind = '' } = {}) {
        const p = new URLSearchParams({ channel, offset: start, limit: size, sort, kind, group: 1 }); // 회차는 시리즈로 묶어서
        const data = await api(`${this.base}/videos?${p}`);
        // 아직 훑지 않은 오래된 메시지가 남았으면 total보다 더 있을 수 있다 -> 한 칸 더 있다고 알려 계속 불러오게
        const total = data.more ? Math.max(data.total, start + data.items.length + 1) : data.total;
        return { items: data.items.map((v) => this.toItem(v)), total, shown: data.total, approx: !!data.more };
    }

    /** 사진/웹툰 채널에는 랜덤 재생할 영상이 없다 */
    canRandom(channel) {
        const c = this.channels.find((x) => x.id === String(channel));
        return !(c && (c.kind === 'image' || c.kind === 'webtoon'));
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
        const isBook = (it) => it.raw && it.raw.kind === 'book';
        const videos = items.filter((it) => it.type !== 'photo' && !isBook(it)), photos = items.filter((it) => it.type === 'photo');
        const books = items.filter(isBook);
        return [
            videos.length ? { title: `영상 · ${this.name}`, items: videos } : null,
            books.length ? { title: `웹툰 · ${this.name}`, items: books } : null,
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
        const facts = fm ? [
            ['작가', (fm.authors || []).join(', ')], ['연재처', fm.platform], ['상태', fm.status], ['연재 요일', fm.day],
            ['연령', fm.age], ['연재 시작', fm.year], ['태그', (fm.tags || []).join(', ')], ['링크', fm.url],
            ...(fm.extra || []),
        ].filter(([, v]) => v) : [];
        if (data.file_meta && data.file_meta.error) facts.push(['작품 정보 파일', data.file_meta.error]);
        return {
            pid: this.id, id, type: 'show', title: (fm && fm.title) || data.title, subtitle: '',
            poster: (fm && fm.cover) || cover, posterShape: 'poster', art: '',
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

    // ----- 웹툰 보기 (reader.js) -----
    /** 재생기 대신 이 소스가 직접 여는 항목이면 열고 true (웹툰 한 화 / 웹툰 작품 -> 다음 볼 회차) */
    async openItem(id) {
        const v = this._seen.get(String(id));
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
        return (d.onDeck && d.onDeck.id) || (d.children[0] && d.children[0].id) || null;
    }

    async getDetail(id) {
        if (String(id).startsWith('series:')) return this._seriesDetail(id);

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
        if (!m) return base;
        const d = this._withMeta(base, m);
        if (t && t.season != null) {
            d.metaLine = [`시즌 ${t.season} · ${t.ep}화${epInfo && epInfo.name ? ` ${epInfo.name}` : ''}`, ...d.metaLine];
            if (epInfo && epInfo.overview) d.summary = epInfo.overview;
        }
        d.extraFacts = [['파일', v.file_name || v.title]];
        return d;
    }

    async setWatched(id, watched) {
        if (String(id).startsWith('series:')) {
            // 시리즈 전체를 봤음/안 봄으로
            const d = await this._seriesDetail(id);
            for (const ep of d.children) await this.setWatched(ep.id, watched);
            return;
        }
        const [channel, msg] = String(id).split(':');
        await api(`${this.base}/progress`, { method: 'POST', json: { channel, msg, position: 0, duration: 0, watched } });
    }

    /**
     * 재생 직전 확인. 여러 파일로 나눠 올린 영상의 두 번째 이후 조각을 누르면 첫 조각부터 볼지 묻는다.
     * 반환: 재생할 항목 id (이 조각 또는 첫 조각), 취소하면 null.
     * 묻지 않는 경우: 조각 이름이 아님 / 다음 조각 자동 재생 / 이 조각을 보던 중 / 앞 조각을 다 봤음
     */
    async confirmPlay(id, opts = {}) {
        // 시리즈 카드의 [재생] 등 시리즈 아이디로 오면 다음 볼 회차(보던 회차 -> 안 본 첫 회차)로 바꿔서
        if (String(id).startsWith('series:')) return this._seriesNext(id);
        if (opts.auto) return id;
        const seen = this._seen.get(id);
        if (seen && !looksLikePart(seen)) return id;
        let v;
        try { v = await this._video(id); } catch (e) { return id; } // 재생 단계에서 오류를 보여 준다
        const parts = v.parts || [];
        const idx = parts.findIndex((p) => p.msg === v.msg);
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
        const v = await this._video(id, true);
        const [channel, msg] = String(id).split(':');
        const play = v.play || { mode: 'direct' };
        if (IS_SAFARI) play.mode = safariMode(play);
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
        };
        // 자막 파일: 고른 것은 영상마다 기억 (처음엔 한국어, 없으면 첫 자막). 서버 변환 스트림은 요청 위치가 0초라
        // 자막 시각도 그만큼 당겨 받는다 (shift)
        const subs = (v.subs || []).map(subInfo);
        const subKey = `yp_tg_sub_${channel}:${msg}`;
        let activeSub = storeGet(subKey, null);
        if (activeSub == null || (activeSub !== '0' && !subs.some((x) => x.id === activeSub))) {
            activeSub = subs.length ? (subs.find((x) => x.lang === 'ko') || subs[0]).id : '0';
        }
        const trackFor = (shift) => {
            const st = subs.find((x) => x.id === activeSub);
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
            url: `${this.base}/transcode/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode })}`,
            kind: 'direct',
            startTime: 0,
            timeOffset: Math.floor(at),
            engineNote: TRANSCODE_NOTE[mode] || '서버 변환',
            reloadAt: async (sec) => streamed(sec, mode),
        }, at);
        // Safari: 서버가 HLS 조각을 만들기 시작하고 첫 조각이 나오면 재생목록 주소를 준다
        const viaHls = async (at, mode) => {
            const j = await api(`${this.base}/hls/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode })}`,
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
