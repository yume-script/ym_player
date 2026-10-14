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

// 서버(/api/plex-sub)가 WebVTT로 바꿔 줄 수 있는 외부 자막 형식 (ym_player/subtitles.py TEXT_CODECS와 같게)
const TEXT_SUB_CODECS = new Set(['srt', 'subrip', 'smi', 'sami', 'ass', 'ssa', 'vtt', 'webvtt']);

/**
 * Plex 파일(Part)의 자막 목록. streamType 3이 자막이다.
 * text=true  : 외부 텍스트 자막 파일 -> 브라우저 자막(<track>)으로 띄움, 원본 재생에서도 나옴
 * text=false : 파일 안에 든 자막이나 이미지 자막(PGS/VobSub) -> Plex 변환으로 영상에 입혀야 함
 */
export function subtitleStreams(part) {
    return ((part && part.Stream) || []).filter((st) => st.streamType === 3).map((st) => {
        const codec = String(st.codec || st.format || '').toLowerCase();
        return {
            id: String(st.id),
            label: st.extendedDisplayTitle || st.displayTitle || st.title || st.language || '자막',
            lang: st.languageCode || '',
            codec,
            key: st.key || '',
            text: !!st.key && TEXT_SUB_CODECS.has(codec),
            selected: !!st.selected,
            forced: !!st.forced,
        };
    });
}

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
        // 설정에서 체크를 끈(가져오지 않을) 라이브러리 섹션 key
        this.hidden = new Set((src.hidden_libraries || []).map(String));
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

    /** all=true면 설정 화면용으로 숨긴 라이브러리까지 전부 */
    async getLibraries({ all = false } = {}) {
        const mc = await this.req('/library/sections');
        return (mc.Directory || [])
            .filter((d) => LIB_ICONS[d.type] && (all || !this.hidden.has(String(d.key))))
            .map((d) => ({ id: String(d.key), title: d.title, type: d.type, icon: LIB_ICONS[d.type] }));
    }

    // 숨긴 라이브러리의 항목은 홈 허브/검색 결과에서도 뺀다 (허브 하나가 여러 라이브러리를 섞어 담기도 해서 항목 단위로)
    visible(m) {
        return m.librarySectionID === undefined || !this.hidden.has(String(m.librarySectionID));
    }

    hubsToRows(hubs, suffix = '') {
        return (hubs || [])
            .map((hub) => ({ hub, items: (hub.Metadata || []).filter((m) => this.visible(m)) }))
            .filter(({ items }) => items.length)
            .map(({ hub, items }) => ({ title: `${hub.title}${suffix}`, source: suffix ? undefined : this.name, items: items.map((m) => this.toItem(m)) }));
    }

    async getHomeHubs() {
        const mc = await this.req('/hubs', { count: 18 });
        return this.hubsToRows(mc.Hub);
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
            subtitles: subtitleStreams(((media.Part || [])[0])).map((st) => st.label),
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
        return this.hubsToRows(mc.Hub, ` · ${this.name}`);
    }

    async setWatched(id, watched) {
        await this.ping(watched ? '/:/scrobble' : '/:/unscrobble', { identifier: 'com.plexapp.plugins.library', key: id });
    }

    // Plex에 이 파일의 자막 선택을 저장한다 (Plex 앱의 선택과 같아지게). '0'이면 끄기
    setSubtitle(partId, streamId) {
        return fetch(this.url(`/library/parts/${partId}`, { subtitleStreamID: streamId, allParts: 1 }), {
            method: 'PUT', headers: { Accept: 'application/json' },
        }).then((r) => { if (!r.ok) throw new Error(`자막 선택 저장 실패 (HTTP ${r.status})`); });
    }

    subtitleTrack(id, st) {
        if (!st || !st.text) return null;
        const p = new URLSearchParams({ path: st.key, codec: st.codec });
        return { id: st.id, src: `/api/plex-sub/${encodeURIComponent(this.id)}?${p}`, lang: st.lang, label: st.label };
    }

    /**
     * opts.fromStart  처음부터
     * opts.startAt    이 위치(초)부터 - 자막을 바꾸느라 다시 불러올 때
     * opts.subtitleId 이 자막으로 ('0'=끄기). 없으면 Plex에 저장된 선택을 따른다
     */
    async getPlayback(id, { fromStart = false, startAt = null, subtitleId } = {}) {
        const mc = await this.req(`/library/metadata/${id}`);
        const m = (mc.Metadata || [])[0];
        const media = m && (m.Media || [])[0];
        const part = media && (media.Part || [])[0];
        if (!part) throw new Error('재생할 파일이 없습니다.');

        const audioOnly = m.type === 'track';
        let subs = audioOnly ? [] : subtitleStreams(part);
        const plexChoice = subs.find((st) => st.selected);
        if (subtitleId !== undefined && subtitleId !== (plexChoice ? plexChoice.id : '0')) {
            await this.setSubtitle(part.id, subtitleId);
            subs = subs.map((st) => ({ ...st, selected: st.id === subtitleId }));
        }
        const chosen = subs.find((st) => st.selected) || null;
        // 브라우저가 못 그리는 자막(내장/이미지)을 골랐으면 원본 재생 대신 Plex가 영상에 입혀서 보내게 한다
        const burn = !!chosen && !chosen.text;

        const session = uuid();
        // 상세 화면의 [이어보기] 버튼과 같은 기준: 30초 넘게 봤고 거의 끝까지 보진 않았을 때만 이어서
        const vo = m.viewOffset || 0;
        const resumable = vo > 30000 && (!m.duration || vo < m.duration * 0.95);
        const startTime = startAt != null ? startAt : (!fromStart && resumable ? vo / 1000 : 0);
        let url, kind;
        if (canDirectPlay(media, audioOnly) && !burn) {
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
                // 텍스트 자막은 브라우저가 따로 띄우므로 입히지 않는다
                subtitles: burn ? 'burn' : 'none',
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
            subtitles: subs,
            activeSubtitle: chosen ? chosen.id : '0',
            subtitleBurned: burn,
            textTrack: this.subtitleTrack(id, chosen),
            /**
             * 재생 중 자막 바꾸기. Plex에도 선택을 저장한다.
             * 텍스트 자막끼리/끄기는 그 자리에서 <track>만 바꾸고 ({track}),
             * 영상에 입혀야 하는 자막으로 가거나 입힌 자막을 떼야 하면 그 위치부터 다시 불러온다 ({reload}).
             */
            selectSubtitle: async (subId, atSec) => {
                const st = subs.find((x) => x.id === subId) || null;
                const needsBurn = !!st && !st.text;
                if (needsBurn || burn) {
                    return { reload: await this.getPlayback(id, { startAt: atSec, subtitleId: subId }) };
                }
                await this.setSubtitle(part.id, subId);
                return { track: this.subtitleTrack(id, st) };
            },
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
