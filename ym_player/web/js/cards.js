// 포스터/채널 카드와 가로 줄(허브). 홈, 라이브러리, 검색, 상세 화면이 같이 쓴다.
import { h, icon, fmtDuration } from './util.js';
import { player } from './player.js';
import { registry } from './providers/index.js';
import { channelHealth } from './providers/iptv.js';
import { openViewer } from './viewer.js';

const PLAYABLE = new Set(['movie', 'episode', 'track', 'clip']);

function img(src, cls, fallbackIcon) {
    const box = h('div', { class: cls });
    if (src) {
        const im = h('img', { src, alt: '', loading: 'lazy', decoding: 'async' });
        im.addEventListener('error', () => { im.remove(); box.appendChild(icon(fallbackIcon, 'card-fallback')); });
        box.appendChild(im);
    } else {
        box.appendChild(icon(fallbackIcon, 'card-fallback'));
    }
    return box;
}

export function progressBar(ratio) {
    if (!ratio || ratio <= 0) return null;
    return h('div', { class: 'card-progress' }, h('div', { style: { width: `${Math.min(100, ratio * 100)}%` } }));
}

/** 포스터(2:3) / 정사각(음악) / 와이드(16:9) 카드 */
export function posterCard(item, { wide = false } = {}) {
    const provider = registry.byId(item.pid);
    const shape = wide ? 'wide' : item.shape;
    const fallback = { movie: 'film', show: 'tv', season: 'tv', episode: 'tv', artist: 'music', album: 'compact-disc', track: 'music', photo: 'image', book: 'book-open' }[item.type] || 'photo-film';
    const art = img(wide ? item.wideThumb : item.thumb, `card-art shape-${shape}`, fallback);

    if (item.isNew) art.appendChild(h('span', { class: 'card-new', title: '지난번에 본 뒤 새로 올라옴', text: 'NEW' }));
    if (item.watched) art.appendChild(h('span', { class: 'card-watched', title: '시청함' }, icon('check')));
    else if (item.unwatched) art.appendChild(h('span', { class: 'card-count', text: String(item.unwatched) }));
    const bar = progressBar(item.progress);
    if (bar) art.appendChild(bar);

    // item.playable: 시리즈 카드처럼 종류는 재생 대상이 아니어도 [재생]이 되는 것 (다음 볼 회차를 틂)
    if ((PLAYABLE.has(item.type) || item.playable) && provider) {
        art.appendChild(h('button', {
            class: 'card-play', title: '재생',
            on: {
                click: (e) => { e.preventDefault(); e.stopPropagation(); player.playItem(provider, item.id); },
                // 누르기 직전에 서버가 미리 준비할 수 있는 소스면 (텔레그램: 형식 확인)
                pointerenter: () => { if (provider.warm) provider.warm(item.id); },
            },
        }, icon('play')));
    }
    if (item.type === 'photo') {
        art.appendChild(h('span', { class: 'card-kind', title: '사진' }, icon('image')));
        // 사진은 뷰어로. 같은 격자/줄에 있는 사진끼리 좌우로 넘겨 본다
        const card = h('a', {
            class: `card card-${shape} card-photo`, href: '#',
            on: {
                click: (e) => {
                    e.preventDefault();
                    const box = card.closest('.grid, .row-track, .item-list, .item-table') || card.parentNode;
                    const cards = [...box.querySelectorAll('.card-photo')];
                    openViewer(cards.map((c) => c._item), cards.indexOf(card));
                },
            },
        },
        art,
        h('div', { class: 'card-title', text: item.title, title: item.title }),
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null);
        card._item = item;
        return card;
    }
    // item.onOpen: 상세 화면 대신 바로 여는 것 (텔레그램 웹툰 한 화 -> 웹툰 보기)
    return h('a', { class: `card card-${shape}${item.cardClass ? ` ${item.cardClass}` : ''}`, href: item.route || '#',
        on: item.onOpen ? { click: (e) => { e.preventDefault(); item.onOpen(); } } : {} },
        art,
        h('div', { class: 'card-title', text: item.title, title: item.title }),
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null);
}

/** 세부/테이블 보기에 쓸 정보 (Plex 메타데이터 / 텔레그램 색인) */
export function itemFacts(item) {
    const m = item.raw || {};
    const p = registry.byId(item.pid);
    if (p && p.kind === 'plex') {
        return {
            year: m.year ? String(m.year) : '',
            date: m.addedAt ? new Date(m.addedAt * 1000).toISOString().slice(0, 10) : '',
            length: m.duration ? fmtDuration(m.duration) : (m.leafCount ? `${m.leafCount}화` : (m.childCount ? `시즌 ${m.childCount}` : '')),
            rating: m.audienceRating || m.rating || '',
            extra: m.contentRating || '',
            summary: m.summary || '',
        };
    }
    const caption = String(item.caption != null ? item.caption : (m.caption || ''));
    const lines = caption.split('\n');
    const summary = (lines[0] || '').trim() === item.title ? lines.slice(1).join('\n').trim() : caption.trim();
    return {
        year: '',
        date: (m.date || '').slice(0, 10),
        length: m.series ? `${m.series.count}화`
            : m.kind === 'book' || (m.kind === 'doc' && m.mime === 'application/pdf') ? (m.duration ? `${Math.round(m.duration)}쪽` : '')
                : m.kind === 'doc' ? ''
                : (m.duration ? fmtDuration(m.duration * 1000) : ''),
        rating: '',
        extra: '',
        summary,
    };
}

/** 세부 보기(포스터 + 제목·정보·줄거리) / 테이블 보기(작은 그림 + 한 줄) 한 항목. 누르면 카드를 누른 것과 같다 */
export function listItem(item, mode) {
    const card = posterCard(item);
    const f = itemFacts(item);
    const status = item.watched ? h('span', { class: 'list-status done', title: '봤음' }, icon('check'))
        : item.progress > 0 ? h('span', { class: 'list-status', text: `${Math.round(item.progress * 100)}%` })
            : item.unwatched ? h('span', { class: 'list-status', text: `안 본 ${item.unwatched}` }) : h('span', { class: 'list-status' });
    const open = (e) => { if (!e.target.closest('a, button')) card.click(); };
    if (mode === 'table') {
        return h('div', { class: 'table-row', role: 'row', on: { click: open } },
            card,
            h('div', { class: 'table-title' }, h('b', { text: item.title }), item.subtitle ? h('small', { text: item.subtitle }) : null),
            h('span', { class: 'table-cell', text: f.year || f.date }),
            h('span', { class: 'table-cell', text: f.length }),
            status);
    }
    const meta = [f.year, f.length, f.extra, f.rating ? `★ ${Number(f.rating).toFixed(1)}` : '', !f.year ? f.date : ''].filter(Boolean);
    return h('div', { class: 'list-row', on: { click: open } },
        card,
        h('div', { class: 'list-info' },
            h('h3', { class: 'list-title', text: item.title }),
            item.subtitle ? h('div', { class: 'list-sub muted', text: item.subtitle }) : null,
            meta.length ? h('div', { class: 'list-meta' }, meta.map((x) => h('span', { text: x }))) : null,
            f.summary ? h('p', { class: 'list-summary', text: f.summary }) : null,
            status));
}

/** 라이브 채널 카드 - 누르면 바로 재생. playlist: 위/아래 채널 전환용 */
export function channelCard(item, playlist) {
    const provider = registry.byId(item.pid);
    const ch = item.channel;
    const art = img(item.logo, 'card-art shape-channel', item.fallbackIcon || 'tv');
    art.appendChild(h('span', { class: 'live-badge corner', text: 'LIVE' }));
    const bar = progressBar(item.progress);
    if (bar) art.appendChild(bar);
    art.appendChild(h('span', { class: 'card-play static' }, icon('play')));
    const offline = channelHealth.get(ch.id) === 'offline';
    return h('button', {
        class: `card card-channel${offline ? ' offline' : ''}`,
        title: item.subtitle ? `${item.title} — ${item.subtitle}` : item.title,
        on: { click: () => player.playChannel(provider, ch, playlist) },
    },
    art,
    h('div', { class: 'card-title', text: item.title }),
    h('div', { class: 'card-sub', text: item.subtitle || ' ' }));
}

export function cardFor(item, playlist, wide = false) {
    return item.type === 'channel' ? channelCard(item, playlist) : posterCard(item, { wide });
}

export function playlistOf(items) {
    return items.filter((i) => i.type === 'channel').map((i) => ({ provider: registry.byId(i.pid), channel: i.channel }));
}

/** 제목 + 좌우로 넘기는 카드 줄. hub.wide면 모든 카드를 16:9로 (여러 소스를 섞은 이어보기 줄) */
export function hubRow(hub) {
    const playlist = hub.live ? playlistOf(hub.items) : null;
    const track = h('div', { class: 'row-track' }, hub.items.map((it) => cardFor(it, playlist, !!hub.wide)));
    const scroll = (dir) => track.scrollBy({ left: dir * track.clientWidth * 0.85, behavior: 'smooth' });
    return h('section', { class: 'row' },
        h('div', { class: 'row-head' },
            h('h3', {}, hub.title, hub.source ? h('span', { class: 'row-source', text: hub.source }) : null),
            hub.more ? h('a', { class: 'row-more', href: hub.more, text: '모두 보기' }) : null),
        h('div', { class: 'row-wrap' },
            h('button', { class: 'row-arrow left', title: '이전', on: { click: () => scroll(-1) } }, icon('chevron-left')),
            track,
            h('button', { class: 'row-arrow right', title: '다음', on: { click: () => scroll(1) } }, icon('chevron-right'))));
}

export function emptyState(iconName, title, text, action) {
    return h('div', { class: 'empty' },
        icon(iconName, 'empty-icon'),
        h('h2', { text: title }),
        text ? h('p', { text }) : null,
        action || null);
}

export function loading(text = '불러오는 중...') {
    return h('div', { class: 'loading' }, icon('circle-notch', 'fa-spin'), h('span', { text }));
}
