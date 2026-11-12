// 공중파 라디오 소스 - 한국 지상파·지역 라디오를 지역별 채널로 (kradio.py).
// 채널 목록은 서버가 공개 저장소(BSofDeath/radio.bsod.kr)에서 받아 준다 (/api/kradio/{id}/stations).
// 사이드바에는 [방송국] 화면 하나, 지역은 그 화면 위쪽의 단추 줄로 고른다.
// 로고는 관리자가 정한 그림(소스 설정의 채널 로고), 없으면 방송사 글자 로고(kradio_logo.js). 카드의 별로 즐겨찾기.
// 즐겨찾기는 지역과 상관없이 채널 하나로 친다 (전국 방송은 지역마다 들어 있어서) - 채널 id에 지역을 넣지 않는다.
// 재생은 인터넷 라디오와 같은 길(채널 카드, 위/아래로 채널 전환, 미니 플레이어, 시각화)을 쓰되,
// 방송 주소는 틀 때마다 서버가 찾아 넘겨 준다 (/live/{채널}) - KBS·MBC·SBS처럼 주소에 토큰이 붙어 시간이 지나면 못 쓰는 방송이 있어서.
import { api, h, modal, toast } from '../util.js';
import { registry } from './index.js';
import { isFavorite, streamProxyUrl } from './iptv.js';
import { frequencyOf, textLogo } from './kradio_logo.js';
import { RadioProvider } from './radio.js';

const HOME_MAX = 24;

export class KRadioProvider extends RadioProvider {
    constructor(src) {
        super({ ...src, stations: [] });
        this.kind = 'kradio';
        this.base = `/api/kradio/${encodeURIComponent(src.id)}`;
        this.regions = [];
        this.error = '';
        this.src = src;
        this.logos = new Map((src.logos || []).map((l) => [l.name, l.url]));
        this.names = new Map((src.names || []).map((n) => [n.from, n.to]));
        this._logoCache = new Map();
        this._loading = null;
    }

    /** 채널 목록 (한 번 받아 두고, 못 받았으면 다음에 다시) */
    _load(force = false) {
        if (this._loading && !force) return this._loading;
        this._loading = api(`${this.base}/stations${force ? '?refresh=1' : ''}`).then((d) => {
            this.regions = d.regions || [];
            // name = 보여 줄 이름 (관리자가 바꾼 이름), origName = 저장소의 이름
            this.stations = (d.stations || []).map((st) => ({ ...st, origName: st.name, name: this.names.get(st.name) || st.name,
                subtitle: st.group, url: `${this.base}/live/${encodeURIComponent(st.id)}` }));
            this.error = d.error || '';
            if (!this.stations.length) this._loading = null;
            return this.stations;
        }).catch((e) => { this._loading = null; this.error = e.message; return []; });
        return this._loading;
    }

    /** 지역을 뺀 채널 키 ("seoul.mbc.fm4u.-" -> "mbc.fm4u.-") - 즐겨찾기·채널 id용 */
    keyOf(st) { return String(st.id).slice(String(st.region).length + 1); }

    logoOf(st) {
        const custom = this.logos.get(st.name) || this.logos.get(st.origName);
        if (custom) return custom.startsWith('http://') ? streamProxyUrl(custom) : custom;
        // 바꾼 이름에 주파수(숫자)가 이미 있으면 수도권 주파수를 덧붙이지 않는다
        const freq = /\d+(\.\d+)?\s*$/.test(st.name) ? '' : frequencyOf(st.id);
        if (!this._logoCache.has(st.id)) this._logoCache.set(st.id, textLogo(st.name, freq));
        return this._logoCache.get(st.id);
    }

    /** [관리자] 채널 이름 바꾸기 (예: "광주MBC FM4U" -> "광주MBC FM4U 91.5"). 비우면 원래 이름으로 */
    rename(st) {
        const input = h('input', { class: 'input', value: st.name, placeholder: st.origName, maxlength: 60 });
        const save = async () => {
            const to = input.value.trim().replace(/\s+/g, ' ');
            const names = (this.src.names || []).filter((n) => n.from !== st.origName);
            if (to && to !== st.origName) names.push({ from: st.origName, to });
            const sources = registry.config.sources.map((s) => (s.id === this.id ? { ...s, names } : { id: s.id, type: s.type, keep: true }));
            try {
                registry.config = await api('/api/config', { method: 'PUT', json: { sources } });
                m.close();
                toast(to && to !== st.origName ? `"${st.origName}" → "${to}"` : `"${st.origName}" 원래 이름으로 되돌렸습니다.`, 'success');
                // 이 화면에 바로 반영 (같은 채널은 지역마다 있다)
                this.src = registry.config.sources.find((s) => s.id === this.id) || { ...this.src, names };
                this.names = new Map((this.src.names || []).map((n) => [n.from, n.to]));
                for (const x of this.stations) x.name = this.names.get(x.origName) || x.origName;
                this._logoCache.clear();
                window.dispatchEvent(new HashChangeEvent('hashchange'));
            } catch (e) {
                toast(`저장 실패: ${e.message}`, 'error', 6000);
            }
        };
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
        const m = modal('채널 이름 바꾸기', h('div', {},
            h('p', { class: 'muted', text: `원래 이름: ${st.origName} · ${st.group}` }),
            input,
            h('p', { class: 'field-hint', text: '주파수를 붙여 쓰면 로고에도 나옵니다 (예: 광주MBC FM4U 91.5). 비우고 저장하면 원래 이름으로. 모든 사용자에게 같이 바뀝니다.' })),
        [h('button', { class: 'btn', on: { click: () => m.close() } }, '취소'),
            h('button', { class: 'btn btn-accent', on: { click: save } }, '저장')]);
        setTimeout(() => { input.focus(); input.select(); }, 30);
    }

    channelOf(st) {
        return { id: `${this.id}:${this.keyOf(st)}`, stationId: st.id, name: st.name, group: st.group || '', logo: this.logoOf(st), url: st.url };
    }

    toItem(st) {
        return { ...super.toItem(st), logo: this.logoOf(st), favoritable: true,
            onRename: registry.isAdmin() ? () => this.rename(st) : null };
    }

    /** 즐겨찾은 채널 (지역마다 겹치는 전국 방송은 한 번만) */
    favorites() {
        const seen = new Set();
        return this.stations.filter((st) => {
            const key = this.keyOf(st);
            if (seen.has(key) || !isFavorite(this.channelOf(st))) return false;
            seen.add(key);
            return true;
        });
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

    /** 지역 고르기 - 방송국 화면 위쪽 단추 줄 (전국 방송은 지역마다 들어 있어 '전체'는 두지 않는다). 맨 앞은 즐겨찾기 */
    libraryKinds() { return this.regions.length ? [['@fav', '⭐ 즐겨찾기'], ...this.regions.map((r) => [r.id, r.name])] : null; }

    /** 처음 열 때: 즐겨찾기가 있으면 즐겨찾기, 없으면 첫 지역 */
    libraryKindDefault() { return this.favorites().length ? '@fav' : ((this.regions[0] || {}).id || ''); }

    libraryKindStyle() { return 'chips'; }

    libraryKindLabel() { return '지역'; }

    _region(kind) {
        return this.regions.some((r) => r.id === kind) ? kind : (this.regions[0] || {}).id;
    }

    async getLibraryItems(libId, { kind = '', sort = 'default' } = {}) {
        await this._load();
        if (!this.stations.length && this.error) throw new Error(this.error);
        const region = kind === '@fav' ? '' : this._region(kind || libId);   // (예전 주소 #/library/{id}/{지역}도 그 지역으로)
        const list = kind === '@fav' ? this.favorites() : this.stations.filter((st) => st.region === region);
        if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
        const items = list.map((st) => this.toItem(st));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        await this._load();
        const first = this.regions[0];
        if (!first) return [];
        const favs = this.favorites();
        if (favs.length) {
            return [{ title: '즐겨찾는 라디오', source: this.name, live: true, items: favs.slice(0, HOME_MAX).map((st) => this.toItem(st)),
                more: `#/library/${this.id}/stations` }];
        }
        const list = this.stations.filter((st) => st.region === first.id).slice(0, HOME_MAX);
        return [{ title: `공중파 라디오 · ${first.name}`, source: this.name, live: true, items: list.map((st) => this.toItem(st)),
            more: `#/library/${this.id}/stations` }];
    }

    async search(query) {
        await this._load();
        const q = query.toLowerCase();
        const seen = new Set();   // 전국 방송은 지역마다 있다 -> 한 번만
        const hits = this.stations.filter((st) => {
            const key = this.keyOf(st);
            if (!`${st.name} ${st.origName}`.toLowerCase().includes(q) || seen.has(key)) return false;
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
