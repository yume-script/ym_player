// 앱 뼈대: 왼쪽 사이드바(홈 / 라이브 TV / Plex 라이브러리 / 설정) + 위쪽 검색창 + 해시 라우터.
import { h, icon, clear, toast, storeGet, storeSet } from './util.js';
import { registry } from './providers/index.js';
import { emptyState } from './cards.js';
import './player.js';
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

    for (const [re, view] of ROUTES) {
        const m = path.match(re);
        if (!m) continue;
        highlightNav();
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

async function buildNav() {
    clear(nav);
    nav.appendChild(navLink('#/home', 'house', '홈'));
    const iptvs = registry.ofKind('iptv');
    if (iptvs.length) nav.appendChild(navLink('#/live', 'tower-broadcast', '라이브 TV'));

    const accordion = registry.providers.length > 1;
    for (const p of registry.providers) {
        const items = h('div', { class: 'nav-items' });
        const head = accordion
            ? h('button', {
                class: 'nav-head nav-toggle', title: `${p.name} 메뉴 펼치기/접기`,
                on: { click: () => openGroup(group.classList.contains('collapsed') ? p.id : null) },
            }, h('span', { text: p.name }), icon('chevron-down', 'nav-chevron'))
            : h('div', { class: 'nav-head', text: p.name });
        const group = h('div', { class: 'nav-group', dataset: { pid: p.id } }, head, items);
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
            libs.forEach((l) => items.appendChild(navLink(`#/library/${p.id}/${l.id}`, l.icon, l.title)));
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
    highlightNav();
}

async function boot() {
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
        await registry.load();
    } catch (e) {
        toast(`설정을 불러오지 못했습니다: ${e.message}`, 'error', 8000);
    }
    await buildNav();
    window.addEventListener('hashchange', render);
    render();
}

boot();
