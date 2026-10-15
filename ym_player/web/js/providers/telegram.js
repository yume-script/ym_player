// 텔레그램 채널 소스. 서버(/api/tg/{id}/...)가 Telethon으로 채널 영상을 색인하고 스트림을 이어 준다.
// 설정에서 고른 채널 하나하나가 라이브러리처럼 보인다. 항목 id는 "채널id:메시지id".
import { api, fmtDuration, h, modal, IS_SAFARI } from '../util.js';

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

export class TelegramProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'telegram';
        this.channels = (src.channels || []).map((c) => ({ id: String(c.id), title: c.title }));
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
        const thumb = v.has_thumb ? `${this.base}/thumb/${v.channel}/${v.msg}` : '';
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
            raw: v,
        };
    }

    async getLibraries() {
        return this.channels.map((c) => ({ id: c.id, title: c.title, type: 'telegram', icon: 'paper-plane' }));
    }

    async getHomeHubs() {
        const data = await api(`${this.base}/home`);
        const hubs = [];
        if (data.continue && data.continue.length) {
            hubs.push({ title: '이어보기', source: this.name, continue: true, items: data.continue.map((v) => this.toItem(v)) });
        }
        for (const row of data.channels || []) {
            if (!row.items.length) continue;
            hubs.push({
                title: `${this.channelTitle(row.channel) || '채널'} 최신 영상`, source: this.name,
                items: row.items.map((v) => this.toItem(v)), more: `#/library/${this.id}/${row.channel}`,
            });
        }
        return hubs;
    }

    librarySorts() {
        return [['latest', '최신순'], ['oldest', '오래된 순'], ['title', '제목']];
    }

    /** 라이브러리 화면의 보기 고르기 (영상/사진) */
    libraryKinds() {
        return [['', '전체'], ['video', '영상'], ['image', '사진']];
    }

    async getLibraryItems(channel, { start = 0, size = 60, sort = 'latest', kind = '' } = {}) {
        const p = new URLSearchParams({ channel, offset: start, limit: size, sort, kind });
        const data = await api(`${this.base}/videos?${p}`);
        // 아직 훑지 않은 오래된 메시지가 남았으면 total보다 더 있을 수 있다 -> 한 칸 더 있다고 알려 계속 불러오게
        const total = data.more ? Math.max(data.total, start + data.items.length + 1) : data.total;
        return { items: data.items.map((v) => this.toItem(v)), total, shown: data.total, approx: !!data.more };
    }

    async search(query) {
        const data = await api(`${this.base}/videos?${new URLSearchParams({ q: query, limit: 40 })}`);
        const items = data.items.map((v) => this.toItem(v));
        const videos = items.filter((it) => it.type !== 'photo'), photos = items.filter((it) => it.type === 'photo');
        return [
            videos.length ? { title: `영상 · ${this.name}`, items: videos } : null,
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
    warm(id) {
        if (this._warmed.has(id)) return;
        this._warmed.add(id);
        const [channel, msg] = String(id).split(':');
        fetch(`${this.base}/probe/${channel}/${msg}`, { method: 'POST' }).catch(() => {});
    }

    async getDetail(id) {
        this.warm(id);
        const v = await this._video(id);
        const item = this.toItem(v);
        const metaLine = [(v.date || '').slice(0, 10), v.duration ? fmtDuration(v.duration * 1000) : '',
            v.height ? `${v.height}p` : '', fmtSize(v.size)].filter(Boolean);
        const facts = this.channelTitle(v.channel);
        return {
            ...item,
            subtitle: '',
            poster: item.thumb,
            posterShape: 'wide',
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
            parent: facts ? { label: facts, route: `#/library/${this.id}/${v.channel}` } : null,
            children: [],
            onDeck: null,
        };
    }

    async setWatched(id, watched) {
        const [channel, msg] = String(id).split(':');
        await api(`${this.base}/progress`, { method: 'POST', json: { channel, msg, position: 0, duration: 0, watched } });
    }

    /**
     * 재생 직전 확인. 여러 파일로 나눠 올린 영상의 두 번째 이후 조각을 누르면 첫 조각부터 볼지 묻는다.
     * 반환: 재생할 항목 id (이 조각 또는 첫 조각), 취소하면 null.
     * 묻지 않는 경우: 조각 이름이 아님 / 다음 조각 자동 재생 / 이 조각을 보던 중 / 앞 조각을 다 봤음
     */
    async confirmPlay(id, opts = {}) {
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
        // 나눠진 파일이면 끝난 뒤 다음 조각을 이어서 재생
        const parts = v.parts || [];
        const nextPart = parts[parts.findIndex((p) => p.msg === v.msg) + 1];
        if (parts.length && nextPart && parts.some((p) => p.msg === v.msg)) {
            common.getNext = async () => ({ pid: this.id, id: `${channel}:${nextPart.msg}`, title: nextPart.title });
        }
        const streamed = (at, mode) => ({
            ...common,
            url: `${this.base}/transcode/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode })}`,
            kind: 'direct',
            startTime: 0,
            timeOffset: Math.floor(at),
            engineNote: TRANSCODE_NOTE[mode] || '서버 변환',
            reloadAt: async (sec) => streamed(sec, mode),
        });
        // Safari: 서버가 HLS 조각을 만들기 시작하고 첫 조각이 나오면 재생목록 주소를 준다
        const viaHls = async (at, mode) => {
            const j = await api(`${this.base}/hls/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode })}`,
                { method: 'POST' });
            return {
                ...common,
                url: j.url,
                kind: 'hls',
                startTime: 0,
                timeOffset: Math.floor(at),
                engineNote: `${TRANSCODE_NOTE[j.mode] || '서버 변환'} · HLS`,
                reloadAt: async (sec) => viaHls(sec, mode),
            };
        };
        const converted = (at, mode) => (IS_SAFARI ? viaHls(at, mode) : streamed(at, mode));
        if (play.mode !== 'direct') return converted(begin, play.mode);
        return {
            ...common,
            url: `${this.base}/stream/${channel}/${msg}`,
            kind: 'direct',
            startTime: begin,
            // 형식을 몰랐거나(ffprobe 실패) 판단이 틀려 브라우저가 못 열면 다시 압축해서
            fallback: play.available ? async (at) => converted(at || begin, 'transcode') : null,
        };
    }
}
