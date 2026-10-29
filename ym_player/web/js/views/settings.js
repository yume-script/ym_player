// 설정: 소스(Plex 서버, IPTV 재생목록) 추가/수정/삭제. 저장하면 서버의 data/config.json에 들어간다.
// Plex는 토큰을 직접 넣거나, plex.tv PIN 로그인으로 서버 목록을 받아 고를 수 있다.
import { h, icon, clear, api, toast, modal, storeGet, storeSet } from '../util.js';
import { registry } from '../providers/index.js';
import { PlexProvider } from '../providers/plex.js';
import { applyBranding } from '../brand.js';
import { setGithubInfo, showGithubUpdate } from '../notice.js';
import { ownThemeLabel, setDefaults, themePicker, useDefaultTheme } from '../theme.js';
import { CHANGELOG, CREDITS, REPO } from '../credits.js';

function field(label, input, hint) {
    return h('label', { class: 'field' }, h('span', { class: 'field-label', text: label }), input,
        hint ? h('span', { class: 'field-hint', text: hint }) : null);
}

export async function renderSettings(main, _params, query, ctx) {
    let drafts = registry.config.sources.map((s) => ({ ...s, token: '' }));
    // 저장할 때 바뀌지 않은 소스는 {id, keep}만 보낸다 - 라디오 방송국 목록 등으로 요청이 수십 KB가 되면
    // 앞단 프록시(nginx 등)가 큰 본문을 못 받아 저장이 통째로 실패하는 경우가 있어서
    const snapshot = (list) => new Map(list.map((s) => [s.id, JSON.stringify(s)]));
    let pristine = snapshot(drafts);
    let dirty = false;

    const list = h('div', { class: 'src-list' });
    const saveBtn = h('button', { class: 'btn btn-accent', on: { click: save } }, icon('check'), ' 저장');
    // 소스는 종류마다 하위 탭으로 (한 화면에 다 늘어놓으면 너무 길어서). [저장]은 모든 탭 공통
    const SRC_TYPES = [
        ['plex', 'server', 'Plex', () => [
            h('button', { class: 'btn', on: { click: plexLogin } }, icon('right-to-bracket'), ' Plex 계정으로 추가'),
            h('button', { class: 'btn', on: { click: () => add({ type: 'plex', name: 'Plex', url: '', enabled: true, verify_ssl: true }) } },
                icon('server'), ' Plex 직접 입력')],
        'Plex 토큰은 이 서버(data/config.json)에만 저장되고 브라우저로는 내려오지 않습니다. 토큰 칸을 비워두고 저장하면 기존 토큰을 그대로 씁니다.'],
        ['iptv', 'tower-broadcast', 'IPTV', () => [
            h('button', { class: 'btn', on: { click: () => add({ type: 'iptv', name: 'IPTV', m3u: '', epg: '', enabled: true }) } },
                icon('tower-broadcast'), ' IPTV 추가')],
        'M3U·EPG 주소는 한 줄에 하나씩 여러 개를 넣으면 하나로 합쳐집니다.'],
        ['telegram', 'paper-plane', '텔레그램', () => [
            h('button', {
                class: 'btn',
                on: { click: () => add({ type: 'telegram', name: 'Telegram', api_id: '', api_hash: '', phone: '', channels: [], enabled: true }) },
            }, icon('paper-plane'), ' 텔레그램 추가')],
        'API Hash는 이 서버에만 저장됩니다. 칸을 비워두고 저장하면 기존 값을 그대로 씁니다.'],
        ['radio', 'radio', '라디오', () => [
            h('button', { class: 'btn', on: { click: () => add({ type: 'radio', name: '라디오', enabled: true }) } },
                icon('radio'), ' 라디오 추가')],
        ''],
    ];
    let srcType = storeGet('yp_settings_srctype', '');
    if (!SRC_TYPES.some(([t]) => t === srcType)) srcType = (drafts[0] && drafts[0].type) || 'plex';
    const srcTabs = h('div', { class: 'tabs src-tabs', role: 'tablist' });
    const addBar = h('div', { class: 'toolbar pane-toolbar' });
    const srcNote = h('p', { class: 'muted settings-note' });
    const showSrcType = (t) => {
        srcType = t;
        storeSet('yp_settings_srctype', t);
        const [, , , buttons, note] = SRC_TYPES.find(([x]) => x === t);
        clear(addBar).append(...buttons(), saveBtn);
        srcNote.textContent = note;
        srcNote.classList.toggle('hidden', !note);
        draw();
    };
    const panes = {
        sources: h('div', { class: 'settings-pane' }, srcTabs, addBar, srcNote, list),
        general: h('div', { class: 'settings-pane' }),
        users: h('div', { class: 'settings-pane' }, usersBox()),
        status: h('div', { class: 'settings-pane' }),
        cache: h('div', { class: 'settings-pane' }),
        look: h('div', { class: 'settings-pane' }),
        backup: h('div', { class: 'settings-pane' }),
        mcp: h('div', { class: 'settings-pane' }),
        discord: h('div', { class: 'settings-pane' }),
        mirror: h('div', { class: 'settings-pane' }),
        thanks: h('div', { class: 'settings-pane' }),
    };
    const TABS = [['sources', 'layer-group', '소스'], ['general', 'sliders', '공통'], ['users', 'users', '사용자'], ['status', 'heart-pulse', '상태'], ['cache', 'hard-drive', '캐시'], ['look', 'palette', '화면'], ['backup', 'box-archive', '백업'], ['mcp', 'robot', 'MCP'], ['discord', 'comment-dots', '디스코드'], ['mirror', 'cloud-arrow-up', '자동 올리기'], ['thanks', 'heart', 'Thanks to']];
    const tabBar = h('div', { class: 'tabs', role: 'tablist' });
    const showTab = (key) => {
        if (!panes[key]) key = 'sources';
        storeSet('yp_settings_tab', key);
        tabBar.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === key));
        Object.entries(panes).forEach(([k, el]) => el.classList.toggle('hidden', k !== key));
        if (key === 'status') renderStatus(panes.status);
        if (key === 'general') renderGeneral(panes.general);
        if (key === 'cache') renderCache(panes.cache);
        if (key === 'look') renderLook(panes.look);
        if (key === 'backup' && !panes.backup.firstChild) renderBackup(panes.backup);
        if (key === 'thanks' && !panes.thanks.firstChild) renderThanks(panes.thanks);
        if (key === 'mcp') renderMcp(panes.mcp);
        if (key === 'discord') renderDiscord(panes.discord);
        if (key === 'mirror') renderMirror(panes.mirror);
    };
    TABS.forEach(([key, ic, label]) => tabBar.appendChild(h('button', {
        class: 'tab', role: 'tab', dataset: { tab: key }, on: { click: () => showTab(key) },
    }, icon(ic), ` ${label}`)));
    const page = h('div', { class: 'page settings' },
        h('div', { class: 'page-head' }, h('h1', {}, icon('gear'), ' 설정')),
        tabBar, Object.values(panes));
    main.appendChild(page);

    window.onbeforeunload = () => (dirty ? true : undefined);
    ctx.onLeave(() => { window.onbeforeunload = null; });

    /** 사용자 관리 (바로 적용 - 아래 소스의 [저장]과 상관없음) */
    function usersBox() {
        const box = h('div', { class: 'src-card users-box' });
        const draw = async () => {
            clear(box);
            let users = [];
            try { users = (await api('/api/users')).users; } catch (e) { box.appendChild(h('p', { class: 'muted error', text: e.message })); return; }
            const me = registry.user.username;
            for (const u of users) {
                const roleSel = h('select', { class: 'select' },
                    h('option', { value: 'admin', text: '관리자', selected: u.role === 'admin' }),
                    h('option', { value: 'user', text: '사용자', selected: u.role === 'user' }));
                roleSel.addEventListener('change', async () => {
                    try {
                        await api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'PUT', json: { role: roleSel.value } });
                        toast(`${u.username}: ${roleSel.value === 'admin' ? '관리자' : '사용자'}로 바꿨습니다.`, 'success');
                    } catch (e) { toast(e.message, 'error'); }
                    draw();
                });
                const pwBtn = h('button', { class: 'btn btn-sm', title: '이 사용자의 비밀번호를 새로 정하기' }, icon('key'), ' 비밀번호');
                pwBtn.addEventListener('click', async () => {
                    const pw = prompt(`${u.username}의 새 비밀번호 (6자 이상)`);
                    if (!pw) return;
                    try {
                        await api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'PUT', json: { password: pw } });
                        toast('비밀번호를 바꿨습니다. 그 사용자의 기존 로그인은 끊깁니다.', 'success');
                    } catch (e) { toast(e.message, 'error'); }
                });
                const accBtn = h('button', {
                    class: 'btn btn-sm', disabled: u.role === 'admin',
                    title: u.role === 'admin' ? '관리자는 항상 전부 봅니다' : '이 사용자가 볼 수 있는 소스와 라이브러리',
                }, icon('eye'), u.role === 'admin' || !u.access ? ' 전부 보기' : ` ${u.access.sources.length}개 소스만`);
                accBtn.addEventListener('click', () => accessDialog(u, draw));
                const delBtn = h('button', { class: 'icon-btn danger', title: '삭제', disabled: u.username === me }, icon('trash'));
                delBtn.addEventListener('click', async () => {
                    if (!confirm(`사용자 "${u.username}"를 지울까요?`)) return;
                    try { await api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'DELETE' }); toast('지웠습니다.'); } catch (e) { toast(e.message, 'error'); }
                    draw();
                });
                box.appendChild(h('div', { class: 'user-row' },
                    h('span', { class: 'uname' }, icon(u.role === 'admin' ? 'user-shield' : 'user'), ` ${u.username}`,
                        u.username === me ? h('span', { class: 'me', text: '나' }) : null),
                    roleSel, accBtn, pwBtn, delBtn));
            }
            const nameIn = h('input', { class: 'input', placeholder: '새 아이디', autocomplete: 'off' });
            const pwIn = h('input', { class: 'input', type: 'password', placeholder: '비밀번호 (6자 이상)', autocomplete: 'new-password' });
            const roleIn = h('select', { class: 'select' }, h('option', { value: 'user', text: '사용자' }), h('option', { value: 'admin', text: '관리자' }));
            const addBtn = h('button', { class: 'btn btn-accent' }, icon('user-plus'), ' 추가');
            addBtn.addEventListener('click', async () => {
                try {
                    await api('/api/users', { method: 'POST', json: { username: nameIn.value.trim(), password: pwIn.value, role: roleIn.value } });
                    toast(`${nameIn.value.trim()} 사용자를 추가했습니다.`, 'success');
                    draw();
                } catch (e) { toast(e.message, 'error'); }
            });
            box.append(h('div', { class: 'user-add' }, nameIn, pwIn, roleIn, addBtn),
                h('p', { class: 'field-hint', text: '사용자는 보기만 할 수 있고 이 설정 화면에는 들어올 수 없습니다. [전부 보기]를 눌러 볼 수 있는 소스·라이브러리를 줄일 수 있습니다 (예: 아이 계정).' }));
        };
        draw();
        return box;
    }

    /**
     * 사용자가 볼 수 있는 범위. 서버가 지키므로 고르지 않은 소스/라이브러리는 주소를 알아도 열리지 않는다.
     * access = null(전부) | { sources: [id], libraries: { plexId: [섹션key] } } (libraries에 없는 Plex 소스는 라이브러리 전부)
     */
    function accessDialog(u, done) {
        const sources = registry.config.sources;
        const acc = u.access || { sources: sources.map((s) => s.id), libraries: {} };
        const chosen = new Set(acc.sources);
        const libs = Object.fromEntries(Object.entries(acc.libraries || {}).map(([k, v]) => [k, new Set(v)]));
        const allIn = h('input', { type: 'radio', name: 'acc-mode', checked: !u.access });
        const someIn = h('input', { type: 'radio', name: 'acc-mode', checked: !!u.access });
        const listBox = h('div', { class: 'acc-list' });
        const sync = () => listBox.classList.toggle('disabled', allIn.checked);
        allIn.addEventListener('change', sync);
        someIn.addEventListener('change', sync);

        sources.forEach((src) => {
            const chk = h('input', { type: 'checkbox', checked: chosen.has(src.id) });
            const sub = h('div', { class: 'acc-libs' });
            chk.addEventListener('change', () => { if (chk.checked) chosen.add(src.id); else chosen.delete(src.id); sub.classList.toggle('hidden', !chk.checked); });
            sub.classList.toggle('hidden', !chk.checked);
            const hasItems = src.type === 'plex' || src.type === 'telegram' || src.type === 'radio';
            const srcIcon = { plex: 'server', telegram: 'paper-plane', radio: 'radio' }[src.type] || 'tower-broadcast';
            listBox.appendChild(h('div', { class: 'acc-src' },
                h('label', { class: 'check' }, chk, icon(srcIcon), ` ${src.name}`),
                hasItems ? sub : null));
            if (!hasItems) return;
            // 그 소스 안에서 볼 것: Plex는 라이브러리, 텔레그램은 설정에서 고른 채널, 라디오는 방송국
            const what = { plex: '라이브러리', telegram: '채널', radio: '방송국' }[src.type];
            const fill = (items) => {
                clear(sub);
                if (!items.length) { sub.appendChild(h('span', { class: 'muted', text: `고를 ${what}이 없습니다.` })); return; }
                const everyIn = h('input', { type: 'checkbox', checked: !libs[src.id] });
                const box = h('div', { class: 'acc-lib-items' });
                const paint = () => box.classList.toggle('hidden', everyIn.checked);
                everyIn.addEventListener('change', () => {
                    if (everyIn.checked) delete libs[src.id]; else libs[src.id] = new Set();
                    box.querySelectorAll('input').forEach((x) => { x.checked = false; });
                    paint();
                });
                items.forEach((it) => {
                    const c = h('input', { type: 'checkbox', checked: !!(libs[src.id] && libs[src.id].has(it.id)) });
                    c.addEventListener('change', () => {
                        libs[src.id] = libs[src.id] || new Set();
                        if (c.checked) libs[src.id].add(it.id); else libs[src.id].delete(it.id);
                    });
                    box.appendChild(h('label', { class: 'check lib-check' }, c, icon(it.icon), ` ${it.title}`));
                });
                sub.append(h('label', { class: 'check' }, everyIn, ` 모든 ${what} (새로 추가하는 것 포함)`), box);
                paint();
            };
            if (src.type === 'telegram') {
                fill((src.channels || []).map((c) => ({ id: String(c.id), title: c.title, icon: 'bullhorn' })));
                return;
            }
            if (src.type === 'radio') {
                fill((src.stations || []).map((st) => ({ id: st.id, title: st.name, icon: 'radio' })));
                return;
            }
            sub.appendChild(h('span', { class: 'muted' }, icon('circle-notch', 'fa-spin'), ' 라이브러리 불러오는 중...'));
            new PlexProvider(src, registry.config.client_id).getLibraries({ all: true }).then(fill)
                .catch((e) => { clear(sub).appendChild(h('span', { class: 'muted error', text: `라이브러리 목록 실패: ${e.message}` })); });
        });
        sync();

        const saveAcc = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
        const m = modal(`${u.username} - 볼 수 있는 범위`, h('div', { class: 'acc-dialog' },
            h('label', { class: 'check' }, allIn, ' 전부 (나중에 추가하는 소스도)'),
            h('label', { class: 'check' }, someIn, ' 고른 것만'),
            listBox,
            h('p', { class: 'field-hint', text: '고르지 않은 소스, Plex 라이브러리, 텔레그램 채널, 라디오 방송국은 이 사용자의 사이드바·홈·검색·이어보기에 안 나오고, 서버도 열어 주지 않습니다.' })),
        [saveAcc]);
        saveAcc.addEventListener('click', async () => {
            const access = allIn.checked ? null : {
                sources: [...chosen],
                libraries: Object.fromEntries(Object.entries(libs).filter(([k]) => chosen.has(k)).map(([k, v]) => [k, [...v]])),
            };
            try {
                await api(`/api/users/${encodeURIComponent(u.username)}`, { method: 'PUT', json: { access } });
                toast(`${u.username}: 볼 수 있는 범위를 저장했습니다.`, 'success');
                m.close();
                done();
            } catch (e) { toast(e.message, 'error'); }
        });
    }

    function touch() { dirty = true; saveBtn.classList.add('pulse'); }

    function add(src) {
        drafts.push(src);
        touch();
        if (src.type !== srcType) showSrcType(src.type); else draw();
        list.lastElementChild && list.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    /**
     * 라디오 방송국 목록: 한 줄에 하나 "이름 | 스트림 주소 | 설명 | 홈페이지" (설명/홈페이지는 빼도 됨).
     * 비워 두고 저장하면 서버가 기본 재즈 방송국 3개를 넣는다.
     */
    function stationEditor(src) {
        // 한 줄에 하나: 이름 | 스트림 주소 | 설명 | 홈페이지 | 분류(지역 등). 뒤쪽 빈 칸은 줄인다
        const toText = (list) => (list || []).map((s) => {
            const cols = [s.name, s.url, s.subtitle || '', s.homepage || '', s.group || ''];
            while (cols.length > 2 && !cols[cols.length - 1]) cols.pop();
            return cols.join(' | ');
        }).join('\n');
        const area = h('textarea', {
            class: 'input url-list', rows: Math.min(14, Math.max(3, (src.stations || []).length + 1)), spellcheck: false, text: toText(src.stations),
            placeholder: '이름 | 스트림 주소 | 설명 | 홈페이지 | 분류\n비워 두고 저장하면 기본 재즈 방송국 3개(RelaxingJazz, Radio Swiss Jazz, Jazz24)를 넣습니다.',
        });
        const parse = () => {
            const old = new Map((src.stations || []).map((s) => [s.name, s.id]));
            src.stations = area.value.split('\n').map((line) => line.split('|').map((x) => x.trim()))
                .filter(([name, url]) => name && url)
                .map(([name, url, subtitle = '', homepage = '', group = '']) => ({ id: old.get(name), name, url, subtitle, homepage, group }));
            touch();
        };
        area.addEventListener('input', parse);
        // 한국 지역 라디오(radio.bsod.kr 지역별 목록)를 지역 분류와 함께 더한다. 이미 있는 주소는 건너뜀
        const krBtn = h('button', { class: 'btn btn-sm', type: 'button', on: { click: async (e) => {
            e.preventDefault();
            const { KR_RADIO } = await import('../data/kr_radio.js');
            parse();
            const have = new Set((src.stations || []).map((st) => st.url));
            const add = KR_RADIO.filter((st) => !have.has(st.url));
            if (!add.length) { toast('한국 지역 라디오가 이미 다 들어 있습니다.'); return; }
            area.value = [area.value.trim(), toText(add)].filter(Boolean).join('\n');
            area.rows = 14;
            parse();
            toast(`한국 지역 라디오 ${add.length}개를 더했습니다. [저장]을 누르면 적용됩니다.`);
        } } }, icon('map-location-dot'), ' 한국 지역 라디오 넣기');
        return h('div', {},
            field('방송국', area, 'MP3/AAC/Ogg(Icecast/SHOUTcast)·HLS 인터넷 라디오 주소. 곡 정보를 보내는 방송국은 지금 나오는 곡과 앨범 표지가 보입니다. '
                + '분류를 적으면 방송국 화면에서 분류(지역)별로 골라 볼 수 있습니다.'),
            h('div', { class: 'toolbar' }, krBtn,
                h('span', { class: 'field-hint', text: 'KBS·MBC·SBS 지역국, EBS, CBS, TBN, 공동체 라디오 등 194개 (전국 + 9개 지역, radio.bsod.kr 목록)' })));
    }

    function bindInput(src, key, input, kind = 'value') {
        input.addEventListener(kind === 'checked' ? 'change' : 'input', () => { src[key] = input[kind]; touch(); });
        return input;
    }

    /**
     * 가져올 라이브러리 체크 목록. 서버에 저장된 소스만 목록을 받아올 수 있다(토큰이 서버에 있어서).
     * 체크를 끈 것만 hidden_libraries에 남기므로 나중에 Plex에 생긴 새 라이브러리는 기본으로 보인다.
     */
    function libraryPicker(src) {
        const box = h('div', { class: 'lib-picker' });
        // field()는 <label>이라 안에 체크박스 여러 개를 넣으면 제목을 눌러도 첫 체크박스가 바뀐다 -> div로
        const wrap = h('div', { class: 'field' },
            h('span', { class: 'field-label', text: '가져올 라이브러리' }), box,
            h('span', { class: 'field-hint', text: '체크를 끈 라이브러리는 사이드바·홈·검색에 나오지 않습니다.' }));
        const saved = registry.config.sources.some((s) => s.id === src.id);
        if (!saved) {
            box.appendChild(h('span', { class: 'muted', text: '먼저 [저장]하면 이 서버의 라이브러리 목록이 나옵니다.' }));
            return wrap;
        }
        box.appendChild(h('span', { class: 'muted' }, icon('circle-notch', 'fa-spin'), ' 라이브러리 목록을 불러오는 중...'));
        new PlexProvider(src, registry.config.client_id).getLibraries({ all: true }).then((libs) => {
            clear(box);
            if (!libs.length) {
                box.appendChild(h('span', { class: 'muted', text: '볼 수 있는 라이브러리(영화/TV/음악)가 없습니다.' }));
                return;
            }
            const hidden = new Set((src.hidden_libraries || []).map(String));
            libs.forEach((lib) => {
                const chk = h('input', { type: 'checkbox', checked: !hidden.has(lib.id) });
                chk.addEventListener('change', () => {
                    if (chk.checked) hidden.delete(lib.id); else hidden.add(lib.id);
                    src.hidden_libraries = [...hidden];
                    touch();
                });
                box.appendChild(h('label', { class: 'check lib-check' }, chk, icon(lib.icon), ` ${lib.title}`));
            });
        }).catch((e) => {
            clear(box).appendChild(h('span', { class: 'muted error', text: `라이브러리 목록을 못 불러왔습니다: ${e.message}` }));
        });
        return wrap;
    }

    /**
     * 텔레그램 로그인 + 볼 채널 고르기. 서버에 저장된 소스(API ID/Hash가 서버에 있음)만 가능.
     * 로그인: [인증 코드 받기] -> 텔레그램 앱으로 온 코드 입력 -> (2단계 인증이면) 비밀번호
     */
    function telegramLogin(src) {
        const box = h('div', { class: 'tg-login' });
        const wrap = h('div', { class: 'field' }, h('span', { class: 'field-label', text: '로그인과 채널' }), box);
        if (!registry.config.sources.some((x) => x.id === src.id)) {
            box.appendChild(h('span', { class: 'muted', text: 'API ID/Hash를 넣고 먼저 [저장]하면 여기서 로그인할 수 있습니다.' }));
            return wrap;
        }
        const base = `/api/tg/${encodeURIComponent(src.id)}`;
        const say = (text, cls = 'muted') => h('span', { class: cls, text });

        const showChannels = async () => {
            const list = h('div', { class: 'lib-picker' }, say('채널 목록을 불러오는 중...'));
            box.appendChild(h('div', { class: 'field' },
                h('span', { class: 'field-label', text: '볼 채널 (체크한 채널이 라이브러리처럼 보입니다)' }), list));
            try {
                const { channels } = await api(`${base}/channels`);
                clear(list);
                if (!channels.length) { list.appendChild(say('들어가 있는 채널/그룹이 없습니다.')); return; }
                const chosen = new Map((src.channels || []).map((c) => [String(c.id), c]));
                channels.forEach((c) => {
                    const id = String(c.id);
                    const chk = h('input', { type: 'checkbox', checked: chosen.has(id) });
                    // 채널 종류: 영상만 보이게(사진 숨김) / 사진만 / 웹툰(zip·cbz 압축 파일을 웹툰 보기로) / 책(PDF·EPUB만) / 영상+사진
                    const kindSel = h('select', { class: 'select select-sm', title: '이 채널에서 보여 줄 것' },
                        [['', '영상+사진'], ['video', '영상만'], ['image', '사진만'], ['webtoon', '웹툰(압축 파일)'], ['doc', '책(PDF·EPUB)']].map(([v, label]) =>
                            h('option', { value: v, text: label, selected: ((chosen.get(id) || {}).kind || '') === v })));
                    // 시리즈(회차 묶음) 상세의 회차 순서
                    const orderSel = h('select', { class: 'select select-sm', title: '시리즈 회차 목록 순서' },
                        [['asc', '1화부터'], ['desc', '최신 회차부터']].map(([v, label]) =>
                            h('option', { value: v, text: label, selected: ((chosen.get(id) || {}).ep_order || 'asc') === v })));
                    kindSel.disabled = orderSel.disabled = !chk.checked;
                    const save = () => {
                        if (chk.checked) {
                            chosen.set(id, { id: c.id, title: c.title, ...(kindSel.value ? { kind: kindSel.value } : {}),
                                ...(orderSel.value === 'desc' ? { ep_order: 'desc' } : {}) });
                        } else chosen.delete(id);
                        kindSel.disabled = orderSel.disabled = !chk.checked;
                        src.channels = [...chosen.values()];
                        touch();
                    };
                    chk.addEventListener('change', save);
                    kindSel.addEventListener('change', save);
                    orderSel.addEventListener('change', save);
                    list.appendChild(h('div', { class: 'lib-check-row' },
                        h('label', { class: 'check lib-check', title: c.username ? `@${c.username}` : '' },
                            chk, icon(c.kind === 'group' ? 'users' : 'bullhorn'), ` ${c.title}`),
                        kindSel, orderSel));
                });
            } catch (e) {
                clear(list).appendChild(say(`채널 목록 실패: ${e.message}`, 'muted error'));
            }
        };

        const render = async () => {
            clear(box).appendChild(say('상태 확인 중...'));
            let st;
            try { st = await api(`${base}/status`); } catch (e) { st = { authorized: false, error: e.message }; }
            clear(box);
            if (st.fast_crypto === false) {
                box.appendChild(say('서버에 cryptg가 없어 텔레그램 영상이 느립니다. 서버에서 '
                    + '`.venv/bin/pip install -r requirements.txt` 후 재시작하세요.', 'muted error'));
            }
            if (st.authorized) {
                box.appendChild(h('div', { class: 'row-inline' },
                    h('span', { class: 'tg-status ok' }, icon('circle-check'), ` ${st.name || '로그인됨'} (으)로 로그인됨`),
                    h('button', {
                        class: 'btn btn-sm',
                        on: { click: async () => {
                            if (!confirm('이 서버의 텔레그램 로그인을 끊을까요? (텔레그램 "활성 세션"에서도 빠집니다)')) return;
                            try { await api(`${base}/logout`, { method: 'POST' }); } catch (e) { toast(e.message, 'error'); }
                            render();
                        } },
                    }, icon('right-from-bracket'), ' 로그아웃')));
                showChannels();
                return;
            }
            if (st.error) box.appendChild(say(st.error, 'muted error'));
            const phoneIn = h('input', { class: 'input', value: src.phone || st.phone || '', placeholder: '+821012345678' });
            const codeIn = h('input', { class: 'input', placeholder: '텔레그램 앱으로 온 인증 코드', inputmode: 'numeric', autocomplete: 'one-time-code' });
            const pwIn = h('input', { class: 'input', type: 'password', placeholder: '2단계 인증 비밀번호', autocomplete: 'current-password' });
            const codeRow = h('div', { class: 'row-inline hidden' }, codeIn);
            const pwRow = h('div', { class: 'row-inline hidden' }, pwIn);
            const run = async (btn, fn) => {
                btn.disabled = true;
                try { await fn(); } catch (e) { toast(e.message, 'error', 6000); } finally { btn.disabled = false; }
            };
            const sendBtn = h('button', { class: 'btn btn-sm', text: '인증 코드 받기' });
            sendBtn.addEventListener('click', () => run(sendBtn, async () => {
                await api(`${base}/code`, { method: 'POST', json: { phone: phoneIn.value.trim() } });
                codeRow.classList.remove('hidden');
                codeIn.focus();
                toast('텔레그램 앱(또는 SMS)으로 인증 코드를 보냈습니다.');
            }));
            const signBtn = h('button', { class: 'btn btn-sm btn-accent', text: '로그인' });
            signBtn.addEventListener('click', () => run(signBtn, async () => {
                const res = await api(`${base}/sign-in`, { method: 'POST', json: { code: codeIn.value.trim() } });
                if (res.password_required) { pwRow.classList.remove('hidden'); pwIn.focus(); toast('2단계 인증 비밀번호를 넣어 주세요.'); return; }
                toast('텔레그램에 로그인했습니다.', 'success');
                render();
            }));
            const pwBtn = h('button', { class: 'btn btn-sm btn-accent', text: '확인' });
            pwBtn.addEventListener('click', () => run(pwBtn, async () => {
                await api(`${base}/sign-in`, { method: 'POST', json: { password: pwIn.value } });
                toast('텔레그램에 로그인했습니다.', 'success');
                render();
            }));
            codeRow.appendChild(signBtn);
            pwRow.appendChild(pwBtn);
            box.append(h('div', { class: 'row-inline' }, phoneIn, sendBtn), codeRow, pwRow,
                say('비밀번호는 이 서버를 거쳐 텔레그램으로 바로 넘어가고 저장되지 않습니다. 로그인 세션만 서버에 남습니다.'));
        };
        render();
        return wrap;
    }

    function draw() {
        clear(list);
        // 하위 탭 (소스 수와 함께)
        clear(srcTabs).append(...SRC_TYPES.map(([t, ic, label]) => {
            const n = drafts.filter((d) => d.type === t).length;
            return h('button', {
                class: `tab${t === srcType ? ' active' : ''}`, role: 'tab', on: { click: () => showSrcType(t) },
            }, icon(ic), ` ${label}`, n ? h('span', { class: 'tab-count', text: String(n) }) : null);
        }));
        if (!drafts.some((d) => d.type === srcType)) {
            const label = SRC_TYPES.find(([t]) => t === srcType)[2];
            list.appendChild(h('div', { class: 'empty small' }, h('p', { text: `${label} 소스가 없습니다. 위 버튼으로 추가하세요.` })));
        }
        drafts.forEach((src, i) => {
            if (src.type !== srcType) return;
            const isPlex = src.type === 'plex';
            const isTg = src.type === 'telegram';
            const isRadio = src.type === 'radio';
            const fields = isRadio ? [stationEditor(src)] : isTg ? [
                h('div', { class: 'row-2' },
                    field('API ID', bindInput(src, 'api_id', h('input', { class: 'input', value: src.api_id || '', placeholder: '숫자', inputmode: 'numeric' }))),
                    field('API Hash', bindInput(src, 'api_hash', h('input', {
                        class: 'input', type: 'password', value: src.api_hash || '', autocomplete: 'off',
                        placeholder: src.has_api_hash ? '저장된 값 사용 중 (바꿀 때만 입력)' : '32자리',
                    })))),
                h('span', { class: 'field-hint' }, 'my.telegram.org → API development tools에서 발급합니다. 바꾼 뒤에는 먼저 [저장]하세요.'),
                field('전화번호', bindInput(src, 'phone', h('input', { class: 'input', value: src.phone || '', placeholder: '+821012345678 (국가번호 포함)' }))),
                field('작품 정보 - TMDB API 키 (선택)', bindInput(src, 'tmdb_key', h('input', {
                    class: 'input', type: 'password', value: '', autocomplete: 'off',
                    placeholder: src.has_tmdb_key ? '저장된 키 사용 중 (바꿀 때만 입력)' : 'API 키(32자) 또는 읽기 액세스 토큰',
                })), 'themoviedb.org 가입 → 설정 > API에서 무료로 발급. 넣으면 영상·시리즈 상세에 포스터·줄거리·출연진·회차 제목을 붙입니다.'),
                telegramLogin(src),
            ] : isPlex ? [
                field('서버 주소', bindInput(src, 'url', h('input', { class: 'input', value: src.url || '', placeholder: 'http://192.168.0.10:32400' }))),
                field('토큰 (X-Plex-Token)', bindInput(src, 'token', h('input', {
                    class: 'input', type: 'password', value: src.token || '', autocomplete: 'off',
                    placeholder: src.has_token ? '저장된 토큰 사용 중 (바꿀 때만 입력)' : 'Plex 토큰',
                }))),
                h('label', { class: 'check' }, bindInput(src, 'verify_ssl', h('input', { type: 'checkbox', checked: src.verify_ssl !== false }), 'checked'),
                    ' HTTPS 인증서 확인 (자체 서명 인증서면 끄기)'),
                libraryPicker(src),
            ] : [
                field('M3U 주소', bindInput(src, 'm3u', h('textarea', {
                    class: 'input url-list', rows: Math.max(2, (src.m3u || '').split('\n').length), spellcheck: false, text: src.m3u || '',
                    placeholder: 'https://iptv-org.github.io/iptv/countries/kr.m3u\n(여러 개면 한 줄에 하나씩)',
                })), '여러 개를 넣으면 한 채널 목록으로 합칩니다. 같은 채널은 위 주소의 것을 쓰고, 아래 주소는 예비로 둡니다.'),
                field('EPG(XMLTV) 주소', bindInput(src, 'epg', h('textarea', {
                    class: 'input url-list', rows: Math.max(1, (src.epg || '').split('\n').length), spellcheck: false, text: src.epg || '',
                    placeholder: '선택 - .xml 또는 .xml.gz (여러 개면 한 줄에 하나씩)',
                })), '편성표 채널 매칭: tvg-id → 채널 표시명 순서로 찾습니다.'),
            ];
            // 같은 종류끼리만 자리를 바꾼다 (다른 탭의 소스를 건너뛰고 가장 가까운 같은 종류와)
            const move = (d) => {
                let j = i + d;
                while (j >= 0 && j < drafts.length && drafts[j].type !== src.type) j += d;
                if (j < 0 || j >= drafts.length) return;
                [drafts[i], drafts[j]] = [drafts[j], drafts[i]]; touch(); draw();
            };
            list.appendChild(h('div', { class: `src-card${src.enabled ? '' : ' disabled'}` },
                h('div', { class: 'src-head' },
                    h('span', { class: `src-type ${src.type}` },
                        icon(isRadio ? 'radio' : isTg ? 'paper-plane' : isPlex ? 'server' : 'tower-broadcast'),
                        isRadio ? ' 라디오' : isTg ? ' 텔레그램' : isPlex ? ' Plex' : ' IPTV'),
                    bindInput(src, 'name', h('input', { class: 'input src-name', value: src.name || '', placeholder: '이름' })),
                    h('label', { class: 'check' }, bindInput(src, 'enabled', h('input', { type: 'checkbox', checked: !!src.enabled }), 'checked'), ' 사용'),
                    h('button', { class: 'icon-btn', title: '위로', on: { click: () => move(-1) } }, icon('arrow-up')),
                    h('button', { class: 'icon-btn', title: '아래로', on: { click: () => move(1) } }, icon('arrow-down')),
                    h('button', {
                        class: 'icon-btn danger', title: '삭제',
                        on: { click: () => { if (confirm(`"${src.name}" 소스를 지울까요?`)) { drafts.splice(i, 1); touch(); draw(); } } },
                    }, icon('trash'))),
                h('div', { class: 'src-fields' }, fields)));
        });
    }

    async function save() {
        saveBtn.disabled = true;
        try {
            const sources = drafts.map((s) => (pristine.get(s.id) === JSON.stringify(s) ? { id: s.id, type: s.type, keep: true } : s));
            const cfg = await api('/api/config', { method: 'PUT', json: { sources } });
            registry.config = cfg;
            drafts = cfg.sources.map((s) => ({ ...s, token: '' }));
            pristine = snapshot(drafts);
            await ctx.reloadProviders();
            dirty = false;
            saveBtn.classList.remove('pulse');
            toast('저장했습니다.', 'success');
            draw();
        } catch (e) {
            toast(`저장 실패: ${e.message}`, 'error', 6000);
        } finally {
            saveBtn.disabled = false;
        }
    }

    async function plexLogin() {
        let pin;
        try {
            pin = await api('/api/plex-auth/pin', { method: 'POST' });
        } catch (e) {
            return toast(`plex.tv 연결 실패: ${e.message}`, 'error');
        }
        const win = window.open(pin.auth_url, 'plexauth', 'width=600,height=720');
        const status = h('p', {}, icon('circle-notch', 'fa-spin'), ' 열린 창에서 Plex에 로그인하고 허용을 눌러 주세요...');
        const body = h('div', {}, status,
            h('p', { class: 'muted' }, '창이 안 열렸으면 ', h('a', { href: pin.auth_url, target: '_blank', rel: 'noopener', text: '여기를 눌러' }), ' 직접 여세요.'));
        const m = modal('Plex 계정으로 추가', body);
        const startedAt = Date.now();
        while (document.body.contains(m.el) && Date.now() - startedAt < 5 * 60 * 1000) {
            await new Promise((r) => setTimeout(r, 2000));
            let res;
            try { res = await api(`/api/plex-auth/pin/${pin.id}`); } catch (e) { continue; }
            if (!res.done) continue;
            if (win && !win.closed) win.close();
            clear(body);
            if (!res.servers.length) {
                body.appendChild(h('p', { text: '이 계정에서 접근할 수 있는 Plex 서버가 없습니다.' }));
                return;
            }
            body.appendChild(h('p', { text: '추가할 서버와 접속 주소를 고르세요. (같은 집 네트워크면 "로컬" 주소가 빠릅니다)' }));
            for (const sv of res.servers) {
                body.appendChild(h('div', { class: 'pick-server' },
                    h('b', {}, icon('server'), ` ${sv.name}`, sv.owned ? '' : ' (공유받음)'),
                    sv.connections.map((c) => h('button', {
                        class: 'btn btn-sm',
                        on: { click: () => {
                            add({ type: 'plex', name: sv.name, url: c.uri, token: sv.token, enabled: true, verify_ssl: true });
                            m.close();
                            toast('추가했습니다. [저장]을 눌러야 적용됩니다.');
                        } },
                    }, c.relay ? '릴레이' : c.local ? '로컬' : '원격', ` · ${c.uri}`))));
            }
            return;
        }
        if (document.body.contains(m.el)) { clear(body).appendChild(h('p', { text: '시간이 지나 로그인을 취소했습니다. 다시 시도해 주세요.' })); }
    }

    showSrcType(srcType);
    showTab((query && query.get('tab')) || storeGet('yp_settings_tab', 'sources'));
}

const TYPE_ICON = { plex: 'server', iptv: 'tower-broadcast', telegram: 'paper-plane', radio: 'radio' };

function fmtUptime(sec) {
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), mm = Math.floor((sec % 3600) / 60);
    return d ? `${d}일 ${hh}시간` : hh ? `${hh}시간 ${mm}분` : `${mm}분`;
}

/** 상태 점검: 소스마다 실제로 연결해 보고 결과, 서버 정보, 최근 경고 */
async function renderStatus(pane) {
    const refresh = h('button', { class: 'btn btn-sm', on: { click: () => renderStatus(pane) } }, icon('rotate'), ' 다시 점검');
    clear(pane).append(h('div', { class: 'toolbar pane-toolbar' }, refresh),
        h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text: '소스마다 연결해 보는 중...' })));
    let st;
    try { st = await api('/api/admin/status'); } catch (e) {
        pane.lastChild.replaceWith(h('p', { class: 'muted error', text: e.message }));
        return;
    }
    const sv = st.server;
    const rows = st.sources.map((s) => h('div', { class: `status-row ${s.ok === true ? 'ok' : s.ok === false ? 'bad' : 'off'}` },
        icon(s.ok === true ? 'circle-check' : s.ok === false ? 'circle-xmark' : 'circle-minus', 'status-icon'),
        h('span', { class: 'status-name' }, icon(TYPE_ICON[s.type] || 'circle'), ` ${s.name}`),
        h('span', { class: 'status-detail', text: s.detail }),
        s.ms ? h('span', { class: 'status-ms', text: `${s.ms}ms` }) : null));
    const facts = [
        ['버전', sv.version || '-'],
        ['가동 시간', fmtUptime(sv.uptime)],
        ['Python', sv.python],
        ['사용자', `${sv.users}명 (관리자 ${sv.admins})`],
        ['텔레그램', sv.telethon ? (sv.cryptg ? 'Telethon + cryptg (빠름)' : 'Telethon (cryptg 없음 - 느림)') : 'Telethon 없음'],
        ['동영상 캐시', sv.cache ? (sv.cache.enabled ? `${fmtBytes(sv.cache.bytes)} / ${fmtBytes(sv.cache.max_bytes)} · 영상 ${sv.cache.files}개` : '꺼 둠') : '-'],
        ['서버 변환 (ffmpeg)', sv.ffmpeg ? `있음 · 지금 ${sv.transcoding}개 변환 중` : '없음 - 텔레그램의 mkv·HEVC·AC-3 영상이 안 나올 수 있음 (sudo apt install ffmpeg)'],
        ['데이터 폴더', sv.data_dir],
    ];
    pane.lastChild.replaceWith(h('div', {},
        h('h3', { class: 'pane-title', text: '소스' }),
        h('div', { class: 'src-card status-list' }, rows.length ? rows : h('p', { class: 'muted', text: '소스가 없습니다.' })),
        h('h3', { class: 'pane-title', text: '서버' }),
        h('div', { class: 'src-card facts' }, facts.map(([k, v]) => h('div', { class: 'fact' }, h('span', { text: k }), h('b', { text: v })))),
        h('h3', { class: 'pane-title', text: '업데이트 (GitHub)' }),
        updateBox(),
        h('h3', { class: 'pane-title', text: `최근 경고 (${st.warnings.length})` }),
        h('div', { class: 'src-card warn-list' }, st.warnings.length
            ? st.warnings.map((w) => h('div', { class: `warn-row ${w.level.toLowerCase()}` },
                h('span', { class: 'warn-time', text: new Date(w.time * 1000).toLocaleString() }),
                h('span', { class: 'warn-msg', text: w.message, title: w.logger })))
            : h('p', { class: 'muted', text: '서버를 켠 뒤로 경고가 없습니다.' }))));
}

/** GitHub에 새 버전이 있는지 (서버가 6시간마다 확인, [지금 확인]으로 바로) */
function updateBox() {
    const box = h('div', { class: 'src-card update-box' });
    const draw = (u, busy = false) => {
        const when = u.checked_at ? new Date(u.checked_at * 1000).toLocaleString() : '아직 확인 안 함';
        const state = u.error ? h('span', { class: 'error', text: `확인 실패: ${u.error}` })
            : !u.latest ? h('span', { class: 'muted', text: '아직 확인하지 않았습니다.' })
                : u.newer ? h('b', { class: 'update-new', text: `새 버전 ${u.latest}이(가) 있습니다` })
                    : h('span', { class: 'ok', text: '최신 버전입니다' });
        clear(box).append(
            h('div', { class: 'update-line' }, icon(u.newer ? 'circle-up' : 'code-branch'), ' ', state),
            h('div', { class: 'muted update-meta', text: `이 서버 ${u.current} · GitHub ${u.latest || '-'} · 마지막 확인 ${when}`
                + (u.enabled ? ' · 6시간마다 자동 확인' : ' · 자동 확인 꺼짐 (MP_UPDATE_CHECK=0)') }),
            h('div', { class: 'toolbar' },
                h('button', { class: 'btn btn-sm', disabled: busy, on: { click: async () => {
                    draw(u, true);
                    try { u = await api('/api/admin/update/check', { method: 'POST' }); setGithubInfo(u); } catch (e) { u = { ...u, error: e.message }; }
                    draw(u);
                } } }, icon(busy ? 'circle-notch' : 'rotate', busy ? 'fa-spin' : ''), busy ? ' 확인 중...' : ' 지금 확인'),
                u.newer ? h('button', { class: 'btn btn-sm btn-accent', on: { click: () => showGithubUpdate(u) } }, icon('download'), ' 업데이트 방법') : null,
                h('a', { class: 'btn btn-sm', href: u.repo || 'https://github.com/yume-script/ym_player', target: '_blank', rel: 'noopener' },
                    icon('arrow-up-right-from-square'), ' 저장소')));
    };
    box.appendChild(h('p', { class: 'muted', text: '불러오는 중...' }));
    api('/api/admin/update').then((u) => draw(u)).catch((e) => clear(box).appendChild(h('p', { class: 'muted error', text: e.message })));
    return box;
}

function fmtBytes(n) {
    if (!n) return '0';
    const gb = n / 1024 ** 3;
    return gb >= 1 ? `${gb.toFixed(gb >= 100 ? 0 : 1)}GB` : `${Math.max(1, Math.round(n / 1024 ** 2))}MB`;
}

/** 동영상 캐시: 텔레그램/Plex 원본 재생 파일의 본 조각을 디스크에 남기고, 재생 중 앞쪽을 미리 받는다 */
/** 모든 사용자에게 적용되는 설정 (바꾸면 바로 저장) */
async function renderGeneral(pane) {
    let st;
    try { st = (await api('/api/admin/general')).settings; } catch (e) { clear(pane).appendChild(h('p', { class: 'muted error', text: e.message })); return; }
    const set = async (patch, chk) => {
        chk.disabled = true;
        try {
            st = (await api('/api/admin/general', { method: 'PUT', json: patch })).settings;
            toast('저장했습니다.', 'success');
        } catch (e) {
            toast(e.message, 'error');
            chk.checked = !chk.checked;
        } finally { chk.disabled = false; }
    };
    const blChk = h('input', { type: 'checkbox', checked: st.hide_bl_gl, on: { change: () => set({ hide_bl_gl: blChk.checked }, blChk) } });
    clear(pane).append(h('div', { class: 'src-card' },
        h('h3', { class: 'pane-title' }, icon('sliders'), ' 공통'),
        h('label', { class: 'check' }, blChk, ' 웹툰 목록에서 BL·GL 작품 숨기기'),
        h('p', { class: 'field-hint', text: '작품 정보 파일(웹툰명.yaml)의 장르·태그나 회차의 제목·캡션·파일 이름에 BL / GL 표시(#BL, [GL], 보이즈러브, 걸즈러브, 백합 …)가 있으면 '
            + '그 작품 전체를 웹툰 목록·검색·홈·이어보기에서 뺍니다. 모든 사용자에게 적용됩니다. 처음 켜면 작품 정보 파일을 뒤에서 한 번씩 읽느라 '
            + '모두 가려지기까지 잠깐 걸릴 수 있습니다.' })));
}

async function renderCache(pane) {
    clear(pane).appendChild(h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text: '불러오는 중...' })));
    let data;
    try { data = await api('/api/admin/cache'); } catch (e) { clear(pane).appendChild(h('p', { class: 'muted error', text: e.message })); return; }
    const { settings: st, stats } = data;
    const enabledIn = h('input', { type: 'checkbox', checked: st.enabled });
    const dirIn = h('input', { class: 'input', value: st.dir || '', placeholder: `${stats.dir} (기본)` });
    const maxIn = h('input', { class: 'input', type: 'number', min: '0.5', step: '0.5', value: String(st.max_gb) });
    const aheadIn = h('input', { class: 'input', type: 'number', min: '0', max: '30', step: '1', value: String(st.ahead_min) });
    const pct = stats.max_bytes ? Math.min(100, (stats.bytes / stats.max_bytes) * 100) : 0;
    const save = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
    const wipe = h('button', { class: 'btn' }, icon('trash'), ' 캐시 비우기');
    save.addEventListener('click', async () => {
        save.disabled = true;
        try {
            await api('/api/admin/cache', { method: 'PUT', json: { enabled: enabledIn.checked, dir: dirIn.value.trim(), max_gb: Number(maxIn.value), ahead_min: Number(aheadIn.value) } });
            toast('캐시 설정을 저장했습니다.', 'success');
            renderCache(pane);
        } catch (e) { toast(e.message, 'error', 6000); } finally { save.disabled = false; }
    });
    wipe.addEventListener('click', async () => {
        if (!confirm('캐시에 저장된 동영상 조각을 모두 지울까요? (보던 영상은 다시 받아옵니다)')) return;
        try { await api('/api/admin/cache', { method: 'DELETE' }); toast('캐시를 비웠습니다.', 'success'); } catch (e) { toast(e.message, 'error'); }
        renderCache(pane);
    });
    clear(pane).append(
        h('div', { class: 'src-card cache-card' },
            h('h3', { class: 'pane-title' }, icon('hard-drive'), ' 사용량'),
            h('div', { class: 'cache-bar' }, h('div', { style: { width: `${pct}%` } })),
            h('p', { class: 'muted', text: `${fmtBytes(stats.bytes)} / ${fmtBytes(stats.max_bytes)} 사용 · 영상 ${stats.files}개 · ${stats.dir}` }),
            wipe),
        h('div', { class: 'src-card cache-card' },
            h('h3', { class: 'pane-title' }, icon('sliders'), ' 설정'),
            h('label', { class: 'check' }, enabledIn, ' 본 부분을 디스크에 남기기'),
            field('캐시 폴더', dirIn, '비워 두면 데이터 폴더 안 cache. 큰 디스크로 옮기려면 그 경로를 넣으세요 (서버가 쓸 수 있어야 함).'),
            h('div', { class: 'row-2' },
                field('최대 용량 (GB)', maxIn, '넘치면 가장 오래 안 본 영상부터 지웁니다.'),
                field('미리 받기 (분)', aheadIn, '재생 중 지금 위치보다 이만큼 앞까지 미리 받습니다. 0이면 끔.')),
            h('p', { class: 'field-hint', text: '텔레그램 영상과 Plex 원본 재생(변환 없이 보는 파일)에 쓰입니다. Plex 변환(HLS) 재생과 IPTV 라이브는 캐시하지 않습니다. 캐시를 꺼도 텔레그램은 앞쪽 미리 받기를 메모리로 합니다.' }),
            save));
}

/** 왼쪽 위 이름과 아이콘 (로그인 화면, 브라우저 탭에도 쓰임) */
async function renderLook(pane) {
    let b;
    try { b = await api('/api/branding'); } catch (e) { clear(pane).appendChild(h('p', { class: 'muted error', text: e.message })); return; }
    const mark = h('span', { class: 'brand-mark' });
    const name = h('span', { class: 'brand-name' });
    const preview = h('div', { class: 'brand-preview' }, mark, name);
    const show = (info) => {
        b = info;
        applyBranding(info); // 미리보기도 .brand-mark/.brand-name이라 같이 바뀐다
    };
    const titleIn = h('input', { class: 'input', value: b.title, maxlength: '40', placeholder: 'YMPlayer' });
    const saveTitle = h('button', { class: 'btn btn-accent' }, icon('check'), ' 이름 저장');
    saveTitle.addEventListener('click', async () => {
        try { show(await api('/api/admin/branding', { method: 'PUT', json: { title: titleIn.value.trim() } })); toast('이름을 바꿨습니다.', 'success'); } catch (e) { toast(e.message, 'error'); }
    });
    const fileIn = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp,image/x-icon,.ico', class: 'hidden' });
    const upload = h('button', { class: 'btn', on: { click: () => fileIn.click() } }, icon('upload'), ' 아이콘 올리기');
    const reset = h('button', { class: 'btn' }, icon('rotate-left'), ' 기본 아이콘');
    fileIn.addEventListener('change', async () => {
        const f = fileIn.files[0];
        fileIn.value = '';
        if (!f) return;
        if (f.size > 512 * 1024) return toast('아이콘 파일은 512KB까지 올릴 수 있습니다.', 'error');
        try {
            show(await api('/api/admin/branding/icon', { method: 'PUT', body: f, headers: { 'Content-Type': f.type || 'application/octet-stream' } }));
            toast('아이콘을 바꿨습니다.', 'success');
        } catch (e) { toast(e.message, 'error'); }
    });
    reset.addEventListener('click', async () => {
        try { show(await api('/api/admin/branding/icon', { method: 'DELETE' })); toast('기본 아이콘으로 되돌렸습니다.'); } catch (e) { toast(e.message, 'error'); }
    });
    // 관리자가 사이드바 [테마]에서 자기 테마를 따로 골라 두었으면 기본값을 바꿔도 내 화면은 그대로다 -> 알려 준다
    const ownNote = h('div', { class: 'theme-own-note hidden' });
    const paintOwnNote = () => {
        const label = ownThemeLabel();
        ownNote.classList.toggle('hidden', !label);
        if (!label) return;
        clear(ownNote).append(icon('circle-info'),
            h('span', { text: ` 지금 이 화면은 내가 따로 고른 테마(${label})를 쓰고 있어서, 기본 테마를 바꿔도 내 화면은 그대로입니다.` }),
            h('button', { class: 'btn btn-sm', on: { click: async () => { await useDefaultTheme(); paintOwnNote(); toast('기본 테마를 따릅니다.'); } } },
                '기본 테마 쓰기'));
    };
    paintOwnNote();
    clear(pane).append(h('div', { class: 'src-card brand-card' },
        h('h3', { class: 'pane-title' }, icon('palette'), ' 이름과 아이콘'),
        preview,
        field('이름', titleIn, '왼쪽 위, 로그인 화면, 브라우저 탭에 나옵니다. 비우면 YMPlayer.'),
        saveTitle,
        h('div', { class: 'brand-row' }, upload, reset, fileIn),
        h('p', { class: 'field-hint', text: 'PNG, JPG, GIF, WebP, ICO 그림 (512KB까지). 정사각형 그림이 잘 맞습니다. 브라우저 탭 아이콘(favicon)으로도 쓰입니다.' }),
        h('label', { class: 'check' }, h('input', {
            type: 'checkbox', id: 'brandShowVersion', checked: b.show_version !== false,
            on: { change: async (e) => {
                try { show(await api('/api/admin/branding', { method: 'PUT', json: { show_version: e.target.checked } })); } catch (err) { toast(err.message, 'error'); }
            } },
        }), ` 아이콘에 버전 표시 (지금 ${b.version || '?'})`)),
    h('div', { class: 'src-card brand-card' },
        h('h3', { class: 'pane-title' }, icon('swatchbook'), ' 기본 테마'),
        h('p', { class: 'field-hint', text: '모든 사용자와 로그인 화면의 기본 테마입니다. 사용자는 사이드바 아래 [테마] 버튼으로 자기 테마를 따로 고를 수 있습니다.' }),
        ownNote,
        themePicker({
            admin: true, value: { theme: b.theme, accent: b.accent },
            onPick: async (v) => {
                try {
                    const info = await api('/api/admin/branding', { method: 'PUT', json: v });
                    setDefaults(info);
                    toast(ownThemeLabel() ? '기본 테마를 바꿨습니다. 지금 화면은 내 테마를 쓰고 있어 그대로입니다.' : '기본 테마를 바꿨습니다.', 'success');
                    paintOwnNote();
                } catch (e) { toast(e.message, 'error'); }
            },
        })));
    show(b);
}

/** 백업 내려받기 / 백업 파일로 되돌리기 */
/** 참고한 저장소, 쓰는 라이브러리, 변경 이력 (credits.js) */
function renderThanks(pane) {
    const linkRow = (it) => h('li', { class: 'thanks-item' },
        h('div', { class: 'thanks-name' },
            it.url ? h('a', { href: it.url, target: '_blank', rel: 'noopener', text: it.name }) : h('b', { text: it.name }),
            it.url ? h('span', { class: 'thanks-url', text: it.url.replace(/^https?:\/\//, '') }) : null),
        h('p', { class: 'thanks-note', text: it.note }));
    // 변경 이력은 날짜별로 묶는다 (새것이 위)
    const byDate = [];
    for (const e of CHANGELOG) {
        const last = byDate[byDate.length - 1];
        if (last && last.date === e.date) last.items.push(e); else byDate.push({ date: e.date, items: [e] });
    }
    clear(pane).append(
        h('div', { class: 'src-card thanks-card thanks-people' },
            h('h3', { class: 'pane-title' }, icon('hand-holding-heart'), ' 특별히 고마운 분'),
            (CREDITS.people || []).map((p) => h('div', { class: 'thanks-person' },
                h('div', { class: 'thanks-name' }, h('b', { text: p.name }), h('span', { class: 'thanks-url', text: p.role })),
                h('p', { class: 'thanks-note', text: p.note })))),
        h('div', { class: 'src-card thanks-card' },
            h('h3', { class: 'pane-title' }, icon('heart'), ' 참고한 저장소'),
            h('p', { class: 'field-hint', text: 'YMPlayer는 아래 프로젝트에서 출발했거나 아이디어를 얻었습니다. 고맙습니다.' }),
            h('ul', { class: 'thanks-list' }, CREDITS.sources.map(linkRow))),
        h('div', { class: 'src-card thanks-card' },
            h('h3', { class: 'pane-title' }, icon('cubes'), ' 쓰고 있는 오픈소스'),
            h('ul', { class: 'thanks-list' }, CREDITS.libraries.map(linkRow))),
        h('div', { class: 'src-card thanks-card' },
            h('h3', { class: 'pane-title' }, icon('clock-rotate-left'), ' 변경 이력'),
            h('p', { class: 'field-hint' }, '저장소: ', h('a', { href: REPO, target: '_blank', rel: 'noopener', text: REPO.replace(/^https?:\/\//, '') })),
            byDate.map((g) => h('section', { class: 'changelog-day' },
                h('h4', { text: g.date }),
                h('ul', { class: 'changelog' }, g.items.map((e) => h('li', {},
                    h('a', { class: 'changelog-pr', href: `${REPO}/pull/${e.pr}`, target: '_blank', rel: 'noopener', text: `#${e.pr}` }),
                    h('span', {}, e.version ? h('span', { class: 'changelog-ver', text: `v${e.version}` }) : null, e.title))))))));
}

/** 복사 버튼이 붙은 한 줄 (명령·주소) */
function copyLine(text) {
    const code = h('code', { text });
    return h('div', { class: 'cmd-box' }, code, h('button', { class: 'btn btn-sm', on: { click: async () => {
        try { await navigator.clipboard.writeText(text); toast('복사했습니다.'); }
        catch (e) { getSelection().selectAllChildren(code); toast('복사하지 못했습니다. 선택된 글자를 직접 복사해 주세요.', 'error'); }
    } } }, icon('copy'), ' 복사'));
}

/** MCP: Claude 같은 AI 앱이 YMPlayer를 도구로 쓰게 하는 토큰 (사용자마다, 그 사용자가 볼 수 있는 것만) */
async function renderMcp(pane) {
    clear(pane);
    let data, users;
    try {
        [data, users] = await Promise.all([api('/api/admin/mcp'), api('/api/users').then((r) => r.users)]);
    } catch (e) {
        pane.appendChild(h('p', { class: 'muted error', text: e.message }));
        return;
    }
    const fmt = (t) => (t ? new Date(t * 1000).toLocaleString() : '쓴 적 없음');
    const nameIn = h('input', { class: 'input', placeholder: '이름 (예: 내 Claude)', maxlength: 60 });
    const userSel = h('select', { class: 'select' },
        users.map((u) => h('option', { value: u.username, text: `${u.username} (${u.role === 'admin' ? '관리자' : '사용자'})`,
            selected: u.username === registry.user.username })));
    const create = async () => {
        try {
            const r = await api('/api/admin/mcp', { method: 'POST', json: { name: nameIn.value, user: userSel.value } });
            showMcpToken(r);
            renderMcp(pane);
        } catch (e) { toast(e.message, 'error'); }
    };
    const rows = data.tokens.length
        ? h('div', { class: 'mcp-tokens' }, data.tokens.map((t) => h('div', { class: 'mcp-token' },
            h('div', { class: 'mcp-token-info' },
                h('b', { text: t.name }),
                h('span', { class: 'muted', text: ` · ${t.user} · …${t.hint} · 만든 때 ${fmt(t.created)} · 마지막 사용 ${fmt(t.last_used)}` })),
            h('button', { class: 'btn btn-sm', on: { click: async () => {
                if (!confirm(`"${t.name}" 토큰을 지울까요? 이 토큰을 쓰는 앱은 더 이상 연결되지 않습니다.`)) return;
                try { await api(`/api/admin/mcp/${encodeURIComponent(t.id)}`, { method: 'DELETE' }); renderMcp(pane); }
                catch (e) { toast(e.message, 'error'); }
            } } }, icon('trash'), ' 지우기'))))
        : h('p', { class: 'muted', text: '아직 만든 토큰이 없습니다.' });
    pane.append(
        h('div', { class: 'src-card' },
            h('h3', { class: 'pane-title' }, icon('robot'), ' MCP 서버'),
            h('p', { text: 'Claude 같은 AI 앱에 YMPlayer를 연결하면 "호프 찾아서 링크 줘", "이어볼 거 뭐 있어?", "재즈 라디오 지금 무슨 곡이야?"처럼 물어볼 수 있습니다. '
                + '찾기 · 이어보기 · 최근 추가 · 라이브러리 목록 · 라디오 지금 나오는 곡 · 상태를 읽기만 합니다 (기록이나 설정은 바꾸지 않음).' }),
            h('p', { class: 'field-hint', text: '토큰은 사용자마다 만들고, 그 사용자가 볼 수 있는 소스·라이브러리만 보입니다. 서버 주소:' }),
            copyLine(data.url)),
        h('div', { class: 'src-card' },
            h('h3', { class: 'pane-title' }, icon('key'), ' 토큰'),
            rows,
            h('div', { class: 'toolbar mcp-new' }, nameIn, userSel, h('button', { class: 'btn btn-accent', on: { click: create } }, icon('plus'), ' 토큰 만들기'))));
}

/** 디스코드 채널의 새 글을 화면 아래쪽에 흘리기 (서버: discord_feed.py, 화면: ticker.js) */
async function renderDiscord(pane) {
    let data;
    try { data = await api('/api/admin/discord'); } catch (e) { clear(pane).appendChild(h('p', { class: 'muted error', text: e.message })); return; }
    const st = data.settings;
    const enabledIn = h('input', { type: 'checkbox', checked: st.enabled });
    const tokenIn = h('input', { class: 'input', type: 'password', autocomplete: 'off',
        placeholder: st.has_token ? '저장됨 (바꿀 때만 입력)' : '봇 토큰' });
    const chanIn = h('textarea', { class: 'input', rows: '3', placeholder: '채널 ID (한 줄에 하나, 채널 링크를 붙여 넣어도 됨)' });
    chanIn.value = st.channels.join('\n');
    const num = (v, min, max) => h('input', { class: 'input', type: 'number', min: String(min), max: String(max), value: String(v) });
    const intervalIn = num(st.interval, 10, 600), repeatIn = num(st.repeat, 1, 5), speedIn = num(st.speed, 30, 300);
    const statusBox = h('div', { class: 'discord-status' });
    const paint = (s) => {
        const chans = Object.entries(s.channels || {});
        statusBox.replaceChildren(
            s.error ? h('p', { class: 'muted error', text: s.error })
                : h('p', { class: 'muted', text: s.checked ? `마지막 확인 ${new Date(s.checked * 1000).toLocaleTimeString()}` : (st.enabled ? '아직 확인 전' : '꺼져 있음') }),
            ...chans.map(([id, c]) => h('p', { class: c.error ? 'muted error' : 'muted' },
                icon(c.error ? 'triangle-exclamation' : 'check'), ` ${c.name || id}${c.error ? ` - ${c.error}` : ' 지켜보는 중'}`)));
    };
    paint(data.status);
    const save = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
    save.addEventListener('click', async () => {
        save.disabled = true;
        try {
            const r = await api('/api/admin/discord', { method: 'PUT', json: {
                enabled: enabledIn.checked, token: tokenIn.value.trim(), channels: chanIn.value,
                interval: Number(intervalIn.value), repeat: Number(repeatIn.value), speed: Number(speedIn.value) } });
            toast('디스코드 설정을 저장했습니다.', 'success');
            tokenIn.value = '';
            Object.assign(st, r.settings);
            tokenIn.placeholder = st.has_token ? '저장됨 (바꿀 때만 입력)' : '봇 토큰';
            chanIn.value = st.channels.join('\n');
            paint(r.status);
        } catch (e) { toast(e.message, 'error', 6000); } finally { save.disabled = false; }
    });
    const test = h('button', { class: 'btn', on: { click: async () => {
        try { await api('/api/admin/discord/test', { method: 'POST' }); toast('몇 초 안에 아래쪽에 시험 글이 흐릅니다.'); } catch (e) { toast(e.message, 'error'); }
    } } }, icon('play'), ' 시험 글 흘리기');
    const refresh = h('button', { class: 'btn', on: { click: () => renderDiscord(pane) } }, icon('rotate-right'), ' 상태 새로 보기');
    clear(pane).append(
        h('div', { class: 'src-card' },
            h('h3', { class: 'pane-title' }, icon('comment-dots'), ' 디스코드 새 글 흘리기'),
            h('p', { text: '정한 디스코드 채널에 새 글이 올라오면 모든 사용자의 화면 아래쪽에 글이 흘러갑니다 (재생 중에도, 전체화면에서도). '
                + '마우스를 올리면 멈추고, 사용자는 사이드바 [테마]에서 자기 브라우저만 끌 수 있습니다.' }),
            h('label', { class: 'check' }, enabledIn, ' 켜기'),
            field('봇 토큰', tokenIn, 'Discord Developer Portal > 내 앱 > Bot에서 [Reset Token]으로 받은 값. 서버에만 저장되고 브라우저로는 다시 보내지 않습니다.'),
            field('채널', chanIn, '디스코드 설정 > 고급 > 개발자 모드를 켜고 채널을 오른쪽 클릭 > [채널 ID 복사]. 10개까지.'),
            h('div', { class: 'row-3' },
                field('확인 간격 (초)', intervalIn, '10~600'),
                field('글마다 흘리는 횟수', repeatIn, '1~5'),
                field('흐르는 속도 (px/초)', speedIn, '30~300')),
            h('div', { class: 'toolbar' }, save, test, refresh)),
        h('div', { class: 'src-card' },
            h('h3', { class: 'pane-title' }, icon('heart-pulse'), ' 상태'),
            statusBox,
            h('p', { class: 'field-hint', text: '봇 준비: ① Developer Portal에서 앱을 만들고 Bot 메뉴에서 MESSAGE CONTENT INTENT를 켭니다 (안 켜면 글 내용이 비어 옵니다). '
                + '② OAuth2 > URL Generator에서 scope "bot", 권한 "View Channels"와 "Read Message History"를 골라 나온 주소로 봇을 그 서버에 초대합니다. '
                + '③ 위에 토큰과 채널 ID를 넣고 저장. 켠 뒤에 올라온 글부터 보입니다 (예전 글은 흘리지 않음).' })));
}

/** Plex 라이브러리에 새로 들어온 동영상을 텔레그램 채널에 자동으로 올리기 (서버: plex_to_tg.py) */
async function renderMirror(pane) {
    clear(pane).appendChild(h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text: '불러오는 중...' })));
    let data, opts;
    try {
        [data, opts] = await Promise.all([api('/api/admin/plex-tg'), api('/api/admin/plex-tg/options')]);
    } catch (e) { clear(pane).appendChild(h('p', { class: 'muted error', text: e.message })); return; }
    const st = data.settings;
    const channelCache = {};
    const loadChannels = (sid) => (channelCache[sid] = channelCache[sid]
        || api(`/api/tg/${encodeURIComponent(sid)}/channels`).then((r) => r.channels).catch((e) => { delete channelCache[sid]; throw e; }));
    const intervalIn = h('input', { class: 'input', type: 'number', min: '2', max: '1440', value: String(st.interval) });
    const maxSel = h('select', { class: 'select' }, [[2, '2GB (일반 계정)'], [4, '4GB (텔레그램 프리미엄)']].map(([v, t]) =>
        h('option', { value: String(v), text: t, selected: st.max_gb === v })));
    const rulesBox = h('div', { class: 'mirror-rules' });
    const rows = [];
    const opt = (v, t, sel, name) => h('option', { value: v, text: t, selected: sel, dataset: { name: name || t } });

    const addRule = (r = {}) => {
        const enabledIn = h('input', { type: 'checkbox', checked: r.enabled !== false });
        const plexSel = h('select', { class: 'select' }, opt('', 'Plex 라이브러리 고르기', !r.section),
            ...opts.plex.flatMap((p) => p.sections.map((sec) => opt(`${p.id}|${sec.key}`, `${p.name} · ${sec.title} (${sec.type === 'show' ? 'TV' : '영화'})`,
                r.plex === p.id && r.section === sec.key, sec.title))));
        const tgSel = h('select', { class: 'select' }, opts.telegram.map((t) => opt(t.id, t.name, r.tg === t.id)));
        const chSel = h('select', { class: 'select' }, r.channel ? opt(r.channel, r.channel_title || r.channel, true) : opt('', '채널 고르기', true));
        const waitIn = h('input', { type: 'checkbox', checked: r.wait_subs !== false });
        const burnIn = h('input', { type: 'checkbox', checked: r.burn_subs !== false });
        const hoursIn = h('input', { class: 'input input-sm mirror-days', type: 'number', min: '0', max: '8760', value: String(r.wait_hours ?? 12) });
        const capIn = h('textarea', { class: 'input', rows: '3', placeholder: '{name}\n\n{summary}' });
        capIn.value = r.caption || '{name}\n\n{summary}';
        const fillChannels = async () => {
            if (!tgSel.value) return;
            const keep = chSel.value;
            try {
                const list = await loadChannels(tgSel.value);
                clear(chSel).append(opt('', '채널 고르기', !keep),
                    ...list.map((c) => opt(String(c.id), `${c.title}${c.kind === 'group' ? ' (그룹)' : ''}`, String(c.id) === keep, c.title)));
            } catch (e) { toast(`텔레그램 채널 목록을 못 받았습니다: ${e.message}`, 'error'); }
        };
        tgSel.addEventListener('change', () => { chSel.value = ''; fillChannels(); });
        chSel.addEventListener('focus', fillChannels, { once: true });
        const test = h('button', { class: 'btn btn-sm', title: '저장한 뒤에 쓰세요', on: { click: async () => {
            try { const t = await api('/api/admin/plex-tg/test', { method: 'POST', json: { rule: row.id } }); toast(`"${t.title}"을(를) 올리기 시작합니다. 아래 상태에서 진행을 보세요.`); setTimeout(paintStatus, 1500); }
            catch (e) { toast(e.message, 'error', 6000); }
        } } }, icon('paper-plane'), ' 가장 최근 것 하나 올려 보기');
        const del = h('button', { class: 'btn btn-sm', on: { click: () => { rows.splice(rows.indexOf(row), 1); card.remove(); } } }, icon('trash'), ' 지우기');
        const card = h('div', { class: 'mirror-rule' },
            h('label', { class: 'check' }, enabledIn, ' 켜기'),
            h('div', { class: 'row-2 mirror-pick' }, field('Plex 라이브러리', plexSel), field('올릴 텔레그램', h('div', { class: 'toolbar' }, tgSel, chSel))),
            h('div', { class: 'mirror-wait' }, h('label', { class: 'check' }, waitIn, ' 자막이 생길 때까지 기다렸다가 올리기'),
                h('span', { class: 'muted', text: ' 최대' }), hoursIn, h('span', { class: 'muted', text: '시간 (0이면 계속 기다림, 하드섭은 상태 표에서 [지금 올리기])' })),
            h('label', { class: 'check', title: '영상 안에 자막이 없고 외부 자막만 있을 때. 다시 인코딩하느라 시간이 걸립니다.' }, burnIn,
                ' 영상 안에 자막이 없으면 외부 자막을 화면에 입혀 다시 인코딩 (텔레그램 앱에서도 자막이 보임)'),
            field('캡션', capIn, '{name} 제목(영화 "제목 (연도)", 에피소드 "작품 S01E02 - 제목") · {summary} 줄거리 · {title} {year} {show} {season} {episode} {code} {genres} {tags}(#장르) {library} {filename}'),
            h('div', { class: 'toolbar' }, test, del));
        const row = { id: r.id, get: () => {
            const [plex, section] = plexSel.value.split('|');
            return { id: row.id, enabled: enabledIn.checked, plex, section, section_title: ((plexSel.selectedOptions[0] || {}).dataset || {}).name || '',
                tg: tgSel.value, channel: chSel.value, channel_title: ((chSel.selectedOptions[0] || {}).dataset || {}).name || '', caption: capIn.value,
                wait_subs: waitIn.checked, wait_hours: Number(hoursIn.value) || 0, burn_subs: burnIn.checked };
        } };
        rows.push(row);
        rulesBox.appendChild(card);
        if (r.channel) fillChannels();
    };
    st.rules.forEach(addRule);

    const save = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
    save.addEventListener('click', async () => {
        save.disabled = true;
        try {
            const r = await api('/api/admin/plex-tg', { method: 'PUT', json: { interval: Number(intervalIn.value), max_gb: Number(maxSel.value), rules: rows.map((x) => x.get()) } });
            r.settings.rules.forEach((x, i) => { if (rows[i]) rows[i].id = x.id; });
            toast('저장했습니다. 새 규칙은 지금 있는 것 다음부터 올립니다.', 'success', 5000);
            setTimeout(paintStatus, 1500);
        } catch (e) { toast(e.message, 'error', 6000); } finally { save.disabled = false; }
    });
    const check = h('button', { class: 'btn', on: { click: async () => { await api('/api/admin/plex-tg/check', { method: 'POST' }); toast('지금 확인합니다.'); setTimeout(paintStatus, 2000); } } }, icon('rotate-right'), ' 지금 확인');
    const statusBox = h('div', { class: 'mirror-status' });
    const fmtB = (n) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)}GB` : `${Math.round(n / 1024 ** 2)}MB`);
    async function paintStatus() {
        if (!statusBox.isConnected && pane.isConnected === false) return;
        let s;
        try { s = (await api('/api/admin/plex-tg')).status; } catch (e) { return; }
        const cur = s.current;
        const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '');
        const chip = (cls, text) => h('span', { class: `mirror-chip ${cls}`, text });
        const row = (title, status, subs, note, extra = null) => h('div', { class: 'mirror-row', role: 'row' },
            h('span', { class: 'mirror-title', title, text: title }), h('span', {}, status, extra), h('span', { class: 'mirror-subs', text: subs }),
            h('span', { class: 'mirror-note muted', title: note, text: note }));
        const head = () => h('div', { class: 'mirror-row mirror-head', role: 'row' },
            h('span', { text: '제목' }), h('span', { text: '상태' }), h('span', { text: '자막' }), h('span', { text: '비고' }));
        // 진행: 지금 올리는 것 -> 올릴 차례 -> 자막 기다리는 것 (자막을 기다리는 것은 확인할 때마다 대기열에 다시 들어가므로 한 줄로)
        const waitKeys = new Map((s.waiting || []).map((w) => [`${w.rule}/${w.key}`, w]));
        const live = [];
        if (cur) {
            const pct = cur.total ? Math.min(100, (cur.done / cur.total) * 100) : 0;
            live.push(row(cur.title, chip('run', '올리는 중'), '', `${cur.phase}${cur.total && /올리는|받는/.test(cur.phase) ? ` · ${fmtB(cur.done)} / ${fmtB(cur.total)}` : ''}`,
                h('div', { class: 'cache-bar mirror-bar' }, h('div', { style: { width: `${pct}%` } }))));
        }
        const curKey = cur ? `${cur.rule}/${cur.key}` : '';
        const shown = new Set([curKey]);
        for (const q of s.queue) {
            const k = `${q.rule}/${q.key}`;
            if (shown.has(k) || waitKeys.has(k)) continue;
            shown.add(k);
            live.push(row(q.title, chip('queue', '올릴 차례'), '확인 전', ''));
        }
        // 자막 기다림: 자막을 영상에 입힌 하드섭은 자동으로 알 수 없으니 직접 [지금 올리기] / [빼기]
        const act = (w, action, label, ask) => h('button', { class: 'btn btn-sm', on: { click: async (e) => {
            if (ask && !confirm(ask)) return;
            e.currentTarget.disabled = true;
            try {
                await api('/api/admin/plex-tg/item', { method: 'POST', json: { rule: w.rule, key: w.key, action } });
                toast(action === 'upload' ? `"${w.title}"을(를) 지금 올립니다.` : `"${w.title}"을(를) 뺐습니다.`);
            } catch (err) { toast(err.message, 'error'); }
            paintStatus();
        } } }, label);
        for (const [k, w] of waitKeys) {
            if (shown.has(k)) continue;
            const r = row(w.title, chip('wait', '자막 기다림'), '없음', `${when(w.since)}부터`);
            r.lastChild.append(h('span', { class: 'mirror-actions' }, act(w, 'upload', '지금 올리기'),
                act(w, 'drop', '빼기', `"${w.title}"을(를) 올리지 않고 뺄까요?`)));
            live.push(r);
        }
        const hist = s.history.map((x) => row(x.title,
            x.ok ? chip('ok', '완료') : String(x.note || '').startsWith('건너뜀') ? chip('skip', '건너뜀') : chip('fail', '실패'),
            x.subs || (x.ok ? '' : '-'),
            // 자막 이야기는 자막 칸에 있으니 비고에서는 뺀다
            [when(x.at), x.channel ? `→ ${x.channel}` : '',
                ...String(x.note || '').split(', ').filter((n) => n && !(x.subs && /^(자막 파일 \d+개 첨부|내장 자막|자막 없음|자막 입혀 인코딩)$/.test(n)))]
                .filter(Boolean).join(' · ')));
        statusBox.replaceChildren(...[
            s.error ? h('p', { class: 'muted error', text: s.error }) : null,
            h('p', { class: 'muted', text: s.last_check ? `마지막 확인 ${new Date(s.last_check * 1000).toLocaleString()}` : '아직 확인 전' }),
            h('h4', { text: `진행 (${live.length})` }),
            live.length ? h('div', { class: 'mirror-table', role: 'table' }, head(), ...live) : h('p', { class: 'muted', text: '올릴 것이 없습니다.' }),
            h('h4', { text: '최근 기록' }),
            hist.length ? h('div', { class: 'mirror-table', role: 'table' }, head(), ...hist) : h('p', { class: 'muted', text: '아직 올린 것이 없습니다.' }),
        ].filter(Boolean));
        clearTimeout(pane._mirrorTimer);
        if (pane.isConnected && !pane.classList.contains('hidden')) pane._mirrorTimer = setTimeout(paintStatus, cur || s.queue.length ? 2000 : 15000);
    }
    const plexErr = opts.plex.filter((p) => p.error).map((p) => h('p', { class: 'muted error', text: `${p.name}: ${p.error}` }));
    clear(pane).append(
        h('div', { class: 'src-card' },
            h('h3', { class: 'pane-title' }, icon('cloud-arrow-up'), ' Plex → 텔레그램 자동 올리기'),
            h('p', { text: '고른 Plex 라이브러리에 새 동영상(영화·에피소드)이 들어오면, 로그인해 둔 텔레그램 계정으로 고른 채널에 파일을 올립니다. '
                + '규칙을 처음 저장할 때 이미 있던 것은 올리지 않고 그 뒤에 들어온 것부터 올립니다. 한 번에 하나씩 올리고, 실패하면 다음 확인 때 다시(3번까지). '
                + '한도를 넘는 파일은 ffmpeg로 시간을 나눠 "이름 part1", "part2"…로 올리고(YMPlayer에서 이어서 재생), 외부 자막 파일도 같이 올립니다. '
                + 'Plex의 포스터·줄거리·장르·출연·회차 정보를 같이 남겨 텔레그램 화면에서 Plex처럼 보입니다.' }),
            ...(opts.plex.length && opts.telegram.length ? [] : [h('p', { class: 'muted error', text: 'Plex 소스와 텔레그램 소스(로그인까지)가 둘 다 있어야 합니다.' })]),
            ...plexErr,
            h('div', { class: 'row-2' }, field('확인 간격 (분)', intervalIn, '2~1440'), field('파일 크기 한도', maxSel, '넘는 파일은 나눠서 올립니다 (서버에 ffmpeg 필요).')),
            rulesBox,
            h('div', { class: 'toolbar' }, h('button', { class: 'btn', on: { click: () => addRule() } }, icon('plus'), ' 규칙 추가'), save, check),
            h('p', { class: 'field-hint', text: '올리려면 그 채널에 글을 쓸 수 있어야 합니다(채널 관리자). Plex 서버가 이 컴퓨터에 있고 원본 파일 경로가 보이면 그대로 올리고, '
                + '아니면 Plex에서 데이터 폴더의 임시 폴더로 받은 뒤 올리고 지웁니다(파일 크기만큼 빈 공간 필요). mp4는 텔레그램 앱에서 바로 재생되고, mkv 등은 파일로 올라갑니다(YMPlayer에서는 재생됨).' })),
        h('div', { class: 'src-card' }, h('h3', { class: 'pane-title' }, icon('heart-pulse'), ' 상태'), statusBox));
    paintStatus();
}

function showMcpToken(r) {
    const name = 'ymplayer';
    const body = h('div', { class: 'whats-new mcp-made' },
        h('p', { class: 'muted error', text: '토큰은 지금 한 번만 보입니다. 잃어버리면 지우고 새로 만드세요.' }),
        h('h4', { text: '토큰' }), copyLine(r.token),
        h('h4', { text: 'Claude Code' }), copyLine(`claude mcp add --transport http ${name} ${r.url} --header "Authorization: Bearer ${r.token}"`),
        h('h4', { text: 'Claude 앱 (설정 > 커넥터 > 사용자 지정 커넥터 추가) · 헤더를 못 넣는 앱' }), copyLine(r.url_with_token),
        h('p', { class: 'field-hint', text: 'Claude 앱(claude.ai)의 커넥터는 Anthropic 서버에서 접속하므로 인터넷에서 열리는 https 주소여야 합니다. '
            + '집 안에서만 열리는 주소면 Claude Code나 Claude 데스크톱처럼 같은 네트워크에서 도는 앱을 쓰세요. 주소에 토큰이 들어 있으니 남에게 보여 주지 마세요.' }),
        h('h4', { text: '다른 MCP 앱 (JSON 설정)' }),
        copyLine(JSON.stringify({ mcpServers: { [name]: { type: 'http', url: r.url, headers: { Authorization: `Bearer ${r.token}` } } } })));
    const m = modal(`MCP 토큰 - ${r.item.name}`, body, [h('button', { class: 'btn btn-accent', on: { click: () => m.close() } }, '닫기')]);
}

function renderBackup(pane) {
    const fileIn = h('input', { type: 'file', accept: '.json,application/json', class: 'hidden' });
    const restoreBtn = h('button', { class: 'btn', on: { click: () => fileIn.click() } }, icon('upload'), ' 백업 파일로 복원');
    fileIn.addEventListener('change', async () => {
        const f = fileIn.files[0];
        fileIn.value = '';
        if (!f) return;
        let data;
        try { data = JSON.parse(await f.text()); } catch (e) { return toast('JSON 파일이 아닙니다.', 'error'); }
        const when = data.created ? new Date(data.created * 1000).toLocaleString() : '?';
        const n = (data.config && data.config.sources || []).length, u = (data.users || []).length;
        if (!confirm(`${when}에 만든 백업(소스 ${n}개, 사용자 ${u}명)으로 지금 설정과 사용자를 모두 바꿀까요?`)) return;
        restoreBtn.disabled = true;
        try {
            const res = await api('/api/admin/restore', { method: 'POST', json: data });
            toast(`복원했습니다 (소스 ${res.sources}개, 사용자 ${res.users}명). 다시 불러옵니다...`, 'success');
            setTimeout(() => { location.href = res.still_logged_in ? '/' : '/login'; }, 1200);
        } catch (e) {
            toast(`복원 실패: ${e.message}`, 'error', 7000);
        } finally {
            restoreBtn.disabled = false;
        }
    });
    pane.append(h('div', { class: 'src-card backup-card' },
        h('h3', { class: 'pane-title' }, icon('download'), ' 백업 내려받기'),
        h('p', { text: '소스 설정(Plex 토큰, 텔레그램 API Hash 포함), 사용자 계정(비밀번호는 해시), 사용자별 즐겨찾기를 JSON 파일 하나로 받습니다.' }),
        h('p', { class: 'muted error', text: '토큰이 들어 있으니 안전한 곳에 보관하세요. 텔레그램 로그인 세션과 영상 색인/이어보기는 들어가지 않습니다 (다른 서버로 옮기면 텔레그램은 다시 로그인).' }),
        h('a', { class: 'btn btn-accent', href: '/api/admin/backup', download: '' }, icon('download'), ' 백업 파일 받기')),
    h('div', { class: 'src-card backup-card' },
        h('h3', { class: 'pane-title' }, icon('upload'), ' 복원'),
        h('p', { text: '백업 파일의 내용으로 지금 설정과 사용자 목록을 통째로 바꿉니다. 내 계정이 백업에 없거나 관리자가 아니면 로그인 화면으로 갑니다.' }),
        restoreBtn, fileIn));
}
