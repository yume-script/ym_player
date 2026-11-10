// 만개의레시피 (10000recipe.com). 추천·분류·검색과 레시피는 서버(/api/recipe/{id}, recipe.py)가 읽어 글자만 넘긴다.
// 카드를 누르면 글 읽기 화면(article.js)에서 재료·만드는 순서를 한 페이지로 ([듣기]로 순서를 소리 내어).
import { api, h } from '../util.js';
// 글 읽기 화면은 열 때 받는다
const openArticle = (...a) => import('../article.js').then((m) => m.openArticle(...a));

const HOME_MAX = 12;
const CATEGORIES = ['밑반찬', '메인반찬', '국/탕', '찌개', '밥/죽/떡', '면/만두', '초스피드', '도시락', '야식', '디저트', '다이어트'];

export class RecipeProvider {
    constructor(src) {
        this.id = src.id;
        this.name = src.name;
        this.kind = 'recipe';
        this.base = `/api/recipe/${encodeURIComponent(src.id)}`;
        this._pages = new Map();   // 분류 -> 다음에 받을 쪽
    }

    async getLibraries() {
        return [{ id: 'recipes', title: '만개의레시피', type: 'recipe', icon: 'utensils' }];
    }

    librarySorts() { return [['default', '사이트 순']]; }

    libraryKinds() { return [['', '추천 레시피'], ...CATEGORIES.map((c, i) => [String(i), c])]; }

    libraryKindLabel() { return '분류'; }

    toItem(r) {
        return {
            pid: this.id, id: r.id, type: 'article', title: r.title, subtitle: r.by || '',
            thumb: r.image, wideThumb: r.image, shape: 'wide', progress: 0,
            facts: { year: '', date: '', length: '', rating: '', extra: r.by || '', summary: '' },
            onOpen: () => this.read(r.id, r),
            raw: r,
        };
    }

    async getLibraryItems(_libId, { start = 0, kind = '' } = {}) {
        const page = start > 0 ? (this._pages.get(kind) || 2) : 1;
        const d = await api(`${this.base}/list?${new URLSearchParams({ cat: kind, page })}`);
        this._pages.set(kind, page + 1);
        const items = d.items.map((r) => this.toItem(r));
        const more = items.length > 0;
        const shown = start + items.length;
        return { items, total: more ? shown + 1 : shown, shown, approx: more };
    }

    async getHomeHubs() {
        const d = await api(`${this.base}/list`);
        const items = d.items.slice(0, HOME_MAX).map((r) => this.toItem(r));
        return items.length ? [{ title: '추천 레시피', source: this.name, items, more: `#/library/${this.id}/recipes` }] : [];
    }

    async search(query) {
        const d = await api(`${this.base}/list?${new URLSearchParams({ q: query })}`);
        const items = d.items.slice(0, 40).map((r) => this.toItem(r));
        return items.length ? [{ title: `레시피 · ${this.name}`, items }] : [];
    }

    read(id, r = {}) {
        openArticle({ title: r.title || '레시피', sub: '만개의레시피', load: (rid) => this._load(rid), listenNext: false }, id);
    }

    async _load(id) {
        const r = (await api(`${this.base}/item/${encodeURIComponent(id)}`)).recipe;
        const nodes = [];
        if (r.intro) nodes.push(h('p', { text: r.intro }));
        if (r.ingredients.length) {
            nodes.push(h('h2', { text: '재료' }), h('ul', { class: 'recipe-ing' }, r.ingredients.map((t) => h('li', { text: t }))));
        }
        if (r.steps.length) {
            nodes.push(h('h2', { text: '만드는 순서' }));
            r.steps.forEach((s, i) => nodes.push(h('div', { class: 'recipe-step' },
                h('div', { class: 'recipe-step-no', text: `${i + 1}` }),
                h('div', { class: 'recipe-step-body' },
                    h('p', { text: s.text }),
                    s.image ? h('img', { src: s.image, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }) : null))));
        } else {
            nodes.push(h('p', { class: 'muted', text: '만드는 순서를 읽지 못했습니다. 원문에서 확인해 주세요.' }));
        }
        return {
            title: r.title, sub: '만개의레시피', meta: [r.by, ...(r.info || [])].filter(Boolean).join(' · '),
            cover: r.image, nodes, url: r.url, source: '출처: 만개의레시피 (10000recipe.com)',
        };
    }
}
