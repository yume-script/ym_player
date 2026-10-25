// 인터넷 라디오 소스 (MP3/AAC/Ogg 스트림과 HLS .m3u8). 방송국 목록은 설정(config)에 있고, 지금 나오는 곡과 앨범 표지는 서버(/api/radio/{id}/now)가
// 방송 스트림의 곡 정보(ICY)와 iTunes 검색으로 알려 준다. 재생은 IPTV 라이브 채널과 같은 길(채널 카드, OSD,
// 위/아래 방송국 전환, 미니 플레이어)을 쓰고, 화면에는 영상 대신 앨범 표지를 크게 보여 준다.
// 방송국에 분류(group, 예: 한국 지역 라디오의 "서울·경기")가 있으면 방송국 화면에서 분류별로 골라 본다.
import { api, storeGet, storeSet } from '../util.js';
import { streamProxyUrl } from './iptv.js';

const POLL_MS = 20000;
const NOW_BATCH = 60;           // 곡 정보를 한 번에 물어보는 방송국 수 (서버도 같은 한도)
const HOME_MAX = 24;            // 홈 "라디오" 줄에 보여 줄 방송국 수
const HLS_KEY = 'yp_radio_hls'; // 주소만 봐서는 몰랐는데 HLS로 나온 방송국 주소들 (다음부터 바로 HLS로)

export class RadioProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'radio';
        this.stations = src.stations || [];
        this.now = {};          // 방송국 id -> {artist, title, text, art, since}
        this._nowAt = {};       // 방송국 id -> 마지막으로 받은 시각
        this._timer = null;
        this._watchSeq = 0;
        this._playing = null;   // 재생 중인 방송국 id (주기적으로 그 방송국 곡 정보만)
        this._hls = new Set(storeGet(HLS_KEY, []));
    }

    groups() {
        const out = [];
        for (const st of this.stations) if (st.group && !out.includes(st.group)) out.push(st.group);
        return out;
    }

    channelOf(st) {
        const now = this.now[st.id] || {};
        return {
            id: `${this.id}:${st.id}`, stationId: st.id, name: st.name, group: st.group || st.subtitle || '',
            logo: now.art || '', url: st.url, homepage: st.homepage,
        };
    }

    /** 지금 나오는 곡 다시 받기. ids가 있으면 그 방송국만 (10초 안에 받은 것은 그대로) */
    async refreshNow(force = false, ids = null) {
        const list = (ids || this.stations.map((s) => s.id)).slice(0, NOW_BATCH);
        const stale = force ? list : list.filter((id) => Date.now() - (this._nowAt[id] || 0) >= 10000);
        if (!stale.length) return this.now;
        try {
            const q = this.stations.length > NOW_BATCH || ids ? `?ids=${encodeURIComponent(stale.join(','))}` : '';
            const got = (await api(`/api/radio/${encodeURIComponent(this.id)}/now${q}`)).now || {};
            const at = Date.now();
            for (const id of stale) this._nowAt[id] = at;
            Object.assign(this.now, got);
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
            subtitle: text ? `♪ ${text}` : (st.subtitle || st.group || ''),
            logo: ch.logo, fallbackIcon: 'radio', shape: 'channel', progress: 0, channel: ch, group: st.group || '',
        };
    }

    async getLibraries() {
        return [{ id: 'stations', title: '방송국', type: 'radio', icon: 'radio' }];
    }

    librarySorts() {
        return [['default', '설정 순서'], ['name', '이름']];
    }

    /** 방송국 화면의 분류 고르기 (분류가 있을 때만) */
    libraryKinds() {
        const groups = this.groups();
        return groups.length > 1 ? [['', '전체'], ...groups.map((g) => [g, g])] : null;
    }

    libraryKindLabel() { return '지역'; }

    async getLibraryItems(_libId, { kind = '', sort = 'default' } = {}) {
        let list = kind ? this.stations.filter((st) => st.group === kind) : this.stations.slice();
        if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
        await this.refreshNow(false, list.map((st) => st.id));
        const items = list.map((st) => this.toItem(st));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        if (!this.stations.length) return [];
        const list = this.stations.slice(0, HOME_MAX);
        await this.refreshNow(false, list.map((st) => st.id));
        return [{ title: '라디오', source: this.name, live: true, items: list.map((st) => this.toItem(st)),
            more: `#/library/${this.id}/stations` }];
    }

    async search(query) {
        const q = query.toLowerCase();
        const hits = this.stations.filter((st) => `${st.name} ${st.subtitle} ${st.group || ''} ${this.nowText(st)}`.toLowerCase().includes(q));
        if (!hits.length) return [];
        await this.refreshNow(false, hits.map((st) => st.id));
        return [{ title: `라디오 · ${this.name}`, live: true, items: hits.map((st) => this.toItem(st)) }];
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
        this._timer = on ? setInterval(() => this._playing && this.refreshNow(true, [this._playing]), POLL_MS) : null;
    }

    _rememberHls(url) {
        if (this._hls.has(url)) return;
        this._hls.add(url);
        storeSet(HLS_KEY, [...this._hls].slice(-500));
    }

    getPlayback(ch) {
        const st = this.stations.find((s) => s.id === ch.stationId) || ch;
        // HLS(.m3u8) 방송은 hls.js로 (Chrome은 <audio>에 m3u8을 바로 못 넣음, Safari는 기본 지원).
        // 서버 중계(/api/stream)가 재생목록 안의 조각 주소도 중계 주소로 바꿔 준다.
        // 주소만으로 모르는 방송(radio.bsod.kr/stream?... 처럼 방송사 HLS로 넘겨 주는 주소)은 먼저 그냥 열어 보고,
        // 안 되면 HLS로 다시 - 그렇게 나온 방송국은 기억해 두고 다음부터 바로 HLS로
        const knownHls = /\.m3u8(\?|$)/i.test(st.url || '') || this._hls.has(st.url);
        this._playing = st.id;
        this.refreshNow(false, [st.id]);
        this._watch(true);
        // 방송국을 바꾸면 새 재생을 먼저 준비한 뒤 이전 재생의 onStop이 불린다 -> 자기 차례일 때만 끈다
        const watch = ++this._watchSeq;
        const { cur } = this.nowNext(ch);
        // 서버 중계(같은 사이트 주소)로 받아야 브라우저가 소리를 분석해 시각화가 음악에 반응한다 (아이패드 포함).
        // 중계가 안 되면 방송국 주소로 바로 (소리는 나지만 시각화는 기본 움직임)
        const base = {
            live: true,
            audioOnly: true,
            visualizer: true,
            poster: this.artFor(ch),
            title: st.name,
            subtitle: cur ? cur[2] : (st.subtitle || st.group || ''),
            channel: this.channelOf(st),
            provider: this,
            onStop: () => { if (this._watchSeq === watch) { this._watch(false); this._playing = null; } },
        };
        const direct = { ...base, kind: knownHls ? 'hls' : 'direct', url: st.url, altNote: '방송국에 바로 연결' };
        const hlsProxied = {
            ...base, kind: 'hls', url: streamProxyUrl(st.url), altNote: 'HLS로 다시 연결',
            onStarted: () => this._rememberHls(st.url),
            alternate: () => direct,
        };
        if (knownHls) return { ...hlsProxied, altNote: undefined };
        return {
            ...base,
            kind: 'direct',
            url: streamProxyUrl(st.url),
            alternate: () => hlsProxied,
        };
    }
}
