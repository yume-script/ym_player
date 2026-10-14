// 설정: 소스(Plex 서버, IPTV 재생목록) 추가/수정/삭제. 저장하면 서버의 data/config.json에 들어간다.
// Plex는 토큰을 직접 넣거나, plex.tv PIN 로그인으로 서버 목록을 받아 고를 수 있다.
import { h, icon, clear, api, toast, modal } from '../util.js';
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
    const page = h('div', { class: 'page settings' },
        h('div', { class: 'page-head' },
            h('h1', {}, icon('gear'), ' 설정'),
            h('div', { class: 'toolbar' },
                h('button', { class: 'btn', on: { click: plexLogin } }, icon('right-to-bracket'), ' Plex 계정으로 추가'),
                h('button', { class: 'btn', on: { click: () => add({ type: 'plex', name: 'Plex', url: '', enabled: true, verify_ssl: true }) } },
                    icon('server'), ' Plex 직접 입력'),
                h('button', { class: 'btn', on: { click: () => add({ type: 'iptv', name: 'IPTV', m3u: '', epg: '', enabled: true }) } },
                    icon('tower-broadcast'), ' IPTV 추가'),
                h('button', {
                    class: 'btn',
                    on: { click: () => add({ type: 'telegram', name: 'Telegram', api_id: '', api_hash: '', phone: '', channels: [], enabled: true }) },
                }, icon('paper-plane'), ' 텔레그램 추가'),
                saveBtn)),
        h('p', { class: 'muted settings-note' },
            'Plex 토큰은 이 서버(data/config.json)에만 저장되고 브라우저로는 내려오지 않습니다. ',
            '토큰 칸을 비워두고 저장하면 기존 토큰을 그대로 씁니다. (텔레그램 API Hash도 같습니다)'),
        list);
    main.appendChild(page);

    window.onbeforeunload = () => (dirty ? true : undefined);
    ctx.onLeave(() => { window.onbeforeunload = null; });

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
}
