// 새 버전 알림 (왼쪽 위 아이콘 옆 작은 표시).
//   [NEW]     이 사람이 아직 "새로워진 점"을 안 본 버전 -> 누르면 그 뒤로 바뀐 것(credits.js CHANGELOG의 version)을 보여 주고
//             본 버전을 서버(/api/me/prefs seen_version)에 적는다 (기기를 바꿔도 한 번만)
//   [업데이트] 화면을 열어 둔 사이 서버가 새 버전으로 바뀜 -> 누르면 새로고침 안내 (예전 화면 파일로 계속 쓰지 않게)
// 지금 버전은 서버가 HTML에 넣어 준다 (<html data-version>). 서버 버전은 가끔(/api/branding) 다시 물어본다.
import { api, h, icon, modal } from './util.js';
import { CHANGELOG, REPO } from './credits.js';

const CURRENT = document.documentElement.dataset.version || '';
const CHECK_EVERY_MS = 10 * 60 * 1000;
const MIN_GAP_MS = 60 * 1000;      // 탭을 오가도 1분에 한 번만

let seen = '';
let serverVersion = '';           // 화면을 연 뒤 서버가 알려 준 더 새 버전
let lastCheck = 0;
let admin = false;               // 설정 > Thanks to(전체 변경 이력)는 관리자만

/** "1.1.10" > "1.1.9" 처럼 숫자로 비교 (a가 크면 양수) */
export function cmpVersion(a, b) {
    const pa = String(a || '').split('.').map(Number), pb = String(b || '').split('.').map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
        const d = (pa[i] || 0) - (pb[i] || 0);
        if (d) return d;
    }
    return 0;
}

/** 본 버전 뒤로 바뀐 것 (처음 보는 사람은 지금 버전 것만) */
export function changesSince(since, current = CURRENT, log = CHANGELOG) {
    return log.filter((e) => e.version && cmpVersion(e.version, current) <= 0
        && (since ? cmpVersion(e.version, since) > 0 : e.version === current));
}

export function initVersionNotice(prefs, isAdmin = false) {
    if (!CURRENT) return;
    admin = isAdmin;
    seen = (prefs && prefs.seen_version) || '';
    render();
    setInterval(check, CHECK_EVERY_MS);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') check(); });
}

async function check() {
    if (Date.now() - lastCheck < MIN_GAP_MS) return;
    lastCheck = Date.now();
    try {
        const v = (await api('/api/branding')).version || '';
        if (v && cmpVersion(v, CURRENT) > 0 && v !== serverVersion) {
            serverVersion = v;
            render();
        }
    } catch (e) { /* 서버가 재시작 중이면 다음에 */ }
}

function mode() {
    if (serverVersion) return 'update';
    return !seen || cmpVersion(CURRENT, seen) > 0 ? 'new' : '';
}

function render() {
    const m = mode();
    document.querySelectorAll('.brand').forEach((brand) => {
        let pill = brand.querySelector('.ver-notice');
        if (!m) { if (pill) pill.remove(); return; }
        if (!pill) {
            // 아이콘 링크(홈으로) 안에 있으므로 누르면 홈으로 가지 않고 알림만 연다
            const open = (e) => { e.preventDefault(); e.stopPropagation(); openNotice(); };
            pill = h('span', { class: 'ver-notice', role: 'button', tabindex: '0', on: {
                click: open, keydown: (e) => { if (e.key === 'Enter' || e.key === ' ') open(e); },
            } });
            brand.appendChild(pill);
        }
        pill.className = `ver-notice ${m}`;
        pill.textContent = m === 'update' ? '업데이트' : 'NEW';
        pill.title = m === 'update' ? `새 버전 ${serverVersion}이(가) 나왔습니다. 눌러서 새로고침` : `버전 ${CURRENT} - 새로워진 점 보기`;
    });
}

function openNotice() {
    if (serverVersion) {
        const m = modal(`새 버전 ${serverVersion}`, h('p', {
            text: `서버가 ${CURRENT}에서 ${serverVersion}(으)로 업데이트됐습니다. 새로고침하면 새 기능과 고친 점이 적용됩니다.`
                + ' (재생 중이면 처음 화면으로 돌아갑니다)',
        }), [
            h('button', { class: 'btn', on: { click: () => m.close() } }, '나중에'),
            h('button', { class: 'btn btn-accent', on: { click: () => location.reload() } }, icon('rotate-right'), ' 새로고침'),
        ]);
        return;
    }
    const list = changesSince(seen);
    const body = h('div', { class: 'whats-new' },
        list.length
            ? h('ul', { class: 'changelog' }, list.map((e) => h('li', {},
                h('a', { class: 'changelog-pr', href: `${REPO}/pull/${e.pr}`, target: '_blank', rel: 'noopener', text: `#${e.pr}` }),
                h('span', {}, h('span', { class: 'changelog-ver', text: `v${e.version}` }), e.title))))
            : h('p', { text: `버전 ${CURRENT}입니다.` }),
        admin ? h('p', { class: 'field-hint' }, '지난 변경 이력은 ', h('a', { href: '#/settings?tab=thanks', text: '설정 > Thanks to' }), '에 있습니다.') : null);
    const m = modal(`새로워진 점 · ${CURRENT}`, body, [
        h('button', { class: 'btn btn-accent', on: { click: () => m.close() } }, '확인'),
    ]);
    const more = body.querySelector('a[href^="#/settings"]');
    if (more) more.addEventListener('click', () => m.close());
    // 열어 본 것으로 기억 (실패해도 다음에 또 보일 뿐)
    seen = CURRENT;
    render();
    api('/api/me/prefs', { method: 'PUT', json: { seen_version: CURRENT } }).catch(() => {});
}
