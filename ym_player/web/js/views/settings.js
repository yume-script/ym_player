// 설정: 소스(Plex 서버, IPTV 재생목록) 추가/수정/삭제. 저장하면 서버의 data/config.json에 들어간다.
// Plex는 토큰을 직접 넣거나, plex.tv PIN 로그인으로 서버 목록을 받아 고를 수 있다.
import { h, icon, clear, api, toast, modal } from '../util.js';
import { registry } from '../providers/index.js';

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
                saveBtn)),
        h('p', { class: 'muted settings-note' },
            'Plex 토큰은 이 서버(data/config.json)에만 저장되고 브라우저로는 내려오지 않습니다. ',
            '토큰 칸을 비워두고 저장하면 기존 토큰을 그대로 씁니다.'),
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

    function draw() {
        clear(list);
        if (!drafts.length) {
            list.appendChild(h('div', { class: 'empty small' }, h('p', { text: '위 버튼으로 Plex 서버나 IPTV 재생목록을 추가하세요.' })));
        }
        drafts.forEach((src, i) => {
            const isPlex = src.type === 'plex';
            const fields = isPlex ? [
                field('서버 주소', bindInput(src, 'url', h('input', { class: 'input', value: src.url || '', placeholder: 'http://192.168.0.10:32400' }))),
                field('토큰 (X-Plex-Token)', bindInput(src, 'token', h('input', {
                    class: 'input', type: 'password', value: src.token || '', autocomplete: 'off',
                    placeholder: src.has_token ? '저장된 토큰 사용 중 (바꿀 때만 입력)' : 'Plex 토큰',
                }))),
                h('label', { class: 'check' }, bindInput(src, 'verify_ssl', h('input', { type: 'checkbox', checked: src.verify_ssl !== false }), 'checked'),
                    ' HTTPS 인증서 확인 (자체 서명 인증서면 끄기)'),
            ] : [
                field('M3U 주소', bindInput(src, 'm3u', h('input', { class: 'input', value: src.m3u || '', placeholder: 'https://iptv-org.github.io/iptv/countries/kr.m3u' }))),
                field('EPG(XMLTV) 주소', bindInput(src, 'epg', h('input', { class: 'input', value: src.epg || '', placeholder: '선택 - .xml 또는 .xml.gz' })),
                    '편성표 채널 매칭: tvg-id → 채널 표시명 순서로 찾습니다.'),
            ];
            const move = (d) => { const j = i + d; if (j < 0 || j >= drafts.length) return; [drafts[i], drafts[j]] = [drafts[j], drafts[i]]; touch(); draw(); };
            list.appendChild(h('div', { class: `src-card${src.enabled ? '' : ' disabled'}` },
                h('div', { class: 'src-head' },
                    h('span', { class: `src-type ${src.type}` }, icon(isPlex ? 'server' : 'tower-broadcast'), isPlex ? ' Plex' : ' IPTV'),
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
