// 검색: 모든 소스에 같은 검색어를 던져서 소스별 줄로 보여준다.
import { h, clear } from '../util.js';
import { registry } from '../providers/index.js';
import { hubRow, emptyState, loading } from '../cards.js';

export async function renderSearch(main, _params, query, ctx) {
    const q = (query.get('q') || '').trim();
    const page = h('div', { class: 'page search' }, h('div', { class: 'page-head' }, h('h1', { text: q ? `"${q}" 검색 결과` : '검색' })));
    main.appendChild(page);
    if (!q) {
        page.appendChild(emptyState('magnifying-glass', '위쪽 검색창에 찾을 제목이나 채널명을 넣어 주세요'));
        return;
    }
    const body = h('div', {}, loading('찾는 중...'));
    page.appendChild(body);

    const results = await Promise.allSettled(registry.providers.map((p) => p.search(q)));
    if (!ctx.alive()) return;
    clear(body);
    let any = false;
    results.forEach((r, i) => {
        if (r.status === 'rejected') {
            body.appendChild(h('div', { class: 'home-note error', text: `${registry.providers[i].name}: ${r.reason.message}` }));
            return;
        }
        r.value.forEach((hub) => { any = true; body.appendChild(hubRow(hub)); });
    });
    if (!any) body.appendChild(emptyState('magnifying-glass', '찾은 것이 없습니다'));
}
