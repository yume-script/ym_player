// 라이브 TV 편성표 (Plex Live TV 가이드처럼): 왼쪽 채널 열 + 가로 시간축 위에 방송 칸.
// 채널이 수천 개인 공개 재생목록도 있어서 줄은 80개씩 스크롤할 때 이어 붙인다.
import { h, icon, clear, fmtHM, normalizeName, debounce, toast, storeGet, storeSet } from '../util.js';
import { registry } from '../providers/index.js';
import { player } from '../player.js';
import { channelHealth, isFavorite, toggleFavorite } from '../providers/iptv.js';
import { emptyState, loading } from '../cards.js';

const PX_PER_MIN = 6;
const SLOT_MIN = 30;
const HOURS = 8;
const BATCH = 80;

export async function renderLive(main, [onlyPid], _query, ctx) {
    const providers = registry.ofKind('iptv').filter((p) => !onlyPid || p.id === onlyPid);
    if (!providers.length) {
        main.appendChild(emptyState('tower-broadcast', 'IPTV 소스가 없습니다', 'M3U 재생목록을 추가하면 편성표가 보입니다.',
            h('a', { class: 'btn btn-accent', href: '#/settings' }, icon('plus'), ' 소스 추가')));
        return;
    }
    main.appendChild(loading('채널 목록을 불러오는 중...'));

    // 1) 채널 목록
    const all = [];
    const results = await Promise.allSettled(providers.map((p) => p.loadChannels()));
    if (!ctx.alive()) return;
    results.forEach((r, i) => {
        if (r.status === 'fulfilled') r.value.forEach((channel) => all.push({ provider: providers[i], channel }));
        else toast(`${providers[i].name}: ${r.reason.message}`, 'error', 6000);
    });
    clear(main);
    if (!all.length) {
        main.appendChild(emptyState('tower-broadcast', '채널이 없습니다', 'M3U 주소가 맞는지 설정에서 확인해 주세요.'));
        return;
    }

    // 2) 시간축: 지금이 들어있는 30분 칸 하나 전부터 HOURS시간
    const now = Date.now() / 1000;
    const t0 = Math.floor(now / (SLOT_MIN * 60)) * SLOT_MIN * 60 - SLOT_MIN * 60;
    const t1 = t0 + HOURS * 3600;
    const width = ((t1 - t0) / 60) * PX_PER_MIN;
    // 채널 열 너비는 CSS(--guide-ch-w, 좁은 화면에선 더 좁음)와 맞춘다
    const chW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--guide-ch-w'), 10) || 220;
    const x = (t) => ((Math.min(Math.max(t, t0), t1) - t0) / 60) * PX_PER_MIN;

    // 3) 필터
    const pref = storeGet('yp_live_filter', { group: '', favOnly: false, hideOffline: false });
    const groups = [...new Set(all.map((c) => c.channel.group).filter(Boolean))].sort();
    const groupSel = h('select', { class: 'select' },
        h('option', { value: '', text: `전체 그룹 (${groups.length})` }),
        groups.map((g) => h('option', { value: g, text: g, selected: g === pref.group })));
    const searchIn = h('input', { class: 'input', type: 'search', placeholder: '채널 / 프로그램 찾기' });
    const favChk = h('input', { type: 'checkbox', checked: !!pref.favOnly });
    const offChk = h('input', { type: 'checkbox', checked: !!pref.hideOffline });
    const countEl = h('span', { class: 'muted' });
    const epgEl = h('span', { class: 'muted' }, icon('circle-notch', 'fa-spin'), ' 편성표 불러오는 중');

    const ticks = [];
    for (let t = t0; t < t1; t += SLOT_MIN * 60) {
        ticks.push(h('div', { class: 'guide-tick', style: { left: `${x(t)}px`, width: `${SLOT_MIN * PX_PER_MIN}px` }, text: fmtHM(t) }));
    }
    const nowLine = h('div', { class: 'guide-now' });
    const rowsEl = h('div', { class: 'guide-rows' });
    const sentinel = h('div', { class: 'sentinel' });
    const scroller = h('div', { class: 'guide-scroll' },
        h('div', { class: 'guide-inner', style: { width: `${width + chW}px` } },
            h('div', { class: 'guide-header' },
                h('div', { class: 'guide-corner', text: '채널' }),
                h('div', { class: 'guide-ticks', style: { width: `${width}px` } }, ticks)),
            h('div', { class: 'guide-body' }, rowsEl, nowLine),
            sentinel));

    main.appendChild(h('div', { class: 'page live' },
        h('div', { class: 'page-head' },
            h('h1', {}, icon('tower-broadcast'), onlyPid ? ` ${providers[0].name}` : ' 라이브 TV'),
            h('div', { class: 'toolbar' },
                searchIn, groupSel,
                h('label', { class: 'check' }, favChk, ' ⭐ 즐겨찾기만'),
                h('label', { class: 'check' }, offChk, ' 안 나온 채널 숨기기'),
                h('button', {
                    class: 'btn', title: '재생목록/편성표 다시 받기',
                    on: { click: () => { providers.forEach((p) => p.loadChannels(true)); ctx.rerender(); } },
                }, icon('rotate-right')),
                countEl, epgEl)),
        scroller));

    // 4) 줄 그리기
    let filtered = [];
    let drawn = 0;

    const filter = () => {
        const q = normalizeName(searchIn.value);
        filtered = all.filter(({ provider, channel }) => {
            if (groupSel.value && channel.group !== groupSel.value) return false;
            if (favChk.checked && !isFavorite(channel)) return false;
            if (offChk.checked && channelHealth.get(channel.id) === 'offline') return false;
            if (!q) return true;
            if (normalizeName(channel.name).includes(q)) return true;
            const cur = provider.nowNext(channel).cur;
            return cur && normalizeName(cur[2]).includes(q);
        });
        // 즐겨찾기를 맨 위로 (나머지는 재생목록 순서 그대로)
        filtered.sort((a, b) => Number(isFavorite(b.channel)) - Number(isFavorite(a.channel)));
        countEl.textContent = `채널 ${filtered.length.toLocaleString()}개`;
        storeSet('yp_live_filter', { group: groupSel.value, favOnly: favChk.checked, hideOffline: offChk.checked });
        clear(rowsEl);
        drawn = 0;
        drawMore();
    };

    const progsFor = (provider, channel) => {
        const box = h('div', { class: 'guide-progs', style: { width: `${width}px` } });
        const list = provider.programmesFor(channel).filter((pg) => pg[0] < t1 && (pg[1] || pg[0] + 3600) > t0);
        if (!list.length) {
            box.appendChild(h('div', { class: 'guide-prog empty', style: { left: '0', width: `${width}px` }, text: channel.name }));
            return box;
        }
        for (const pg of list) {
            const [start, stopRaw, title, desc, cat] = pg;
            const stop = stopRaw || start + 3600;
            const left = x(start), w = Math.max(2, x(stop) - left);
            const isNow = start <= now && stop > now;
            const past = stop <= now;
            box.appendChild(h('div', {
                class: `guide-prog${isNow ? ' now' : ''}${past ? ' past' : ''}`,
                style: { left: `${left}px`, width: `${w}px` },
                title: `${fmtHM(start)} ~ ${fmtHM(stop)}  ${title}${cat ? ` [${cat}]` : ''}${desc ? `\n\n${desc}` : ''}`,
            }, h('b', { text: title }), h('span', { text: `${fmtHM(start)} ~ ${fmtHM(stop)}` })));
        }
        return box;
    };

    const rowFor = ({ provider, channel }) => {
        const fav = isFavorite(channel);
        const star = h('button', { class: `star${fav ? ' on' : ''}`, title: '즐겨찾기' }, icon('star'));
        star.addEventListener('click', (e) => {
            e.stopPropagation();
            const on = toggleFavorite(channel);
            star.classList.toggle('on', on);
        });
        const logo = channel.logo
            ? h('img', { src: channel.logo, alt: '', loading: 'lazy', on: { error: (e) => { e.target.replaceWith(icon('tv')); } } })
            : icon('tv');
        const play = () => player.playChannel(provider, channel, filtered);
        const offline = channelHealth.get(channel.id) === 'offline';
        return h('div', { class: `guide-row${offline ? ' offline' : ''}`, on: { click: play } },
            h('div', { class: 'guide-ch', title: `${channel.name}${channel.group ? ` · ${channel.group}` : ''}` },
                h('div', { class: 'ch-logo' }, logo),
                h('div', { class: 'guide-ch-name' },
                    channel.chno ? h('span', { class: 'chno', text: channel.chno }) : null,
                    h('b', { text: channel.name }),
                    providers.length > 1 ? h('span', { class: 'muted', text: provider.name }) : null),
                star),
            progsFor(provider, channel));
    };

    const drawMore = () => {
        const frag = document.createDocumentFragment();
        const end = Math.min(filtered.length, drawn + BATCH);
        for (let i = drawn; i < end; i++) frag.appendChild(rowFor(filtered[i]));
        rowsEl.appendChild(frag);
        drawn = end;
    };

    const placeNow = () => { nowLine.style.left = `${chW + x(Date.now() / 1000)}px`; };
    placeNow();
    const timer = setInterval(placeNow, 30000);

    const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting) && drawn < filtered.length) drawMore();
    }, { root: scroller, rootMargin: '800px' });
    io.observe(sentinel);
    ctx.onLeave(() => { io.disconnect(); clearInterval(timer); });

    searchIn.addEventListener('input', debounce(filter, 200));
    [groupSel, favChk, offChk].forEach((el) => el.addEventListener('change', filter));
    filter();

    // 5) 편성표는 뒤에서 받아서 오면 다시 그린다
    const epgs = await Promise.all(providers.map((p) => p.loadEpg()));
    if (!ctx.alive()) return;
    const okCount = epgs.filter(Boolean).length;
    const wanted = providers.filter((p) => p.hasEpg).length;
    clear(epgEl).append(wanted ? `편성표 ${okCount}/${wanted}개 적용` : '편성표 없음');
    if (okCount) filter();
}
