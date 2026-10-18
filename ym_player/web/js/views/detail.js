// 상세 화면: 흐린 배경 그림 + 포스터 + 제목/정보 + [재생/이어보기] + 시즌/에피소드/출연진
import { h, icon, clear, fmtClock, fmtDuration, toast } from '../util.js';
import { registry } from '../providers/index.js';
import { player } from '../player.js';
import { hubRow, progressBar, emptyState, loading } from '../cards.js';

function episodeRow(p, it) {
    const m = it.raw || {};
    const art = h('div', { class: 'ep-thumb' });
    if (it.wideThumb) art.appendChild(h('img', { src: it.wideThumb, alt: '', loading: 'lazy' }));
    const bar = progressBar(it.progress);
    if (bar) art.appendChild(bar);
    art.appendChild(h('button', {
        class: 'card-play', title: '재생',
        on: { click: (e) => { e.preventDefault(); e.stopPropagation(); player.playItem(p, it.id); } },
    }, icon('play')));
    return h('a', { class: `ep-row${it.watched ? ' watched' : ''}`, href: it.route },
        art,
        h('div', { class: 'ep-info' },
            h('div', { class: 'ep-title' },
                h('span', { class: 'ep-index', text: m.index != null ? `${m.index}.` : '' }),
                ` ${m.title || it.title}`,
                it.watched ? h('span', { class: 'ep-check', title: '시청함' }, icon('check')) : null),
            h('div', { class: 'ep-meta muted', text: [m.originallyAvailableAt, fmtDuration(m.duration)].filter(Boolean).join(' · ') }),
            m.summary ? h('p', { class: 'ep-summary', text: m.summary }) : null));
}

function trackRow(p, it, i) {
    const m = it.raw || {};
    return h('div', { class: 'track-row', on: { click: () => player.playItem(p, it.id) } },
        h('span', { class: 'track-no', text: String(m.index || i + 1) }),
        h('span', { class: 'track-title', text: m.title }),
        h('span', { class: 'muted', text: fmtClock((m.duration || 0) / 1000) }));
}

export async function renderDetail(main, [pid, id], _query, ctx) {
    const p = registry.byId(pid);
    if (!p || !p.getDetail) return main.appendChild(emptyState('circle-question', '항목을 찾을 수 없습니다'));
    main.appendChild(loading());

    let d;
    try {
        d = await p.getDetail(id);
    } catch (e) {
        if (ctx.alive()) clear(main).appendChild(emptyState('triangle-exclamation', '불러오지 못했습니다', e.message));
        return;
    }
    if (!ctx.alive()) return;
    clear(main);

    const actions = h('div', { class: 'detail-actions' });
    if (d.playable) {
        const resume = d.viewOffset > 30000 && d.viewOffset < d.duration * 0.95;
        actions.appendChild(h('button', { class: 'btn btn-accent btn-lg', on: { click: () => player.playItem(p, d.id) } },
            icon('play'), resume ? ` 이어보기 (${fmtClock(d.viewOffset / 1000)})` : ' 재생'));
        if (resume) {
            actions.appendChild(h('button', { class: 'btn', on: { click: () => player.playItem(p, d.id, { fromStart: true }) } },
                icon('backward-fast'), ' 처음부터'));
        }
    } else if (d.onDeck) {
        actions.appendChild(h('button', { class: 'btn btn-accent btn-lg', on: { click: () => player.playItem(p, d.onDeck.id) } },
            icon('play'), ` ${d.onDeck.subtitle.trim()}`));
    } else if (d.childrenStyle === 'track' && d.children.length) {
        actions.appendChild(h('button', { class: 'btn btn-accent btn-lg', on: { click: () => player.playItem(p, d.children[0].id) } },
            icon('play'), ' 재생'));
    }
    if (p.setWatched && (d.playable || d.type === 'show' || d.type === 'season')) {
        const isWatched = d.playable ? d.watched : d.unwatched === 0;
        actions.appendChild(h('button', {
            class: 'btn', title: isWatched ? '안 본 것으로 표시' : '본 것으로 표시',
            on: { click: async () => {
                await p.setWatched(d.id, !isWatched);
                toast(isWatched ? '안 본 것으로 표시했습니다.' : '본 것으로 표시했습니다.');
                ctx.rerender();
            } },
        }, icon(isWatched ? 'eye-slash' : 'check'), isWatched ? ' 안 봄으로' : ' 봤음'));
    }

    // 소스가 더 붙이는 버튼 (텔레그램: [정보 고치기])
    for (const a of d.extraActions || []) {
        actions.appendChild(h('button', { class: 'btn', title: a.title || '', on: { click: () => a.onClick(ctx) } }, icon(a.icon), ` ${a.label}`));
    }

    const poster = h('div', { class: `detail-poster shape-${d.posterShape}` });
    if (d.poster) poster.appendChild(h('img', { src: d.poster, alt: '' }));
    const bar = progressBar(d.progress);
    if (bar) poster.appendChild(bar);

    const facts = [];
    if (d.directors.length) facts.push(['감독', d.directors.join(', ')]);
    if (d.genres.length) facts.push(['장르', d.genres.join(', ')]);
    if (d.subtitles && d.subtitles.length) facts.push(['자막', d.subtitles.join(', ')]);
    for (const f of d.extraFacts || []) facts.push(f);

    const hero = h('div', { class: 'detail-hero' },
        poster,
        h('div', { class: 'detail-info' },
            d.parent ? h('a', { class: 'detail-parent', href: d.parent.route, text: d.parent.label }) : null,
            h('h1', { text: d.title }),
            d.subtitle && !d.parent ? h('div', { class: 'detail-sub', text: d.subtitle }) : null,
            h('div', { class: 'detail-meta' },
                d.metaLine.map((t) => h('span', { text: t })),
                d.rating ? h('span', { class: 'rating' }, icon('star'), ` ${Number(d.rating).toFixed(1)}`) : null),
            actions,
            d.tagline ? h('p', { class: 'detail-tagline', text: d.tagline }) : null,
            d.summary ? h('p', { class: 'detail-summary', text: d.summary }) : null,
            facts.length ? h('dl', { class: 'detail-facts' }, facts.map(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])) : null));

    const page = h('div', { class: 'page detail' },
        h('div', { class: 'detail-bg', style: d.art ? { backgroundImage: `url("${d.art}")` } : {} }),
        hero);

    if (d.children.length) {
        if (d.childrenStyle === 'episode') {
            page.appendChild(h('section', { class: 'detail-section' },
                h('h3', { text: `${d.childrenTitle} ${d.children.length}개` }),
                h('div', { class: 'ep-list' }, d.children.map((it) => episodeRow(p, it)))));
        } else if (d.childrenStyle === 'track') {
            page.appendChild(h('section', { class: 'detail-section' },
                h('h3', { text: d.childrenTitle }),
                h('div', { class: 'track-list' }, d.children.map((it, i) => trackRow(p, it, i)))));
        } else {
            page.appendChild(hubRow({ title: d.childrenTitle, items: d.children }));
        }
    }

    if (d.cast.length) {
        page.appendChild(h('section', { class: 'detail-section' },
            h('h3', { text: '출연' }),
            h('div', { class: 'cast-row' }, d.cast.map((c) => h('div', { class: 'cast' },
                h('div', { class: 'cast-photo' }, c.thumb ? h('img', { src: c.thumb, alt: '', loading: 'lazy' }) : icon('user')),
                h('b', { text: c.name }),
                h('span', { class: 'muted', text: c.role }))))));
    }
    main.appendChild(page);
}
