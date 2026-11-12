// Plex 라이브러리: [추천] 탭(섹션 허브) / [라이브러리] 탭(정렬 가능한 포스터 격자, 스크롤하면 더 불러옴)
import { h, icon, clear, storeGet, storeSet } from '../util.js';
import { registry } from '../providers/index.js';
import { player } from '../player.js';
import { hubRow, posterCard, channelCard, listItem, playlistOf, emptyState, loading } from '../cards.js';
import { getViewMode } from '../viewmode.js';

const PAGE = 60;

export async function renderLibrary(main, [pid, libId], query, ctx) {
    const p = registry.byId(pid);
    if (!p) return main.appendChild(emptyState('circle-question', '소스를 찾을 수 없습니다'));
    if (p.kind === 'iptv') { location.hash = `#/live/${pid}`; return; }

    const libs = await p.getLibraries().catch(() => []);
    if (!ctx.alive()) return;
    const lib = libs.find((l) => l.id === libId) || { title: '라이브러리', type: 'movie' };
    if (p.prepareLibrary) await p.prepareLibrary(libId);   // (도로 CCTV: 분류 목록을 알려면 먼저 받아야 함)
    if (!ctx.alive()) return;
    // 추천 탭은 섹션 허브가 있는 소스(Plex)만
    const tab = p.getLibraryHubs ? (query.get('tab') || 'library') : 'library';

    const tabLink = (id, label) => h('a', {
        class: `tab${tab === id ? ' active' : ''}`, href: `#/library/${pid}/${libId}?tab=${id}`, text: label,
    });
    const body = h('div', { class: 'lib-body' });
    const page = h('div', { class: 'page library' },
        h('div', { class: 'page-head' },
            // 소스 › 라이브러리 (이름이 같으면 한 번만 - 만개의레시피 등 라이브러리가 하나인 소스)
            h('h1', {}, icon(lib.icon || 'film'),
                p.name && p.name !== lib.title ? [h('span', { class: 'crumb-src', text: p.name }), h('span', { class: 'crumb-sep', text: '›' })] : null,
                h('span', { class: 'crumb-title', text: lib.title })),
            p.getLibraryHubs ? h('div', { class: 'tabs' }, tabLink('recommended', '추천'), tabLink('library', '라이브러리')) : null),
        body);
    main.appendChild(page);

    if (tab === 'recommended') {
        body.appendChild(loading());
        try {
            const hubs = await p.getLibraryHubs(libId);
            if (!ctx.alive()) return;
            clear(body);
            if (!hubs.length) body.appendChild(emptyState('film', '추천 항목이 없습니다'));
            hubs.forEach((hub) => body.appendChild(hubRow(hub)));
        } catch (e) {
            clear(body).appendChild(emptyState('triangle-exclamation', '불러오지 못했습니다', e.message));
        }
        return;
    }

    // 정렬/필터는 라이브러리마다 기억해둔다
    const prefKey = `yp_lib_${pid}_${libId}`;
    const pref = storeGet(prefKey, { sort: 'titleSort', unwatched: false, kind: '' });
    const sortSel = h('select', { class: 'select' },
        p.librarySorts(libId).map(([v, label]) => h('option', { value: v, text: label, selected: v === pref.sort })));
    const unwatchedChk = h('input', { type: 'checkbox', checked: !!pref.unwatched });
    // 텔레그램: 영상/사진 골라 보기 (관리자가 "영상만/사진만"으로 정한 채널은 없음)
    const kindOpts = p.libraryKinds ? p.libraryKinds(libId) : null;
    const kindSel = kindOpts ? h('select', { class: 'select' },
        kindOpts.map(([v, label]) => h('option', { value: v, text: label,
            selected: v === (pref.kind || (p.libraryKindDefault ? p.libraryKindDefault(libId) : '')) }))) : null;
    // 분류를 위쪽 단추 줄로 보여 주는 소스 (공중파 라디오의 지역): 단추가 위 select의 값을 바꾼다
    const kindChips = kindSel && p.libraryKindStyle && p.libraryKindStyle(libId) === 'chips' ? h('div', { class: 'kind-chips', role: 'tablist' }) : null;
    const drawChips = () => {
        if (!kindChips) return;
        clear(kindChips);
        kindOpts.forEach(([v, label]) => kindChips.appendChild(h('button', {
            class: `chip${v === kindSel.value ? ' active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(v === kindSel.value), text: label,
            on: { click: () => { if (kindSel.value === v) return; kindSel.value = v; drawChips(); kindSel.dispatchEvent(new Event('change')); } },
        })));
    };
    drawChips();
    // 소스 안에서 찾기 (만개의레시피 등 - 소스가 librarySearchHint를 주면 검색창). 검색어는 주소(?q=)에도 남겨 뒤로 가기로 돌아와도 그대로
    const searchHint = p.librarySearchHint ? p.librarySearchHint(libId) : '';
    const searchBox = searchHint ? h('input', {
        type: 'search', class: 'input lib-search', placeholder: searchHint, value: query.get('q') || '', enterkeyhint: 'search',
    }) : null;
    const countEl = h('span', { class: 'muted' });
    // 고르기 귀찮을 때: 이 라이브러리(채널)에서 아무거나 틀고, 끝나면 또 아무거나 (Plex는 [안 본 것만]을 따름)
    const randomBtn = p.randomPick && (!p.canRandom || p.canRandom(libId)) ? h('button', {
        class: 'btn', title: '이 라이브러리에서 아무거나 골라 계속 재생',
        on: { click: () => player.playRandom((exclude) => p.randomPick(libId, { unwatched: unwatchedChk.checked, exclude }), lib.title) },
    }, icon('shuffle'), ' 랜덤 재생') : null;
    const gridClass = `grid${lib.type === 'artist' || String(lib.type).startsWith('podcast') ? ' grid-square' : ''}${lib.type === 'telegram' ? ' grid-wide' : ''}${lib.type === 'radio' || lib.type === 'cctv' ? ' grid-channels' : ''}`;
    const grid = h('div', { class: gridClass });
    const sentinel = h('div', { class: 'sentinel' });
    // 보기 방식 (위쪽 [보기] 메뉴): 그리드 / 세부 / 테이블 (라디오 방송국도 - 테이블은 분류·LIVE 칸)
    let loaded = [];
    let mode = 'grid';
    const tableHead = () => h('div', { class: 'table-row table-head', role: 'row' },
        h('span'), h('span', { text: { radio: '방송국', cctv: 'CCTV' }[lib.type] || '제목' }),
        h('span', { class: 'table-cell', text: lib.type === 'radio' || lib.type === 'cctv' ? '분류' : '연도·날짜' }),
        h('span', { class: 'table-cell', text: lib.type === 'radio' || lib.type === 'cctv' ? '' : '길이' }), h('span', { text: '상태' }));
    const setMode = () => {
        mode = getViewMode();
        grid.className = mode === 'grid' ? gridClass : (mode === 'detail' ? 'item-list' : 'item-table');
    };
    const draw = (items) => {
        const playlist = playlistOf(loaded);
        for (const it of items) {
            if (mode === 'table' && !grid.firstChild) grid.appendChild(tableHead());
            grid.appendChild(mode !== 'grid' ? listItem(it, mode, playlist) : it.type === 'channel' ? channelCard(it, playlist) : posterCard(it));
        }
    };
    setMode();
    const onView = () => {
        setMode();
        clear(grid);
        draw(loaded);
        if (!loaded.length && start >= total) grid.appendChild(emptyState('film', '항목이 없습니다'));
    };
    window.addEventListener('yp-view', onView);
    ctx.onLeave(() => window.removeEventListener('yp-view', onView));
    // (append는 null을 "null" 글자로 넣는다 - 단추 줄이 없는 소스는 빼고)
    body.append(...[kindChips].filter(Boolean),
        h('div', { class: 'toolbar' },
            countEl,
            searchBox,
            randomBtn,
            p.kind === 'plex' || p.kind === 'podcast' ? h('label', { class: 'check' }, unwatchedChk, p.kind === 'podcast' ? ' 안 들은 것만' : ' 안 본 것만') : null,
            kindSel && !kindChips ? h('label', { class: 'muted' }, `${p.libraryKindLabel ? p.libraryKindLabel() : '보기'} `, kindSel) : null,
            // 소스가 내는 단추 (공중파 라디오의 [편집] 등) - 누른 뒤 목록을 다시 그리게 reset을 넘긴다
            ...(p.libraryTools ? p.libraryTools(libId, () => reset()) : []),
            // 고를 것이 하나뿐이면(만개의레시피 등) 정렬 칸을 두지 않는다
            sortSel.options.length > 1 ? h('label', { class: 'muted' }, '정렬 ', sortSel) : null),
        grid, sentinel);

    let start = 0, total = Infinity, busy = false, gen = 0;
    const loadMore = async () => {
        if (busy || start >= total || !ctx.alive()) return;
        busy = true;
        const myGen = gen;
        try {
            const res = await p.getLibraryItems(libId, {
                start, size: PAGE, sort: sortSel.value, unwatched: unwatchedChk.checked, kind: kindSel ? kindSel.value : '',
                q: searchBox ? searchBox.value.trim() : '',
            });
            if (myGen !== gen || !ctx.alive()) return;
            total = res.total;
            start += res.items.length;
            if (!res.items.length) total = start;
            // 텔레그램처럼 오래된 쪽을 아직 다 훑지 않은 소스는 "지금까지 찾은 수+"로
            countEl.textContent = res.approx ? `${(res.shown || 0).toLocaleString()}개+` : `${total.toLocaleString()}개`;
            // 라디오 방송국처럼 채널이면 채널 카드 (누르면 바로 재생, 위/아래로 다른 방송국)
            loaded = loaded.concat(res.items);
            draw(res.items);
            if (!start) grid.appendChild(searchBox && searchBox.value.trim()
                ? emptyState('magnifying-glass', `"${searchBox.value.trim()}" 검색 결과가 없습니다`) : emptyState('film', '항목이 없습니다'));
        } catch (e) {
            if (myGen === gen) grid.appendChild(emptyState('triangle-exclamation', '불러오지 못했습니다', e.message));
            total = start;
        } finally {
            busy = false;
        }
        // 화면이 아직 안 찼으면 계속
        if (myGen === gen && sentinel.getBoundingClientRect().top < window.innerHeight + 400) loadMore();
    };
    const reset = () => {
        storeSet(prefKey, { sort: sortSel.value, unwatched: unwatchedChk.checked, kind: kindSel ? kindSel.value : '' });
        gen++; start = 0; total = Infinity; busy = false; loaded = [];
        clear(grid);
        loadMore();
    };
    sortSel.addEventListener('change', reset);
    unwatchedChk.addEventListener('change', reset);
    if (kindSel) kindSel.addEventListener('change', reset);
    if (searchBox) {
        // 글자를 칠 때마다가 아니라 잠깐 멈추거나 Enter를 누르면 (원본 사이트에 매번 묻지 않게)
        let timer = 0, last = searchBox.value.trim();
        if (kindSel) kindSel.disabled = !!last;   // 검색 중에는 분류를 쓰지 않는다
        const go = () => {
            clearTimeout(timer);
            const q = searchBox.value.trim();
            if (q === last) return;
            last = q;
            if (kindSel) kindSel.disabled = !!q;
            if (kindChips) kindChips.classList.toggle('disabled', !!q);
            const params = new URLSearchParams(query);
            if (q) params.set('q', q); else params.delete('q');
            const qs = params.toString();
            history.replaceState(null, '', `#/library/${pid}/${libId}${qs ? `?${qs}` : ''}`);
            reset();
        };
        searchBox.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(go, 600); });
        searchBox.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
        searchBox.addEventListener('search', go);   // (검색창의 지우기 ×)
        ctx.onLeave(() => clearTimeout(timer));
    }

    const io = new IntersectionObserver((entries) => {
        if (!ctx.alive()) return io.disconnect();
        if (entries.some((e) => e.isIntersecting)) loadMore();
    }, { rootMargin: '600px' });
    io.observe(sentinel);
    ctx.onLeave(() => io.disconnect());
    loadMore();
}
