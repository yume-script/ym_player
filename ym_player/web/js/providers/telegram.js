// 텔레그램 채널 소스. 서버(/api/tg/{id}/...)가 Telethon으로 채널 영상을 색인하고 스트림을 이어 준다.
// 설정에서 고른 채널 하나하나가 라이브러리처럼 보인다. 항목 id는 "채널id:메시지id".
import { api, fmtDuration } from '../util.js';

const TRANSCODE_NOTE = { remux: '서버 변환(포장만)', audio: '서버 변환(음성)', transcode: '서버 변환(H.264)' };

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
    }

    channelTitle(id) {
        const c = this.channels.find((x) => x.id === String(id));
        return c ? c.title : '';
    }

    toItem(v) {
        const id = `${v.channel}:${v.msg}`;
        const thumb = v.has_thumb ? `${this.base}/thumb/${v.channel}/${v.msg}` : '';
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

    async getLibraryItems(channel, { start = 0, size = 60, sort = 'latest' } = {}) {
        const p = new URLSearchParams({ channel, offset: start, limit: size, sort });
        const data = await api(`${this.base}/videos?${p}`);
        // 아직 훑지 않은 오래된 메시지가 남았으면 total보다 더 있을 수 있다 -> 한 칸 더 있다고 알려 계속 불러오게
        const total = data.more ? Math.max(data.total, start + data.items.length + 1) : data.total;
        return { items: data.items.map((v) => this.toItem(v)), total, shown: data.total, approx: !!data.more };
    }

    async search(query) {
        const data = await api(`${this.base}/videos?${new URLSearchParams({ q: query, limit: 40 })}`);
        return data.items.length ? [{ title: `영상 · ${this.name}`, items: data.items.map((v) => this.toItem(v)) }] : [];
    }

    async _video(id, probe = false) {
        const [channel, msg] = String(id).split(':');
        const data = await api(`${this.base}/video/${channel}/${msg}${probe ? '?probe=1' : ''}`);
        return data.item;
    }

    async getDetail(id) {
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
        const converted = (at, mode) => ({
            ...common,
            url: `${this.base}/transcode/${channel}/${msg}?${new URLSearchParams({ start: Math.floor(at), mode })}`,
            kind: 'direct',
            startTime: 0,
            timeOffset: Math.floor(at),
            engineNote: TRANSCODE_NOTE[mode] || '서버 변환',
            reloadAt: async (sec) => converted(sec, mode),
        });
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
