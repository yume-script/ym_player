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
    ms = Math.max(ms, Math.min(15000, String(message).length * 70));   // 긴 안내는 읽을 시간만큼 더
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
    let data = null, text = '';
    try { text = await res.text(); data = text ? JSON.parse(text) : null; } catch (e) { data = null; /* JSON이 아닌 응답 (중간 프록시의 오류 화면 등) */ }
    if (res.status === 401 && data && data.login) {
        // 로그인이 풀렸으면(만료, 비밀번호 변경, 계정 삭제) 로그인 화면으로
        location.href = '/login';
        throw new Error('로그인이 필요합니다.');
    }
    if (!res.ok || (data && data.success === false)) {
        throw new Error((data && (data.error || data.message)) || describeHttpError(res.status, text, init.body ? init.body.length : 0));
    }
    return data;
}

/**
 * YMPlayer가 보낸 것이 아닌 오류 응답(JSON이 아님)을 알아보기 쉬운 말로.
 * YMPlayer는 오류를 늘 JSON으로 보낸다 -> JSON이 아니면 대개 중간 프록시(nginx 등)가 대신 답한 것
 * (서버 로그에 그 요청이 아예 없다). sentBytes: 보낸 본문 크기
 */
export function describeHttpError(status, body = '', sentBytes = 0) {
    const text = String(body || '');
    const kb = sentBytes > 1024 ? ` (보낸 크기 ${Math.round(sentBytes / 1024)}KB)` : '';
    if (/Server got itself in trouble/i.test(text)) {
        return `YMPlayer 서버 오류 (HTTP ${status}). 서버 로그(journalctl -u ym_player)에 원인이 남습니다.`;
    }
    if (status === 404 && /^404: Not Found/.test(text.trim())) {
        // aiohttp가 그 주소를 모른다 -> 화면(정적 파일)은 새 버전인데 서버 프로그램은 아직 옛 버전 (git pull 뒤 재시작 안 됨)
        return 'YMPlayer 서버에 이 기능이 없습니다 (HTTP 404). 업데이트한 뒤 서버를 다시 켜지 않은 것 같습니다 - '
            + 'sudo systemctl restart ym_player 후 화면을 새로고침해 주세요.';
    }
    if (status === 413) {
        return `보낸 내용이 너무 큽니다 (HTTP 413)${kb}. 중간 프록시의 요청 크기 제한(nginx: client_max_body_size)을 늘려 주세요.`;
    }
    if (status === 502 || status === 503 || status === 504) {
        return `YMPlayer 서버에 닿지 못했습니다 (HTTP ${status}). 서버가 꺼져 있거나 다시 켜지는 중일 수 있습니다. 잠시 뒤 다시 시도해 주세요.`;
    }
    const proxy = (text.match(/nginx|openresty|cloudflare|caddy|apache|traefik|litespeed/i) || [''])[0].toLowerCase();
    const who = proxy ? `중간 프록시(${proxy})` : '중간 프록시';
    if (status >= 500) {
        return `${who}에서 HTTP ${status} 오류가 났습니다. YMPlayer까지 요청이 오지 않았습니다${kb}. `
            + '프록시 오류 로그를 확인해 주세요 (nginx: /var/log/nginx/error.log - 큰 요청을 임시 파일에 못 쓰는 경우가 많아 '
            + 'client_body_buffer_size 1m; 설정이나 /var/lib/nginx 폴더 권한으로 고칩니다).';
    }
    return `HTTP ${status}${text && !text.trimStart().startsWith('{') ? ` - ${who} 응답` : ''}`;
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

/**
 * Safari(아이폰·아이패드·Mac Safari)인지. Safari는 Chrome보다 원본으로 여는 형식이 좁다:
 * MP4 안의 MP3 소리는 처음 몇 초를 못 내고, WebM·VP9·AV1·Opus·FLAC은 기기와 버전마다 되다 안 되다 한다.
 * 그래서 Safari에서는 H.264 + AAC(또는 소리 없음)만 원본으로 보내고 나머지는 서버 변환으로 연다.
 */
/** 아이폰·아이패드 (아이패드 Safari는 기본으로 Mac인 척하므로 터치로 구별) */
export const IS_IOS = typeof navigator !== 'undefined' && (/iPad|iPhone|iPod/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 0));

export const IS_SAFARI = typeof navigator !== 'undefined' && /safari/i.test(navigator.userAgent)
    && !/chrome|crios|chromium|android|fxios|edg/i.test(navigator.userAgent);
