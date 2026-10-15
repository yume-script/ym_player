// 화면 테마와 강조색. <html data-theme data-accent>만 바꾸면 app.css의 변수가 따라 바뀐다.
// 관리자 기본값은 서버가 HTML에 넣어 주고(data-default-*), 사용자가 고른 값은 서버(/api/me/prefs)에 두고
// 다음에 열 때 깜빡이지 않게 브라우저(yp_theme)에도 적어 둔다 - index.html/login.html 머리의 짧은 스크립트가 먼저 읽는다.
import { api, h, icon, modal, storeGet, storeSet, toast } from './util.js';

export const THEMES = [
    { id: 'dark', label: '다크', colors: ['#141517', '#1d1f22', '#ececec'] },
    { id: 'light', label: '라이트', colors: ['#f3f4f6', '#ffffff', '#1c1e22'] },
    { id: 'black', label: '블랙 (OLED)', colors: ['#000000', '#0d0e10', '#ececec'] },
    { id: 'navy', label: '네이비', colors: ['#0f1623', '#172030', '#e6ebf3'] },
    { id: 'auto', label: '시스템 따라', colors: ['#141517', '#f3f4f6', '#888888'] },
];
export const ACCENTS = [
    { id: 'amber', label: '주황 (기본)', color: '#e5a00d' },
    { id: 'blue', label: '파랑', color: '#3b82f6' },
    { id: 'green', label: '초록', color: '#22c55e' },
    { id: 'purple', label: '보라', color: '#a855f7' },
    { id: 'red', label: '빨강', color: '#ef4444' },
    { id: 'pink', label: '분홍', color: '#ec4899' },
    { id: 'teal', label: '청록', color: '#14b8a6' },
];
const KEY = 'yp_theme';
const root = document.documentElement;
// 관리자 기본값 (처음 HTML에 들어온 값)
const defaults = { theme: root.dataset.defaultTheme || 'dark', accent: root.dataset.defaultAccent || 'amber' };
let mine = { theme: '', accent: '' };

/** 지금 보이는 값으로 <html>을 고친다. 빈 값은 관리자 기본값 */
export function applyTheme() {
    root.dataset.theme = mine.theme || defaults.theme;
    root.dataset.accent = mine.accent || defaults.accent;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = getComputedStyle(root).getPropertyValue('--bg').trim() || '#141517';
}

export function setDefaults(d) {
    if (d.theme) defaults.theme = d.theme;
    if (d.accent) defaults.accent = d.accent;
    applyTheme();
}

/** 로그인 뒤 서버에 저장된 내 선택을 불러온다 (다른 기기에서 고른 것도 따라오게) */
export function loadMyTheme(prefs) {
    mine = { theme: (prefs && prefs.theme) || '', accent: (prefs && prefs.accent) || '' };
    storeSet(KEY, mine.theme || mine.accent ? mine : null);
    applyTheme();
}

async function saveMine() {
    storeSet(KEY, mine.theme || mine.accent ? mine : null);
    applyTheme();
    try {
        await api('/api/me/prefs', { method: 'PUT', json: mine });
    } catch (e) {
        toast(`테마 저장 실패: ${e.message}`, 'error');
    }
}

function swatch(t) {
    return h('div', { class: 'sw' }, t.colors.map((c) => h('i', { style: { background: c } })));
}

/**
 * 테마 고르기. 고르는 즉시 화면에 반영한다.
 * opts.admin 이면 관리자 기본값을 고르고 onPick({theme, accent})으로 넘긴다 (저장은 부른 쪽에서).
 */
export function themePicker({ admin = false, value = null, onPick = null } = {}) {
    const cur = admin ? { ...value } : { ...mine };
    const box = h('div', { class: 'theme-pick' });
    const draw = () => {
        const shownTheme = cur.theme || defaults.theme;
        const shownAccent = cur.accent || defaults.accent;
        const pick = async (key, id) => {
            cur[key] = id;
            if (admin) { onPick && onPick({ ...cur }); } else { mine = { ...cur }; await saveMine(); }
            draw();
        };
        box.replaceChildren(...[
            h('div', {},
                h('h4', { text: '테마' }),
                h('div', { class: 'theme-grid' }, THEMES.map((t) => h('button', {
                    type: 'button', class: `theme-opt${shownTheme === t.id ? ' on' : ''}`, dataset: { theme: t.id },
                    on: { click: () => pick('theme', t.id) },
                }, swatch(t), h('span', { text: t.label }))))),
            h('div', {},
                h('h4', { text: '강조색' }),
                h('div', { class: 'accent-row' }, ACCENTS.map((a) => h('button', {
                    type: 'button', class: `accent-opt${shownAccent === a.id ? ' on' : ''}`, title: a.label, dataset: { accent: a.id },
                    style: { background: a.color }, on: { click: () => pick('accent', a.id) },
                }, shownAccent === a.id ? icon('check') : null)))),
            admin ? null : h('label', { class: 'theme-default' },
                h('input', {
                    type: 'checkbox', checked: !cur.theme && !cur.accent,
                    on: { change: async (e) => { if (e.target.checked) { cur.theme = ''; cur.accent = ''; mine = { ...cur }; await saveMine(); draw(); } } },
                }),
                '관리자가 정한 기본 테마 쓰기'),
        ].filter(Boolean));
    };
    draw();
    return box;
}

export function openThemeDialog() {
    const m = modal('테마', themePicker(), [h('button', { class: 'btn btn-accent', on: { click: () => m.close() } }, '닫기')]);
}

// 시스템 테마가 바뀌면 (auto일 때) 주소창 색도 따라가게
try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', applyTheme);
} catch (e) { /* 오래된 브라우저 */ }
mine = storeGet(KEY, null) || mine;
