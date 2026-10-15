// IPTV(M3U + XMLTV) 소스. 재생목록/편성표는 서버가 받아서 JSON으로 풀어준다.
// 재생은 브라우저가 원본 주소로 먼저 시도하고, 막히면(CORS 등) /api/stream 중계로 다시 시도한다.
import { api, normalizeName, storeGet, storeSet, toast } from '../util.js';

// 즐겨찾기는 사용자마다 서버(/api/me/prefs)에 둔다. 예전 브라우저 저장분(yp_fav_channels)은 처음 한 번 옮긴다
const FAV_KEY = 'yp_fav_channels';
const favorites = new Set();

export async function loadFavorites() {
    let list;
    try { list = (await api('/api/me/prefs')).prefs.fav_channels; } catch (e) { return; }
    const local = storeGet(FAV_KEY, null);
    if (list === undefined && Array.isArray(local) && local.length) {
        list = local;
        saveFavorites(list);
    }
    if (local !== null) storeSet(FAV_KEY, null);
    favorites.clear();
    (list || []).forEach((id) => favorites.add(id));
}

function saveFavorites(list) {
    api('/api/me/prefs', { method: 'PUT', json: { fav_channels: list } })
        .catch((e) => toast(`즐겨찾기 저장 실패: ${e.message}`, 'error'));
}

// 재생해보고 안 된 채널 (이번 탭 동안만 기억)
export const channelHealth = new Map();

export function isFavorite(ch) { return favorites.has(ch.id); }
export function toggleFavorite(ch) {
    if (favorites.has(ch.id)) favorites.delete(ch.id); else favorites.add(ch.id);
    saveFavorites([...favorites]);
    return favorites.has(ch.id);
}

function urlPath(raw) {
    try {
        const u = new URL(raw, location.href);
        return { path: u.pathname.toLowerCase(), params: u.searchParams };
    } catch (e) {
        return { path: String(raw).split('?')[0].toLowerCase(), params: null };
    }
}

// 원래 플러그인과 같은 규칙: .ts/output=ts -> mpegts.js, 확실한 동영상 파일 -> <video>, 나머지 -> hls.js
export function detectKind(raw) {
    const { path, params } = urlPath(raw);
    if (params && (params.get('output') || '').toLowerCase() === 'ts') return 'ts';
    if (path.endsWith('.ts')) return 'ts';
    if (path.endsWith('.mpd') || (params && /^(mpd|dash)$/i.test(params.get('type') || params.get('format') || ''))) return 'dash';
    if (/\.(mp4|m4v|webm|mov|mkv|ogg|mp3|aac)$/.test(path)) return 'direct';
    return 'hls';
}

export function streamProxyUrl(target, ua, referer) {
    const p = new URLSearchParams({ url: target });
    if (ua) p.set('ua', ua);
    if (referer) p.set('ref', referer);
    return `/api/stream?${p}`;
}

export class IptvProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'iptv';
        this.hasEpg = !!src.epg;
        this.channels = null;
        this.epg = null;
        this._channelsPromise = null;
        this._epgPromise = null;
    }

    loadChannels(refresh = false) {
        if (refresh) { this._channelsPromise = null; this._epgPromise = null; this.epg = null; }
        if (!this._channelsPromise) {
            this._channelsPromise = api(`/api/iptv/${encodeURIComponent(this.id)}/channels${refresh ? '?refresh=1' : ''}`)
                .then((j) => {
                    // 여러 M3U 중 일부만 못 받았으면 나머지로 보여 주고 콘솔에만 남긴다 (설정 > 상태에서도 보임)
                    for (const err of j.errors || []) console.warn(`[IPTV] ${this.name} M3U 일부 실패:`, err);
                    this.channels = j.channels || [];
                    return this.channels;
                })
                .catch((e) => { this._channelsPromise = null; throw e; });
        }
        return this._channelsPromise;
    }

    loadEpg() {
        if (!this.hasEpg) return Promise.resolve(null);
        if (!this._epgPromise) {
            this._epgPromise = api(`/api/iptv/${encodeURIComponent(this.id)}/epg`)
                .then((j) => { this.epg = j; return j; })
                .catch((e) => { console.warn(`[IPTV] ${this.name} EPG 실패:`, e.message); this._epgPromise = null; return null; });
        }
        return this._epgPromise;
    }

    // [시작, 끝, 제목, 설명, 분류] 목록. tvg-id -> 표시명 -> 채널명 순으로 찾는다
    programmesFor(ch) {
        const epg = this.epg;
        if (!epg || !ch) return [];
        const P = epg.programmes || {};
        if (ch.tvg_id && P[ch.tvg_id]) return P[ch.tvg_id];
        const mapped = (epg.names || {})[normalizeName(ch.name)];
        if (mapped && P[mapped]) return P[mapped];
        return P[ch.name] || [];
    }

    nowNext(ch, now = Date.now() / 1000) {
        const list = this.programmesFor(ch);
        const idx = list.findIndex((p) => p[0] <= now && (p[1] || p[0] + 3600) > now);
        const cur = idx >= 0 ? list[idx] : null;
        const next = idx >= 0 ? list[idx + 1] || null : list.find((p) => p[0] > now) || null;
        let progress = 0;
        if (cur && cur[1]) progress = Math.min(1, Math.max(0, (now - cur[0]) / (cur[1] - cur[0])));
        return { cur, next, progress };
    }

    toItem(ch) {
        const { cur, progress } = this.nowNext(ch);
        return {
            pid: this.id,
            id: ch.id,
            type: 'channel',
            title: ch.name,
            subtitle: cur ? cur[2] : (ch.group || ''),
            logo: ch.logo,
            shape: 'channel',
            progress,
            channel: ch,
        };
    }

    async getLibraries() {
        return [{ id: 'live', title: this.name, type: 'live', icon: 'tower-broadcast' }];
    }

    async getHomeHubs() {
        const chans = await this.loadChannels();
        // 편성표는 홈 화면을 오래 붙잡지 않게 잠깐만 기다린다
        await Promise.race([this.loadEpg(), new Promise((r) => setTimeout(r, 3000))]);
        const favs = chans.filter(isFavorite);
        const hubs = [];
        if (favs.length) hubs.push({ title: '즐겨찾는 채널', source: this.name, items: favs.map((c) => this.toItem(c)), live: true });
        const rest = chans.filter((c) => !isFavorite(c) && channelHealth.get(c.id) !== 'offline').slice(0, 24);
        if (rest.length) hubs.push({ title: '지금 방송 중', source: this.name, items: rest.map((c) => this.toItem(c)), live: true });
        return hubs;
    }

    async search(query) {
        const q = normalizeName(query);
        const chans = await this.loadChannels();
        const hits = chans.filter((c) => {
            if (normalizeName(c.name).includes(q)) return true;
            const { cur } = this.nowNext(c);
            return cur && normalizeName(cur[2]).includes(q);
        }).slice(0, 40);
        return hits.length ? [{ title: `채널 · ${this.name}`, items: hits.map((c) => this.toItem(c)), live: true }] : [];
    }

    /**
     * alt번째 주소로 재생. 0은 원래 주소, 1부터는 여러 M3U를 합칠 때 붙은 예비 주소(ch.alts).
     * 이 주소가 끝내 안 나오면 재생기가 alternate()로 다음 예비 주소를 시도한다.
     */
    getPlayback(ch, alt = 0) {
        const { cur } = this.nowNext(ch);
        const urls = [{ url: ch.url, ua: ch.ua, referer: ch.referer }, ...(ch.alts || [])];
        const u = urls[alt] || urls[0];
        return {
            url: u.url,
            proxyUrl: streamProxyUrl(u.url, u.ua, u.referer),
            kind: detectKind(u.url),
            live: true,
            title: ch.name,
            subtitle: cur ? cur[2] : (ch.group || ''),
            channel: ch,
            provider: this,
            altNote: alt ? `예비 주소 ${alt}/${urls.length - 1}` : '',
            alternate: alt + 1 < urls.length ? () => this.getPlayback(ch, alt + 1) : null,
        };
    }
}
