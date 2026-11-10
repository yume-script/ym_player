// 브런치 연재 (brunch.co.kr 브런치북 오리지널 연재, 요일별). 목록·글은 서버(/api/brunch/{id}, brunch.py)가 받아 넘긴다.
// 카드를 누르면 글 읽기 화면(article.js)에서 바로 읽는다 - 같은 브런치북의 앞뒤 회차로 이어서, [듣기]로 소리 내어.
import { api } from '../util.js';
import { openArticle } from '../article.js';

const DAYS = [['MONDAY', '월'], ['TUESDAY', '화'], ['WEDNESDAY', '수'], ['THURSDAY', '목'], ['FRIDAY', '금'], ['SATURDAY', '토'], ['SUNDAY', '일']];
const HOME_MAX = 12;

function today() { return DAYS[(new Date().getDay() + 6) % 7][0]; }

function ago(ms) {
    if (!ms) return '';
    const min = Math.round((Date.now() - ms) / 60000);
    if (min < 60) return `${Math.max(1, min)}분 전`;
    if (min < 24 * 60) return `${Math.round(min / 60)}시간 전`;
    return new Date(ms).toISOString().slice(0, 10);
}

export class BrunchProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'brunch';
        this.base = `/api/brunch/${encodeURIComponent(src.id)}`;
        this._pages = new Map();   // "요일|정렬" -> 다음 페이지 주소 (더 불러오기)
    }

    async getLibraries() {
        return [{ id: 'serial', title: '브런치 연재', type: 'brunch', icon: 'mug-hot' }];
    }

    librarySorts() { return [['PUBLISH_TIME', '최신순'], ['DONATION_TOTAL_AMOUNT', '응원순'], ['LIKE_COUNT', '라이킷순']]; }

    libraryKinds() { return [['', `오늘 (${DAYS.find(([d]) => d === today())[1]})`], ...DAYS, ['COMPLETE', '완결']]; }

    libraryKindLabel() { return '요일'; }

    toItem(it) {
        const id = `${it.user_id}/${it.article_no}`;
        return {
            pid: this.id, id, type: 'article', title: it.book_title || it.title,
            subtitle: [it.author, it.title !== it.book_title ? it.title : ''].filter(Boolean).join(' · '),
            thumb: it.image || it.author_image, wideThumb: it.image || it.author_image, shape: 'wide',
            isNew: !!it.is_new, progress: 0,
            facts: { year: '', date: ago(it.publish_time), length: '', rating: '', extra: `라이킷 ${it.like_count || 0}`, summary: it.summary || '' },
            onOpen: () => this.read(id, it),
            raw: it,
        };
    }

    async getLibraryItems(_libId, { start = 0, kind = '', sort = 'PUBLISH_TIME' } = {}) {
        const day = kind || today();
        const key = `${day}|${sort}`;
        let url = `${this.base}/list?${new URLSearchParams({ day, sort })}`;
        if (start > 0) {
            const next = this._pages.get(key);
            if (!next) return { items: [], total: start };
            url += `&${new URLSearchParams({ next })}`;
        }
        const d = await api(url);
        this._pages.set(key, d.more ? d.next_url : '');
        const items = d.items.filter((x) => x.user_id).map((x) => this.toItem(x));
        const shown = start + items.length;
        return { items, total: d.more && d.next_url ? shown + 1 : shown, shown, approx: !!(d.more && d.next_url) };
    }

    async getHomeHubs() {
        const d = await api(`${this.base}/list?${new URLSearchParams({ day: today(), sort: 'PUBLISH_TIME' })}`);
        const items = d.items.filter((x) => x.user_id).slice(0, HOME_MAX).map((x) => this.toItem(x));
        return items.length ? [{ title: `오늘의 브런치 연재 (${DAYS.find(([x]) => x === today())[1]}요일)`, source: this.name,
            items, more: `#/library/${this.id}/serial` }] : [];
    }

    /** 브런치는 검색 API가 없어서 오늘 연재 안에서만 찾는다 */
    async search(query) {
        const q = query.toLowerCase();
        const d = await api(`${this.base}/list?${new URLSearchParams({ day: today(), sort: 'PUBLISH_TIME' })}`).catch(() => ({ items: [] }));
        const hits = d.items.filter((x) => `${x.book_title} ${x.title} ${x.author}`.toLowerCase().includes(q));
        return hits.length ? [{ title: `브런치 연재 · ${this.name}`, items: hits.map((x) => this.toItem(x)) }] : [];
    }

    read(id, it = {}) {
        openArticle({
            title: it.book_title || '브런치',
            sub: it.author || '',
            load: (aid) => this._load(aid),
        }, id);
    }

    async _load(id) {
        const [user, no] = String(id).split('/');
        const a = (await api(`${this.base}/article/${encodeURIComponent(user)}/${encodeURIComponent(no)}`)).article;
        const eps = (a.episodes || []).map((e) => ({ id: `${e.user_id}/${e.article_no}`, title: e.title, num: e.num, current: e.current }));
        const at = eps.findIndex((e) => e.current);
        return {
            title: a.title, subtitle: a.subtitle, kicker: a.book_title,
            sub: [a.book_title, a.author].filter(Boolean).join(' · '),
            meta: [a.author, a.publish_time ? new Date(a.publish_time).toLocaleDateString() : '', a.like_count ? `라이킷 ${a.like_count}` : ''].filter(Boolean).join(' · '),
            cover: a.cover, html: a.body, align: a.align, url: a.url,
            notice: a.restricted ? '멤버십 글이라 앞부분만 보입니다.' : '',
            source: '출처: 브런치 (brunch.co.kr)',
            episodes: eps,
            prev: at > 0 ? eps[at - 1] : null,
            next: at >= 0 && at < eps.length - 1 ? eps[at + 1] : null,
        };
    }
}
