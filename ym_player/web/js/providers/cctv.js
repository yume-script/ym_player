// 도로 CCTV (국가교통정보센터 오픈 API). 목록은 서버(/api/cctv/{id}/cams, cctv.py)가 받아 두고, 재생은 /live/{cctv id}가
// 그때그때 새 영상 주소의 서버 중계(/api/stream, HLS)로 넘겨 준다 (영상 주소에 만료되는 값이 붙는 곳이 있어서).
// 재생은 라디오·IPTV 라이브 채널과 같은 길 (채널 카드, 위/아래로 다른 CCTV, 즐겨찾기, 미니 플레이어).
// 도로 이름("[경부선] 서울TG"의 경부선)을 분류로 골라 본다.
import { api } from '../util.js';
import { isFavorite } from './iptv.js';

const HOME_MAX = 12;

export class CctvProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'cctv';
        this.base = `/api/cctv/${encodeURIComponent(src.id)}`;
        this.cams = null;
        this._loading = null;
    }

    async load(refresh = false) {
        if (refresh) this._loading = null;
        if (!this._loading) {
            this._loading = api(`${this.base}/cams${refresh ? '?refresh=1' : ''}`).then((d) => {
                if (d.error) console.warn(`[CCTV] ${this.name}:`, d.error);
                this.error = d.error || '';
                this.cams = d.cams || [];
                return this.cams;
            }).catch((e) => { this._loading = null; throw e; });
        }
        return this._loading;
    }

    roadLabel(c) { return c.road === 'ex' ? '고속도로' : '국도'; }

    channelOf(c) {
        return { id: `${this.id}:${c.id}`, camId: c.id, name: c.name, group: c.group || this.roadLabel(c), logo: '' };
    }

    toItem(c) {
        const ch = this.channelOf(c);
        return {
            pid: this.id, id: ch.id, type: 'channel', title: c.name,
            subtitle: [this.roadLabel(c), c.group !== this.roadLabel(c) ? c.group : ''].filter(Boolean).join(' · '),
            logo: '', fallbackIcon: 'video', shape: 'channel', progress: 0, channel: ch, group: c.group || '',
        };
    }

    async getLibraries() {
        return [{ id: 'cams', title: 'CCTV', type: 'cctv', icon: 'video' }];
    }

    librarySorts() { return [['default', '도로 순'], ['name', '이름']]; }

    /** 라이브러리 화면을 그리기 전에 (도로 분류를 알려면 목록이 있어야 해서) */
    async prepareLibrary() { await this.load().catch(() => {}); }

    libraryKinds() {
        const groups = [];
        for (const c of this.cams || []) if (c.group && !groups.includes(c.group)) groups.push(c.group);
        return groups.length > 1 ? [['', '전체'], ['@fav', '즐겨찾기'], ...groups.map((g) => [g, g])] : null;
    }

    libraryKindLabel() { return '도로'; }

    async getLibraryItems(_libId, { kind = '', sort = 'default' } = {}) {
        const cams = await this.load();
        let list = kind === '@fav' ? cams.filter((c) => isFavorite(this.channelOf(c)))
            : kind ? cams.filter((c) => c.group === kind) : cams.slice();
        if (sort === 'name') list.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
        const items = list.map((c) => this.toItem(c));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        const cams = await this.load();
        if (!cams.length) {
            if (this.error) throw new Error(this.error);   // 홈에 "도로 CCTV: 인증키가 …"처럼 보이게
            return [];
        }
        const favs = cams.filter((c) => isFavorite(this.channelOf(c)));
        const list = favs.length ? favs : cams.slice(0, HOME_MAX);
        return [{ title: favs.length ? '즐겨찾는 CCTV' : '도로 CCTV', source: this.name, live: true,
            items: list.map((c) => this.toItem(c)), more: `#/library/${this.id}/cams` }];
    }

    async search(query) {
        const q = query.toLowerCase();
        const cams = await this.load();
        const hits = cams.filter((c) => `${c.name} ${c.group}`.toLowerCase().includes(q)).slice(0, 60);
        return hits.length ? [{ title: `CCTV · ${this.name}`, live: true, items: hits.map((c) => this.toItem(c)) }] : [];
    }

    nowNext() { return { cur: null, next: null, progress: 0 }; }

    getPlayback(ch) {
        const c = (this.cams || []).find((x) => x.id === ch.camId) || { id: ch.camId, name: ch.name, group: ch.group };
        const live = `${this.base}/live/${c.id}`;
        const base = {
            live: true,
            title: c.name,
            subtitle: ch.group || '',
            channel: this.channelOf(c),
            provider: this,
        };
        // 대부분 HLS. 형식을 모르면 먼저 HLS로, 안 되면 그냥 열어 본다
        const direct = { ...base, kind: 'direct', url: live, altNote: '바로 열기' };
        return { ...base, kind: 'hls', url: live, alternate: /hls|m3u8/i.test(c.format || 'hls') ? () => direct : null };
    }
}
