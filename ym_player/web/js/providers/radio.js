// 인터넷 라디오 소스. 방송국 목록은 설정(config)에 있고, 지금 나오는 곡과 앨범 표지는 서버(/api/radio/{id}/now)가
// 방송 스트림의 곡 정보(ICY)와 iTunes 검색으로 알려 준다. 재생은 IPTV 라이브 채널과 같은 길(채널 카드, OSD,
// 위/아래 방송국 전환, 미니 플레이어)을 쓰고, 화면에는 영상 대신 앨범 표지를 크게 보여 준다.
import { api } from '../util.js';
import { streamProxyUrl } from './iptv.js';

const POLL_MS = 20000;

export class RadioProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'radio';
        this.stations = src.stations || [];
        this.now = {};          // 방송국 id -> {artist, title, text, art, since}
        this._nowAt = 0;
        this._timer = null;
    }

    channelOf(st) {
        const now = this.now[st.id] || {};
        return {
            id: `${this.id}:${st.id}`, stationId: st.id, name: st.name, group: st.subtitle || '',
            logo: now.art || '', url: st.url, homepage: st.homepage,
        };
    }

    /** 지금 나오는 곡 다시 받기 (10초 안에 받았으면 그대로) */
    async refreshNow(force = false) {
        if (!force && Date.now() - this._nowAt < 10000) return this.now;
        try {
            this.now = (await api(`/api/radio/${encodeURIComponent(this.id)}/now`)).now || {};
            this._nowAt = Date.now();
        } catch (e) {
            console.warn(`[라디오] ${this.name} 곡 정보 실패:`, e.message);
        }
        return this.now;
    }

    nowText(st) {
        const now = this.now[st.id];
        return now && now.text ? now.text : '';
    }

    toItem(st) {
        const ch = this.channelOf(st);
        const text = this.nowText(st);
        return {
            pid: this.id, id: ch.id, type: 'channel', title: st.name,
            subtitle: text ? `♪ ${text}` : (st.subtitle || ''),
            logo: ch.logo, fallbackIcon: 'radio', shape: 'channel', progress: 0, channel: ch,
        };
    }

    async getLibraries() {
        return [{ id: 'stations', title: '방송국', type: 'radio', icon: 'radio' }];
    }

    librarySorts() {
        return [['default', '설정 순서']];
    }

    async getLibraryItems() {
        await this.refreshNow();
        const items = this.stations.map((st) => this.toItem(st));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        await this.refreshNow();
        if (!this.stations.length) return [];
        return [{ title: '라디오', source: this.name, live: true, items: this.stations.map((st) => this.toItem(st)),
            more: `#/library/${this.id}/stations` }];
    }

    async search(query) {
        const q = query.toLowerCase();
        await this.refreshNow();
        const hits = this.stations.filter((st) => `${st.name} ${st.subtitle} ${this.nowText(st)}`.toLowerCase().includes(q));
        return hits.length ? [{ title: `라디오 · ${this.name}`, live: true, items: hits.map((st) => this.toItem(st)) }] : [];
    }

    /** 재생기 OSD/부제가 쓰는 "지금 방송" 모양 (IPTV 편성표와 같은 [시작, 끝, 제목]) */
    nowNext(ch) {
        const now = this.now[ch.stationId];
        const cur = now && now.text ? [now.since || Date.now() / 1000, null, `♪ ${now.text}`, '', ''] : null;
        return { cur, next: null, progress: 0 };
    }

    /** 재생 화면에 크게 보여 줄 앨범 표지 (곡이 바뀌면 재생기가 다시 물어본다) */
    artFor(ch) {
        const now = this.now[ch.stationId];
        return (now && now.art) || '';
    }

    _watch(on) {
        clearInterval(this._timer);
        this._timer = on ? setInterval(() => this.refreshNow(true), POLL_MS) : null;
    }

    getPlayback(ch) {
        const st = this.stations.find((s) => s.id === ch.stationId) || ch;
        this.refreshNow();
        this._watch(true);
        const { cur } = this.nowNext(ch);
        return {
            url: st.url,
            proxyUrl: streamProxyUrl(st.url),
            kind: 'direct',
            live: true,
            audioOnly: true,
            poster: this.artFor(ch),
            title: st.name,
            subtitle: cur ? cur[2] : (st.subtitle || ''),
            channel: this.channelOf(st),
            provider: this,
            onStop: () => this._watch(false),
        };
    }
}
