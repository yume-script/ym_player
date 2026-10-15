// 전체화면 재생기 + 작은 미니 플레이어 (Plex처럼 뒤로 가면 오른쪽 아래로 접힌다).
//
// 재생 엔진은 북오아시스 M3U 플러그인의 하이브리드 방식을 그대로 옮겼다:
//   kind 'ts'     -> mpegts.js (라이브 MPEG-TS)
//   kind 'hls'    -> hls.js (Safari는 네이티브)
//   kind 'direct' -> <video src>
// 원본 주소가 막히면(CORS/혼합콘텐츠) playback.proxyUrl(서버 중계)로 한 번 더 시도한다.
// 시도마다 token을 올려서, 늦게 도착한 이전 채널의 콜백이 새 채널을 건드리지 못하게 한다.
import { h, icon, clear, fmtClock, fmtHM, storeGet, storeSet, toast } from './util.js';
import { channelHealth, isFavorite, toggleFavorite } from './providers/iptv.js';
import { registry } from './providers/index.js';

const START_TIMEOUT_MS = 20000;
const PROGRESS_EVERY_SEC = 10;
const UI_HIDE_MS = 3000;
const OSD_MS = 4000;
// 재생 중 끊겼을 때 자동으로 다시 붙는 횟수 (중계로 바꾸는 것은 따로 1번). 이만큼 잘 돌면 횟수를 되돌린다
const AUTO_RECOVER_MAX = 3;
const AUTO_RECOVER_RESET_SEC = 30;
// 직접 연결로는 끊기고 중계로는 되는 채널 - 다음부터 처음부터 중계로 연다 (이번 탭 동안)
const PROXY_FIRST_KEY = 'yp_proxy_first';

// DASH(.mpd) 엔진은 크고(약 800KB) 쓰는 채널이 드물어서, 처음 필요할 때만 불러온다
const DASHJS_URL = 'https://cdn.jsdelivr.net/npm/dashjs@4.7.4/dist/dash.all.min.js';
let dashjsLoading = null;
function loadDashjs() {
    if (window.dashjs) return Promise.resolve(window.dashjs);
    if (!dashjsLoading) {
        dashjsLoading = new Promise((resolve, reject) => {
            const el = document.createElement('script');
            el.src = DASHJS_URL;
            el.onload = () => (window.dashjs ? resolve(window.dashjs) : reject(new Error('dash.js를 불러오지 못했습니다')));
            el.onerror = () => { dashjsLoading = null; el.remove(); reject(new Error('dash.js를 불러오지 못했습니다 (인터넷 연결 확인)')); };
            document.head.appendChild(el);
        });
    }
    return dashjsLoading;
}

/** 다시 시도해도 소용없는 실패 (DRM 등) - 서버 중계로 다시 열지 않는다 */
function finalError(message) {
    const e = new Error(message);
    e.final = true;
    return e;
}

function proxyFirst() {
    try { return new Set(JSON.parse(sessionStorage.getItem(PROXY_FIRST_KEY) || '[]')); } catch (e) { return new Set(); }
}
function rememberProxyFirst(id) {
    const set = proxyFirst();
    set.add(id);
    try { sessionStorage.setItem(PROXY_FIRST_KEY, JSON.stringify([...set])); } catch (e) { /* 저장 못 해도 이번 재생엔 영향 없음 */ }
}

class Player {
    constructor() {
        this.pb = null;
        this.token = 0;
        this.attemptSeq = 0;
        this.prepSeq = 0;   // [재생]을 누른 뒤 준비(getPlayback)하는 동안 다른 걸 누르거나 닫으면 버리기 위함
        this.recoverCount = 0;
        this.viaProxy = false;
        this.playingSince = 0;
        this.hls = null;
        this.mpegts = null;
        this.needsRecreate = false;
        this.minimized = false;
        this.lastReport = 0;
        this.uiTimer = null;
        this.osdTimer = null;
        this.dragging = false;
        this._build();
        this._bind();
    }

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------
    _build() {
        const btn = (name, title, onClick, cls = '') =>
            h('button', { class: `pbtn ${cls}`.trim(), title, on: { click: (e) => { e.stopPropagation(); onClick(); } } }, icon(name));

        this.video = this._newVideo();
        this.posterEl = h('div', { class: 'player-poster hidden' });
        this.msgText = h('span');
        this.msgSpin = icon('circle-notch', 'fa-spin');
        this.retryBtn = h('button', { class: 'btn btn-accent hidden', text: '다시 시도', on: { click: () => this.retry() } });
        this.msgEl = h('div', { class: 'player-msg hidden' }, this.msgSpin, this.msgText, this.retryBtn);

        this.osd = {
            logo: h('img', { alt: '' }),
            name: h('span', { class: 'osd-name' }),
            group: h('span', { class: 'osd-group' }),
            prog: h('span', { class: 'osd-prog' }),
            time: h('span', { class: 'osd-time' }),
            fill: h('div', { class: 'osd-fill' }),
            next: h('span', { class: 'osd-next' }),
        };
        this.osd.logo.addEventListener('error', () => { this.osd.logo.style.visibility = 'hidden'; });
        this.osdEl = h('div', { class: 'osd hidden' },
            h('div', { class: 'osd-logo' }, this.osd.logo),
            h('div', { class: 'osd-body' },
                h('div', { class: 'osd-row' }, h('span', { class: 'live-badge', text: 'LIVE' }), this.osd.name, this.osd.group),
                h('div', { class: 'osd-row osd-row-prog' }, this.osd.prog, this.osd.time),
                h('div', { class: 'osd-track' }, this.osd.fill),
                this.osd.next));

        this.stage = h('div', { class: 'player-stage', on: { click: () => this.togglePlay(), dblclick: () => this.toggleFullscreen() } },
            this.video, this.posterEl, this.msgEl, this.osdEl);

        this.titleEl = h('h2');
        this.subEl = h('p');
        this.liveBadge = h('span', { class: 'live-badge hidden', text: 'LIVE' });
        this.favBtn = btn('star', '즐겨찾기', () => this.toggleFav(), 'live-only');
        this.top = h('div', { class: 'player-top' },
            btn('chevron-down', '작게 보기 (Esc)', () => this.minimize()),
            h('div', { class: 'player-titles' }, h('div', { class: 'player-title-row' }, this.titleEl, this.liveBadge), this.subEl),
            this.favBtn,
            btn('list', '채널 목록', () => this.toggleDrawer(), 'live-only'));

        this.curEl = h('span', { class: 'seek-time', text: '0:00' });
        this.durEl = h('span', { class: 'seek-time', text: '0:00' });
        this.bufEl = h('div', { class: 'seek-buf' });
        this.playedEl = h('div', { class: 'seek-played' });
        this.tipEl = h('div', { class: 'seek-tip hidden' });
        this.seekBar = h('div', { class: 'seek-bar' }, this.bufEl, this.playedEl, this.tipEl);
        this.seekRow = h('div', { class: 'seek vod-only' }, this.curEl, this.seekBar, this.durEl);

        this.playBtn = btn('play', '재생/일시정지 (Space)', () => this.togglePlay(), 'pbtn-lg');
        this.muteBtn = btn('volume-high', '음소거 (M)', () => this.toggleMute());
        this.volRange = h('input', { type: 'range', min: '0', max: '1', step: '0.05', class: 'vol-range' });
        this.engineEl = h('span', { class: 'engine-badge' });
        this.fsBtn = btn('expand', '전체화면 (F)', () => this.toggleFullscreen());

        this.bottom = h('div', { class: 'player-bottom' },
            this.seekRow,
            h('div', { class: 'ctrl-row' },
                h('div', { class: 'ctrl-group' },
                    btn('backward-step', '이전 채널 (↑)', () => this.switchChannel(-1), 'live-only'),
                    btn('rotate-left', '10초 뒤로 (←)', () => this.seekBy(-10), 'vod-only'),
                    this.playBtn,
                    btn('rotate-right', '30초 앞으로 (→)', () => this.seekBy(30), 'vod-only'),
                    btn('forward-step', '다음 채널 (↓)', () => this.switchChannel(1), 'live-only'),
                    h('div', { class: 'vol' }, this.muteBtn, this.volRange)),
                h('div', { class: 'ctrl-group' }, this.engineEl),
                h('div', { class: 'ctrl-group' },
                    this.ccBtn = btn('closed-captioning', '자막 (C)', () => this.toggleSubMenu(), 'vod-only'),
                    this.optBtn = btn('sliders', '음성·화질', () => this.toggleOptMenu(), 'vod-only'),
                    btn('clone', '화면 속 화면', () => this.togglePip()),
                    this.fsBtn,
                    btn('xmark', '재생 종료', () => this.stop()))));

        this.drawerSearch = h('input', { class: 'input', type: 'search', placeholder: '채널 찾기' });
        this.drawerList = h('div', { class: 'drawer-list' });
        this.drawer = h('aside', { class: 'ch-drawer hidden', on: { click: (e) => e.stopPropagation() } },
            h('div', { class: 'drawer-head' }, h('b', { text: '채널' }), this.drawerSearch), this.drawerList);

        this.subList = h('div', { class: 'sub-list' });
        this.subSizes = h('div', { class: 'sub-sizes' },
            [['s', '작게'], ['m', '보통'], ['l', '크게']].map(([v, label]) =>
                h('button', { class: 'chip', dataset: { size: v }, text: label, on: { click: () => this.setCueSize(v) } })));
        this.subMenu = h('div', { class: 'sub-menu hidden', on: { click: (e) => e.stopPropagation() } },
            h('div', { class: 'sub-menu-head', text: '자막' }), this.subList,
            h('div', { class: 'sub-menu-head', text: '글자 크기' }), this.subSizes);

        // 음성 트랙 / 화질 (Plex만)
        this.optMenu = h('div', { class: 'sub-menu hidden', on: { click: (e) => e.stopPropagation() } });

        this.root = h('div', { class: 'player hidden' }, this.stage, this.top, this.bottom, this.drawer, this.subMenu, this.optMenu);

        this.miniSlot = h('div', { class: 'mini-video', on: { click: () => this.expand() } });
        this.miniTitle = h('b');
        this.miniSub = h('span');
        this.miniPlayBtn = btn('pause', '재생/일시정지', () => this.togglePlay());
        this.mini = h('div', { class: 'mini hidden' },
            this.miniSlot,
            h('div', { class: 'mini-bar' },
                h('div', { class: 'mini-info', on: { click: () => this.expand() } }, this.miniTitle, this.miniSub),
                this.miniPlayBtn,
                btn('up-right-and-down-left-from-center', '크게 보기', () => this.expand()),
                btn('xmark', '닫기', () => this.stop())));

        document.body.append(this.root, this.mini);
        document.body.dataset.cueSize = storeGet('yp_cue_size', 'm');
        const vol = storeGet('yp_volume', 1);
        this.volRange.value = String(vol);
    }

    _newVideo() {
        const v = h('video', { playsinline: true, preload: 'auto' });
        v.playsInline = true;
        v.volume = storeGet('yp_volume', 1);
        v.muted = storeGet('yp_muted', false);
        for (const ev of ['play', 'pause', 'playing', 'waiting']) v.addEventListener(ev, () => this._syncPlayState());
        v.addEventListener('timeupdate', () => this._onTime());
        v.addEventListener('progress', () => this._renderBuffered());
        v.addEventListener('ended', () => this._onEnded());
        v.addEventListener('volumechange', () => this._syncVolume());
        v.addEventListener('waiting', () => { if (this.pb && !this.msgEl.classList.contains('error')) this._spinner(true); });
        v.addEventListener('playing', () => this._spinner(false));
        return v;
    }

    _bind() {
        const wake = () => this._wakeUi();
        this.root.addEventListener('mousemove', wake);
        this.root.addEventListener('touchstart', wake, { passive: true });
        this.volRange.addEventListener('input', () => {
            this.video.volume = Number(this.volRange.value);
            this.video.muted = this.video.volume === 0;
        });
        this.drawerSearch.addEventListener('input', () => this._renderDrawer());

        const ratioAt = (e) => {
            const r = this.seekBar.getBoundingClientRect();
            return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        };
        this.seekBar.addEventListener('pointerdown', (e) => {
            this.dragging = true;
            this.seekBar.setPointerCapture(e.pointerId);
            this._previewSeek(ratioAt(e));
        });
        this.seekBar.addEventListener('pointermove', (e) => {
            const ratio = ratioAt(e);
            const d = this._duration();
            this.tipEl.textContent = fmtClock(ratio * d);
            this.tipEl.style.left = `${ratio * 100}%`;
            this.tipEl.classList.remove('hidden');
            if (this.dragging) this._previewSeek(ratio);
        });
        this.seekBar.addEventListener('pointerleave', () => this.tipEl.classList.add('hidden'));
        this.seekBar.addEventListener('pointerup', (e) => {
            if (!this.dragging) return;
            this.dragging = false;
            this.seekTo(ratioAt(e) * this._duration());
        });

        document.addEventListener('keydown', (e) => this._onKey(e));
        document.addEventListener('fullscreenchange', () => {
            this.fsBtn.firstChild.className = document.fullscreenElement ? 'fa-solid fa-compress' : 'fa-solid fa-expand';
        });
        setInterval(() => this._tickLive(), 30000);
    }

    // ------------------------------------------------------------------
    // 시작/종료
    // ------------------------------------------------------------------
    /** provider.getPlayback()이 돌려준 playback으로 재생한다. playlist: 라이브 채널 전환용 [{provider, channel}] */
    /**
     * opts.auto       끊겨서 자동으로 다시 붙는 중 (자동 재시도 횟수를 이어서 센다)
     * opts.forceProxy 직접 연결은 건너뛰고 서버 중계로 바로
     */
    async start(pb, playlist = null, { auto = false, forceProxy = false } = {}) {
        if (!auto) this.recoverCount = 0;
        if (this.pb && this.pb !== pb) this._finishCurrent();
        if (playlist) pb.playlist = playlist;
        else if (this.pb && this.pb.playlist && pb.live) pb.playlist = this.pb.playlist;
        this.pb = pb;
        const token = ++this.token;
        this.lastReport = 0;

        this.expand();
        this.root.classList.toggle('is-live', !!pb.live);
        this.root.classList.toggle('is-audio', !!pb.audioOnly);
        this.liveBadge.classList.toggle('hidden', !pb.live);
        this.titleEl.textContent = pb.title || '';
        this.subEl.textContent = pb.subtitle || '';
        this.miniTitle.textContent = pb.title || '';
        this.miniSub.textContent = pb.subtitle || '';
        this.posterEl.classList.toggle('hidden', !pb.audioOnly);
        this.posterEl.style.backgroundImage = pb.audioOnly && pb.poster ? `url("${pb.poster}")` : '';
        this.engineEl.textContent = '';
        this.subMenu.classList.add('hidden');
        this.optMenu.classList.add('hidden');
        this.ccBtn.classList.toggle('hidden', !(pb.subtitles && pb.subtitles.length));
        this.optBtn.classList.toggle('hidden', !((pb.audios && pb.audios.length > 1) || (pb.qualities && pb.qualities.length)));
        this._syncCc();
        this._syncFav();
        this._showMsg(`"${pb.title}" 연결 중...`, { spinner: true });
        if (pb.live) this.showOsd();
        if (!this.drawer.classList.contains('hidden')) this._renderDrawer();

        if (this.needsRecreate) { this.needsRecreate = false; this._recreateVideo(); }
        this._destroyEngines();
        this.video.removeAttribute('src');
        this.video.load();
        this._applyTrack(pb.textTrack);

        let label;
        const viaProxyOnly = pb.proxyUrl && (forceProxy || (pb.channel && proxyFirst().has(pb.channel.id)));
        this.viaProxy = false;
        try {
            if (viaProxyOnly) {
                this.viaProxy = true;
                label = (await this._attempt(pb.proxyUrl, pb.kind, token, pb.startTime || 0, !!pb.live)) + ' · 중계';
            } else {
                label = await this._attempt(pb.url, pb.kind, token, pb.startTime || 0, !!pb.live);
            }
        } catch (e1) {
            if (token !== this.token) return;
            if (viaProxyOnly || e1.final) return this._failed(e1);
            if (token !== this.token) return;
            if (!pb.proxyUrl && pb.fallback) {
                console.warn('[Player] 원본 재생 실패, 서버 변환으로 다시 시도:', e1.message);
                this._showMsg('브라우저가 못 여는 형식이라 서버 변환으로 다시 여는 중...', { spinner: true });
                try {
                    const next = await pb.fallback(pb.startTime || 0);
                    if (token !== this.token) return;
                    if (next) return this.start(next, null, { auto: true });
                } catch (e3) { console.warn('[Player] 서버 변환 실패:', e3.message); }
                if (token !== this.token) return;
            }
            if (!pb.proxyUrl) return this._failed(e1);
            console.warn('[Player] 직접 재생 실패, 서버 중계로 다시 시도:', e1.message);
            this._showMsg('서버 중계로 다시 연결하는 중...', { spinner: true });
            this.viaProxy = true;
            try {
                label = (await this._attempt(pb.proxyUrl, pb.kind, token, pb.startTime || 0, !!pb.live)) + ' · 중계';
            } catch (e2) {
                if (token !== this.token) return;
                return this._failed(e2);
            }
        }
        if (token !== this.token) return;
        this.engineEl.textContent = pb.engineNote || label;
        this.playingSince = this.video.currentTime || 0;
        this._hideMsg();
        if (pb.channel) channelHealth.set(pb.channel.id, 'online');
        this._wakeUi();
    }

    retry() {
        if (!this.pb) return;
        if (this.pb.reloadAt) return this._reloadAt(this.pb, this._pos());
        if (!this.pb.live) this.pb.startTime = this.video.currentTime || this.pb.startTime || 0;
        this.start(this.pb);
    }

    /**
     * 지금 재생 위치(초). 서버 변환 스트림은 요청한 위치(timeOffset)부터 시작해서 <video>의 0초가
     * 실제로는 timeOffset초다. 그 밖에는 video.currentTime 그대로.
     */
    _pos() {
        const pb = this.pb;
        return ((pb && pb.timeOffset) || 0) + (this.video.currentTime || 0);
    }

    /** 서버 변환 스트림을 sec초부터 다시 받는다 (탐색, 다시 시도). */
    async _reloadAt(pb, sec, opts = {}) {
        const token = this.token;
        this._showMsg('그 위치부터 다시 불러오는 중...', { spinner: true });
        try {
            const next = await pb.reloadAt(Math.max(0, sec));
            if (this.pb !== pb || this.token !== token) return;
            await this.start(next, null, { auto: true, ...opts });
        } catch (e) {
            if (this.pb === pb) this._showMsg(`다시 불러오지 못했습니다: ${e.message}`, { error: true });
        }
    }

    /** sec초로 이동. 서버 변환 스트림은 이미 받아 둔 범위 밖이면 그 위치부터 다시 받는다 */
    seekTo(sec) {
        const pb = this.pb;
        if (!pb || pb.live) return;
        const d = this._duration();
        sec = Math.max(0, d ? Math.min(d - 1, sec) : sec);
        if (!pb.reloadAt) { this.video.currentTime = sec; return; }
        const v = this.video;
        const local = sec - (pb.timeOffset || 0);
        const bufEnd = v.buffered.length ? v.buffered.end(v.buffered.length - 1) : 0;
        if (local >= 0 && local <= bufEnd) { v.currentTime = local; return; }
        // 연타(→ 여러 번)는 마지막 위치로 한 번만
        clearTimeout(this._seekTimer);
        this._pendingSeek = sec;
        this.curEl.textContent = fmtClock(sec);
        if (d) this.playedEl.style.width = `${(sec / d) * 100}%`;
        this._seekTimer = setTimeout(() => { this._pendingSeek = null; this._reloadAt(pb, sec, { auto: false }); }, 400);
    }

    stop() {
        this.prepSeq++;
        this._finishCurrent();
        this.token++;
        this._destroyEngines();
        try { this.video.pause(); } catch (e) { /* 이미 정지 */ }
        this.video.removeAttribute('src');
        this.video.load();
        if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        this.pb = null;
        this.root.classList.add('hidden');
        this.mini.classList.add('hidden');
        document.body.classList.remove('player-open');
    }

    minimize() {
        if (!this.pb) return this.stop();
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        this.minimized = true;
        this.toggleDrawer(false);
        this.miniSlot.appendChild(this.video);
        this.root.classList.add('hidden');
        this.mini.classList.remove('hidden');
        document.body.classList.remove('player-open');
    }

    expand() {
        this.minimized = false;
        if (this.video.parentNode !== this.stage) this.stage.prepend(this.video);
        this.mini.classList.add('hidden');
        this.root.classList.remove('hidden');
        document.body.classList.add('player-open');
        this._wakeUi();
    }

    _finishCurrent() {
        const pb = this.pb;
        if (pb && pb.onStop) {
            try { pb.onStop(this._pos()); } catch (e) { /* 알림 실패는 무시 */ }
        }
    }

    _failed(err) {
        console.warn('[Player] 재생 실패:', err && err.message);
        if (this.pb && this.pb.channel) channelHealth.set(this.pb.channel.id, 'offline');
        // 실패한 MSE가 같은 <video>에 남아 다음 채널까지 망가뜨리는 브라우저 버그 대응 (원래 플러그인 참고)
        this.needsRecreate = true;
        this._destroyEngines();
        // 이유가 분명한 실패(DRM 등)는 그 이유를, 라이브의 흔한 연결 실패는 짧게
        const reason = err && err.message ? err.message : err;
        this._showMsg(err && err.final ? reason
            : this.pb && this.pb.live ? '스트림에 연결할 수 없습니다 (오프라인 또는 차단).' : `재생할 수 없습니다: ${reason}`,
            { error: true });
    }

    // ------------------------------------------------------------------
    // 엔진
    // ------------------------------------------------------------------
    _attempt(url, kind, token, startTime, live) {
        const v = this.video;
        // 같은 채널 안에서도 직접 시도 -> 중계 시도로 넘어가므로 시도마다 번호를 따로 매긴다.
        // 버린 엔진이 늦게 던지는 오류가 새 엔진을 끄지 못하게 하기 위함.
        const attempt = ++this.attemptSeq;
        const stale = () => token !== this.token || attempt !== this.attemptSeq;
        return new Promise((resolve, reject) => {
            let settled = false;
            let dead = false;
            const timer = setTimeout(() => fail(new Error('응답 시간 초과')), START_TIMEOUT_MS);
            const ok = (label) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                resolve(label);
            };
            // engineError=false: play() 거부처럼 "시작 실패"만 뜻하는 경우 (시작한 뒤라면 무시)
            const fail = (err, engineError = true) => {
                if (stale()) { clearTimeout(timer); return; }
                if (!settled) {
                    settled = true;
                    clearTimeout(timer);
                    this._destroyEngines(true);
                    reject(err);
                } else if (engineError && !dead) {
                    // 같은 엔진이 정리되기 전에 오류를 여러 번 던져도 다시 붙기는 한 번만
                    dead = true;
                    this._runtimeError(err);
                }
            };

            if (kind === 'dash') {
                loadDashjs().then((dashjs) => {
                    if (stale()) { clearTimeout(timer); return; }
                    const p = dashjs.MediaPlayer().create();
                    this.dash = p;
                    const E = dashjs.MediaPlayer.events;
                    if (url.startsWith('/api/stream')) {
                        // 중계로 받은 MPD는 서버가 기준 주소를 원본 절대 주소로 바꿔 둔다 -> 조각 요청도 중계로 감싼다
                        const extra = new URLSearchParams(url.split('?')[1] || '');
                        p.extend('RequestModifier', () => ({
                            modifyRequestHeader: (xhr) => xhr,
                            modifyRequestURL: (u) => {
                                if (!/^https?:/i.test(u) || u.startsWith(location.origin)) return u;
                                const q = new URLSearchParams({ url: u });
                                if (extra.get('ua')) q.set('ua', extra.get('ua'));
                                if (extra.get('ref')) q.set('ref', extra.get('ref'));
                                return `/api/stream?${q}`;
                            },
                        }), true);
                    }
                    p.updateSettings({ debug: { logLevel: dashjs.Debug.LOG_LEVEL_WARNING }, streaming: { retryAttempts: { MPD: 2 } } });
                    p.on(E.MANIFEST_LOADED, (e) => {
                        // 암호화된 스트림: 키(라이선스) 없이는 못 튼다. DRM 해제는 하지 않는다
                        if (JSON.stringify(e.data || {}).includes('ContentProtection')) {
                            fail(finalError('DRM(암호화)이 걸린 DASH 스트림이라 재생할 수 없습니다.'));
                        }
                    });
                    p.on(E.ERROR, (e) => {
                        const err = e && e.error;
                        fail(new Error(`DASH 오류: ${(err && (err.message || err.code)) || '알 수 없음'}`));
                    });
                    p.on(E.CAN_PLAY, () => ok('DASH'));
                    p.on(E.PLAYBACK_PLAYING, () => ok('DASH'));
                    if (startTime > 0) p.on(E.STREAM_INITIALIZED, () => { if (!live) p.seek(startTime); });
                    p.initialize(v, url, true);
                    v.play().catch((e) => { if (e && e.name === 'NotAllowedError') { ok('DASH'); this._needsGesture(); } });
                }).catch((e) => fail(e));
                return;
            }

            if (kind === 'ts' && window.mpegts && window.mpegts.isSupported()) {
                try {
                    const p = window.mpegts.createPlayer(
                        { type: 'mpegts', isLive: true, url },
                        { enableWorker: true, enableStashBuffer: false, liveBufferLatencyChasing: true });
                    this.mpegts = p;
                    p.on(window.mpegts.Events.ERROR, (type, detail) => fail(new Error(`${type}: ${detail}`)));
                    p.attachMediaElement(v);
                    p.load();
                    Promise.resolve(p.play()).then(() => ok('MPEG-TS')).catch((e) => {
                        if (e && e.name === 'NotAllowedError') { ok('MPEG-TS'); this._needsGesture(); } else fail(e, false);
                    });
                } catch (e) { fail(e); }
                return;
            }

            if (kind !== 'direct' && window.Hls && window.Hls.isSupported()) {
                const Hls = window.Hls;
                const hls = new Hls({
                    enableWorker: true,
                    // 저지연(LL-HLS)이 아닌 일반 IPTV에 켜면 라이브 끝자락에 너무 붙어서 시작하자마자 버퍼가 바닥나기 쉽다
                    lowLatencyMode: false,
                    liveSyncDurationCount: 4,
                    backBufferLength: live ? 30 : 90,
                    startPosition: startTime > 0 ? startTime : -1,
                    manifestLoadingTimeOut: 20000,
                    fragLoadingTimeOut: 30000,
                });
                this.hls = hls;
                let recovered = false;
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    v.play().catch((e) => {
                        if (e && e.name === 'NotAllowedError') { ok('HLS'); this._needsGesture(); }
                    });
                });
                // 첫 조각이 실제로 버퍼에 들어가야 성공 - 매니페스트만 읽히고 코덱이 안 맞는 경우를 걸러낸다
                hls.on(Hls.Events.FRAG_BUFFERED, () => ok('HLS'));
                hls.on(Hls.Events.ERROR, (evt, data) => {
                    if (!data.fatal) return;
                    if (data.type === Hls.ErrorTypes.MEDIA_ERROR && !recovered && settled) {
                        recovered = true;
                        hls.recoverMediaError();
                        return;
                    }
                    fail(new Error(data.details || 'HLS 오류'));
                });
                hls.loadSource(url);
                hls.attachMedia(v);
                return;
            }

            // direct 또는 hls.js가 없는 Safari(네이티브 HLS)
            const onError = () => fail(new Error(v.error ? `미디어 오류 ${v.error.code}` : '미디어 오류'));
            v.addEventListener('error', onError);
            this._detachDirect = () => v.removeEventListener('error', onError);
            if (startTime > 0) v.addEventListener('loadedmetadata', () => { v.currentTime = startTime; }, { once: true });
            v.src = url;
            v.play().then(() => ok(kind === 'hls' ? 'HLS (네이티브)' : 'DIRECT')).catch((e) => {
                if (e && e.name === 'NotAllowedError') { ok('DIRECT'); this._needsGesture(); }
                else if (e && e.name !== 'AbortError') fail(e, false);
            });
        });
    }

    /**
     * 재생이 시작된 뒤에 끊겼을 때 (fragLoadError 등). 예전엔 여기서 그냥 멈췄다.
     * 1) 직접 연결로 보던 중이면 -> 서버 중계로 바꿔서 다시 (재생목록은 열리는데 영상 조각만 CORS로
     *    막히는 채널이 흔하다). 그 채널은 다음부터 처음부터 중계로 연다.
     * 2) 이미 중계였으면 -> 조금씩 기다렸다가 AUTO_RECOVER_MAX번까지 다시 붙는다 (주소 만료, 순간 끊김).
     * VOD는 보던 위치부터 다시 이어서 받는다.
     */
    _runtimeError(err) {
        const pb = this.pb;
        console.warn('[Player] 재생 중 오류:', err && err.message, this.viaProxy ? '(중계)' : '(직접)');
        this._destroyEngines(true);
        this.needsRecreate = true;
        if (!pb) return;
        if (pb.reloadAt) {
            // 서버 변환 스트림: 보던 위치부터 새로 받는다 (몇 번까지)
            if (this.recoverCount >= AUTO_RECOVER_MAX) { this._showMsg('재생이 끊겼습니다. 서버 변환이 멈췄습니다.', { error: true }); return; }
            this.recoverCount += 1;
            const at = this._pos();
            setTimeout(() => { if (this.pb === pb) this._reloadAt(pb, at); }, 800 * this.recoverCount);
            return;
        }
        if (!pb.live) pb.startTime = this.video.currentTime || pb.startTime || 0;

        const token = this.token;
        const again = (opts, delayMs, msg) => {
            this._showMsg(msg, { spinner: true });
            setTimeout(() => { if (this.pb === pb && this.token === token) this.start(pb, null, { auto: true, ...opts }); }, delayMs);
        };
        if (!this.viaProxy && pb.proxyUrl) {
            if (pb.channel) rememberProxyFirst(pb.channel.id);
            return again({ forceProxy: true }, 300, '연결이 끊겨 서버 중계로 다시 연결하는 중...');
        }
        if (this.recoverCount < AUTO_RECOVER_MAX) {
            this.recoverCount += 1;
            return again({ forceProxy: this.viaProxy }, 1000 * this.recoverCount,
                `연결이 끊겨 다시 연결하는 중... (${this.recoverCount}/${AUTO_RECOVER_MAX})`);
        }
        this._showMsg('재생이 끊겼습니다. 원본 서버가 응답하지 않습니다.', { error: true });
    }

    _needsGesture() {
        this._showMsg('화면을 눌러 재생을 시작하세요.', {});
        this.video.addEventListener('playing', () => this._hideMsg(), { once: true });
    }

    // later=true: 엔진 자신의 오류 콜백 안에서 부를 때. 그 자리에서 destroy하면 hls.js가
    // 남은 처리 중에 null을 건드려 예외를 내므로, 참조만 바로 끊고 실제 정리는 다음 틱에 한다.
    _destroyEngines(later = false) {
        const hls = this.hls, ts = this.mpegts, dash = this.dash;
        this.hls = null;
        this.mpegts = null;
        this.dash = null;
        if (this._detachDirect) { this._detachDirect(); this._detachDirect = null; }
        const run = () => {
            if (hls) { try { hls.destroy(); } catch (e) { /* 이미 깨짐 */ } }
            if (dash) { try { dash.reset(); } catch (e) { /* 이미 깨짐 */ } }
            if (ts) {
                for (const fn of ['pause', 'unload', 'detachMediaElement', 'destroy']) {
                    try { ts[fn](); } catch (e) { /* 이미 깨짐 */ }
                }
            }
        };
        if (later) setTimeout(run, 0); else run();
    }

    _recreateVideo() {
        if (document.pictureInPictureElement === this.video) return;
        const old = this.video;
        const fresh = this._newVideo();
        old.replaceWith(fresh);
        this.video = fresh;
    }

    // ------------------------------------------------------------------
    // 조작
    // ------------------------------------------------------------------
    togglePlay() {
        if (!this.pb) return;
        if (this.msgEl.classList.contains('error')) return this.retry();
        const v = this.video;
        if (v.paused) v.play().catch(() => {}); else v.pause();
        this._wakeUi();
    }

    seekBy(sec) {
        if (!this.pb || this.pb.live) return;
        const base = this._pendingSeek != null ? this._pendingSeek : this._pos();
        this.seekTo(base + sec);
        this._wakeUi();
    }

    toggleMute() { this.video.muted = !this.video.muted; }

    toggleFullscreen() {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        else if (this.root.requestFullscreen) this.root.requestFullscreen().catch(() => {});
        else if (this.video.webkitEnterFullscreen) this.video.webkitEnterFullscreen(); // iOS
    }

    async togglePip() {
        try {
            if (document.pictureInPictureElement) await document.exitPictureInPicture();
            else await this.video.requestPictureInPicture();
        } catch (e) { toast('이 브라우저는 화면 속 화면을 지원하지 않습니다.', 'error'); }
    }

    switchChannel(offset) {
        const pb = this.pb;
        if (!pb || !pb.live || !pb.playlist || !pb.playlist.length) return;
        const list = pb.playlist;
        const cur = list.findIndex((x) => x.channel.id === pb.channel.id);
        const next = list[(cur + offset + list.length) % list.length];
        this.playChannel(next.provider, next.channel, list);
    }

    playChannel(provider, channel, playlist) {
        this.prepSeq++; // 준비 중이던 다른 영상은 버린다
        return this.start(provider.getPlayback(channel), playlist);
    }

    async playItem(provider, id, opts) {
        // 재생 전에 물어볼 게 있으면 (텔레그램 나눠진 파일의 두 번째 이후 조각 등) 먼저 묻는다
        if (provider.confirmPlay) {
            const ask = ++this.prepSeq;
            id = await provider.confirmPlay(id, opts || {});
            if (id == null || ask !== this.prepSeq) return;
        }
        // 누르자마자 재생 화면부터 띄운다. 준비(서버의 형식 확인, Plex 메타데이터 등)가 몇 초 걸려도
        // 아무 반응 없이 멈칫한 것처럼 보이지 않게.
        const seq = ++this.prepSeq;
        this._preparing();
        try {
            const pb = await provider.getPlayback(id, opts);
            if (seq !== this.prepSeq) return;
            await this.start(pb);
        } catch (e) {
            if (seq !== this.prepSeq) return;
            toast(`재생 준비 실패: ${e.message}`, 'error');
            this.stop();
        }
    }

    /** 지금 보던 것을 정리하고 빈 재생 화면에 "재생 준비 중"을 띄운다 */
    _preparing() {
        this._finishCurrent();
        this.token++;
        this._destroyEngines();
        try { this.video.pause(); } catch (e) { /* 이미 정지 */ }
        this.video.removeAttribute('src');
        this.video.load();
        this._applyTrack(null);
        this.pb = null;
        this.titleEl.textContent = '';
        this.subEl.textContent = '';
        this.engineEl.textContent = '';
        this.subMenu.classList.add('hidden');
        this.optMenu.classList.add('hidden');
        this.posterEl.classList.add('hidden');
        this.liveBadge.classList.add('hidden');
        this.root.classList.remove('is-live', 'is-audio');
        this.curEl.textContent = fmtClock(0);
        this.durEl.textContent = fmtClock(0);
        this.playedEl.style.width = '0';
        this.bufEl.style.width = '0';
        this.expand();
        this._showMsg('재생 준비 중...', { spinner: true });
    }

    toggleFav() {
        const pb = this.pb;
        if (!pb || !pb.channel) return;
        const on = toggleFavorite(pb.channel);
        toast(on ? '즐겨찾기에 추가했습니다.' : '즐겨찾기에서 뺐습니다.');
        this._syncFav();
    }

    _syncFav() {
        const on = this.pb && this.pb.channel && isFavorite(this.pb.channel);
        this.favBtn.firstChild.className = on ? 'fa-solid fa-star' : 'fa-regular fa-star';
        this.favBtn.classList.toggle('on', !!on);
    }

    // ------------------------------------------------------------------
    // 화면 갱신
    // ------------------------------------------------------------------
    _duration() {
        // 서버 변환 스트림은 <video>가 아는 길이가 일부뿐이라 원래 길이를 쓴다
        if (this.pb && this.pb.timeOffset !== undefined && this.pb.durationHint) return this.pb.durationHint;
        const d = this.video.duration;
        if (isFinite(d) && d > 0) return d;
        return (this.pb && this.pb.durationHint) || 0;
    }

    _onTime() {
        const v = this.video;
        if (!this.pb) return;
        if (this.recoverCount && v.currentTime - (this.playingSince || 0) > AUTO_RECOVER_RESET_SEC) this.recoverCount = 0;
        const pos = this._pos();
        if (!this.dragging && this._pendingSeek == null) {
            const d = this._duration();
            this.curEl.textContent = fmtClock(pos);
            this.durEl.textContent = fmtClock(d);
            this.playedEl.style.width = d ? `${(pos / d) * 100}%` : '0';
        }
        if (this.pb.onProgress && !v.paused && Math.abs(pos - this.lastReport) >= PROGRESS_EVERY_SEC) {
            this.lastReport = pos;
            this.pb.onProgress('playing', pos);
        }
    }

    _previewSeek(ratio) {
        const d = this._duration();
        this.playedEl.style.width = `${ratio * 100}%`;
        this.curEl.textContent = fmtClock(ratio * d);
    }

    _renderBuffered() {
        const v = this.video, d = this._duration();
        if (!d || !v.buffered.length) return;
        const off = (this.pb && this.pb.timeOffset) || 0;
        this.bufEl.style.width = `${Math.min(100, ((off + v.buffered.end(v.buffered.length - 1)) / d) * 100)}%`;
    }

    async _onEnded() {
        const pb = this.pb;
        if (!pb || pb.live) return;
        if (pb.onProgress) pb.onProgress('stopped', this._duration());
        if (!pb.getNext) return;
        try {
            const next = await pb.getNext();
            if (!next || this.pb !== pb) return;
            const provider = registry.byId(next.pid);
            toast(`다음: ${next.title}`);
            this.playItem(provider, next.id, { fromStart: true, auto: true });
        } catch (e) { /* 다음 화가 없으면 그냥 끝 */ }
    }

    _syncPlayState() {
        const paused = this.video.paused;
        const cls = paused ? 'fa-solid fa-play' : 'fa-solid fa-pause';
        this.playBtn.firstChild.className = cls;
        this.miniPlayBtn.firstChild.className = cls;
        this.root.classList.toggle('paused', paused);
        if (paused && this.pb && this.pb.onProgress && !this.pb.live && this._pos() > 0) {
            this.pb.onProgress('paused', this._pos());
        }
        this._wakeUi();
    }

    _syncVolume() {
        const v = this.video;
        storeSet('yp_volume', v.volume);
        storeSet('yp_muted', v.muted);
        this.volRange.value = String(v.muted ? 0 : v.volume);
        this.muteBtn.firstChild.className = v.muted || v.volume === 0 ? 'fa-solid fa-volume-xmark'
            : v.volume < 0.5 ? 'fa-solid fa-volume-low' : 'fa-solid fa-volume-high';
    }

    _showMsg(text, { spinner = false, error = false } = {}) {
        this.msgText.textContent = text;
        this.msgSpin.classList.toggle('hidden', !spinner);
        this.retryBtn.classList.toggle('hidden', !error);
        this.msgEl.classList.toggle('error', error);
        this.msgEl.classList.remove('hidden', 'spin-only');
    }

    _hideMsg() { this.msgEl.classList.add('hidden'); this.msgEl.classList.remove('error'); }

    _spinner(on) {
        if (this.msgEl.classList.contains('error')) return;
        if (on) {
            this.msgText.textContent = '';
            this.msgSpin.classList.remove('hidden');
            this.retryBtn.classList.add('hidden');
            this.msgEl.classList.remove('hidden');
            this.msgEl.classList.add('spin-only');
        } else {
            this._hideMsg();
        }
    }

    _wakeUi() {
        this.root.classList.add('show-ui');
        clearTimeout(this.uiTimer);
        this.uiTimer = setTimeout(() => {
            if (!this.video.paused && this.drawer.classList.contains('hidden')
                && this.subMenu.classList.contains('hidden') && this.optMenu.classList.contains('hidden') && !this.dragging) {
                this.root.classList.remove('show-ui');
            }
        }, UI_HIDE_MS);
    }

    showOsd() {
        const pb = this.pb;
        if (!pb || !pb.live || !pb.channel) return;
        const ch = pb.channel;
        const { cur, next, progress } = pb.provider.nowNext(ch);
        this.osd.logo.style.visibility = ch.logo ? 'visible' : 'hidden';
        if (ch.logo) this.osd.logo.src = ch.logo;
        this.osd.name.textContent = ch.chno ? `${ch.chno}  ${ch.name}` : ch.name;
        this.osd.group.textContent = ch.group || '';
        this.osd.prog.textContent = cur ? cur[2] : '편성표 정보 없음';
        this.osd.time.textContent = cur && cur[1] ? `${fmtHM(cur[0])} ~ ${fmtHM(cur[1])}` : '';
        this.osd.fill.style.width = `${progress * 100}%`;
        this.osd.next.textContent = next ? `다음  ${fmtHM(next[0])}  ${next[2]}` : '';
        this.osdEl.classList.remove('hidden');
        clearTimeout(this.osdTimer);
        this.osdTimer = setTimeout(() => this.osdEl.classList.add('hidden'), OSD_MS);
    }

    // 라이브 채널은 방송이 바뀌면 부제(지금 프로그램)도 바꿔준다
    _tickLive() {
        const pb = this.pb;
        if (!pb || !pb.live || !pb.channel) return;
        const { cur } = pb.provider.nowNext(pb.channel);
        const sub = cur ? cur[2] : (pb.channel.group || '');
        this.subEl.textContent = sub;
        this.miniSub.textContent = sub;
    }

    // ------------------------------------------------------------------
    // 자막
    // ------------------------------------------------------------------
    /** 외부 텍스트 자막을 <track>으로 붙인다 (null이면 떼기). 엔진과 상관없이 같은 <video>에 붙는다. */
    _applyTrack(track) {
        const v = this.video;
        v.querySelectorAll('track').forEach((t) => t.remove());
        if (!track) return;
        const el = h('track', { kind: 'subtitles', src: track.src, label: track.label, srclang: track.lang || 'und', default: true });
        el.addEventListener('load', () => { el.track.mode = 'showing'; });
        el.addEventListener('error', () => toast('자막 파일을 불러오지 못했습니다.', 'error'));
        v.appendChild(el);
        el.track.mode = 'showing';
    }

    toggleSubMenu() {
        const pb = this.pb;
        if (!pb || !pb.subtitles || !pb.subtitles.length) return;
        this.optMenu.classList.add('hidden');
        const open = this.subMenu.classList.toggle('hidden') === false;
        if (open) this._renderSubMenu();
        this._wakeUi();
    }

    _renderSubMenu() {
        const pb = this.pb;
        clear(this.subList);
        const row = (id, label, note) => h('button', {
            class: `sub-item${pb.activeSubtitle === id ? ' active' : ''}`,
            on: { click: () => this.chooseSubtitle(id) },
        }, icon('check', 'sub-check'), h('span', { class: 'sub-label', text: label }), note ? h('span', { class: 'sub-note', text: note }) : null);
        this.subList.appendChild(row('0', '끄기'));
        for (const st of pb.subtitles) {
            this.subList.appendChild(row(st.id, st.label, st.text ? '' : '영상에 입힘'));
        }
        const size = storeGet('yp_cue_size', 'm');
        this.subSizes.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c.dataset.size === size));
    }

    async chooseSubtitle(id) {
        const pb = this.pb;
        this.subMenu.classList.add('hidden');
        if (!pb || !pb.selectSubtitle || id === pb.activeSubtitle) return;
        const at = this._pos();
        try {
            const res = await pb.selectSubtitle(id, at);
            if (this.pb !== pb) return; // 그 사이 다른 걸 틀었으면 무시
            if (res.reload) {
                toast('자막을 영상에 입히느라 다시 불러옵니다...');
                await this.start(res.reload);
            } else {
                pb.activeSubtitle = id;
                pb.textTrack = res.track;
                this._applyTrack(res.track);
                this._syncCc();
            }
        } catch (e) {
            toast(`자막 바꾸기 실패: ${e.message}`, 'error');
        }
    }

    // ------------------------------------------------------------------
    // 음성 트랙 / 화질 (Plex)
    // ------------------------------------------------------------------
    toggleOptMenu() {
        if (!this.pb) return;
        this.subMenu.classList.add('hidden');
        const open = this.optMenu.classList.toggle('hidden') === false;
        if (open) this._renderOptMenu();
        this._wakeUi();
    }

    _renderOptMenu() {
        const pb = this.pb;
        clear(this.optMenu);
        const row = (active, label, note, fn) => h('button', { class: `sub-item${active ? ' active' : ''}`, on: { click: fn } },
            icon('check', 'sub-check'), h('span', { class: 'sub-label', text: label }), note ? h('span', { class: 'sub-note', text: note }) : null);
        if (pb.audios && pb.audios.length > 1) {
            this.optMenu.appendChild(h('div', { class: 'sub-menu-head', text: '음성' }));
            this.optMenu.appendChild(h('div', { class: 'sub-list' }, pb.audios.map((a) =>
                row(a.id === pb.activeAudio, a.label, a.def ? '' : '변환 재생', () => this._reloadWith('selectAudio', a.id, a.id === pb.activeAudio)))));
        }
        if (pb.qualities && pb.qualities.length) {
            this.optMenu.appendChild(h('div', { class: 'sub-menu-head', text: `화질${pb.transcoding ? ' (Plex 변환 중)' : ' (원본 재생 중)'}` }));
            this.optMenu.appendChild(h('div', { class: 'sub-list' }, pb.qualities.map((q) =>
                row(q.id === pb.activeQuality, q.label, q.note || '', () => this._reloadWith('selectQuality', q.id, q.id === pb.activeQuality)))));
        }
    }

    async _reloadWith(method, value, same) {
        const pb = this.pb;
        this.optMenu.classList.add('hidden');
        if (!pb || !pb[method] || same) return;
        const at = this._pos();
        try {
            const next = (await pb[method](value, at)).reload;
            if (this.pb !== pb) return;
            toast(method === 'selectAudio' ? '음성을 바꿔 다시 불러옵니다...' : '화질을 바꿔 다시 불러옵니다...');
            await this.start(next);
        } catch (e) {
            toast(`바꾸기 실패: ${e.message}`, 'error');
        }
    }

    setCueSize(size) {
        storeSet('yp_cue_size', size);
        document.body.dataset.cueSize = size;
        this._renderSubMenu();
    }

    _syncCc() {
        const on = this.pb && this.pb.activeSubtitle && this.pb.activeSubtitle !== '0';
        this.ccBtn.classList.toggle('on', !!on);
    }

    toggleDrawer(force) {
        const open = this.drawer.classList.toggle('hidden', force === undefined ? undefined : !force) === false;
        this.root.classList.toggle('drawer-open', open);
        if (open) { this._renderDrawer(); this.drawerSearch.focus(); }
        else this.drawerSearch.blur(); // 검색칸에 포커스가 남으면 방향키 채널 전환이 막힌다
        this._wakeUi();
    }

    _renderDrawer() {
        const pb = this.pb;
        clear(this.drawerList);
        if (!pb || !pb.playlist) return;
        const q = this.drawerSearch.value.trim().toLowerCase();
        let activeEl = null;
        for (const { provider, channel } of pb.playlist) {
            if (q && !channel.name.toLowerCase().includes(q)) continue;
            const { cur, progress } = provider.nowNext(channel);
            const logo = channel.logo ? h('img', { src: channel.logo, alt: '', loading: 'lazy', on: { error: (e) => { e.target.style.visibility = 'hidden'; } } }) : icon('tv');
            const row = h('div', {
                class: `drawer-ch${channel.id === pb.channel.id ? ' active' : ''}${channelHealth.get(channel.id) === 'offline' ? ' offline' : ''}`,
                on: { click: () => this.playChannel(provider, channel, pb.playlist) },
            },
            h('div', { class: 'ch-logo' }, logo),
            h('div', { class: 'drawer-ch-info' },
                h('b', { text: channel.name }),
                h('span', { text: cur ? cur[2] : (channel.group || '') }),
                cur ? h('div', { class: 'mini-track' }, h('div', { style: { width: `${progress * 100}%` } })) : null));
            if (channel.id === pb.channel.id) activeEl = row;
            this.drawerList.appendChild(row);
        }
        if (activeEl) activeEl.scrollIntoView({ block: 'center' });
    }

    _onKey(e) {
        if (!this.pb && !this.minimized && e.key === 'Escape' && !this.root.classList.contains('hidden')) {
            this.stop(); // 준비 중에 Esc
            return;
        }
        if (!this.pb || this.minimized) return;
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag) && e.key !== 'Escape') return;
        const live = this.pb.live;
        const k = e.key;
        if (k === ' ' || k === 'k') { e.preventDefault(); this.togglePlay(); }
        else if (k === 'ArrowLeft' && !live) { e.preventDefault(); this.seekBy(-10); }
        else if (k === 'ArrowRight' && !live) { e.preventDefault(); this.seekBy(30); }
        else if (k === 'ArrowUp') { e.preventDefault(); if (live) this.switchChannel(-1); else this.video.volume = Math.min(1, this.video.volume + 0.1); }
        else if (k === 'ArrowDown') { e.preventDefault(); if (live) this.switchChannel(1); else this.video.volume = Math.max(0, this.video.volume - 0.1); }
        else if (k === 'f' || k === 'F') this.toggleFullscreen();
        else if (k === 'm' || k === 'M') this.toggleMute();
        else if (k === 'i' || k === 'I') this.showOsd();
        else if ((k === 'c' || k === 'C') && !live) this.toggleSubMenu();
        else if (k === 'Escape' && !document.fullscreenElement) {
            if (!this.subMenu.classList.contains('hidden')) this.subMenu.classList.add('hidden');
            else if (!this.optMenu.classList.contains('hidden')) this.optMenu.classList.add('hidden');
            else if (!this.drawer.classList.contains('hidden')) this.toggleDrawer(false);
            else this.minimize();
        }
        this._wakeUi();
    }
}

export const player = new Player();
