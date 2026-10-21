// 라이브러리 보기 방식 (위쪽 막대의 [보기] 메뉴): 그리드 / 세부(포스터 + 줄거리) / 테이블(한 줄씩)
// 이 브라우저에 기억하고, 바뀌면 window에 'yp-view' 이벤트 - 라이브러리 화면이 받은 항목을 다시 그린다.
import { h, icon, storeGet, storeSet } from './util.js';

const KEY = 'yp_view_mode';
export const VIEW_MODES = [['grid', '그리드 보기', 'table-cells-large'], ['detail', '세부 보기', 'rectangle-list'], ['table', '테이블 보기', 'table-list']];

export function getViewMode() {
    const v = storeGet(KEY, 'grid');
    return VIEW_MODES.some(([m]) => m === v) ? v : 'grid';
}

export function initViewMode() {
    const box = document.getElementById('viewMode');
    if (!box) return;
    const btn = box.querySelector('button');
    const menu = h('div', { class: 'view-menu hidden', role: 'menu' });
    box.appendChild(menu);
    const draw = () => {
        const cur = getViewMode();
        const [, , ic] = VIEW_MODES.find(([m]) => m === cur);
        btn.firstElementChild.className = `fa-solid fa-${ic}`;
        menu.replaceChildren(...VIEW_MODES.map(([m, label]) => h('button', {
            class: `view-item${m === cur ? ' active' : ''}`, role: 'menuitemradio', 'aria-checked': String(m === cur),
            on: { click: () => { storeSet(KEY, m); close(); draw(); window.dispatchEvent(new CustomEvent('yp-view', { detail: m })); } },
        }, h('span', { class: 'view-check' }, m === cur ? icon('check') : null), label)));
    };
    const close = () => menu.classList.add('hidden');
    btn.addEventListener('click', (e) => { e.stopPropagation(); menu.classList.toggle('hidden'); });
    document.addEventListener('click', (e) => { if (!box.contains(e.target)) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    // 보기 방식은 라이브러리 화면에만 (홈·상세는 줄/한 항목이라)
    const route = () => document.body.classList.toggle('lib-route', location.hash.startsWith('#/library/'));
    window.addEventListener('hashchange', route);
    route();
    draw();
}
