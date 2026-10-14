// 작은 DOM/포맷 도우미들. 외부에서 온 글자(채널명, 제목 등)는 전부 textContent로 넣는다.

/**
 * h('div', {class: 'x', on: {click: fn}, text: '...'}, child1, child2)
 * attrs: class, text, html(신뢰하는 고정 문자열만), style(객체), dataset, on, 그 밖은 setAttribute
 */
export function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'html') el.innerHTML = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k === 'on') for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
        else if (k in el && typeof v !== 'string') el[k] = v;
        else el.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat(Infinity)) {
        if (c === null || c === undefined || c === false) continue;
        el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
}

export const icon = (name, extra = '') => h('i', { class: `fa-solid fa-${name} ${extra}`.trim() });

export function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
}

// 1:02:03 / 4:05
export function fmtClock(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    sec = Math.floor(sec);
    const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
    const p = (n) => String(n).padStart(2, '0');
    return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${mm}:${p(ss)}`;
}

// ms -> "1시간 52분"
export function fmtDuration(ms) {
    if (!ms) return '';
    const min = Math.round(ms / 60000);
    if (min < 60) return `${min}분`;
    return `${Math.floor(min / 60)}시간${min % 60 ? ` ${min % 60}분` : ''}`;
}

// 유닉스 초 또는 Date -> "21:30"
export function fmtHM(t) {
    const d = t instanceof Date ? t : new Date(t * 1000);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function normalizeName(name) {
    return String(name || '').replace(/\s+/g, '').toLowerCase();
}

export function uuid() {
    // crypto.randomUUID는 https/localhost에서만 있어서 LAN(http) 접속용 대체
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
}

export function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

let toastBox = null;
export function toast(message, type = 'info', ms = 3500) {
    if (!toastBox) {
        toastBox = h('div', { class: 'toast-box' });
        document.body.appendChild(toastBox);
    }
    const t = h('div', { class: `toast toast-${type}`, text: message });
    toastBox.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
}

export function storeGet(key, fallback) {
    try {
        const v = localStorage.getItem(key);
        return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
}

export function storeSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 사생활 보호 모드 등 */ }
}

export async function api(path, opts = {}) {
    const init = { ...opts, headers: { Accept: 'application/json', ...(opts.headers || {}) } };
    if (opts.json !== undefined) {
        init.body = JSON.stringify(opts.json);
        init.headers['Content-Type'] = 'application/json';
        delete init.json;
    }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
    if (!res.ok || (data && data.success === false)) {
        throw new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
    }
    return data;
}

export function modal(title, body, actions = []) {
    const close = () => wrap.remove();
    const wrap = h('div', { class: 'modal', on: { click: (e) => { if (e.target === wrap) close(); } } },
        h('div', { class: 'modal-card' },
            h('div', { class: 'modal-head' },
                h('h3', { text: title }),
                h('button', { class: 'icon-btn', title: '닫기', on: { click: close } }, icon('xmark'))),
            h('div', { class: 'modal-body' }, body),
            actions.length ? h('div', { class: 'modal-foot' }, actions) : null));
    document.body.appendChild(wrap);
    return { el: wrap, close };
}
