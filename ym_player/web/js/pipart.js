// 라디오(소리만 있는 방송)의 "화면 속 화면". PiP 창은 <video>의 화면을 보여 주는데 라디오는 영상이 없어서
// 검은 화면만 나왔다. 앨범 표지 + 시각화 + 방송국·곡 이름을 캔버스에 그리고, 그 캔버스를 영상 스트림
// (canvas.captureStream)으로 만든 작은 <video>를 대신 PiP로 띄운다. 소리는 원래 <video>에서 그대로 나온다.
// PiP 창의 재생/일시정지 버튼은 이 <video>에 오므로 원래 재생으로 넘겨 준다.
import { h } from './util.js';
import { streamProxyUrl } from './providers/iptv.js';

const SIZE = 480;   // 정사각형 (앨범 표지 모양)
const FPS = 20;

export class PipArt {
    constructor(player) {
        this.player = player;
        this.canvas = h('canvas', { width: SIZE, height: SIZE });
        this.g = this.canvas.getContext('2d');
        this.video = h('video', { class: 'pip-art-video', muted: true, playsinline: true });
        this.video.muted = true;
        this.timer = 0;
        this.active = false;
        this.art = { url: '', img: null };
        this._mirroring = false;
        this.video.addEventListener('enterpictureinpicture', () => { this.active = true; player.inPip = false; player._onPipEnter(); });
        this.video.addEventListener('leavepictureinpicture', () => this._left());
        // Safari(아이패드)의 PiP는 webkitpresentationmodechanged로 알려 준다
        watchWebkitPip(this.video, () => { this.active = true; player.inPip = false; player._onPipEnter(); }, () => this._left());
        // PiP 창의 재생/일시정지 -> 원래 재생
        this.video.addEventListener('pause', () => { if (this.active && !this._mirroring && !player.video.paused) player.video.pause(); });
        this.video.addEventListener('play', () => { if (this.active && !this._mirroring && player.video.paused) player.video.play().catch(() => {}); });
    }

    static supported() {
        return !!(HTMLCanvasElement.prototype.captureStream && pipAvailable());
    }

    _left() {
        if (!this.active) return;
        this.active = false;
        this._stop();
        this.player._onPipLeave();
    }

    /** 원래 재생이 멈추거나 다시 나오면 PiP 창의 버튼 모양도 맞춘다 */
    syncPlaying(playing) {
        if (!this.active) return;
        this._mirroring = true;
        const done = () => { this._mirroring = false; };
        if (playing) this.video.play().then(done, done); else { this.video.pause(); done(); }
    }

    async open() {
        this._draw();
        if (!this.stream) this.stream = this.canvas.captureStream(FPS);
        if (this.video.srcObject !== this.stream) this.video.srcObject = this.stream;
        if (!this.video.isConnected) document.body.appendChild(this.video);
        this._start();
        if (!document.pictureInPictureEnabled) {
            // Safari 전용 PiP는 누른 그 순간(기다리지 않고) 불러야 받아 준다
            const playing = this.video.play();
            await requestPip(this.video);
            await playing;
            return;
        }
        await this.video.play();
        await requestPip(this.video);
    }

    _start() {
        if (!this.timer) this.timer = setInterval(() => this._draw(), 1000 / FPS);
    }

    _stop() {
        clearInterval(this.timer);
        this.timer = 0;
    }

    /** 앨범 표지는 서버 중계로 받아 같은 사이트 그림으로 (다른 사이트 그림을 그리면 캔버스를 영상으로 못 내보냄) */
    _artImage(url) {
        if (url === this.art.url) return this.art.img;
        this.art = { url, img: null };
        if (!url) return null;
        const img = new Image();
        img.onload = () => { if (this.art.url === url) this.art.img = img; };
        img.src = /^https?:\/\//i.test(url) && !url.startsWith(location.origin) ? streamProxyUrl(url) : url;
        return null;
    }

    _draw() {
        const g = this.g, p = this.player, pb = p.pb;
        g.fillStyle = '#0b0c0e';
        g.fillRect(0, 0, SIZE, SIZE);
        const img = this._artImage((pb && pb.poster) || '');
        if (img) {
            const s = Math.max(SIZE / img.width, SIZE / img.height);
            const w = img.width * s, hgt = img.height * s;
            g.drawImage(img, (SIZE - w) / 2, (SIZE - hgt) / 2, w, hgt);
            // 시각화와 글자가 잘 보이게 아래쪽을 어둡게
            const grad = g.createLinearGradient(0, SIZE * 0.35, 0, SIZE);
            grad.addColorStop(0, 'rgba(0,0,0,0)');
            grad.addColorStop(1, 'rgba(0,0,0,0.78)');
            g.fillStyle = grad;
            g.fillRect(0, 0, SIZE, SIZE);
        }
        // 시각화는 아래쪽 절반에
        g.save();
        g.translate(0, SIZE * 0.42);
        p.viz.drawTo(g, SIZE, SIZE * 0.42);
        g.restore();
        // 방송국 / 지금 나오는 곡
        const title = p.titleEl.textContent || '', sub = p.subEl.textContent || '';
        g.fillStyle = '#fff';
        g.font = '700 26px "Noto Sans KR", system-ui, sans-serif';
        g.textBaseline = 'alphabetic';
        g.fillText(clip(g, title, SIZE - 40), 20, SIZE - (sub ? 52 : 24));
        if (sub) {
            g.fillStyle = 'rgba(255,255,255,0.8)';
            g.font = '500 20px "Noto Sans KR", system-ui, sans-serif';
            g.fillText(clip(g, sub, SIZE - 40), 20, SIZE - 22);
        }
    }
}

/** 표준 PiP(Chrome·Edge·Mac Safari) 또는 Safari 전용 PiP(아이패드·아이폰: webkitSetPresentationMode) */
function pipAvailable() {
    if (document.pictureInPictureEnabled) return true;
    const v = HTMLVideoElement.prototype;
    return typeof v.webkitSetPresentationMode === 'function';
}

export async function requestPip(video) {
    if (document.pictureInPictureEnabled && video.requestPictureInPicture) return video.requestPictureInPicture();
    if (video.webkitSupportsPresentationMode && video.webkitSupportsPresentationMode('picture-in-picture')) {
        video.webkitSetPresentationMode('picture-in-picture');
        return undefined;
    }
    throw new Error('화면 속 화면 기능이 없습니다');
}

/** Safari 전용 PiP의 들어감/나옴 (표준 PiP를 쓰는 브라우저는 표준 이벤트가 따로 온다) */
export function watchWebkitPip(video, onEnter, onLeave) {
    if (document.pictureInPictureEnabled) return;
    let was = false;
    video.addEventListener('webkitpresentationmodechanged', () => {
        const now = video.webkitPresentationMode === 'picture-in-picture';
        if (now && !was) onEnter();
        if (!now && was) onLeave();
        was = now;
    });
}

function clip(g, text, max) {
    if (g.measureText(text).width <= max) return text;
    let t = text;
    while (t.length > 1 && g.measureText(`${t}…`).width > max) t = t.slice(0, -1);
    return `${t}…`;
}
