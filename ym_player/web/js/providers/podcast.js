// 팟캐스트 소스 (RSS). 프로그램 목록·에피소드·들은 위치는 서버(/api/podcast/{id}/…, podcast.py)가 피드를 받아 알려 주고,
// 오디오도 서버가 중계한다(/audio/…, 피드에 적힌 주소만). 재생은 음악처럼 표지를 크게 보여 주는 오디오 재생이고,
// 영상보다 말이 많으니 재생 속도를 쓸 수 있게 한다 (allowRate). 다 들으면 다음 에피소드(그 뒤에 나온 것)로.
// 항목 id: 프로그램 "show:{피드id}", 에피소드 "{피드id}:{에피소드id}"
import { api, fmtDuration } from '../util.js';
import { streamProxyUrl } from './iptv.js';

const HOME_SHOWS = 24;

/** 표지 주소 (http 그림은 https 사이트에서 막히니 서버 중계로) */
function img(url) {
    if (!url) return '';
    return url.startsWith('http://') ? streamProxyUrl(url) : url;
}

function fmtDate(sec) {
    if (!sec) return '';
    const d = new Date(sec * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export class PodcastProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'podcast';
        this.feeds = src.feeds || [];
        this.base = `/api/podcast/${encodeURIComponent(src.id)}`;
        this._shows = new Map();   // 피드 id -> 프로그램 정보 (목록에서 받은 것)
    }

    _ratio(ep) {
        return ep.duration && ep.position ? Math.min(1, ep.position / ep.duration) : 0;
    }

    showItem(s) {
        this._shows.set(s.id, s);
        const sub = s.error && !s.loaded ? `불러오지 못함: ${s.error}`
            : [s.author, s.count ? `에피소드 ${s.count}개` : '', fmtDate(s.latest)].filter(Boolean).join(' · ');
        return {
            pid: this.id, id: `show:${s.id}`, type: 'show', playable: s.count > 0, title: s.title, subtitle: sub,
            thumb: img(s.image), wideThumb: img(s.image), shape: 'square',
            unwatched: s.unplayed || 0, progress: 0,
            route: `#/item/${this.id}/${encodeURIComponent(`show:${s.id}`)}`,
            ts: s.latest || 0,
            facts: { year: '', date: fmtDate(s.latest), length: s.count ? `${s.count}개` : '', rating: '', extra: s.author || '', summary: s.summary || '' },
            raw: s,
        };
    }

    episodeItem(ep, showTitle = '') {
        const show = ep.show || showTitle || (this._shows.get(ep.feed) || {}).title || '';
        const art = img(ep.image || ep.show_image || (this._shows.get(ep.feed) || {}).image || '');
        return {
            pid: this.id, id: `${ep.feed}:${ep.id}`, type: 'episode', title: ep.title,
            subtitle: [show, fmtDate(ep.date), ep.duration ? fmtDuration(ep.duration * 1000) : ''].filter(Boolean).join(' · '),
            thumb: art, wideThumb: art, shape: 'square', cardClass: 'ep-square',
            progress: this._ratio(ep), watched: !!ep.watched,
            route: `#/item/${this.id}/${encodeURIComponent(`${ep.feed}:${ep.id}`)}`,
            ts: (ep.updated || 0) * 1000,
            facts: { year: '', date: fmtDate(ep.date), length: ep.duration ? fmtDuration(ep.duration * 1000) : '', rating: '', extra: show, summary: ep.summary || '' },
            // 상세 화면의 에피소드 줄(episodeRow)이 쓰는 모양 (Plex처럼 길이는 ms)
            raw: { ...ep, index: ep.episode, originallyAvailableAt: fmtDate(ep.date), duration: (ep.duration || 0) * 1000 },
        };
    }

    async getLibraries() {
        return [{ id: 'shows', title: '프로그램', type: 'podcast', icon: 'podcast' },
            { id: 'episodes', title: '새 에피소드', type: 'podcast-episodes', icon: 'list' }];
    }

    librarySorts(libId) {
        if (libId === 'episodes') return [['date', '최신순']];
        return [['latest', '최근 올라온 순'], ['title', '이름'], ['default', '설정 순서']];
    }

    async getLibraryItems(libId, { sort = 'latest', unwatched = false } = {}) {
        if (libId === 'episodes') {
            const d = await api(`${this.base}/home`);
            (d.shows || []).forEach((s) => this._shows.set(s.id, s));
            let items = (d.recent || []).map((ep) => this.episodeItem(ep));
            if (unwatched) items = items.filter((it) => !it.watched);
            return { items, total: items.length };
        }
        const { shows } = await api(`${this.base}/shows`);
        let list = shows.slice();
        if (sort === 'title') list.sort((a, b) => a.title.localeCompare(b.title, 'ko'));
        else if (sort === 'latest') list.sort((a, b) => (b.latest || 0) - (a.latest || 0));
        if (unwatched) list = list.filter((s) => s.unplayed > 0);
        const items = list.map((s) => this.showItem(s));
        return { items, total: items.length };
    }

    async getHomeHubs() {
        if (!this.feeds.length) return [];
        const d = await api(`${this.base}/home`);
        (d.shows || []).forEach((s) => this._shows.set(s.id, s));
        const hubs = [];
        if (d.continue && d.continue.length) {
            hubs.push({ title: '이어 듣기', source: this.name, continue: true, items: d.continue.map((ep) => this.episodeItem(ep)) });
        }
        if (d.recent && d.recent.length) {
            hubs.push({ title: '새 에피소드', source: this.name, items: d.recent.map((ep) => this.episodeItem(ep)),
                more: `#/library/${this.id}/episodes` });
        }
        const shows = (d.shows || []).filter((s) => s.loaded).sort((a, b) => (b.latest || 0) - (a.latest || 0));
        if (shows.length) {
            hubs.push({ title: '팟캐스트', source: this.name, items: shows.slice(0, HOME_SHOWS).map((s) => this.showItem(s)),
                more: `#/library/${this.id}/shows` });
        }
        return hubs;
    }

    async search(query) {
        const q = query.toLowerCase();
        const { shows } = await api(`${this.base}/shows`);
        const hits = shows.filter((s) => `${s.title} ${s.author}`.toLowerCase().includes(q));
        const rows = [];
        if (hits.length) rows.push({ title: `팟캐스트 · ${this.name}`, items: hits.map((s) => this.showItem(s)) });
        // 에피소드 제목도 (홈에 받아 둔 최근 에피소드에서만 - 피드를 다 뒤지면 느려서)
        try {
            const d = await api(`${this.base}/home`);
            const eps = (d.recent || []).filter((ep) => `${ep.title} ${ep.show || ''}`.toLowerCase().includes(q));
            if (eps.length) rows.push({ title: `팟캐스트 에피소드 · ${this.name}`, items: eps.map((ep) => this.episodeItem(ep)) });
        } catch (e) { /* 프로그램 결과만 */ }
        return rows;
    }

    async _show(fid, refresh = false) {
        const d = await api(`${this.base}/show/${fid}${refresh ? '?refresh=1' : ''}`);
        this._shows.set(fid, d.show);
        return d;
    }

    /** 다음에 들을 에피소드: 듣던 것 -> (차례대로 듣는 프로그램이면 안 들은 가장 오래된 것, 아니면 안 들은 최신 것) */
    _nextOf(show, eps) {
        const going = eps.filter((e) => e.position > 10 && !e.watched).sort((a, b) => (b.updated || 0) - (a.updated || 0))[0];
        if (going) return going;
        const fresh = eps.filter((e) => !e.watched);
        if (!fresh.length) return eps[0] || null;
        return show.serial ? fresh[fresh.length - 1] : fresh[0];
    }

    async confirmPlay(id) {
        if (!String(id).startsWith('show:')) return id;
        const fid = String(id).slice(5);
        const d = await this._show(fid);
        const ep = this._nextOf(d.show, d.episodes);
        return ep ? `${fid}:${ep.id}` : null;
    }

    async getDetail(id) {
        const sid = String(id);
        if (sid.startsWith('show:')) return this._showDetail(sid.slice(5));
        const [fid, eid] = sid.split(':');
        const d = await api(`${this.base}/episode/${fid}/${eid}`);
        const ep = d.episode;
        const it = this.episodeItem(ep, d.show.title);
        return {
            ...it,
            subtitle: '',
            poster: it.thumb, posterShape: 'square', art: img(d.show.image),
            metaLine: [fmtDate(ep.date), ep.duration ? fmtDuration(ep.duration * 1000) : '',
                ep.season != null && ep.episode != null ? `시즌 ${ep.season} · ${ep.episode}화` : (ep.episode != null ? `${ep.episode}화` : ''),
                ep.video ? '영상' : ''].filter(Boolean),
            rating: null, summary: ep.summary || '', tagline: '', genres: [], directors: [], cast: [],
            playable: true, listen: !ep.video, viewOffset: (ep.position || 0) * 1000, duration: (ep.duration || 0) * 1000,
            parent: { label: d.show.title, route: `#/item/${this.id}/${encodeURIComponent(`show:${fid}`)}` },
            children: [], onDeck: null,
            extraActions: ep.link ? [{ icon: 'arrow-up-right-from-square', label: '웹 페이지', title: ep.link,
                onClick: () => window.open(ep.link, '_blank', 'noopener') }] : [],
        };
    }

    async _showDetail(fid, refresh = false) {
        const d = await this._show(fid, refresh);
        const s = d.show;
        const next = this._nextOf(s, d.episodes);
        const nextItem = next ? this.episodeItem(next, s.title) : null;
        if (nextItem) {
            const going = next.position > 10 && !next.watched;
            nextItem.subtitle = ` ${going ? '이어 듣기' : '듣기'}: ${next.title}`;
        }
        return {
            pid: this.id, id: `show:${fid}`, type: 'show', title: s.title,
            subtitle: s.author, poster: img(s.image), posterShape: 'square', art: img(s.image),
            metaLine: [s.author, `에피소드 ${s.count}개`, s.unplayed ? `안 들은 것 ${s.unplayed}개` : '다 들음',
                s.serial ? '차례대로 듣는 프로그램' : ''].filter(Boolean),
            rating: null, summary: s.error ? `피드를 새로 받지 못했습니다: ${s.error}\n\n${s.summary || ''}`.trim() : s.summary,
            tagline: '', genres: [], directors: [], cast: [],
            playable: false, listen: true, viewOffset: 0, duration: 0, parent: null,
            unwatched: s.unplayed,
            onDeck: nextItem,
            childrenTitle: '에피소드', childrenStyle: 'episode',
            children: d.episodes.map((ep) => this.episodeItem(ep, s.title)),
            extraActions: [
                { icon: 'rotate', label: '새로 받기', title: '피드를 지금 다시 받아 새 에피소드를 확인',
                    onClick: async (ctx) => { await this._show(fid, true); ctx.rerender(); } },
                ...(s.link ? [{ icon: 'arrow-up-right-from-square', label: '웹 페이지', title: s.link,
                    onClick: () => window.open(s.link, '_blank', 'noopener') }] : []),
            ],
        };
    }

    async setWatched(id, watched) {
        const sid = String(id);
        if (sid.startsWith('show:')) {
            await api(`${this.base}/watched`, { method: 'POST', json: { feed: sid.slice(5), watched } });
            return;
        }
        const [feed, ep] = sid.split(':');
        await api(`${this.base}/progress`, { method: 'POST', json: { feed, ep, position: 0, duration: 0, watched } });
    }

    async getPlayback(id, { fromStart = false, startAt = null } = {}) {
        if (String(id).startsWith('show:')) id = await this.confirmPlay(id);
        if (!id) throw new Error('들을 에피소드가 없습니다.');
        const [fid, eid] = String(id).split(':');
        const d = await api(`${this.base}/episode/${fid}/${eid}`);
        const ep = d.episode;
        const resumable = ep.position > 10 && (!ep.duration || ep.duration - ep.position > Math.min(30, ep.duration * 0.05));
        const begin = startAt != null ? startAt : (!fromStart && resumable ? ep.position : 0);
        let duration = ep.duration || 0;
        const report = (position, dur) => fetch(`${this.base}/progress`, {
            method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feed: fid, ep: eid, position, duration: dur || duration }),
        }).catch(() => {});
        // 다 들으면 그 다음에 나온 에피소드 (최신 것을 듣고 있었으면 끝)
        const newer = d.newer;
        return {
            live: false,
            kind: 'direct',
            url: `${this.base}/audio/${fid}/${eid}`,
            startTime: begin,
            audioOnly: !ep.video,
            allowRate: true,
            title: ep.title,
            subtitle: d.show.title,
            poster: img(ep.image || d.show.image),
            durationHint: duration,
            onProgress: (state, sec) => {
                if (state === 'stopped') { duration = duration || sec; report(sec, sec); } else report(sec);
            },
            onStop: (sec) => report(sec),
            getNext: newer ? async () => ({ pid: this.id, id: `${fid}:${newer.id}`, title: newer.title }) : null,
            provider: this,
        };
    }
}
