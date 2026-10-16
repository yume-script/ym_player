// 라디오 재생 화면의 시각화와 LP 잡음 (Web Audio).
//
// - 재생 중인 <video>의 소리를 AudioContext로 끌어와 분석기(Analyser)에 물리고 캔버스에 그린다.
//   브라우저는 다른 사이트에서 온 소리는 분석하지 못하게 막으므로(값이 전부 0), 라디오는 서버 중계(/api/stream)로
//   같은 사이트 주소에서 받는다. 그래서 Safari(아이패드)에서도 음악에 반응한다.
// - 한 번 AudioContext에 물린 <video>는 계속 그쪽으로 소리가 나간다. 라디오가 아닌 걸 틀 때는 재생기가
//   <video>를 새로 만들어(isRouted) 원래대로 돌린다.
// - LP 잡음: 히스(분홍빛 잡음)와 탁탁 튀는 소리를 20초 분량 만들어 반복한다. 재생 중일 때만 들린다.
import { IS_IOS, storeGet, storeSet } from './util.js';

export const MODES = [
    { id: 'bars', label: '스펙트럼' },
    { id: 'mirror', label: '거울 막대' },
    { id: 'wave', label: '파형' },
    { id: 'vu', label: 'VU 미터' },
    { id: 'bokeh', label: '보케 입자' },
    { id: 'off', label: '끄기' },
];
const MODE_KEY = 'yp_viz_mode';
const LP_KEY = 'yp_lp_noise';
const IOS_LIVE_KEY = 'yp_viz_live_ios';

export class Visualizer {
    constructor(canvas) {
        this.canvas = canvas;
        this.g = canvas.getContext('2d');
        this.mode = storeGet(MODE_KEY, 'bars');
        if (!MODES.some((m) => m.id === this.mode)) this.mode = 'bars';
        this.lp = { on: false, level: 30, ...storeGet(LP_KEY, {}) };
        // 아이폰·아이패드는 소리를 AudioContext로 끌어가면 화면을 끄거나 다른 앱으로 갈 때 함께 멈출 수 있다.
        // 그래서 기본은 끌어가지 않고(백그라운드 재생 우선, 시각화는 기본 움직임), 원하는 사람만 켠다.
        this.iosLive = !!storeGet(IOS_LIVE_KEY, false);
        this.ctx = null;
        this.el = null;          // AudioContext에 물린 <video>
        this.active = false;     // 라디오를 재생 중 (LP 잡음은 미니 플레이어로 접어도 계속)
        this.visible = true;     // 큰 재생 화면이 보임 (그릴 때만)
        this.playing = false;
        this.raf = 0;
        this.silentSince = 0;
        this.needles = [0, 0];
        this.particles = [];
        this.accent = '#e5a00d';
    }

    /** 사용자가 누른 그 순간(동기)에 불러야 한다 - 브라우저가 AudioContext를 소리 나게 해 주는 조건 */
    prime() {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!this.ctx) {
            this.ctx = new AC();
            this.master = this.ctx.createGain();
            this.master.connect(this.ctx.destination);
        }
        if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    }

    isRouted(el) { return !!el && this.el === el; }

    /** 이번 재생에서 소리를 분석기로 끌어갈지 */
    wantsRoute() { return !IS_IOS || this.iosLive; }

    setIosLive(on) {
        this.iosLive = !!on;
        storeSet(IOS_LIVE_KEY, this.iosLive);
    }

    setMode(id) {
        if (!MODES.some((m) => m.id === id)) return;
        this.mode = id;
        storeSet(MODE_KEY, id);
        this.canvas.classList.toggle('hidden', !this.active || this.mode === 'off');
        this._loop();
    }

    /** 이 <video>를 분석기에 문다 (같은 요소면 그대로) */
    attach(el) {
        this.prime();
        if (!this.ctx || this.el === el) return;
        try {
            const src = this.ctx.createMediaElementSource(el);
            const split = this.ctx.createChannelSplitter(2);
            this.analyser = this.ctx.createAnalyser();
            this.analyser.fftSize = 2048;
            this.analyser.smoothingTimeConstant = 0.78;
            this.left = this.ctx.createAnalyser();
            this.right = this.ctx.createAnalyser();
            this.left.fftSize = this.right.fftSize = 1024;
            src.connect(this.analyser);
            src.connect(split);
            split.connect(this.left, 0);
            split.connect(this.right, 1);
            src.connect(this.master);
            this.el = el;
            this.freq = new Uint8Array(this.analyser.frequencyBinCount);
            this.time = new Uint8Array(this.analyser.fftSize);
            this.lt = new Uint8Array(this.left.fftSize);
            this.rt = new Uint8Array(this.right.fftSize);
        } catch (e) {
            console.warn('[시각화] 소리를 연결하지 못했습니다:', e.message);
        }
    }

    show(on) {
        this.active = on;
        this.canvas.classList.toggle('hidden', !on || this.mode === 'off');
        this._loop();
        this._syncNoise();
    }

    setVisible(v) {
        this.visible = v;
        this._loop();
    }

    setPlaying(playing) {
        this.playing = playing;
        this._syncNoise();
        this._loop();
    }

    nextMode() {
        const i = MODES.findIndex((m) => m.id === this.mode);
        this.mode = MODES[(i + 1) % MODES.length].id;
        storeSet(MODE_KEY, this.mode);
        this.canvas.classList.toggle('hidden', !this.active || this.mode === 'off');
        this._loop();
        return MODES.find((m) => m.id === this.mode);
    }

    // ------------------------------------------------------------------ LP 잡음
    setNoise(on, level = this.lp.level) {
        this.lp = { on, level: Math.max(0, Math.min(100, Number(level) || 0)) };
        storeSet(LP_KEY, this.lp);
        if (on) this.prime();
        this._syncNoise();
    }

    _noiseBuffer() {
        const rate = this.ctx.sampleRate, len = rate * 20;
        const buf = this.ctx.createBuffer(2, len, rate);
        for (let ch = 0; ch < 2; ch++) {
            const d = buf.getChannelData(ch);
            // 분홍빛 잡음 (Paul Kellet 근사) - 낮게 깔리는 히스
            let b0 = 0, b1 = 0, b2 = 0;
            for (let i = 0; i < len; i++) {
                const w = Math.random() * 2 - 1;
                b0 = 0.99765 * b0 + w * 0.0990460;
                b1 = 0.96300 * b1 + w * 0.2965164;
                b2 = 0.57000 * b2 + w * 1.0526913;
                d[i] = (b0 + b1 + b2 + w * 0.1848) * 0.018;
            }
            // 탁탁 튀는 소리: 짧게 감쇠하는 충격 (초당 평균 3~4개, 가끔 큰 것)
            const pops = Math.floor(20 * (3 + Math.random()));
            for (let p = 0; p < pops; p++) {
                const at = Math.floor(Math.random() * (len - 400));
                const amp = (Math.random() < 0.12 ? 0.55 : 0.18) * (Math.random() < 0.5 ? -1 : 1);
                const decay = 40 + Math.random() * 160;
                for (let k = 0; k < 400; k++) d[at + k] += amp * Math.exp(-k / decay) * (Math.random() * 0.6 + 0.7);
            }
        }
        return buf;
    }

    _syncNoise() {
        const want = this.active && this.playing && this.lp.on && this.ctx;
        if (!want) {
            if (this.noiseSrc) { try { this.noiseSrc.stop(); } catch (e) { /* 이미 멈춤 */ } this.noiseSrc = null; }
            return;
        }
        if (!this.noiseGain) {
            this.noiseGain = this.ctx.createGain();
            this.noiseGain.connect(this.ctx.destination);
        }
        // 강도 0~100% -> 0~0.9배, 재생기 음량을 따라간다
        const vol = this.el ? (this.el.muted ? 0 : this.el.volume) : 1;
        this.noiseGain.gain.value = (this.lp.level / 100) * 0.9 * vol;
        if (!this.noiseSrc) {
            this.noiseSrc = this.ctx.createBufferSource();
            this.noiseSrc.buffer = this._noiseBuffer(); // 다시 켤 때마다 새로 만들어 같은 소리가 반복되지 않게
            this.noiseSrc.loop = true;
            this.noiseSrc.connect(this.noiseGain);
            this.noiseSrc.start();
        }
    }

    syncVolume() { this._syncNoise(); }

    // ------------------------------------------------------------------ 그리기
    _loop() {
        const want = this.active && this.visible && this.mode !== 'off' && !document.hidden;
        if (want && !this.raf) {
            const step = () => {
                this.raf = 0;
                if (!(this.active && this.visible && this.mode !== 'off')) return;
                this._draw();
                this.raf = requestAnimationFrame(step);
            };
            this.raf = requestAnimationFrame(step);
        } else if (!want && this.raf) {
            cancelAnimationFrame(this.raf);
            this.raf = 0;
        }
    }

    /** 분석 값 (분석이 막혀 전부 0이면 잔잔한 가짜 값으로) */
    _read() {
        const now = performance.now();
        if (this.analyser) {
            this.analyser.getByteFrequencyData(this.freq);
            this.analyser.getByteTimeDomainData(this.time);
            this.left.getByteTimeDomainData(this.lt);
            this.right.getByteTimeDomainData(this.rt);
        }
        let sum = 0;
        if (this.freq) for (let i = 0; i < this.freq.length; i += 8) sum += this.freq[i];
        if (this.playing && sum === 0) {
            if (!this.silentSince) this.silentSince = now;
        } else {
            this.silentSince = 0;
        }
        this.fake = this.playing && this.silentSince && now - this.silentSince > 2000;
        if (!this.freq || this.fake || !this.playing) {
            const n = 1024, t = now / 1000, amp = this.playing ? 1 : 0.15;
            this.freq = this.freq || new Uint8Array(n);
            this.time = this.time || new Uint8Array(2048);
            for (let i = 0; i < this.freq.length; i++) {
                const f = i / this.freq.length;
                this.freq[i] = amp * Math.max(0, 140 * (1 - f) * (0.6 + 0.4 * Math.sin(t * 2.1 + i * 0.07)) * (0.7 + 0.3 * Math.sin(t * 0.7 + f * 9)));
            }
            for (let i = 0; i < this.time.length; i++) this.time[i] = 128 + amp * 30 * Math.sin(t * 3 + i * 0.02) * Math.sin(i * 0.005 + t);
        }
    }

    _level(arr) {
        if (!arr || this.fake || !this.playing) return this.playing ? 0.25 + 0.15 * Math.sin(performance.now() / 300) : 0;
        let s = 0;
        for (let i = 0; i < arr.length; i++) { const v = (arr[i] - 128) / 128; s += v * v; }
        return Math.min(1, Math.sqrt(s / arr.length) * 3.2);
    }

    _draw() {
        const c = this.canvas, g = this.g;
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
        if (!w || !h) return;
        if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
        this.accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || this.accent;
        this._read();
        g.clearRect(0, 0, w, h);
        ({ bars: this._bars, mirror: this._mirror, wave: this._wave, vu: this._vu, bokeh: this._bokeh })[this.mode]
            .call(this, g, w, h, dpr);
    }

    /** 낮은 소리를 넓게 보이도록 로그 간격으로 n개 묶은 막대 높이 (0~1) */
    _bands(n) {
        const out = new Array(n), len = this.freq.length * 0.72;
        for (let i = 0; i < n; i++) {
            const a = Math.floor(Math.pow(len, i / n)), b = Math.max(a + 1, Math.floor(Math.pow(len, (i + 1) / n)));
            let m = 0;
            for (let k = a; k < b; k++) m = Math.max(m, this.freq[k] || 0);
            out[i] = m / 255;
        }
        return out;
    }

    _bars(g, w, h, dpr) {
        const n = Math.max(24, Math.min(72, Math.floor(w / (14 * dpr)))), bands = this._bands(n);
        const gap = 3 * dpr, bw = (w - gap * (n - 1)) / n, base = h * 0.92;
        const grad = g.createLinearGradient(0, base - h * 0.55, 0, base);
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(1, this.accent);
        g.fillStyle = grad;
        bands.forEach((v, i) => {
            const bh = Math.max(2 * dpr, v * h * 0.55);
            g.globalAlpha = 0.55 + v * 0.45;
            g.fillRect(i * (bw + gap), base - bh, bw, bh);
        });
        g.globalAlpha = 1;
    }

    _mirror(g, w, h, dpr) {
        const n = Math.max(20, Math.min(56, Math.floor(w / (18 * dpr)))), bands = this._bands(n);
        const mid = h * 0.5, gap = 4 * dpr, bw = (w / 2 - gap * n) / n;
        g.fillStyle = this.accent;
        bands.forEach((v, i) => {
            const bh = Math.max(2 * dpr, v * h * 0.32);
            g.globalAlpha = 0.35 + v * 0.65;
            for (const x of [w / 2 + i * (bw + gap) + gap / 2, w / 2 - (i + 1) * (bw + gap) + gap / 2]) {
                g.fillRect(x, mid - bh, bw, bh * 2);
            }
        });
        g.globalAlpha = 1;
    }

    _wave(g, w, h, dpr) {
        const t = this.time, mid = h * 0.5;
        for (const [alpha, width, color] of [[0.25, 8, this.accent], [1, 2, '#ffffff']]) {
            g.globalAlpha = alpha;
            g.lineWidth = width * dpr;
            g.strokeStyle = color;
            g.lineJoin = 'round';
            g.beginPath();
            for (let i = 0; i < t.length; i += 4) {
                const x = (i / (t.length - 1)) * w, y = mid + ((t[i] - 128) / 128) * h * 0.38;
                if (i) g.lineTo(x, y); else g.moveTo(x, y);
            }
            g.stroke();
        }
        g.globalAlpha = 1;
    }

    _vu(g, w, h, dpr) {
        const levels = [this._level(this.lt), this._level(this.rt)];
        if (levels[1] < 0.01 && levels[0] > 0.02) levels[1] = levels[0]; // 모노 방송은 오른쪽 채널이 비어 있다
        // 앨범 표지를 가리지 않게 아래쪽에 작게 두 개
        const mw = Math.min(w * 0.34, h * 0.3 * 1.6), mh = mw / 1.6, gap = w * 0.03;
        levels.forEach((lv, k) => {
            // 바늘은 빨리 오르고 천천히 내려온다
            this.needles[k] += (lv - this.needles[k]) * (lv > this.needles[k] ? 0.35 : 0.08);
            const x = w / 2 + (k ? gap / 2 : -gap / 2 - mw), y = h * 0.86 - mh;
            g.fillStyle = '#efe3c2';
            g.strokeStyle = '#3b2f1e';
            g.lineWidth = 3 * dpr;
            g.beginPath();
            g.roundRect ? g.roundRect(x, y, mw, mh, 10 * dpr) : g.rect(x, y, mw, mh);
            g.fill();
            g.stroke();
            const cx = x + mw / 2, cy = y + mh * 0.92, r = mh * 0.72;
            const ang = (v) => Math.PI * (1.25 + 0.5 * v);
            g.lineWidth = 2 * dpr;
            for (let s = 0; s <= 10; s++) {
                const v = s / 10, a = ang(v);
                g.strokeStyle = v > 0.75 ? '#b3261e' : '#3b2f1e';
                g.beginPath();
                g.moveTo(cx + Math.cos(a) * r * 0.86, cy + Math.sin(a) * r * 0.86);
                g.lineTo(cx + Math.cos(a) * r * (s % 5 ? 0.93 : 0.98), cy + Math.sin(a) * r * (s % 5 ? 0.93 : 0.98));
                g.stroke();
            }
            g.fillStyle = '#3b2f1e';
            g.font = `${Math.round(mh * 0.1)}px ui-monospace, monospace`;
            g.textAlign = 'center';
            g.fillText(`VU ${k ? 'R' : 'L'}`, cx, y + mh * 0.62);
            const a = ang(Math.min(1, this.needles[k]));
            g.strokeStyle = '#1b1b1b';
            g.lineWidth = 2.5 * dpr;
            g.beginPath();
            g.moveTo(cx, cy);
            g.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
            g.stroke();
            g.fillStyle = this.playing ? '#ff5b4a' : '#5a1e19';   // 재생 표시등
            g.beginPath();
            g.arc(x + mw - 14 * dpr, y + 14 * dpr, 5 * dpr, 0, Math.PI * 2);
            g.fill();
        });
    }

    _bokeh(g, w, h, dpr) {
        const bands = this._bands(16);
        const bass = (bands[0] + bands[1] + bands[2]) / 3, mid = (bands[5] + bands[6] + bands[7]) / 3;
        const want = Math.round(Math.min(90, (w * h) / (16000 * dpr * dpr)));
        while (this.particles.length < want) {
            this.particles.push({ x: Math.random(), y: Math.random(), r: 0.5 + Math.random(), s: Math.random() * 0.4 + 0.1,
                hue: [38, 200, 320, 160][Math.floor(Math.random() * 4)], ph: Math.random() * 6.28 });
        }
        this.particles.length = want;
        g.globalCompositeOperation = 'lighter';
        const t = performance.now() / 1000;
        for (const p of this.particles) {
            p.y -= 0.0006 * p.s * (1 + bass * 3);
            p.x += 0.0003 * Math.sin(t * 0.5 + p.ph);
            if (p.y < -0.1) { p.y = 1.1; p.x = Math.random(); }
            const r = (6 + p.r * 18 * (0.6 + bass * 1.4)) * dpr;
            const grad = g.createRadialGradient(p.x * w, p.y * h, 0, p.x * w, p.y * h, r);
            grad.addColorStop(0, `hsla(${p.hue}, 80%, 75%, ${0.35 + mid * 0.5})`);
            grad.addColorStop(1, `hsla(${p.hue}, 80%, 60%, 0)`);
            g.fillStyle = grad;
            g.beginPath();
            g.arc(p.x * w, p.y * h, r, 0, Math.PI * 2);
            g.fill();
        }
        g.globalCompositeOperation = 'source-over';
    }
}
