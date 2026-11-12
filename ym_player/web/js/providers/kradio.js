// 공중파 라디오 소스 - 한국 지상파·지역 라디오를 지역별 채널로 (kradio.py).
// 채널 목록은 서버가 공개 저장소(BSofDeath/radio.bsod.kr)에서 받아 준다 (/api/kradio/{id}/stations).
// 사이드바에는 [방송국] 화면 하나, 지역은 그 화면 위쪽의 단추 줄로 고른다.
// 재생은 인터넷 라디오와 같은 길(채널 카드, 위/아래로 채널 전환, 미니 플레이어, 시각화)을 쓰되,
// 방송 주소는 틀 때마다 서버가 찾아 넘겨 준다 (/live/{채널}) - KBS·MBC·SBS처럼 주소에 토큰이 붙어 시간이 지나면 못 쓰는 방송이 있어서.
import { api } from '../util.js';
import { RadioProvider } from './radio.js';

const HOME_MAX = 24;

export class KRadioProvider extends RadioProvider {
    constructor(src) {
        super({ ...src, stations: [] });
        this.kind = 'kradio';
        this.base = `/api/kradio/${encodeURIComponent(src.id)}`;
        this.regions = [];
        this.error = '';
        this._loading = null;
    }

    /** 채널 목록 (한 번 받아 두고, 못 받았으면 다음에 다시) */
    _load(force = false) {
        if (this._loading && !force) return this._loading;
        this._loading = api(`${this.base}/stations${force ? '?refresh=1' : ''}`).then((d) => {
            this.regions = d.regions || [];
            this.stations = (d.stations || []).map((st) => ({ ...st, subtitle: st.group, url: `${this.base}/live/${encodeURIComponent(st.id)}` }));
            this.error = d.error || '';
            if (!this.stations.length) this._loading = null;
            return this.stations;
        }).catch((e) => { this._loading = null; this.error = e.message; return []; });
        return this._loading;
    }

    /** 새로고침 뒤 이어 틀기·채널 전환이 쓰는 목록 */
    async loadChannels() {
        return (await this._load()).map((st) => this.channelOf(st));
    }

    // 곡 정보(ICY)는 없다 (방송사 HLS)
    async refreshNow() { return this.now; }

    async getLibraries() {
        await this._load();
        return [{ id: 'stations', title: '방송국', type: 'radio', icon: 'tower-cell' }];
    }

    librarySorts() { return [['default', '방송사 순서'], ['name', '이름']]; }

    /** 지역 고르기 - 방송국 화면 위쪽 단추 줄 (전국 방송은 지역마다 들어 있어 '전체'는 두지 않는다) */
    libraryKinds() { return this.regions.length ? this.regions.map((r) => [r.id, r.name]) : null; }

    libraryKindStyle() { return 'chips'; }

    libraryKindLabel() { return '지역'; }

    _region(kind) {
        return this.regions.some((r) => r.id === kind) ? kind : (this.regions[0] || {}).id;
    }

    async getLibraryItems(libId, { kind = '', sort = 'default' } = {}) {
        await this._load();
        if (!this.stations.length && this.error) throw new Error(this.error);
        const region = this._region(kind || libId);   // (예전 주소 #/library/{id}/{지역}도 그 지역으로)
        const list = this.stations.filter((st) => st.region === region);
        if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
        const items = list.map((st) => this.toItem(st));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        await this._load();
        const first = this.regions[0];
        if (!first) return [];
        const list = this.stations.filter((st) => st.region === first.id).slice(0, HOME_MAX);
        return [{ title: `공중파 라디오 · ${first.name}`, source: this.name, live: true, items: list.map((st) => this.toItem(st)),
            more: `#/library/${this.id}/stations` }];
    }

    async search(query) {
        await this._load();
        const q = query.toLowerCase();
        const seen = new Set();   // 전국 방송은 지역마다 있다 -> 한 번만
        const hits = this.stations.filter((st) => {
            const key = st.id.slice(st.region.length + 1);
            if (!st.name.toLowerCase().includes(q) || seen.has(key)) return false;
            seen.add(key);
            return true;
        });
        return hits.length ? [{ title: `라디오 · ${this.name}`, live: true, items: hits.map((st) => this.toItem(st)) }] : [];
    }

    getPlayback(ch) {
        const st = this.stations.find((s) => s.id === ch.stationId) || { id: ch.stationId, name: ch.name, group: ch.group, hls: true };
        const live = `${this.base}/live/${encodeURIComponent(st.id)}`;
        const base = {
            live: true, audioOnly: true, visualizer: true, poster: '',
            title: st.name, subtitle: st.group || '',
            channel: this.channelOf(st), provider: this,
        };
        const kind = st.hls ? 'hls' : 'direct';
        // 서버 중계(같은 사이트 주소)로 받는다: http 방송도 되고, 재생목록 안의 조각 주소도 중계로 바뀌며, 시각화가 소리에 반응한다.
        // 안 되면 방송 주소를 새로 받아 방송사에 바로 연결
        return { ...base, kind, url: live, alternate: () => ({ ...base, kind, url: `${live}?direct=1&retry=1`, altNote: '방송사에 바로 연결' }) };
    }
}
