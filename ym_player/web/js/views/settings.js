// 설정: 소스(Plex 서버, IPTV 재생목록) 추가/수정/삭제. 저장하면 서버의 data/config.json에 들어간다.
// Plex는 토큰을 직접 넣거나, plex.tv PIN 로그인으로 서버 목록을 받아 고를 수 있다.
import { h, icon, clear, api, toast, modal, storeGet, storeSet } from '../util.js';
import { registry } from '../providers/index.js';
import { PlexProvider } from '../providers/plex.js';

function field(label, input, hint) {
    return h('label', { class: 'field' }, h('span', { class: 'field-label', text: label }), input,
        hint ? h('span', { class: 'field-hint', text: hint }) : null);
}

export async function renderSettings(main, _params, _query, ctx) {
    let drafts = registry.config.sources.map((s) => ({ ...s, token: '' }));
    let dirty = false;

    const list = h('div', { class: 'src-list' });
    const saveBtn = h('button', { class: 'btn btn-accent', on: { click: save } }, icon('check'), ' 저장');
    const panes = {
        sources: h('div', { class: 'settings-pane' },
            h('div', { class: 'toolbar pane-toolbar' },
                h('button', { class: 'btn', on: { click: plexLogin } }, icon('right-to-bracket'), ' Plex 계정으로 추가'),
                h('button', { class: 'btn', on: { click: () => add({ type: 'plex', name: 'Plex', url: '', enabled: true, verify_ssl: true }) } },
                    icon('server'), ' Plex 직접 입력'),
                h('button', { class: 'btn', on: { click: () => add({ type: 'iptv', name: 'IPTV', m3u: '', epg: '', enabled: true }) } },
                    icon('tower-broadcast'), ' IPTV 추가'),
                h('button', {
                    class: 'btn',
                    on: { click: () => add({ type: 'telegram', name: 'Telegram', api_id: '', api_hash: '', phone: '', channels: [], enabled: true }) },
                }, icon('paper-plane'), ' 텔레그램 추가'),
                saveBtn),
            h('p', { class: 'muted settings-note' },
                'Plex 토큰은 이 서버(data/config.json)에만 저장되고 브라우저로는 내려오지 않습니다. ',
                '토큰 칸을 비워두고 저장하면 기존 토큰을 그대로 씁니다. (텔레그램 API Hash도 같습니다)'),
            list),
        users: h('div', { class: 'settings-pane' }, usersBox()),
        status: h('div', { class: 'settings-pane' }),
        backup: h('div', { class: 'settings-pane' }),
    };
    const TABS = [['sources', 'layer-group', '소스'], ['users', 'users', '사용자'], ['status', 'heart-pulse', '상태'], ['backup', 'box-archive', '백업']];
    const tabBar = h('div', { class: 'tabs', role: 'tablist' });
    const showTab = (key) => {
        if (!panes[key]) key = 'sources';
        storeSet('yp_settings_tab', key);
        tabBar.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === key));
        Object.entries(panes).forEach(([k, el]) => el.classList.toggle('hidden', k !== key));
        if (key === 'status') renderStatus(panes.status);
        if (key === 'backup' && !panes.backup.firstChild) renderBackup(panes.backup);
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
            listBox.appendChild(h('div', { class: 'acc-src' },
                h('label', { class: 'check' }, chk, icon(src.type === 'plex' ? 'server' : src.type === 'telegram' ? 'paper-plane' : 'tower-broadcast'), ` ${src.name}`),
                src.type === 'plex' ? sub : null));
            if (src.type !== 'plex') return;
            sub.appendChild(h('span', { class: 'muted' }, icon('circle-notch', 'fa-spin'), ' 라이브러리 불러오는 중...'));
            new PlexProvider(src, registry.config.client_id).getLibraries({ all: true }).then((items) => {
                clear(sub);
                const everyIn = h('input', { type: 'checkbox', checked: !libs[src.id] });
                const box = h('div', { class: 'acc-lib-items' });
                const paint = () => box.classList.toggle('hidden', everyIn.checked);
                everyIn.addEventListener('change', () => {
                    if (everyIn.checked) delete libs[src.id]; else libs[src.id] = new Set();
                    box.querySelectorAll('input').forEach((x) => { x.checked = false; });
                    paint();
                });
                items.forEach((lib) => {
                    const c = h('input', { type: 'checkbox', checked: !!(libs[src.id] && libs[src.id].has(lib.id)) });
                    c.addEventListener('change', () => {
                        libs[src.id] = libs[src.id] || new Set();
                        if (c.checked) libs[src.id].add(lib.id); else libs[src.id].delete(lib.id);
                    });
                    box.appendChild(h('label', { class: 'check lib-check' }, c, icon(lib.icon), ` ${lib.title}`));
                });
                sub.append(h('label', { class: 'check' }, everyIn, ' 모든 라이브러리 (새로 생기는 것 포함)'), box);
                paint();
            }).catch((e) => { clear(sub).appendChild(h('span', { class: 'muted error', text: `라이브러리 목록 실패: ${e.message}` })); });
        });
        sync();

        const saveAcc = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
        const m = modal(`${u.username} - 볼 수 있는 범위`, h('div', { class: 'acc-dialog' },
            h('label', { class: 'check' }, allIn, ' 전부 (나중에 추가하는 소스도)'),
            h('label', { class: 'check' }, someIn, ' 고른 것만'),
            listBox,
            h('p', { class: 'field-hint', text: '고르지 않은 소스와 라이브러리는 이 사용자의 사이드바·홈·검색에 안 나오고, 서버도 열어 주지 않습니다.' })),
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
        draw();
        list.lastElementChild && list.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
                    const chk = h('input', { type: 'checkbox', checked: chosen.has(String(c.id)) });
                    chk.addEventListener('change', () => {
                        if (chk.checked) chosen.set(String(c.id), { id: c.id, title: c.title }); else chosen.delete(String(c.id));
                        src.channels = [...chosen.values()];
                        touch();
                    });
                    list.appendChild(h('label', { class: 'check lib-check', title: c.username ? `@${c.username}` : '' },
                        chk, icon(c.kind === 'group' ? 'users' : 'bullhorn'), ` ${c.title}`));
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
        if (!drafts.length) {
            list.appendChild(h('div', { class: 'empty small' }, h('p', { text: '위 버튼으로 Plex 서버나 IPTV 재생목록을 추가하세요.' })));
        }
        drafts.forEach((src, i) => {
            const isPlex = src.type === 'plex';
            const isTg = src.type === 'telegram';
            const fields = isTg ? [
                h('div', { class: 'row-2' },
                    field('API ID', bindInput(src, 'api_id', h('input', { class: 'input', value: src.api_id || '', placeholder: '숫자', inputmode: 'numeric' }))),
                    field('API Hash', bindInput(src, 'api_hash', h('input', {
                        class: 'input', type: 'password', value: src.api_hash || '', autocomplete: 'off',
                        placeholder: src.has_api_hash ? '저장된 값 사용 중 (바꿀 때만 입력)' : '32자리',
                    })))),
                h('span', { class: 'field-hint' }, 'my.telegram.org → API development tools에서 발급합니다. 바꾼 뒤에는 먼저 [저장]하세요.'),
                field('전화번호', bindInput(src, 'phone', h('input', { class: 'input', value: src.phone || '', placeholder: '+821012345678 (국가번호 포함)' }))),
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
                field('M3U 주소', bindInput(src, 'm3u', h('input', { class: 'input', value: src.m3u || '', placeholder: 'https://iptv-org.github.io/iptv/countries/kr.m3u' }))),
                field('EPG(XMLTV) 주소', bindInput(src, 'epg', h('input', { class: 'input', value: src.epg || '', placeholder: '선택 - .xml 또는 .xml.gz' })),
                    '편성표 채널 매칭: tvg-id → 채널 표시명 순서로 찾습니다.'),
            ];
            const move = (d) => { const j = i + d; if (j < 0 || j >= drafts.length) return; [drafts[i], drafts[j]] = [drafts[j], drafts[i]]; touch(); draw(); };
            list.appendChild(h('div', { class: `src-card${src.enabled ? '' : ' disabled'}` },
                h('div', { class: 'src-head' },
                    h('span', { class: `src-type ${src.type}` },
                        icon(isTg ? 'paper-plane' : isPlex ? 'server' : 'tower-broadcast'), isTg ? ' 텔레그램' : isPlex ? ' Plex' : ' IPTV'),
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
            const cfg = await api('/api/config', { method: 'PUT', json: { sources: drafts } });
            registry.config = cfg;
            await ctx.reloadProviders();
            drafts = cfg.sources.map((s) => ({ ...s, token: '' }));
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

    draw();
    showTab(storeGet('yp_settings_tab', 'sources'));
}

const TYPE_ICON = { plex: 'server', iptv: 'tower-broadcast', telegram: 'paper-plane' };

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
        ['가동 시간', fmtUptime(sv.uptime)],
        ['Python', sv.python],
        ['사용자', `${sv.users}명 (관리자 ${sv.admins})`],
        ['텔레그램', sv.telethon ? (sv.cryptg ? 'Telethon + cryptg (빠름)' : 'Telethon (cryptg 없음 - 느림)') : 'Telethon 없음'],
        ['서버 변환 (ffmpeg)', sv.ffmpeg ? `있음 · 지금 ${sv.transcoding}개 변환 중` : '없음 - 텔레그램의 mkv·HEVC·AC-3 영상이 안 나올 수 있음 (sudo apt install ffmpeg)'],
        ['데이터 폴더', sv.data_dir],
    ];
    pane.lastChild.replaceWith(h('div', {},
        h('h3', { class: 'pane-title', text: '소스' }),
        h('div', { class: 'src-card status-list' }, rows.length ? rows : h('p', { class: 'muted', text: '소스가 없습니다.' })),
        h('h3', { class: 'pane-title', text: '서버' }),
        h('div', { class: 'src-card facts' }, facts.map(([k, v]) => h('div', { class: 'fact' }, h('span', { text: k }), h('b', { text: v })))),
        h('h3', { class: 'pane-title', text: `최근 경고 (${st.warnings.length})` }),
        h('div', { class: 'src-card warn-list' }, st.warnings.length
            ? st.warnings.map((w) => h('div', { class: `warn-row ${w.level.toLowerCase()}` },
                h('span', { class: 'warn-time', text: new Date(w.time * 1000).toLocaleString() }),
                h('span', { class: 'warn-msg', text: w.message, title: w.logger })))
            : h('p', { class: 'muted', text: '서버를 켠 뒤로 경고가 없습니다.' }))));
}

/** 백업 내려받기 / 백업 파일로 되돌리기 */
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
