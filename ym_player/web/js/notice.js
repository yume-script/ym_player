// 새 버전 알림 (왼쪽 위 아이콘 옆 작은 표시).
//   [NEW]     이 사람이 아직 "새로워진 점"을 안 본 버전 -> 누르면 그 뒤로 바뀐 것(credits.js CHANGELOG의 version)을 보여 주고
//             본 버전을 서버(/api/me/prefs seen_version)에 적는다 (기기를 바꿔도 한 번만)
//   [업데이트] 화면을 열어 둔 사이 서버가 새 버전으로 바뀜 -> 누르면 새로고침 안내 (예전 화면 파일로 계속 쓰지 않게)
//   [GitHub]  (관리자만) GitHub에 이 서버보다 새 버전이 올라옴 -> 누르면 바뀐 점과 서버에서 칠 업데이트 명령
//             (서버가 6시간마다 확인: updates.py, /api/admin/update). [이 버전은 알리지 않기]는 이 브라우저에서만
// 지금 버전은 서버가 HTML에 넣어 준다 (<html data-version>). 서버 버전은 가끔(/api/branding) 다시 물어본다.
import { api, h, icon, modal, toast, storeGet, storeSet } from './util.js';
import { CHANGELOG, REPO } from './credits.js';

const CURRENT = document.documentElement.dataset.version || '';
const CHECK_EVERY_MS = 10 * 60 * 1000;
const MIN_GAP_MS = 60 * 1000;      // 탭을 오가도 1분에 한 번만

let seen = '';
let serverVersion = '';           // 화면을 연 뒤 서버가 알려 준 더 새 버전
let lastCheck = 0;
let admin = false;               // 설정 > Thanks to(전체 변경 이력)와 GitHub 알림은 관리자만
let github = null;               // /api/admin/update 결과
const SKIP_KEY = 'yp_skip_github';

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
    if (admin) checkGithub();
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
    if (admin) checkGithub();
}

/** 서버가 마지막으로 GitHub를 확인한 결과 (관리자). 설정 > 상태의 [지금 확인]도 여기로 결과를 넘긴다 */
async function checkGithub() {
    try { setGithubInfo(await api('/api/admin/update')); } catch (e) { /* 다음에 */ }
}

export function setGithubInfo(info) {
    github = info;
    render();
}

function modes() {
    const out = [];
    if (serverVersion) out.push('update');
    else if (!seen || cmpVersion(CURRENT, seen) > 0) out.push('new');
    // 열어 둔 사이 서버가 이미 그 버전으로 바뀌었으면 [업데이트]만
    if (admin && github && github.newer && storeGet(SKIP_KEY, '') !== github.latest
        && cmpVersion(github.latest, serverVersion || CURRENT) > 0) out.push('github');
    return out;
}

const LABEL = { update: '업데이트', new: 'NEW', github: 'GitHub' };

function render() {
    const want = modes();
    document.querySelectorAll('.brand').forEach((brand) => {
        brand.querySelectorAll('.ver-notice').forEach((el) => { if (!want.includes(el.dataset.mode)) el.remove(); });
        for (const m of want) {
            let pill = brand.querySelector(`.ver-notice[data-mode="${m}"]`);
            if (!pill) {
                // 아이콘 링크(홈으로) 안에 있으므로 누르면 홈으로 가지 않고 알림만 연다
                const open = (e) => { e.preventDefault(); e.stopPropagation(); openNotice(m); };
                pill = h('span', { class: `ver-notice ${m}`, role: 'button', tabindex: '0', dataset: { mode: m }, on: {
                    click: open, keydown: (e) => { if (e.key === 'Enter' || e.key === ' ') open(e); },
                } }, m === 'github' ? icon('code-branch') : null, m === 'github' ? ' ' : null, LABEL[m]);
                brand.appendChild(pill);
            }
            pill.title = m === 'update' ? `새 버전 ${serverVersion}이(가) 나왔습니다. 눌러서 새로고침`
                : m === 'github' ? `GitHub에 새 버전 ${github.latest}이(가) 올라왔습니다. 눌러서 업데이트 방법 보기`
                    : `버전 ${CURRENT} - 새로워진 점 보기`;
        }
    });
}

/** GitHub 새 버전 안내 (아이콘 옆 [GitHub], 설정 > 상태에서도 씀) */
export function showGithubUpdate(info = github) {
    if (!info) return;
    const cmd = h('code', { text: info.command });
    const copy = h('button', { class: 'btn btn-sm', on: { click: async () => {
        try { await navigator.clipboard.writeText(info.command); toast('명령을 복사했습니다.'); }
        catch (e) { getSelection().selectAllChildren(cmd); toast('복사하지 못했습니다. 선택된 글자를 직접 복사해 주세요.', 'error'); }
    } } }, icon('copy'), ' 복사');
    const body = h('div', { class: 'whats-new' },
        h('p', { text: `이 서버는 ${info.current}, GitHub에는 ${info.latest}이(가) 있습니다.` }),
        info.changes && info.changes.length
            ? h('ul', { class: 'changelog' }, info.changes.map((e) => h('li', {},
                h('a', { class: 'changelog-pr', href: `${info.repo}/pull/${e.pr}`, target: '_blank', rel: 'noopener', text: `#${e.pr}` }),
                h('span', {}, h('span', { class: 'changelog-ver', text: `v${e.version}` }), e.title))))
            : null,
        h('p', { class: 'field-hint', text: '서버(터미널)에서 아래 명령으로 업데이트하세요. 서버가 다시 켜지면 열어 둔 화면에 [업데이트]가 뜹니다.' }),
        h('div', { class: 'cmd-box' }, cmd, copy),
        h('p', { class: 'field-hint' }, h('a', { href: `${info.repo}/commits/main`, target: '_blank', rel: 'noopener', text: 'GitHub에서 바뀐 내용 보기' })));
    const m = modal(`GitHub에 새 버전 ${info.latest}`, body, [
        h('button', { class: 'btn', on: { click: () => { storeSet(SKIP_KEY, info.latest); m.close(); render(); } } }, '이 버전은 알리지 않기'),
        h('button', { class: 'btn btn-accent', on: { click: () => m.close() } }, '닫기'),
    ]);
}

function openNotice(mode) {
    if (mode === 'github') return showGithubUpdate();
    if (mode === 'update') {
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
