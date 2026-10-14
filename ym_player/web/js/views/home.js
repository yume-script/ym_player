// 홈: 소스마다 허브(이어보기, 최근 추가, 지금 방송 중...)를 가로 줄로 쌓는다.
import { h, icon, clear } from '../util.js';
import { registry } from '../providers/index.js';
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
            hubs.forEach((hub) => slot.appendChild(hubRow(hub)));
        } catch (e) {
            if (!ctx.alive()) return;
            clear(slot).appendChild(h('div', { class: 'home-note error' },
                icon('triangle-exclamation'), ` ${p.name}: ${e.message}`));
        }
    }));
}
