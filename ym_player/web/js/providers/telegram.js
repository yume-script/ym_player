// 텔레그램 채널 소스. 서버(/api/tg/{id}/...)가 Telethon으로 채널 영상을 색인하고 스트림을 이어 준다.
// 설정에서 고른 채널 하나하나가 라이브러리처럼 보인다. 항목 id는 "채널id:메시지id".
import { api, fmtDuration } from '../util.js';

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
            hubs.push({ title: '이어보기', source: this.name, items: data.continue.map((v) => this.toItem(v)) });
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

    async _video(id) {
        const [channel, msg] = String(id).split(':');
        const data = await api(`${this.base}/video/${channel}/${msg}`);
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

    async getPlayback(id, { fromStart = false } = {}) {
        const v = await this._video(id);
        const [channel, msg] = String(id).split(':');
        const resumable = v.position > 30 && (!v.duration || v.position < v.duration * 0.95);
        const report = (position, duration) => fetch(`${this.base}/progress`, {
            method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel, msg, position, duration: duration || v.duration }),
        }).catch(() => {});
        return {
            url: `${this.base}/stream/${channel}/${msg}`,
            kind: 'direct',
            live: false,
            startTime: !fromStart && resumable ? v.position : 0,
            title: v.title,
            subtitle: this.channelTitle(channel),
            poster: v.has_thumb ? `${this.base}/thumb/${channel}/${msg}` : '',
            durationHint: v.duration || 0,
            onProgress: (state, sec) => report(sec),
            onStop: (sec) => report(sec),
            getNext: null,
        };
    }
}
