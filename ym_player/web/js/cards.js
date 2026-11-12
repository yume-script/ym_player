// 포스터/채널 카드와 가로 줄(허브). 홈, 라이브러리, 검색, 상세 화면이 같이 쓴다.
import { h, icon, fmtDuration, toast } from './util.js';
import { player } from './player.js';
import { registry } from './providers/index.js';
import { channelHealth, isFavorite, toggleFavorite } from './providers/iptv.js';
// 사진 보기는 열 때 받는다
const openViewer = (...a) => import('./viewer.js').then((m) => m.openViewer(...a));

const PLAYABLE = new Set(['movie', 'episode', 'track', 'clip']);

function img(src, cls, fallbackIcon) {
    const box = h('div', { class: cls });
    if (src) {
        // 다른 사이트 그림(브런치·만개의레시피 등)은 우리 주소를 알리지 않고 받는다 (퍼 가기 막는 곳이 있어서)
        const im = h('img', { src, alt: '', loading: 'lazy', decoding: 'async', referrerpolicy: /^https?:/i.test(src) ? 'no-referrer' : null });
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
    const fallback = { movie: 'film', show: 'tv', season: 'tv', episode: 'tv', artist: 'music', album: 'compact-disc', track: 'music', photo: 'image', book: 'book-open', article: 'newspaper' }[item.type] || 'photo-film';
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
        item.subtitle ? h('div', { class: 'card-sub', text: item.subtitle }) : null,
        // 텔레그램 파일 이름(확장자까지) - 격자 보기에서만 보인다 (app.css .grid .card-file)
        item.fileName ? h('div', { class: 'card-file', text: item.fileName, title: item.fileName }) : null);
}

/** 세부/테이블 보기에 쓸 정보 (Plex 메타데이터 / 텔레그램 색인) */
export function itemFacts(item) {
    if (item.facts) return item.facts;   // 소스가 직접 채운 것 (팟캐스트)
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
export function listItem(item, mode, playlist = null) {
    // 라이브 채널(IPTV·라디오 방송국)은 채널 카드를 그림으로, 날짜·길이 대신 분류와 LIVE
    const live = item.type === 'channel';
    const card = live ? channelCard(item, playlist) : posterCard(item);
    // (세부 보기에선 부제가 이미 분류이거나 지금 나오는 곡이라 분류는 부제와 다를 때만, LIVE는 그림 위에 이미 있음)
    const group = live && item.group !== item.subtitle ? item.group || '' : '';
    const f = live ? { year: '', date: mode === 'table' ? item.group || '' : '', length: '', extra: group, rating: '', summary: '' } : itemFacts(item);
    const status = live ? h('span', { class: 'list-status' }, mode === 'table' ? h('span', { class: 'live-badge', text: 'LIVE' }) : null)
        : item.watched ? h('span', { class: 'list-status done', title: '봤음' }, icon('check'))
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
    // 즐겨찾기 별 (공중파 라디오처럼 카드에서 바로 고르는 소스)
    if (item.favoritable) {
        const star = h('span', { class: `card-star${isFavorite(ch) ? ' on' : ''}`, role: 'button', tabindex: '0', title: '즐겨찾기' }, icon('star'));
        const flip = (e) => {
            e.preventDefault(); e.stopPropagation();
            const on = toggleFavorite(ch);
            star.classList.toggle('on', on);
            star.firstChild.className = on ? 'fa-solid fa-star' : 'fa-regular fa-star';
            toast(on ? `${item.title}: 즐겨찾기에 추가했습니다.` : `${item.title}: 즐겨찾기에서 뺐습니다.`);
        };
        star.firstChild.className = isFavorite(ch) ? 'fa-solid fa-star' : 'fa-regular fa-star';
        star.addEventListener('click', flip);
        star.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') flip(e); });
        art.appendChild(star);
    }
    // [관리자] 이름 바꾸기 연필 (공중파 라디오)
    if (item.onRename) {
        const pen = h('span', { class: 'card-star card-rename', role: 'button', tabindex: '0', title: '이름 바꾸기 (관리자)' }, icon('pen'));
        const go = (e) => { e.preventDefault(); e.stopPropagation(); item.onRename(); };
        pen.addEventListener('click', go);
        pen.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') go(e); });
        art.appendChild(pen);
    }
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
