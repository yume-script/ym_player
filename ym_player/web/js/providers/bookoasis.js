// BookOasis 소스 - 서재(BookOasis)의 책·오디오북·영상. 서버가 BookOasis 계정으로 로그인해 이어 준다 (/api/bo/{id}/…, bookoasis.py).
//   책(만화·웹툰 압축/폴더)은 웹툰 보기(reader.js), PDF·EPUB은 책 보기(docreader.js), 오디오북·영상은 재생기로.
//   보던 위치는 BookOasis에 그 계정으로 저장한다 (YMPlayer 사용자가 여럿이어도 BookOasis에는 한 계정의 기록).
// 항목 id: 작품 "s:{세션}:{서재}:{대표id}:{작품 키}", 책 "b:{세션}:{책id}", 오디오 트랙 "a:{오디오북id}:{트랙id}", 영상 한 화 "v:{영상id}:{화id}"
import { api, fmtDuration, toast, storeGet, storeSet, IS_SAFARI } from '../util.js';

const openReader = (...a) => import('../reader.js').then((m) => m.openReader(...a));
const openDocReader = (...a) => import('../docreader.js').then((m) => m.openDocReader(...a));

const SESS = {
    general: { title: '책', icon: 'book', unit: '권' },
    adult: { title: '성인 서재', icon: 'book', unit: '권' },
    audiobook: { title: '오디오북', icon: 'headphones', unit: '트랙' },
    video: { title: '영상', icon: 'film', unit: '화' },
};
const HOME_COUNT = 20;
const refresh = () => { if (location.hash.startsWith('#/item/')) window.dispatchEvent(new HashChangeEvent('hashchange')); };

export class BookOasisProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'bookoasis';
        this.sessions = (src.sessions || ['general']).filter((s) => SESS[s]);
        this.base = `/api/bo/${encodeURIComponent(src.id)}`;
        this._seen = new Map();   // 항목 id -> 받아 둔 정보 (책·트랙·화)
    }

    _get(path, params = {}) {
        const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
        return api(`${this.base}/${path}${[...q].length ? `?${q}` : ''}`);
    }

    /** BookOasis의 표지 값(파일 이름·/covers/…·/api/…·바깥 주소) -> 이 서버를 거친 주소 */
    cover(value) {
        let c = String(value || '').trim();
        if (!c) return '';
        if (/^https?:\/\//.test(c)) return c;
        if (c.startsWith('/api/media/')) return `${this.base}${c}`;
        c = c.replace(/^[/\\]+/, '').replace(/^covers[/\\]+/i, '');
        return c && c.includes('.') ? `${this.base}/covers/${c.split('/').map(encodeURIComponent).join('/')}` : '';
    }

    _route(id) { return `#/item/${this.id}/${encodeURIComponent(id)}`; }

    seriesItem(sess, s) {
        const id = `s:${sess}:${s.library_id ?? 'all'}:${s.representative_book_id ?? ''}:${s.series_key || s.series_name || ''}`;
        const n = sess === 'general' || sess === 'adult' ? s.book_count : (s.total_tracks || s.book_count);
        const art = this.cover(s.cover_image)
            || (sess === 'video' && s.representative_book_id ? `${this.base}/api/media/videos/${s.representative_book_id}/cover` : '')
            || (sess === 'audiobook' && s.representative_book_id ? `${this.base}/api/media/audiobooks/${s.representative_book_id}/cover` : '');
        return {
            pid: this.id, id, type: 'show', playable: sess === 'audiobook' || sess === 'video',
            title: s.display_name || s.series_name || '', subtitle: [s.author, n ? `${n}${SESS[sess].unit}` : ''].filter(Boolean).join(' · '),
            thumb: art, wideThumb: art, shape: sess === 'audiobook' ? 'square' : 'poster', cardClass: sess === 'video' || sess === 'audiobook' ? '' : 'card-book',
            progress: 0, route: this._route(id), ts: Date.parse(s.latest_added || '') || 0,
            facts: { year: '', date: String(s.latest_added || '').slice(0, 10), length: n ? `${n}${SESS[sess].unit}` : '', rating: '', extra: s.author || '', summary: '' },
            raw: s,
        };
    }

    async getLibraries() {
        const out = [];
        for (const sess of this.sessions) {
            out.push({ id: `${sess}:all`, title: SESS[sess].title, type: `bookoasis-${sess}`, icon: SESS[sess].icon });
        }
        // 서재가 여럿인 세션은 서재별로도 (못 받으면 '전체'만)
        await Promise.all(this.sessions.map(async (sess) => {
            try {
                const d = await this._get('api/media/libraries', { type: sess });
                if ((d.libraries || []).length < 2) return;
                const at = out.findIndex((l) => l.id === `${sess}:all`);
                out.splice(at + 1, 0, ...d.libraries.map((l) => ({ id: `${sess}:${l.id}`, title: `${SESS[sess].title} · ${l.name}`, type: `bookoasis-${sess}`, icon: SESS[sess].icon })));
            } catch (e) { /* 전체만 */ }
        }));
        return out;
    }

    librarySorts() { return [['date_desc', '최근 추가순'], ['asc', '이름'], ['desc', '이름 (거꾸로)'], ['count_desc', '권수 많은 순']]; }

    async _list(sess, lib, { start = 0, size = 60, sort = 'date_desc', search = '', unwatched = false } = {}) {
        const d = await this._get('api/media/list', { type: sess, library_id: lib === 'all' ? '' : lib, page: Math.floor(start / size) + 1, limit: size, sort, search,
            read_filter: unwatched ? 'exclude_read' : '' });
        const items = (d.series || []).map((s) => this.seriesItem(sess, s));
        return { items, more: !!d.has_more };
    }

    async getLibraryItems(libId, opts = {}) {
        const [sess, lib] = String(libId).split(':');
        const { items, more } = await this._list(sess, lib || 'all', opts);
        const shown = (opts.start || 0) + items.length;
        return { items, total: more ? shown + 1 : shown, approx: more, shown };
    }

    async getHomeHubs() {
        const hubs = [];
        await Promise.all(this.sessions.filter((s) => s !== 'adult').map(async (sess) => {
            try {
                const { items } = await this._list(sess, 'all', { size: HOME_COUNT });
                if (items.length) hubs.push({ sess, title: `새로 들어온 ${SESS[sess].title}`, source: this.name, items, more: `#/library/${this.id}/${sess}:all` });
            } catch (e) { console.warn(`[BookOasis] ${sess}:`, e.message); }
        }));
        return hubs.sort((a, b) => this.sessions.indexOf(a.sess) - this.sessions.indexOf(b.sess));
    }

    async search(query) {
        const rows = [];
        await Promise.all(this.sessions.map(async (sess) => {
            try {
                const { items } = await this._list(sess, 'all', { size: 30, sort: 'asc', search: query });
                if (items.length) rows.push({ title: `${SESS[sess].title} · ${this.name}`, items });
            } catch (e) { /* 다른 세션 결과만 */ }
        }));
        return rows;
    }

    // ----- 하나하나의 항목 (책 / 트랙 / 화) -----
    bookItem(sess, b, series = '') {
        const id = `b:${sess}:${b.id}`;
        this._seen.set(id, { ...b, sess, series });
        const fmt = String(b.file_format || '').toLowerCase();
        const total = b.total_pages || 0;
        const art = this.cover(b.cover_image);
        return {
            pid: this.id, id, type: 'book', playable: true, title: b.title || series,
            subtitle: [fmt.toUpperCase(), total ? `${total}쪽` : ''].filter(Boolean).join(' · '),
            thumb: art, wideThumb: art, shape: 'poster', cardClass: fmt === 'pdf' || fmt === 'epub' ? 'card-book card-doc' : 'card-book',
            progress: b.is_completed ? 0 : (total && b.pages_read ? Math.min(1, b.pages_read / total) : 0), watched: !!b.is_completed,
            onOpen: () => this.read(id),
            raw: { ...b, index: null, duration: 0 },
        };
    }

    mediaItem(kind, parentId, e, parentTitle = '', art = '') {
        const id = `${kind}:${parentId}:${e.id}`;
        const pct = kind === 'a' ? e.track_progress_pct : e.episode_progress_pct;
        const done = !!(kind === 'a' ? e.is_track_completed : e.is_episode_completed);
        const no = kind === 'a' ? e.track_number : e.episode_number;
        const dur = Number(e.duration) || 0;
        const info = { ...e, kind, parentId, parentTitle, done, position: done ? 0 : dur * (Number(pct) || 0) / 100 };
        this._seen.set(id, info);
        const thumb = (kind === 'v' && this.cover(e.cover_image)) || art;
        return {
            pid: this.id, id, type: 'episode', title: e.title || `${no || ''}`, epLabel: no ? `${no}${kind === 'a' ? '번' : '화'}` : '',
            subtitle: [parentTitle, dur ? fmtDuration(dur * 1000) : ''].filter(Boolean).join(' · '),
            thumb, wideThumb: thumb, shape: kind === 'a' ? 'square' : 'wide', cardClass: kind === 'a' ? 'ep-square' : '',
            progress: done ? 0 : Math.min(1, (Number(pct) || 0) / 100), watched: done,
            route: this._route(id),
            facts: { year: '', date: '', length: dur ? fmtDuration(dur * 1000) : '', rating: '', extra: parentTitle, summary: '' },
            raw: { ...e, index: no, duration: dur * 1000 },
        };
    }

    /** 오디오북·영상 하나의 정보와 트랙·화 목록 (새로 열었을 때도 id만으로 찾게) */
    async _media(kind, parentId) {
        if (kind === 'v') {
            const d = await this._get(`api/media/videos/${parentId}`);
            return { meta: d.meta || {}, list: d.episodes || [], art: `${this.base}/api/media/videos/${parentId}/cover` };
        }
        const d = await this._get('api/media/detail', { type: 'audiobook', representative_book_id: parentId });
        return { meta: d.meta || {}, list: d.books || [], art: this.cover((d.meta || {}).cover_image) || `${this.base}/api/media/audiobooks/${parentId}/cover` };
    }

    _title(meta) { return meta.display_name || meta.series_name || meta.title || ''; }

    /** 다음에 볼 것: 보다 만 것 -> 안 본 첫 것 -> 처음 */
    _nextOf(items) {
        return items.find((it) => it.progress > 0 && !it.watched) || items.find((it) => !it.watched) || items[0] || null;
    }

    async _seriesDetail(id) {
        const [, sess, lib, rep, ...rest] = String(id).split(':');
        const key = rest.join(':');
        const d = await this._get('api/media/detail', { type: sess, series: key, library_id: lib, representative_book_id: rep });
        const meta = d.meta || {};
        const title = this._title(meta) || key;
        const books = d.books || [];
        const isBook = sess === 'general' || sess === 'adult';
        const art = this.cover(meta.cover_image)
            || (sess === 'video' && rep ? `${this.base}/api/media/videos/${rep}/cover` : '')
            || (sess === 'audiobook' && rep ? `${this.base}/api/media/audiobooks/${rep}/cover` : '');
        const children = isBook ? books.map((b) => this.bookItem(sess, b, title))
            : books.map((e) => this.mediaItem(sess === 'video' ? 'v' : 'a', sess === 'video' ? (e.video_id ?? rep) : (e.audiobook_id ?? rep), e, title, art));
        const deck = this._nextOf(children);
        const verb = isBook ? '읽기' : sess === 'video' ? '보기' : '듣기';
        const left = children.filter((c) => !c.watched).length;
        return {
            pid: this.id, id, type: 'show', title, subtitle: meta.author || '',
            poster: art, posterShape: sess === 'audiobook' ? 'square' : 'poster', art,
            metaLine: [meta.author, `${children.length}${SESS[sess].unit}`, meta.total_duration ? fmtDuration(meta.total_duration * 1000) : '',
                children.length ? (left ? `남은 것 ${left}${SESS[sess].unit}` : '다 봄') : ''].filter(Boolean),
            rating: null, summary: meta.summary || '', tagline: '', genres: [].concat(meta.genre || []).filter((g) => typeof g === 'string' && g),
            directors: [], cast: [],
            playable: false, listen: sess === 'audiobook', viewOffset: 0, duration: 0,
            parent: { label: SESS[sess].title, route: `#/library/${this.id}/${sess}:all` },
            unwatched: left,
            onDeck: deck ? { ...deck, subtitle: ` ${deck.progress > 0 ? `이어 ${verb}` : verb}: ${deck.title}` } : null,
            childrenTitle: isBook ? '책' : sess === 'video' ? '회차' : '트랙', childrenStyle: isBook ? undefined : 'episode',
            children,
        };
    }

    async getDetail(id) {
        const sid = String(id);
        if (sid.startsWith('s:')) return this._seriesDetail(sid);
        const [kind, parentId, eid] = sid.split(':');
        if (kind === 'b') {   // 책 하나 (보통은 카드에서 바로 열지만, 주소로 들어왔을 때)
            const d = await this._get(`api/media/books/${eid}/reader-info`, { type: parentId });
            const it = this.bookItem(parentId, d.book || { id: eid });
            return { ...it, poster: it.thumb, art: it.thumb, metaLine: [it.subtitle], rating: null, summary: '', tagline: '', genres: [], directors: [], cast: [],
                playable: true, viewOffset: 0, duration: 0, parent: null, children: [], onDeck: null };
        }
        const m = await this._media(kind, parentId);
        const title = this._title(m.meta);
        const e = m.list.find((x) => String(x.id) === String(eid));
        if (!e) throw new Error('항목을 찾을 수 없습니다.');
        const it = this.mediaItem(kind, parentId, e, title, m.art);
        const info = this._seen.get(sid);
        return {
            ...it, subtitle: '', poster: it.thumb || m.art, posterShape: kind === 'a' ? 'square' : 'wide', art: m.art,
            metaLine: [it.epLabel, e.duration ? fmtDuration(e.duration * 1000) : '', String(e.file_format || '').toUpperCase()].filter(Boolean),
            rating: null, summary: (kind === 'v' && e.summary) || '', tagline: '', genres: [], directors: [], cast: [],
            playable: true, listen: kind === 'a', viewOffset: (info.position || 0) * 1000, duration: (Number(e.duration) || 0) * 1000,
            parent: { label: title, route: `#/library/${this.id}/${kind === 'v' ? 'video' : 'audiobook'}:all` },
            children: [], onDeck: null,
        };
    }

    // ----- 책 읽기 -----
    async openItem(id) {
        const sid = String(id);
        if (sid.startsWith('b:')) { this.read(sid); return true; }
        if (sid.startsWith('s:general:') || sid.startsWith('s:adult:')) {
            const d = await this._seriesDetail(sid);
            const next = d.onDeck || d.children[0];
            if (next) this.read(next.id);
            return true;
        }
        return false;
    }

    _saveBook(sess, bookId, page, total, done) {
        return api(`${this.base}/progress`, { method: 'POST', json: { kind: 'book', db_type: sess, book_id: Number(bookId),
            page_idx: done ? Math.max(0, total - 1) : Math.max(0, Math.floor(page)), total_pages: Math.max(1, Math.round(total)), flush: !!done } }).catch(() => {});
    }

    async read(id) {
        const [, sess, bookId] = String(id).split(':');
        let b = this._seen.get(String(id));
        if (!b || !b.file_format) {
            try {
                b = { ...(await this._get(`api/media/books/${bookId}/reader-info`, { type: sess })).book, sess };
                this._seen.set(String(id), b);
            } catch (e) { toast(`책을 열지 못했습니다: ${e.message}`, 'error'); return; }
        }
        const fmt = String(b.file_format || '').toLowerCase();
        if (fmt === 'txt') { toast('글(TXT) 책은 BookOasis에서 읽어 주세요.', 'info', 5000); return; }
        if (fmt === 'pdf' || fmt === 'epub') {
            const key = `yp_bo_pos_${this.id}_${sess}_${bookId}`;
            openDocReader({
                id: `${this.id}:${id}`,
                url: fmt === 'pdf' ? `${this.base}/api/media/pdf?${new URLSearchParams({ db_type: sess, book_id: bookId })}`
                    : `${this.base}/api/media/books/${bookId}/download?${new URLSearchParams({ type: sess })}`,   // EPUB은 원본을 받아 연다 (BookOasis 계정에 내려받기 권한 필요)
                format: fmt, title: b.title || '', sub: b.series || this.name,
                position: fmt === 'pdf' ? (b.is_completed ? 0 : (b.pages_read || 0)) : Number(storeGet(key, 0)) || 0,
                save: (position, total, done) => {
                    if (fmt === 'pdf') { Object.assign(b, { pages_read: done ? 0 : position, total_pages: total, is_completed: done || b.is_completed }); return this._saveBook(sess, bookId, position, total, done); }
                    storeSet(key, done ? 0 : position);   // EPUB 위치(천분율)는 BookOasis의 방식과 달라 이 브라우저에만
                    return Promise.resolve();
                },
                onClose: refresh,
            });
            return;
        }
        openReader({
            load: async (bid) => {
                const [, s2, id2] = String(bid).split(':');
                const info = (await this._get(`api/media/books/${id2}/reader-info`, { type: s2 })).book || {};
                const n = info.total_pages || 0;
                if (!n) throw new Error('쪽 수를 알 수 없는 책입니다 (BookOasis에서 다시 스캔해 보세요).');
                const old = this._seen.get(String(bid)) || {};
                this._seen.set(String(bid), { ...old, ...info, sess: s2 });
                return {
                    id: bid, title: info.title || old.title || '', sub: [old.series, `${n}쪽`].filter(Boolean).join(' · '),
                    pages: Array.from({ length: n }, (_x, i) => `${this.base}/api/media/stream?${new URLSearchParams({ db_type: s2, book_id: id2, page_idx: i })}`),
                    position: info.is_completed ? 0 : Math.min(info.pages_read || 0, Math.max(0, n - 1)),
                    series: false, prev: null, next: null,
                };
            },
            save: (bid, position, total, done) => {
                const [, s2, id2] = String(bid).split(':');
                const v = this._seen.get(String(bid));
                if (v) Object.assign(v, { pages_read: done ? 0 : position, total_pages: total, is_completed: done || v.is_completed });
                return this._saveBook(s2, id2, position, total, done);
            },
            onClose: refresh,
        }, id);
    }

    // ----- 오디오북·영상 재생 -----
    async confirmPlay(id) {
        const sid = String(id);
        if (!sid.startsWith('s:')) return id;
        const d = await this._seriesDetail(sid);
        const next = d.onDeck || d.children[0];
        return next ? next.id : null;
    }

    async setWatched(id, watched) {
        const sid = String(id);
        if (sid.startsWith('s:')) throw new Error('작품 전체 표시는 BookOasis에서 해 주세요.');
        const [kind, parentId, eid] = sid.split(':');
        if (kind === 'b') {
            const b = this._seen.get(sid) || {};
            const total = b.total_pages || 1;
            await api(`${this.base}/progress`, { method: 'POST', json: { kind: 'book', db_type: parentId, book_id: Number(eid), page_idx: watched ? total - 1 : 0, total_pages: total, flush: true } });
            return;
        }
        await api(`${this.base}/progress`, { method: 'POST', json: { kind: kind === 'a' ? 'audio' : 'video', id: Number(parentId),
            [kind === 'a' ? 'track' : 'episode']: Number(eid), time: 0, done: !!watched } });
    }

    async getPlayback(id, { fromStart = false, startAt = null } = {}) {
        if (String(id).startsWith('s:')) id = await this.confirmPlay(id);
        if (!id) throw new Error('재생할 것이 없습니다.');
        const [kind, parentId, eid] = String(id).split(':');
        if (kind !== 'a' && kind !== 'v') throw new Error('재생할 수 없는 항목입니다.');
        const m = await this._media(kind, parentId);
        const title = this._title(m.meta);
        const items = m.list.map((e) => this.mediaItem(kind, parentId, e, title, m.art));
        const at = items.findIndex((it) => it.id === String(id));
        if (at < 0) throw new Error('항목을 찾을 수 없습니다.');
        const e = this._seen.get(String(id));
        const duration = Number(e.duration) || 0;
        const resumable = e.position > 10 && (!duration || duration - e.position > Math.min(30, duration * 0.05));
        const begin = startAt != null ? startAt : (!fromStart && resumable ? e.position : 0);
        const report = (sec, done = false) => fetch(`${this.base}/progress`, {
            method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: kind === 'a' ? 'audio' : 'video', id: Number(parentId), [kind === 'a' ? 'track' : 'episode']: Number(eid), time: sec, done }),
        }).catch(() => {});
        const next = items[at + 1];
        const stream = kind === 'a' ? `${this.base}/api/media/audiobooks/${parentId}/tracks/${eid}/stream`
            // Safari는 변환이 필요한 영상을 HLS로 받는다 (BookOasis가 브라우저를 보고 정하므로 알려 준다)
            : `${this.base}/api/media/videos/${parentId}/episodes/${eid}/stream${IS_SAFARI ? '?ua=safari' : ''}`;
        const track = kind === 'v' && e.has_subtitle ? { src: `${this.base}/api/media/videos/${parentId}/episodes/${eid}/subtitle`, label: '자막', lang: 'ko' } : null;
        return {
            live: false, kind: 'direct', url: stream, startTime: begin,
            audioOnly: kind === 'a', allowRate: true,
            title: e.title || title, subtitle: title, poster: items[at].thumb || m.art,
            durationHint: duration,
            ...(track ? { subtitles: [{ id: '1', label: '자막', text: true }], activeSubtitle: '1', textTrack: track,
                selectSubtitle: async (sub) => ({ track: sub === '0' ? null : track }) } : {}),
            onProgress: (state, sec) => report(sec, state === 'stopped' && duration > 0 && sec >= duration - Math.min(30, duration * 0.05)),
            onStop: (sec) => report(sec),
            getNext: next ? async () => ({ pid: this.id, id: next.id, title: next.title }) : null,
            provider: this,
        };
    }
}
