// 앱 뼈대: 왼쪽 사이드바(홈 / 라이브 TV / Plex 라이브러리 / 설정) + 위쪽 검색창 + 해시 라우터.
import { h, icon, clear, toast, storeGet, storeSet, api, modal } from './util.js';
import { registry } from './providers/index.js';
import { emptyState } from './cards.js';
import './player.js';
import { loadFavorites } from './providers/iptv.js';
import { loadMyTheme, openThemeDialog } from './theme.js';
import { initVersionNotice } from './notice.js';
import { initViewMode } from './viewmode.js';
import { renderHome } from './views/home.js';
import { renderLibrary } from './views/library.js';
import { renderDetail } from './views/detail.js';
import { renderLive } from './views/live.js';
import { renderSearch } from './views/search.js';
import { renderSettings } from './views/settings.js';

const ROUTES = [
    [/^\/home$/, renderHome, 'home'],
    [/^\/library\/([^/]+)\/([^/]+)$/, renderLibrary, null],
    [/^\/item\/([^/]+)\/(.+)$/, renderDetail, null],
    [/^\/live(?:\/([^/]+))?$/, renderLive, 'live'],
    [/^\/search$/, renderSearch, null],
    [/^\/settings$/, renderSettings, 'settings'],
];

const main = document.getElementById('main');
const nav = document.getElementById('nav');
const searchInput = document.getElementById('search');
let renderSeq = 0;
let leaveHandlers = [];

function parseHash() {
    const raw = location.hash.replace(/^#/, '') || '/home';
    const [path, qs] = raw.split('?');
    return { path, query: new URLSearchParams(qs || '') };
}

async function render() {
    leaveHandlers.forEach((fn) => { try { fn(); } catch (e) { /* 정리 실패 무시 */ } });
    leaveHandlers = [];
    const seq = ++renderSeq;
    const { path, query } = parseHash();
    const ctx = {
        alive: () => seq === renderSeq,
        onLeave: (fn) => leaveHandlers.push(fn),
        rerender: () => render(),
        reloadProviders: async () => { await registry.load(); await buildNav(); },
    };
    clear(main);
    main.scrollTop = 0;
    document.body.classList.remove('nav-open');
    if (path === '/search') searchInput.value = query.get('q') || '';

    for (const [re, view, name] of ROUTES) {
        const m = path.match(re);
        if (!m) continue;
        highlightNav();
        if (name === 'settings' && !registry.isAdmin()) {
            // 관리자가 아니면 설정 화면 자체를 열지 않는다 (서버도 설정 저장/조회 상세를 막는다)
            main.appendChild(emptyState('lock', '관리자만 들어갈 수 있습니다', null, h('a', { class: 'btn', href: '#/home', text: '홈으로' })));
            return;
        }
        try {
            await view(main, m.slice(1).map((x) => (x === undefined ? x : decodeURIComponent(x))), query, ctx);
        } catch (e) {
            console.error(e);
            if (ctx.alive()) clear(main).appendChild(emptyState('triangle-exclamation', '화면을 그리지 못했습니다', e.message));
        }
        return;
    }
    main.appendChild(emptyState('circle-question', '없는 페이지입니다', null, h('a', { class: 'btn', href: '#/home', text: '홈으로' })));
}

function highlightNav() {
    const here = location.hash || '#/home';
    let activeGroup = null;
    nav.querySelectorAll('a').forEach((a) => {
        const href = a.getAttribute('href');
        const on = here === href || (href !== '#/home' && here.startsWith(href + '?')) || (href.startsWith('#/library') && here.startsWith(href));
        a.classList.toggle('active', on);
        if (on && a.closest('.nav-group')) activeGroup = a.closest('.nav-group');
    });
    // 다른 화면에서 그 소스의 메뉴로 들어오면(홈 카드 클릭 등) 그 소스를 펼쳐 준다
    if (activeGroup) openGroup(activeGroup.dataset.pid);
    const pid = currentItemPid(here);
    if (!activeGroup && pid) openGroup(pid);
}

// 상세 화면(#/item/{pid}/...)도 어느 소스 것인지 알 수 있다
function currentItemPid(hash) {
    const m = hash.match(/^#\/(?:item|library|live)\/([^/?]+)/);
    return m ? decodeURIComponent(m[1]) : null;
}

const NAV_OPEN_KEY = 'yp_nav_open';

/** 소스가 둘 이상이면 아코디언: 하나를 펼치면 나머지 소스의 하위 메뉴는 접는다. pid=null이면 모두 접기 */
function openGroup(pid) {
    const groups = [...nav.querySelectorAll('.nav-group')];
    if (groups.length < 2) return;
    if (pid !== null && !groups.some((g) => g.dataset.pid === pid)) return;
    groups.forEach((g) => {
        const open = g.dataset.pid === pid;
        g.classList.toggle('collapsed', !open);
        g.querySelector('.nav-head').setAttribute('aria-expanded', String(open));
    });
    storeSet(NAV_OPEN_KEY, pid);
}

function navLink(href, iconName, label) {
    return h('a', { href, class: 'nav-link' }, icon(iconName), h('span', { text: label }));
}

// ----- 사이드바 순서 (사람마다, 서버 prefs.nav_order) -----
function navOrder() {
    return (registry.prefs && registry.prefs.nav_order) || {};
}

/** saved에 있는 것은 그 순서로 앞에, 나머지(새로 생긴 것)는 원래 순서대로 뒤에 */
function ordered(list, keyOf, saved) {
    if (!saved || !saved.length) return list;
    const rank = new Map(saved.map((k, i) => [String(k), i]));
    return list.map((x, i) => [x, i]).sort(([a, ia], [b, ib]) => {
        const ra = rank.has(String(keyOf(a))) ? rank.get(String(keyOf(a))) : saved.length + ia;
        const rb = rank.has(String(keyOf(b))) ? rank.get(String(keyOf(b))) : saved.length + ib;
        return ra - rb;
    }).map(([x]) => x);
}

/** 소스 순서는 홈 화면 줄 순서에도 쓰이게 registry 자체를 정렬 */
function applySourceOrder() {
    registry.providers = ordered(registry.providers, (p) => p.id, navOrder().sources);
}

/** 위/아래 버튼 (순서 바꾸기 중에만 보임) */
function moveButtons(el) {
    const move = (dir) => (e) => {
        e.preventDefault(); e.stopPropagation();
        const sib = dir < 0 ? el.previousElementSibling : el.nextElementSibling;
        if (!sib || !sib.classList.contains(el.classList[0])) return;
        if (dir < 0) el.parentNode.insertBefore(el, sib); else el.parentNode.insertBefore(sib, el);
    };
    return h('span', { class: 'nav-move' },
        h('button', { class: 'icon-btn', type: 'button', title: '위로', on: { click: move(-1) } }, icon('chevron-up')),
        h('button', { class: 'icon-btn', type: 'button', title: '아래로', on: { click: move(1) } }, icon('chevron-down')));
}

function startNavEdit() {
    if (!nav._editGuard) {
        // 순서 바꾸는 동안 메뉴를 눌러도 이동하지 않게
        nav._editGuard = true;
        nav.addEventListener('click', (e) => {
            if (document.body.classList.contains('nav-editing') && e.target.closest('.nav-group a.nav-link')) e.preventDefault();
        }, true);
    }
    document.body.classList.add('nav-editing');
    nav.querySelectorAll('.nav-group .nav-link[data-lib]').forEach((a) => { if (!a.querySelector('.nav-move')) a.appendChild(moveButtons(a)); });
}

async function finishNavEdit(save, reset = false) {
    document.body.classList.remove('nav-editing');
    if (!save) { await buildNav(); return; }
    const order = reset ? {} : {
        sources: [...nav.querySelectorAll('.nav-group')].map((g) => g.dataset.pid),
        libs: Object.fromEntries([...nav.querySelectorAll('.nav-group')].map((g) => [g.dataset.pid,
            [...g.querySelectorAll('.nav-link[data-lib]')].map((a) => a.dataset.lib)])),
    };
    try {
        await api('/api/me/prefs', { method: 'PUT', json: { nav_order: order } });
        registry.prefs = { ...(registry.prefs || {}), nav_order: order };
        toast(reset ? '메뉴를 기본 순서로 되돌렸습니다.' : '메뉴 순서를 저장했습니다.', 'success');
    } catch (e) {
        toast(`저장하지 못했습니다: ${e.message}`, 'error');
    }
    // 설정에 적힌 순서로 돌린 뒤 저장한 순서를 입힌다 (기본 순서로 되돌린 경우도)
    registry.providers = ordered(registry.providers, (p) => p.id, (registry.config.sources || []).map((s) => s.id));
    applySourceOrder();
    await buildNav();
    if (location.hash.startsWith('#/home') || !location.hash || location.hash === '#/') window.dispatchEvent(new HashChangeEvent('hashchange'));
}

async function buildNav() {
    clear(nav);
    nav.appendChild(navLink('#/home', 'house', '홈'));
    const iptvs = registry.ofKind('iptv');
    // 모든 IPTV 소스를 합친 편성표. 소스가 하나뿐이면 그 소스의 "채널 편성표"와 똑같아서 메뉴에서 뺀다
    if (iptvs.length > 1) nav.appendChild(navLink('#/live', 'tower-broadcast', '라이브 TV'));

    const accordion = registry.providers.length > 1;
    applySourceOrder();
    const libOrder = navOrder().libs || {};
    for (const p of registry.providers) {
        const items = h('div', { class: 'nav-items' });
        const head = accordion
            ? h('button', {
                class: 'nav-head nav-toggle', title: `${p.name} 메뉴 펼치기/접기`,
                on: { click: () => openGroup(group.classList.contains('collapsed') ? p.id : null) },
            }, h('span', { text: p.name }), icon('chevron-down', 'nav-chevron'))
            : h('div', { class: 'nav-head', text: p.name });
        const group = h('div', { class: 'nav-group', dataset: { pid: p.id } }, head, items);
        group.insertBefore(moveButtons(group), items);   // 제목 줄 오른쪽 (순서 바꾸기 중에만 보임)
        nav.appendChild(group);
        if (p.kind === 'iptv') {
            items.appendChild(navLink(`#/live/${p.id}`, 'list', '채널 편성표'));
            continue;
        }
        const spinner = h('div', { class: 'nav-loading' }, icon('circle-notch', 'fa-spin'));
        items.appendChild(spinner);
        p.getLibraries().then((libs) => {
            spinner.remove();
            if (!libs.length) items.appendChild(h('div', { class: 'nav-note', text: '라이브러리 없음' }));
            ordered(libs, (l) => l.id, libOrder[p.id]).forEach((l) => {
                const a = navLink(`#/library/${p.id}/${l.id}`, l.icon, l.title);
                a.dataset.lib = l.id;
                if (document.body.classList.contains('nav-editing')) a.appendChild(moveButtons(a));
                items.appendChild(a);
            });
            highlightNav();
        }).catch((e) => {
            spinner.remove();
            items.appendChild(h('div', { class: 'nav-note error', title: e.message, text: '연결 실패' }));
        });
    }
    if (accordion) {
        // 처음엔 지난번에 펼쳐 둔 소스, 없으면 첫 소스
        const saved = storeGet(NAV_OPEN_KEY, undefined);
        const ids = registry.providers.map((p) => p.id);
        openGroup(saved === null || ids.includes(saved) ? saved : ids[0]);
    }
    // 순서 바꾸기: 소스(제목 줄)와 라이브러리 옆에 위/아래 버튼 -> [완료]하면 내 계정에 저장 (다른 기기에서도 같은 순서)
    nav.appendChild(h('div', { class: 'nav-order' },
        h('button', { class: 'nav-order-start', type: 'button', on: { click: startNavEdit } }, icon('arrow-down-up-across-line'), ' 메뉴 순서 바꾸기'),
        h('div', { class: 'nav-order-edit' },
            h('button', { class: 'btn btn-sm btn-accent', type: 'button', on: { click: () => finishNavEdit(true) } }, '완료'),
            h('button', { class: 'btn btn-sm', type: 'button', on: { click: () => finishNavEdit(false) } }, '취소'),
            h('button', { class: 'btn btn-sm', type: 'button', title: '설정에 적힌 순서로', on: { click: () => finishNavEdit(true, true) } }, '기본 순서'))));
    nav.appendChild(navFooter());
    highlightNav();
}

/** 사이드바 맨 아래: (관리자만) 설정, 그리고 내 계정 (테마 / 비밀번호 변경 / 로그아웃) */
function navFooter() {
    const u = registry.user || {};
    const foot = h('div', { class: 'nav-footer' });
    if (registry.isAdmin()) foot.appendChild(navLink('#/settings', 'gear', '설정'));
    foot.appendChild(h('div', { class: 'nav-account' },
        h('span', { class: 'avatar', text: (u.username || '?').slice(0, 1).toUpperCase() }),
        h('div', { class: 'who' }, h('b', { text: u.username || '' }), h('span', { text: u.role === 'admin' ? '관리자' : '사용자' })),
        h('button', { class: 'icon-btn', title: '테마', on: { click: openThemeDialog } }, icon('palette')),
        h('button', { class: 'icon-btn', title: '비밀번호 변경', on: { click: changePassword } }, icon('key')),
        h('button', { class: 'icon-btn', title: '로그아웃', on: { click: logout } }, icon('right-from-bracket'))));
    return foot;
}

async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* 이미 풀렸어도 로그인 화면으로 */ }
    location.href = '/login';
}

function changePassword() {
    const cur = h('input', { class: 'input', type: 'password', placeholder: '지금 비밀번호', autocomplete: 'current-password' });
    const next = h('input', { class: 'input', type: 'password', placeholder: '새 비밀번호 (6자 이상)', autocomplete: 'new-password' });
    const again = h('input', { class: 'input', type: 'password', placeholder: '새 비밀번호 확인', autocomplete: 'new-password' });
    const save = h('button', { class: 'btn btn-accent', text: '바꾸기' });
    const m = modal('비밀번호 변경', h('div', { class: 'src-fields' }, cur, next, again,
        h('span', { class: 'field-hint', text: '바꾸면 다른 기기의 로그인은 끊깁니다.' })), [save]);
    save.addEventListener('click', async () => {
        if (next.value !== again.value) return toast('새 비밀번호 확인이 다릅니다.', 'error');
        save.disabled = true;
        try {
            await api('/api/auth/password', { method: 'POST', json: { current: cur.value, new: next.value } });
            m.close();
            toast('비밀번호를 바꿨습니다.', 'success');
        } catch (e) {
            toast(e.message, 'error');
        } finally {
            save.disabled = false;
        }
    });
    cur.focus();
}

/** 위쪽 [포스터 크기] 막대: 카드와 상세 화면 포스터를 60~160%로 (이 브라우저에 기억, 두 번 누르면 100%) */
const POSTER_KEY = 'yp_poster_scale';
function initPosterSize() {
    const input = document.getElementById('posterSize');
    const label = document.getElementById('posterSizeVal');
    if (!input) return;
    const apply = (pct, save) => {
        pct = Math.min(160, Math.max(60, Math.round(Number(pct) / 5) * 5 || 100));
        input.value = pct;
        label.textContent = `${pct}%`;
        input.title = `포스터 크기 ${pct}%`;
        document.documentElement.style.setProperty('--poster-scale', String(pct / 100));
        if (save) storeSet(POSTER_KEY, pct);
    };
    apply(storeGet(POSTER_KEY, 100), false);
    input.addEventListener('input', () => apply(input.value, true));
    // 놓으면 포커스를 풀어 ← → 키가 재생기·화면 단축키로 가게
    input.addEventListener('change', () => input.blur());
    input.closest('.poster-size').addEventListener('dblclick', () => apply(100, true));
}

async function boot() {
    initPosterSize();
    initViewMode();
    document.getElementById('menuBtn').addEventListener('click', () => document.body.classList.toggle('nav-open'));
    document.getElementById('navScrim').addEventListener('click', () => document.body.classList.remove('nav-open'));
    document.getElementById('searchForm').addEventListener('submit', (e) => {
        e.preventDefault();
        const q = searchInput.value.trim();
        if (q) location.hash = `#/search?q=${encodeURIComponent(q)}`;
    });
    document.addEventListener('keydown', (e) => {
        // "/" 로 검색창 바로가기 (재생기가 떠 있을 땐 재생기 단축키가 우선)
        if (e.key === '/' && !document.body.classList.contains('player-open') && document.activeElement.tagName !== 'INPUT') {
            e.preventDefault();
            searchInput.focus();
        }
    });
    try {
        registry.user = (await api('/api/auth/me')).user;
        const [, prefs] = await Promise.all([registry.load(), loadFavorites()]);
        registry.prefs = prefs || {};
        applySourceOrder();
        if (prefs) loadMyTheme(prefs);
        initVersionNotice(prefs, registry.isAdmin());
    } catch (e) {
        toast(`설정을 불러오지 못했습니다: ${e.message}`, 'error', 8000);
    }
    await buildNav();
    window.addEventListener('hashchange', render);
    render();
}

boot();
