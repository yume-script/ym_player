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

class Player {
    constructor() {
        this.pb = null;
        this.token = 0;
        this.attemptSeq = 0;
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
                    btn('clone', '화면 속 화면', () => this.togglePip()),
                    this.fsBtn,
                    btn('xmark', '재생 종료', () => this.stop()))));

        this.drawerSearch = h('input', { class: 'input', type: 'search', placeholder: '채널 찾기' });
        this.drawerList = h('div', { class: 'drawer-list' });
        this.drawer = h('aside', { class: 'ch-drawer hidden', on: { click: (e) => e.stopPropagation() } },
            h('div', { class: 'drawer-head' }, h('b', { text: '채널' }), this.drawerSearch), this.drawerList);

        this.root = h('div', { class: 'player hidden' }, this.stage, this.top, this.bottom, this.drawer);

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
            this.video.currentTime = ratioAt(e) * this._duration();
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
    async start(pb, playlist = null) {
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
        this._syncFav();
        this._showMsg(`"${pb.title}" 연결 중...`, { spinner: true });
        if (pb.live) this.showOsd();
        if (!this.drawer.classList.contains('hidden')) this._renderDrawer();

        if (this.needsRecreate) { this.needsRecreate = false; this._recreateVideo(); }
        this._destroyEngines();
        this.video.removeAttribute('src');
        this.video.load();

        let label;
        try {
            label = await this._attempt(pb.url, pb.kind, token, pb.startTime || 0, !!pb.live);
        } catch (e1) {
            if (token !== this.token) return;
            if (!pb.proxyUrl) return this._failed(e1);
            console.warn('[Player] 직접 재생 실패, 서버 중계로 다시 시도:', e1.message);
            this._showMsg('서버 중계로 다시 연결하는 중...', { spinner: true });
            try {
                label = (await this._attempt(pb.proxyUrl, pb.kind, token, pb.startTime || 0, !!pb.live)) + ' · 중계';
            } catch (e2) {
                if (token !== this.token) return;
                return this._failed(e2);
            }
        }
        if (token !== this.token) return;
        this.engineEl.textContent = label;
        this._hideMsg();
        if (pb.channel) channelHealth.set(pb.channel.id, 'online');
        this._wakeUi();
    }

    retry() {
        if (!this.pb) return;
        if (!this.pb.live) this.pb.startTime = this.video.currentTime || this.pb.startTime || 0;
        this.start(this.pb);
    }

    stop() {
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
            try { pb.onStop(this.video.currentTime || 0); } catch (e) { /* 알림 실패는 무시 */ }
        }
    }

    _failed(err) {
        console.warn('[Player] 재생 실패:', err && err.message);
        if (this.pb && this.pb.channel) channelHealth.set(this.pb.channel.id, 'offline');
        // 실패한 MSE가 같은 <video>에 남아 다음 채널까지 망가뜨리는 브라우저 버그 대응 (원래 플러그인 참고)
        this.needsRecreate = true;
        this._destroyEngines();
        this._showMsg(this.pb && this.pb.live ? '스트림에 연결할 수 없습니다 (오프라인 또는 차단).' : `재생할 수 없습니다: ${err && err.message ? err.message : err}`,
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
                } else if (engineError) {
                    this._runtimeError(err);
                }
            };

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
                    lowLatencyMode: live,
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

    _runtimeError(err) {
        console.warn('[Player] 재생 중 오류:', err && err.message);
        this._destroyEngines(true);
        this.needsRecreate = true;
        this._showMsg('재생이 끊겼습니다.', { error: true });
    }

    _needsGesture() {
        this._showMsg('화면을 눌러 재생을 시작하세요.', {});
        this.video.addEventListener('playing', () => this._hideMsg(), { once: true });
    }

    // later=true: 엔진 자신의 오류 콜백 안에서 부를 때. 그 자리에서 destroy하면 hls.js가
    // 남은 처리 중에 null을 건드려 예외를 내므로, 참조만 바로 끊고 실제 정리는 다음 틱에 한다.
    _destroyEngines(later = false) {
        const hls = this.hls, ts = this.mpegts;
        this.hls = null;
        this.mpegts = null;
        if (this._detachDirect) { this._detachDirect(); this._detachDirect = null; }
        const run = () => {
            if (hls) { try { hls.destroy(); } catch (e) { /* 이미 깨짐 */ } }
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
        const v = this.video;
        v.currentTime = Math.max(0, Math.min(this._duration() || Infinity, v.currentTime + sec));
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
        return this.start(provider.getPlayback(channel), playlist);
    }

    async playItem(provider, id, opts) {
        try {
            const pb = await provider.getPlayback(id, opts);
            await this.start(pb);
        } catch (e) {
            toast(`재생 준비 실패: ${e.message}`, 'error');
        }
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
        const d = this.video.duration;
        if (isFinite(d) && d > 0) return d;
        return (this.pb && this.pb.durationHint) || 0;
    }

    _onTime() {
        const v = this.video;
        if (!this.pb) return;
        if (!this.dragging) {
            const d = this._duration();
            this.curEl.textContent = fmtClock(v.currentTime);
            this.durEl.textContent = fmtClock(d);
            this.playedEl.style.width = d ? `${(v.currentTime / d) * 100}%` : '0';
        }
        if (this.pb.onProgress && !v.paused && Math.abs(v.currentTime - this.lastReport) >= PROGRESS_EVERY_SEC) {
            this.lastReport = v.currentTime;
            this.pb.onProgress('playing', v.currentTime);
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
        this.bufEl.style.width = `${(v.buffered.end(v.buffered.length - 1) / d) * 100}%`;
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
            this.playItem(provider, next.id, { fromStart: true });
        } catch (e) { /* 다음 화가 없으면 그냥 끝 */ }
    }

    _syncPlayState() {
        const paused = this.video.paused;
        const cls = paused ? 'fa-solid fa-play' : 'fa-solid fa-pause';
        this.playBtn.firstChild.className = cls;
        this.miniPlayBtn.firstChild.className = cls;
        this.root.classList.toggle('paused', paused);
        if (paused && this.pb && this.pb.onProgress && !this.pb.live && this.video.currentTime > 0) {
            this.pb.onProgress('paused', this.video.currentTime);
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
            if (!this.video.paused && this.drawer.classList.contains('hidden') && !this.dragging) {
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
        else if (k === 'Escape' && !document.fullscreenElement) {
            if (!this.drawer.classList.contains('hidden')) this.toggleDrawer(false);
            else this.minimize();
        }
        this._wakeUi();
    }
}

export const player = new Player();
