// TV 리모컨으로 고르기 (공간 이동): 방향키로 화면에서 그 방향의 가장 가까운 카드·메뉴·단추로 초점을 옮기고,
// 확인(Enter)은 그것을 누르고, 뒤로 키(Backspace·GoBack·Tizen 10009·webOS 461)는 창 닫기 → 메뉴 닫기 → 뒤로 가기.
// - 재생기가 크게 떠 있을 때(재생기 단축키)·글을 쓰는 칸에서는 끼어들지 않는다 (글 칸에서 위/아래는 칸 밖으로)
// - 창(.modal)이 떠 있으면 그 안에서만 움직인다
// - 리모컨을 쓰기 시작하면(방향키) 또는 TV 브라우저면 body.tv-mode - 초점이 크게 보이고, 화면을 옮길 때 첫 칸에 초점
const TV_UA = /SMART-TV|SmartTV|Tizen|Web0S|webOS|NetCast|BRAVIA|Android TV|GoogleTV|AFT[A-Z]|CrKey|HbbTV|VIDAA/i;
const DIRS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', 38: 'up', 40: 'down', 37: 'left', 39: 'right' };
const BACK_KEYS = new Set(['GoBack', 'BrowserBack', 'Back']);
const BACK_CODES = new Set([10009, 461, 8]);   // Tizen, webOS, Backspace
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function isBackKey(e) {
    if (BACK_KEYS.has(e.key)) return true;
    return BACK_CODES.has(e.keyCode) && !(e.keyCode === 8 && isTyping(document.activeElement));
}

function isTyping(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const t = el.tagName;
    return t === 'TEXTAREA' || t === 'SELECT' || (t === 'INPUT' && !['checkbox', 'radio', 'range', 'button', 'submit'].includes(el.type));
}

function visible(el) {
    if (!el.getClientRects().length) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.opacity !== '0' && !el.closest('.hidden, [inert], [aria-hidden="true"]');
}

function scope() {
    const modals = [...document.querySelectorAll('.modal')].filter((m) => !m.classList.contains('hidden'));
    if (modals.length) return modals[modals.length - 1];
    if (document.body.classList.contains('nav-open')) return document.getElementById('nav');
    return document.body;
}

function candidates(root) {
    return [...root.querySelectorAll(FOCUSABLE)].filter((el) => visible(el) && !el.closest('.player, .sk-card'));
}

/** from에서 dir 방향의 가장 가까운 칸. 같은 줄(좌우면 높이가, 위아래면 너비가 겹치는 칸)을 먼저, 그중 가장 가까운 것.
 * 같은 줄에 없으면 그 방향 반쪽에서 축 거리 + 옆으로 벗어난 거리 x3이 가장 작은 것 (줄 끝에서 오른쪽을 눌러도 위 단추로 튀지 않게
 * 좌우는 같은 줄이 없으면 높이 차이가 큰 칸으로는 가지 않는다) */
export function nearest(from, dir, list) {
    const a = from.getBoundingClientRect();
    const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    const horiz = dir === 'left' || dir === 'right';
    let best = null, bestScore = Infinity, bestLine = null, bestLineScore = Infinity;
    for (const el of list) {
        if (el === from || el.contains(from) || from.contains(el)) continue;
        const b = el.getBoundingClientRect();
        const bx = b.left + b.width / 2, by = b.top + b.height / 2;
        let main, cross;
        if (dir === 'left') { main = a.left - b.right; cross = Math.abs(ay - by); if (bx >= ax - 1) continue; }
        else if (dir === 'right') { main = b.left - a.right; cross = Math.abs(ay - by); if (bx <= ax + 1) continue; }
        else if (dir === 'up') { main = a.top - b.bottom; cross = Math.abs(ax - bx); if (by >= ay - 1) continue; }
        else { main = b.top - a.bottom; cross = Math.abs(ax - bx); if (by <= ay + 1) continue; }
        const overlap = horiz ? Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) : Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const score = Math.max(0, main) + cross * 3;
        if (overlap > 4) {
            const lineScore = Math.max(0, main) + cross * 0.5;
            if (lineScore < bestLineScore) { bestLineScore = lineScore; bestLine = el; }
        } else if (score < bestScore && !horiz) {
            bestScore = score; best = el;
        }
    }
    const nav = document.getElementById('nav'), main = document.getElementById('main');
    const found = bestLine || best;
    // 본문에서 왼쪽으로 사이드바에 들어가면 늘 지금 메뉴로 (줄 높이에 맞는 아무 메뉴가 아니라)
    if (found && dir === 'left' && nav && main && main.contains(from) && nav.contains(found)) {
        return list.find((el) => nav.contains(el) && el.classList.contains('active')) || found;
    }
    if (found && dir === 'right' && nav && main && nav.contains(from) && main.contains(found)
        && lastMain && lastMain.isConnected && main.contains(lastMain) && visible(lastMain)) return lastMain;
    if (found) return found;
    // 좌우로 같은 줄이 없을 때: 본문 왼쪽 끝에서 왼쪽 -> 사이드바의 지금 메뉴, 사이드바에서 오른쪽 -> 본문의 가장 가까운 칸
    if (dir === 'left' && nav && main && main.contains(from)) {
        const act = list.find((el) => nav.contains(el) && el.classList.contains('active'));
        return act || list.find((el) => nav.contains(el)) || null;
    }
    if (dir === 'right' && nav && main && nav.contains(from)) {
        if (lastMain && lastMain.isConnected && main.contains(lastMain) && visible(lastMain)) return lastMain;   // 들어오기 전에 보던 칸으로
        const inMain = list.filter((el) => main.contains(el));
        let pick = null, d = Infinity;
        for (const el of inMain) {
            const b = el.getBoundingClientRect();
            const dd = Math.abs(b.top + b.height / 2 - ay) + Math.max(0, b.left - a.right) * 0.2;
            if (dd < d) { d = dd; pick = el; }
        }
        return pick;
    }
    return null;
}

let lastMain = null;   // 본문에서 마지막으로 고른 칸 (사이드바에 갔다가 오른쪽으로 돌아올 때)

function focusEl(el) {
    const main = document.getElementById('main');
    if (main && main.contains(el)) lastMain = el;
    el.focus({ preventScroll: true });
    // 가로 줄(카드 줄·칩)은 가로로, 페이지는 세로로 보이게
    el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    const r = el.getBoundingClientRect();
    const top = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')) || 60) + 8;
    if (r.top < top || r.bottom > innerHeight - 8) el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
}

/** 화면의 첫 칸에 초점 (본문 먼저) */
export function focusFirst() {
    const s = scope();
    const list = candidates(s === document.body ? (document.getElementById('main') || s) : s);
    const first = list.find((el) => el.matches('.hero-play, .card, .lib-chip.active')) || list[0];
    if (first) focusEl(first);
}

function goBack() {
    const s = scope();
    if (s.classList && s.classList.contains('modal')) {
        const close = s.querySelector('.modal-head button[title="닫기"]');
        if (close) close.click(); else s.remove();
        return;
    }
    if (document.body.classList.contains('nav-open')) { document.body.classList.remove('nav-open'); return; }
    if (location.hash && location.hash !== '#/home' && location.hash !== '#/') history.back();
}

export function initTvNav() {
    if (TV_UA.test(navigator.userAgent)) document.body.classList.add('tv-mode');
    document.addEventListener('keydown', (e) => {
        if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
        const body = document.body;
        if (body.classList.contains('player-open')) return;          // 재생기 단축키가 먼저 (player.js)
        const overlay = [...document.querySelectorAll('.viewer, .reader')].find((x) => x.isConnected && visible(x));
        if (overlay) {   // 사진·글·웹툰 보기는 자기 키 - 리모컨 뒤로 키만 Esc로 바꿔 넘겨 준다
            if (isBackKey(e) && !isTyping(document.activeElement)) {
                e.preventDefault();
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            }
            return;
        }
        const el = document.activeElement;
        if (isBackKey(e)) {
            e.preventDefault();
            if (isTyping(el)) { el.blur(); return; }
            goBack();
            return;
        }
        const dir = DIRS[e.key] || (e.key === undefined || e.key === 'Unidentified' ? DIRS[e.keyCode] : null);
        if (!dir) return;
        if (isTyping(el) && (dir === 'left' || dir === 'right' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return;   // 글 칸 안의 움직임
        if (el && el.type === 'range' && (dir === 'left' || dir === 'right')) return;
        body.classList.add('tv-mode');
        const s = scope();
        const list = candidates(s);
        if (!list.length) return;
        e.preventDefault();
        if (!el || el === body || !s.contains(el) || !visible(el)) { focusFirst(); return; }
        const next = nearest(el, dir, list);
        if (next) focusEl(next);
    }, false);
    // 마우스를 쓰면 리모컨 표시를 끈다 (TV 브라우저는 그대로)
    document.addEventListener('mousemove', (e) => {
        if (Math.abs(e.movementX) + Math.abs(e.movementY) > 3 && !TV_UA.test(navigator.userAgent)) document.body.classList.remove('tv-mode');
    }, { passive: true });
    // 화면을 옮기면 (리모컨으로 쓰는 중이면) 첫 칸에 초점
    window.addEventListener('hashchange', () => {
        if (!document.body.classList.contains('tv-mode')) return;
        let tries = 0;   // 화면이 다 그려질 때까지 (최대 4초) 기다렸다가
        const tick = () => {
            if (document.activeElement && document.activeElement !== document.body) return;
            focusFirst();
            if ((!document.activeElement || document.activeElement === document.body) && ++tries < 16) setTimeout(tick, 250);
        };
        setTimeout(tick, 300);
    });
}
