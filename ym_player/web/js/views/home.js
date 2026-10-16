// 홈: 맨 위에 모든 소스를 합친 이어보기, 그 아래 소스마다 허브(최근 추가, 지금 방송 중...)를 가로 줄로 쌓는다.
import { h, icon, clear } from '../util.js';
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

    // 소스마다 자리를 먼저 잡아두고, 응답이 오는 대로 채운다 (느린 소스가 다른 소스를 막지 않게)
    const slots = providers.map((p) => {
        const slot = h('div', { class: 'home-slot' }, loading(`${p.name} 불러오는 중...`));
        page.appendChild(slot);
        return { p, slot };
    });

    await Promise.all(slots.map(async ({ p, slot }) => {
        try {
            const hubs = await p.getHomeHubs();
            if (!ctx.alive()) return;
            clear(slot);
            if (!hubs.length) {
                slot.appendChild(h('div', { class: 'home-note', text: `${p.name}: 보여줄 항목이 없습니다.` }));
            }
            hubs.filter((hub) => hub.continue).forEach((hub) => addContinue(hub.items));
            const rest = hubs.filter((hub) => !hub.continue);
            if (!rest.length && hubs.length) slot.remove();
            rest.forEach((hub) => slot.appendChild(hubRow(hub)));
        } catch (e) {
            if (!ctx.alive()) return;
            clear(slot).appendChild(h('div', { class: 'home-note error' },
                icon('triangle-exclamation'), ` ${p.name}: ${e.message}`));
        }
    }));
}
