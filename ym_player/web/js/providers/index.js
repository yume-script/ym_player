// 소스 종류 등록소. 새 종류(Jellyfin, Emby 등)를 붙이려면 같은 모양의 클래스를 만들어 TYPES에 넣고
// 서버 config_store.SOURCE_TYPES와 설정 화면(views/settings.js)의 입력 칸만 추가하면 된다.
import { api } from '../util.js';
import { PlexProvider } from './plex.js';
import { IptvProvider } from './iptv.js';
import { TelegramProvider } from './telegram.js';
import { RadioProvider } from './radio.js';

const TYPES = { plex: PlexProvider, iptv: IptvProvider, telegram: TelegramProvider, radio: RadioProvider };

/**
 * 여러 소스를 합친 랜덤 고르기 (홈의 [랜덤 재생]). 소스를 무작위 순서로 돌며 처음 나오는 영상.
 * 안 본 것을 먼저 찾고, 다 봤으면 본 것 중에서라도.
 */
export function randomAcross(providers) {
    return async (exclude) => {
        for (const unwatched of [true, false]) {
            const order = providers.map((p) => [Math.random(), p]).sort((a, b) => a[0] - b[0]).map(([, p]) => p);
            for (const p of order) {
                try {
                    const pick = await p.randomPick(null, { unwatched, exclude });
                    if (pick) return pick;
                } catch (e) {
                    console.warn(`[랜덤] ${p.name}:`, e.message);
                }
            }
        }
        return null;
    };
}

export const registry = {
    config: { client_id: '', sources: [] },
    user: { username: '', role: 'user' },   // 로그인한 사람 (app.js가 채움)
    isAdmin() { return this.user && this.user.role === 'admin'; },
    providers: [],

    async load() {
        this.config = await api('/api/config');
        this.providers = this.config.sources
            .filter((s) => s.enabled && TYPES[s.type])
            .map((s) => new TYPES[s.type](s, this.config.client_id));
        return this.providers;
    },

    /** 랜덤 재생을 할 수 있는 소스들 (Plex, 텔레그램) */
    randomProviders() { return this.providers.filter((p) => p.randomPick); },

    byId(id) { return this.providers.find((p) => p.id === id) || null; },
    ofKind(kind) { return this.providers.filter((p) => p.kind === kind); },
};
