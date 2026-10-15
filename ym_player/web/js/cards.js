// 포스터/채널 카드와 가로 줄(허브). 홈, 라이브러리, 검색, 상세 화면이 같이 쓴다.
import { h, icon } from './util.js';
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
    const fallback = { movie: 'film', show: 'tv', season: 'tv', episode: 'tv', artist: 'music', album: 'compact-disc', track: 'music', photo: 'image' }[item.type] || 'photo-film';
    const art = img(wide ? item.wideThumb : item.thumb, `card-art shape-${shape}`, fallback);

    if (item.watched) art.appendChild(h('span', { class: 'card-watched', title: '시청함' }, icon('check')));
    else if (item.unwatched) art.appendChild(h('span', { class: 'card-count', text: String(item.unwatched) }));
    const bar = progressBar(item.progress);
    if (bar) art.appendChild(bar);

    if (PLAYABLE.has(item.type) && provider) {
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
                    const box = card.closest('.grid, .row-track') || card.parentNode;
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
    return h('a', { class: `card card-${shape}`, href: item.route || '#' },
        art,
        h('div', { class: 'card-title', text: item.title, title: item.title }),
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null);
}

/** 라이브 채널 카드 - 누르면 바로 재생. playlist: 위/아래 채널 전환용 */
export function channelCard(item, playlist) {
    const provider = registry.byId(item.pid);
    const ch = item.channel;
    const art = img(item.logo, 'card-art shape-channel', 'tv');
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
