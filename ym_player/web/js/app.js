// 앱 뼈대: 왼쪽 사이드바(홈 / 라이브 TV / Plex 라이브러리 / 설정) + 위쪽 검색창 + 해시 라우터.
import { h, icon, clear, toast } from './util.js';
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
    nav.querySelectorAll('a').forEach((a) => {
        const href = a.getAttribute('href');
        a.classList.toggle('active', here === href || (href !== '#/home' && here.startsWith(href + '?')) || (href.startsWith('#/library') && here.startsWith(href)));
    });
}

function navLink(href, iconName, label) {
    return h('a', { href, class: 'nav-link' }, icon(iconName), h('span', { text: label }));
}

async function buildNav() {
    clear(nav);
    nav.appendChild(navLink('#/home', 'house', '홈'));
    const iptvs = registry.ofKind('iptv');
    if (iptvs.length) nav.appendChild(navLink('#/live', 'tower-broadcast', '라이브 TV'));

    for (const p of registry.providers) {
        const group = h('div', { class: 'nav-group' }, h('div', { class: 'nav-head', text: p.name }));
        nav.appendChild(group);
        if (p.kind === 'iptv') {
            group.appendChild(navLink(`#/live/${p.id}`, 'list', '채널 편성표'));
            continue;
        }
        group.appendChild(h('div', { class: 'nav-loading' }, icon('circle-notch', 'fa-spin')));
        p.getLibraries().then((libs) => {
            group.lastChild.remove();
            if (!libs.length) group.appendChild(h('div', { class: 'nav-note', text: '라이브러리 없음' }));
            libs.forEach((l) => group.appendChild(navLink(`#/library/${p.id}/${l.id}`, l.icon, l.title)));
            highlightNav();
        }).catch((e) => {
            group.lastChild.remove();
            group.appendChild(h('div', { class: 'nav-note error', title: e.message, text: '연결 실패' }));
        });
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
