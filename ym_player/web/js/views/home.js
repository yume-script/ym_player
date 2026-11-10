// 홈: 맨 위에 모든 소스를 합친 이어보기, 그 아래 소스마다 허브(최근 추가, 지금 방송 중...)를 가로 줄로 쌓는다.
import { h, icon, clear, api, modal, toast } from '../util.js';
import { registry, randomAcross } from '../providers/index.js';
import { player } from '../player.js';
import { hubRow, emptyState, loading } from '../cards.js';

export async function renderHome(main, _params, _query, ctx) {
    const providers = registry.providers;
    if (!providers.length) {
        main.appendChild(emptyState('photo-film', '아직 연결된 소스가 없습니다',
            'Plex 서버나 IPTV(M3U) 재생목록을 추가하면 여기에 보입니다.',
            h('a', { class: 'btn btn-accent', href: '#/settings' }, icon('plus'), ' 소스 추가')));
        return;
    }

    const page = h('div', { class: 'page home' });
    main.appendChild(page);

    // 고르기 귀찮을 때: Plex·텔레그램 전체에서 안 본 것 위주로 아무거나 계속 재생
    const randomSources = registry.randomProviders();
    if (randomSources.length) {
        page.appendChild(h('div', { class: 'home-actions' },
            h('button', {
                class: 'btn', title: `${randomSources.map((p) => p.name).join(', ')}에서 아무거나 골라 계속 재생`,
                on: { click: () => player.playRandom(randomAcross(randomSources)) },
            }, icon('shuffle'), ' 랜덤 재생')));
    }

    // 맨 위: 모든 소스의 이어보기를 최근에 본 순서로 한 줄에 (각 소스의 이어보기 줄은 따로 안 그린다)
    const contSlot = h('div', { class: 'home-slot' });
    page.appendChild(contSlot);
    const contItems = [];
    const addContinue = (items) => {
        const seen = new Set(contItems.map((it) => `${it.pid}/${it.id}`));
        items.forEach((it) => { if (!seen.has(`${it.pid}/${it.id}`)) { seen.add(`${it.pid}/${it.id}`); contItems.push(it); } });
        contItems.sort((a, b) => (b.ts || 0) - (a.ts || 0));
        clear(contSlot).appendChild(hubRow({ title: '이어보기', items: contItems.slice(0, 40), wide: true }));
    };

    // 소스마다 자리를 먼저 잡아두고 채운다 (느린 소스가 다른 소스를 막지 않게).
    // - 위쪽 몇 줄만 바로 부르고, 나머지는 화면에 가까워지면 (홈을 열 때마다 모든 소스에 요청하지 않게)
    // - 이번 방문에서 한 번 받은 줄은 기억해 두었다가 홈에 돌아오면 바로 그리고, 오래됐으면 뒤에서 새로
    const shown = providers.filter((p) => !hiddenSet().has(p.id));
    const slots = shown.map((p) => {
        const slot = h('div', { class: 'home-slot', dataset: { pid: p.id } }, loading(`${p.name} 불러오는 중...`));
        page.appendChild(slot);
        return { p, slot };
    });
    const hiddenCount = providers.length - shown.length;
    page.appendChild(h('div', { class: 'home-foot' },
        h('button', { class: 'btn btn-sm', on: { click: () => homeSourcesDialog(() => window.dispatchEvent(new HashChangeEvent('hashchange'))) } },
            icon('eye'), hiddenCount ? ` 홈에 보일 소스 (${hiddenCount}개 숨김)` : ' 홈에 보일 소스')));

    const draw = ({ p, slot }, hubs) => {
        clear(slot);
        if (!hubs.length) slot.appendChild(h('div', { class: 'home-note', text: `${p.name}: 보여줄 항목이 없습니다.` }));
        hubs.filter((hub) => hub.continue).forEach((hub) => addContinue(hub.items));
        const rest = hubs.filter((hub) => !hub.continue);
        if (!rest.length && hubs.length) slot.classList.add('home-slot-empty');
        rest.forEach((hub) => slot.appendChild(hubRow(hub)));
    };
    const fill = async (s) => {
        const hit = HOME_CACHE.get(s.p.id);
        if (hit) {
            draw(s, hit.hubs);
            if (Date.now() - hit.at < HOME_FRESH_MS) return;
        }
        try {
            const hubs = await s.p.getHomeHubs();
            HOME_CACHE.set(s.p.id, { hubs, at: Date.now() });
            if (!ctx.alive()) return;
            draw(s, hubs);
        } catch (e) {
            if (!ctx.alive() || hit) return;   // (기억해 둔 줄이 있으면 그대로 둔다)
            clear(s.slot).appendChild(h('div', { class: 'home-note error' },
                icon('triangle-exclamation'), ` ${s.p.name}: ${e.message}`));
        }
    };
    const later = [];
    slots.forEach((s, i) => { if (i < HOME_EAGER || HOME_CACHE.has(s.p.id)) fill(s); else later.push(s); });
    if (!later.length) return;
    const io = new IntersectionObserver((entries) => {
        if (!ctx.alive()) return io.disconnect();
        for (const e of entries) {
            if (!e.isIntersecting) continue;
            io.unobserve(e.target);
            const s = later.find((x) => x.slot === e.target);
            if (s) fill(s);
        }
    }, { rootMargin: '600px' });
    later.forEach((s) => io.observe(s.slot));
    ctx.onLeave(() => io.disconnect());
}

const HOME_EAGER = 3;               // 처음에 바로 부를 소스 줄 수 (나머지는 스크롤해서 가까워지면)
const HOME_FRESH_MS = 15 * 1000;    // 기억해 둔 줄을 다시 받지 않고 쓰는 시간 (지나면 바로 그린 뒤 뒤에서 새로 - 이어보기가 늦지 않게)
const HOME_CACHE = new Map();       // 소스 id -> { hubs, at } (이 탭에서 한 번 받은 홈 줄)

function hiddenSet() { return new Set((registry.prefs && registry.prefs.home_hidden) || []); }

/** 홈에 보일 소스 고르기 (사람마다 서버에 저장 - 다른 기기에서도 같게). 숨겨도 사이드바·검색에서는 그대로 */
function homeSourcesDialog(onSaved) {
    const hidden = hiddenSet();
    const checks = registry.providers.map((p) => {
        const c = h('input', { type: 'checkbox', checked: !hidden.has(p.id) });
        return { p, c, row: h('label', { class: 'check home-src-row' }, c, ` ${p.name}`) };
    });
    const save = h('button', { class: 'btn btn-accent' }, icon('check'), ' 저장');
    const m = modal('홈에 보일 소스', h('div', { class: 'home-src-list' },
        h('p', { class: 'field-hint', text: '끈 소스는 홈에 줄이 안 생기고(요청도 안 함) 사이드바·검색에서는 그대로 쓸 수 있습니다. 홈이 가벼워집니다.' }),
        ...checks.map((x) => x.row)), [save]);
    save.addEventListener('click', async () => {
        const home_hidden = checks.filter((x) => !x.c.checked).map((x) => x.p.id);
        try {
            await api('/api/me/prefs', { method: 'PUT', json: { home_hidden } });
            registry.prefs = { ...(registry.prefs || {}), home_hidden };
            m.close();
            onSaved();
        } catch (e) { toast(`저장하지 못했습니다: ${e.message}`, 'error'); }
    });
}
