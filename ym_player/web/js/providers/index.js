// 소스 종류 등록소. 새 종류(Jellyfin, Emby 등)를 붙이려면 같은 모양의 클래스를 만들어 TYPES에 넣고
// 서버 config_store.SOURCE_TYPES와 설정 화면(views/settings.js)의 입력 칸만 추가하면 된다.
import { api } from '../util.js';
import { PlexProvider } from './plex.js';
import { IptvProvider } from './iptv.js';

const TYPES = { plex: PlexProvider, iptv: IptvProvider };

export const registry = {
    config: { client_id: '', sources: [] },
    providers: [],

    async load() {
        this.config = await api('/api/config');
        this.providers = this.config.sources
            .filter((s) => s.enabled && TYPES[s.type])
            .map((s) => new TYPES[s.type](s, this.config.client_id));
        return this.providers;
    },

    byId(id) { return this.providers.find((p) => p.id === id) || null; },
    ofKind(kind) { return this.providers.filter((p) => p.kind === kind); },
};
