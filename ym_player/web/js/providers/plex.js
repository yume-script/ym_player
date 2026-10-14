// Plex 소스. 모든 요청은 서버의 /api/plex/{id}/... 중계를 거친다 (토큰은 서버가 붙임).
//
// 화면이 쓰는 공통 모양(다른 소스도 같은 모양을 돌려준다):
//   item   = { pid, id, type, title, subtitle, thumb, wide, progress, watched, unwatched, route }
//   detail = { ...item, art, poster, metaLine[], summary, tagline, genres[], directors[], cast[],
//              playable, viewOffset, duration, children[], childrenTitle, childrenStyle, parent }
//   playback = { url, kind, live, startTime, title, subtitle, poster, audioOnly,
//                onProgress(state, sec), onStop(sec), getNext() }
import { fmtDuration, uuid } from '../util.js';

const PRODUCT = 'Yume Player';
const LIB_ICONS = { movie: 'film', show: 'tv', artist: 'music' };
const PLAYABLE = new Set(['movie', 'episode', 'track', 'clip']);

// 브라우저 <video>로 그대로 틀 수 있는 조합이면 트랜스코딩 없이 원본 파일을 바로 준다
function canDirectPlay(media, audioOnly) {
    if (!media) return false;
    const c = (media.container || '').toLowerCase();
    const a = (media.audioCodec || '').toLowerCase();
    if (audioOnly) return ['mp3', 'aac', 'flac', 'ogg', 'opus', 'm4a', 'wav'].includes(c) || ['mp3', 'aac', 'flac'].includes(a);
    const v = (media.videoCodec || '').toLowerCase();
    if (['mp4', 'mov', 'm4v'].includes(c)) return v === 'h264' && (!a || ['aac', 'mp3'].includes(a));
    if (c === 'webm') return ['vp8', 'vp9', 'av1'].includes(v) && (!a || ['opus', 'vorbis'].includes(a));
    return false;
}

export class PlexProvider {
    constructor(src, clientId) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'plex';
        this.clientId = clientId;
        this.base = `/api/plex/${encodeURIComponent(src.id)}`;
    }

    url(path, params) {
        const qs = params ? new URLSearchParams(params).toString() : '';
        return this.base + path + (qs ? (path.includes('?') ? '&' : '?') + qs : '');
    }

    async req(path, params) {
        const res = await fetch(this.url(path, params), { headers: { Accept: 'application/json' } });
        if (!res.ok) {
            let msg = `Plex HTTP ${res.status}`;
            try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) { /* XML/빈 본문 */ }
            throw new Error(msg);
        }
        const j = await res.json();
        return j.MediaContainer || {};
    }

    // 응답 본문이 필요 없는 알림용 요청 (timeline/scrobble/stop)
    ping(path, params) {
        return fetch(this.url(path, params), { headers: { Accept: 'application/json' }, keepalive: true }).catch(() => {});
    }

    img(path, w, hgt) {
        if (!path) return '';
        return this.url('/photo/:/transcode', { width: w, height: hgt, minSize: 1, upscale: 1, url: path });
    }

    toItem(m) {
        const t = m.type;
        let title = m.title, subtitle = m.year ? String(m.year) : '', thumb = m.thumb;
        if (t === 'episode') {
            title = m.grandparentTitle || m.title;
            subtitle = `S${m.parentIndex ?? '?'} · E${m.index ?? '?'}  ${m.title}`;
            thumb = m.grandparentThumb || m.parentThumb || m.thumb;
        } else if (t === 'season') {
            subtitle = m.parentTitle || '';
            thumb = m.thumb || m.parentThumb;
        } else if (t === 'album') {
            subtitle = m.parentTitle || subtitle;
        } else if (t === 'track') {
            subtitle = m.grandparentTitle || '';
            thumb = m.parentThumb || m.grandparentThumb;
        }
        const square = ['artist', 'album', 'track'].includes(t);
        return {
            pid: this.id,
            id: String(m.ratingKey),
            type: t,
            title,
            subtitle,
            thumb: this.img(thumb, square ? 300 : 240, square ? 300 : 360),
            wideThumb: this.img(m.thumb || m.art, 400, 225),
            shape: square ? 'square' : 'poster',
            progress: m.viewOffset && m.duration ? m.viewOffset / m.duration : 0,
            watched: PLAYABLE.has(t) && m.viewCount > 0,
            unwatched: m.leafCount ? Math.max(0, m.leafCount - (m.viewedLeafCount || 0)) : 0,
            route: `#/item/${this.id}/${m.ratingKey}`,
            raw: m,
        };
    }

    async getLibraries() {
        const mc = await this.req('/library/sections');
        return (mc.Directory || [])
            .filter((d) => LIB_ICONS[d.type])
            .map((d) => ({ id: String(d.key), title: d.title, type: d.type, icon: LIB_ICONS[d.type] }));
    }

    async getHomeHubs() {
        const mc = await this.req('/hubs', { count: 18 });
        return (mc.Hub || [])
            .filter((hub) => hub.Metadata && hub.Metadata.length)
            .map((hub) => ({ title: `${hub.title}`, source: this.name, items: hub.Metadata.map((m) => this.toItem(m)) }));
    }

    async getLibraryHubs(libId) {
        const mc = await this.req(`/hubs/sections/${libId}`, { count: 18 });
        return (mc.Hub || [])
            .filter((hub) => hub.Metadata && hub.Metadata.length)
            .map((hub) => ({ title: hub.title, items: hub.Metadata.map((m) => this.toItem(m)) }));
    }

    librarySorts() {
        return [
            ['titleSort', '제목'],
            ['addedAt:desc', '최근 추가'],
            ['originallyAvailableAt:desc', '공개일'],
            ['audienceRating:desc', '평점'],
            ['lastViewedAt:desc', '최근 시청'],
        ];
    }

    async getLibraryItems(libId, { start = 0, size = 60, sort = 'titleSort', unwatched = false } = {}) {
        const params = { sort, 'X-Plex-Container-Start': start, 'X-Plex-Container-Size': size };
        if (unwatched) params.unwatched = 1;
        const mc = await this.req(`/library/sections/${libId}/all`, params);
        const items = (mc.Metadata || []).map((m) => this.toItem(m));
        return { items, total: mc.totalSize ?? mc.size ?? items.length };
    }

    async getDetail(id) {
        const mc = await this.req(`/library/metadata/${id}`, { includeOnDeck: 1 });
        const m = (mc.Metadata || [])[0];
        if (!m) throw new Error('항목을 찾을 수 없습니다.');
        const item = this.toItem(m);
        const media = (m.Media || [])[0] || {};

        const metaLine = [];
        if (m.type === 'episode') metaLine.push(`시즌 ${m.parentIndex} · ${m.index}화`);
        if (m.year) metaLine.push(String(m.year));
        if (m.contentRating) metaLine.push(m.contentRating);
        if (m.duration && PLAYABLE.has(m.type)) metaLine.push(fmtDuration(m.duration));
        if (m.type === 'show' && m.childCount) metaLine.push(`시즌 ${m.childCount}개`);
        if (media.videoResolution) metaLine.push(/^\d+$/.test(media.videoResolution) ? `${media.videoResolution}p` : media.videoResolution.toUpperCase());

        const detail = {
            ...item,
            title: m.title,
            subtitle: m.type === 'episode' ? m.grandparentTitle : (m.type === 'season' ? m.parentTitle : (m.originalTitle || '')),
            poster: this.img(m.type === 'episode' ? m.thumb : (m.thumb || m.parentThumb), m.type === 'episode' ? 480 : 300, m.type === 'episode' ? 270 : 450),
            posterShape: m.type === 'episode' ? 'wide' : item.shape,
            art: this.img(m.art || m.grandparentArt || m.parentArt, 1600, 900),
            metaLine,
            rating: m.audienceRating || m.rating || null,
            summary: m.summary || '',
            tagline: m.tagline || '',
            genres: (m.Genre || []).map((g) => g.tag),
            directors: (m.Director || []).map((d) => d.tag),
            cast: (m.Role || []).slice(0, 20).map((r) => ({ name: r.tag, role: r.role || '', thumb: this.img(r.thumb, 160, 160) })),
            playable: PLAYABLE.has(m.type),
            viewOffset: m.viewOffset || 0,
            duration: m.duration || 0,
            parent: null,
            children: [],
            onDeck: null,
        };
        if (m.type === 'episode' && m.grandparentRatingKey) {
            detail.parent = { label: m.grandparentTitle, route: `#/item/${this.id}/${m.grandparentRatingKey}` };
        } else if (m.type === 'season' && m.parentRatingKey) {
            detail.parent = { label: m.parentTitle, route: `#/item/${this.id}/${m.parentRatingKey}` };
        } else if (m.type === 'album' && m.parentRatingKey) {
            detail.parent = { label: m.parentTitle, route: `#/item/${this.id}/${m.parentRatingKey}` };
        }
        if (m.OnDeck && m.OnDeck.Metadata) detail.onDeck = this.toItem(m.OnDeck.Metadata);

        if (['show', 'season', 'artist', 'album'].includes(m.type)) {
            const c = await this.req(`/library/metadata/${id}/children`);
            detail.children = (c.Metadata || []).map((x) => this.toItem(x));
            detail.childrenTitle = { show: '시즌', season: '에피소드', artist: '앨범', album: '트랙' }[m.type];
            detail.childrenStyle = { show: 'poster', season: 'episode', artist: 'poster', album: 'track' }[m.type];
            // 시즌이 하나뿐인 쇼는 Plex처럼 바로 에피소드를 보여준다
            if (m.type === 'show' && detail.children.length === 1) {
                const only = await this.req(`/library/metadata/${detail.children[0].id}/children`);
                detail.children = (only.Metadata || []).map((x) => this.toItem(x));
                detail.childrenTitle = '에피소드';
                detail.childrenStyle = 'episode';
            }
        }
        return detail;
    }

    async search(query) {
        const mc = await this.req('/hubs/search', { query, limit: 20 });
        return (mc.Hub || [])
            .filter((hub) => hub.Metadata && hub.Metadata.length)
            .map((hub) => ({ title: `${hub.title} · ${this.name}`, items: hub.Metadata.map((m) => this.toItem(m)) }));
    }

    async setWatched(id, watched) {
        await this.ping(watched ? '/:/scrobble' : '/:/unscrobble', { identifier: 'com.plexapp.plugins.library', key: id });
    }

    async getPlayback(id, { fromStart = false } = {}) {
        const mc = await this.req(`/library/metadata/${id}`);
        const m = (mc.Metadata || [])[0];
        const media = m && (m.Media || [])[0];
        const part = media && (media.Part || [])[0];
        if (!part) throw new Error('재생할 파일이 없습니다.');

        const audioOnly = m.type === 'track';
        const session = uuid();
        // 상세 화면의 [이어보기] 버튼과 같은 기준: 30초 넘게 봤고 거의 끝까지 보진 않았을 때만 이어서
        const vo = m.viewOffset || 0;
        const resumable = vo > 30000 && (!m.duration || vo < m.duration * 0.95);
        const startTime = !fromStart && resumable ? vo / 1000 : 0;
        let url, kind;
        if (canDirectPlay(media, audioOnly)) {
            url = this.base + part.key;
            kind = 'direct';
        } else {
            kind = 'hls';
            url = this.url('/video/:/transcode/universal/start.m3u8', {
                hasMDE: 1,
                path: `/library/metadata/${id}`,
                mediaIndex: 0,
                partIndex: 0,
                protocol: 'hls',
                fastSeek: 1,
                directPlay: 0,
                directStream: 1,
                directStreamAudio: 1,
                videoQuality: 100,
                videoResolution: '1920x1080',
                maxVideoBitrate: 20000,
                subtitleSize: 100,
                audioBoost: 100,
                subtitles: 'burn',
                location: 'lan',
                session,
                'X-Plex-Session-Identifier': session,
                'X-Plex-Client-Identifier': this.clientId,
                'X-Plex-Product': PRODUCT,
                'X-Plex-Platform': 'Chrome',
                'X-Plex-Client-Profile-Extra':
                    'add-transcode-target(type=videoProfile&context=streaming&protocol=hls&container=mpegts&videoCodec=h264&audioCodec=aac,mp3)',
            });
        }

        const timeline = (state, sec) => this.ping('/:/timeline', {
            ratingKey: id, key: `/library/metadata/${id}`, state,
            time: Math.round((sec || 0) * 1000), duration: m.duration || 0, hasMDE: 1,
            'X-Plex-Session-Identifier': session,
        });

        let title = m.title, subtitle = m.year ? String(m.year) : '';
        if (m.type === 'episode') { title = m.grandparentTitle; subtitle = `S${m.parentIndex} · E${m.index}  ${m.title}`; }
        if (m.type === 'track') { subtitle = [m.grandparentTitle, m.parentTitle].filter(Boolean).join(' — '); }

        return {
            url, kind, live: false, startTime, title, subtitle, audioOnly,
            poster: this.img(audioOnly ? (m.parentThumb || m.thumb) : (m.art || m.grandparentArt), 1280, audioOnly ? 1280 : 720),
            durationHint: (m.duration || 0) / 1000,
            onProgress: (state, sec) => timeline(state, sec),
            onStop: (sec) => {
                timeline('stopped', sec);
                if (kind === 'hls') this.ping('/video/:/transcode/universal/stop', { session });
            },
            getNext: (m.type === 'episode' || m.type === 'track') ? () => this.nextSibling(m) : null,
        };
    }

    // 다음 화/다음 곡 - 같은 시즌(앨범) 안에서만 찾는다
    async nextSibling(m) {
        if (!m.parentRatingKey) return null;
        const c = await this.req(`/library/metadata/${m.parentRatingKey}/children`);
        const list = c.Metadata || [];
        const idx = list.findIndex((x) => String(x.ratingKey) === String(m.ratingKey));
        const next = idx >= 0 ? list[idx + 1] : null;
        return next ? { pid: this.id, id: String(next.ratingKey), title: next.title } : null;
    }
}
