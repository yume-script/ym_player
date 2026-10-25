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
    // 추천 탭은 섹션 허브가 있는 소스(Plex)만
    const tab = p.getLibraryHubs ? (query.get('tab') || 'library') : 'library';

    const tabLink = (id, label) => h('a', {
        class: `tab${tab === id ? ' active' : ''}`, href: `#/library/${pid}/${libId}?tab=${id}`, text: label,
    });
    const body = h('div', { class: 'lib-body' });
    const page = h('div', { class: 'page library' },
        h('div', { class: 'page-head' },
            h('h1', {}, icon(lib.icon || 'film'), ` ${lib.title}`),
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
        p.librarySorts().map(([v, label]) => h('option', { value: v, text: label, selected: v === pref.sort })));
    const unwatchedChk = h('input', { type: 'checkbox', checked: !!pref.unwatched });
    // 텔레그램: 영상/사진 골라 보기 (관리자가 "영상만/사진만"으로 정한 채널은 없음)
    const kindOpts = p.libraryKinds ? p.libraryKinds(libId) : null;
    const kindSel = kindOpts ? h('select', { class: 'select' },
        kindOpts.map(([v, label]) => h('option', { value: v, text: label, selected: v === (pref.kind || '') }))) : null;
    const countEl = h('span', { class: 'muted' });
    // 고르기 귀찮을 때: 이 라이브러리(채널)에서 아무거나 틀고, 끝나면 또 아무거나 (Plex는 [안 본 것만]을 따름)
    const randomBtn = p.randomPick && (!p.canRandom || p.canRandom(libId)) ? h('button', {
        class: 'btn', title: '이 라이브러리에서 아무거나 골라 계속 재생',
        on: { click: () => player.playRandom((exclude) => p.randomPick(libId, { unwatched: unwatchedChk.checked, exclude }), lib.title) },
    }, icon('shuffle'), ' 랜덤 재생') : null;
    const gridClass = `grid${lib.type === 'artist' ? ' grid-square' : ''}${lib.type === 'telegram' ? ' grid-wide' : ''}${lib.type === 'radio' ? ' grid-channels' : ''}`;
    const grid = h('div', { class: gridClass });
    const sentinel = h('div', { class: 'sentinel' });
    // 보기 방식 (위쪽 [보기] 메뉴): 그리드 / 세부 / 테이블 (라디오 방송국도 - 테이블은 분류·LIVE 칸)
    let loaded = [];
    let mode = 'grid';
    const tableHead = () => h('div', { class: 'table-row table-head', role: 'row' },
        h('span'), h('span', { text: lib.type === 'radio' ? '방송국' : '제목' }), h('span', { class: 'table-cell', text: lib.type === 'radio' ? '분류' : '연도·날짜' }),
        h('span', { class: 'table-cell', text: lib.type === 'radio' ? '' : '길이' }), h('span', { text: '상태' }));
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
    body.append(
        h('div', { class: 'toolbar' },
            countEl,
            randomBtn,
            p.kind === 'plex' ? h('label', { class: 'check' }, unwatchedChk, ' 안 본 것만') : null,
            kindSel ? h('label', { class: 'muted' }, `${p.libraryKindLabel ? p.libraryKindLabel() : '보기'} `, kindSel) : null,
            h('label', { class: 'muted' }, '정렬 ', sortSel)),
        grid, sentinel);

    let start = 0, total = Infinity, busy = false, gen = 0;
    const loadMore = async () => {
        if (busy || start >= total || !ctx.alive()) return;
        busy = true;
        const myGen = gen;
        try {
            const res = await p.getLibraryItems(libId, {
                start, size: PAGE, sort: sortSel.value, unwatched: unwatchedChk.checked, kind: kindSel ? kindSel.value : '',
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
            if (!start) grid.appendChild(emptyState('film', '항목이 없습니다'));
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

    const io = new IntersectionObserver((entries) => {
        if (!ctx.alive()) return io.disconnect();
        if (entries.some((e) => e.isIntersecting)) loadMore();
    }, { rootMargin: '600px' });
    io.observe(sentinel);
    ctx.onLeave(() => io.disconnect());
    loadMore();
}
