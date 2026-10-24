// 화면 아래쪽에 흐르는 글 - 관리자가 설정 > 디스코드에서 정한 채널에 새 글이 올라오면 (서버: discord_feed.py)
// 글 하나를 오른쪽에서 왼쪽으로 정한 횟수만큼 흘리고 다음 글로, 다 흘리면 막대를 내린다.
// 마우스를 올리면 멈추고, [x]는 지금 쌓인 글을 넘긴다. 설정 > 화면에서 이 브라우저만 끌 수 있다(yp_ticker_off).
import { h, icon, api, storeGet } from './util.js';

const POLL_MS = 15000;
const POLL_OFF_MS = 60000;   // 관리자가 꺼 두었으면 가끔만 (켜면 알아채게)
export const TICKER_OFF_KEY = 'yp_ticker_off';

class Ticker {
    constructor() {
        this.queue = [];
        this.after = null;
        this.boot = null;
        this.repeat = 2;
        this.speed = 90;   // 초당 px
        this.running = false;

        this.label = h('span', { class: 'ticker-label' });
        this.text = h('span', { class: 'ticker-text' });
        this.track = h('div', { class: 'ticker-track' }, this.text);
        this.count = h('span', { class: 'ticker-count hidden' });
        this.el = h('div', { class: 'ticker', role: 'status', 'aria-live': 'polite' },
            h('span', { class: 'ticker-icon' }, icon('comment-dots')), this.label, this.track, this.count,
            h('button', { class: 'ticker-close', title: '지금 쌓인 글 넘기기', on: { click: () => this.skip() } }, icon('xmark')));
        document.body.appendChild(this.el);
        this.el.addEventListener('mouseenter', () => { if (this.anim) this.anim.pause(); });
        this.el.addEventListener('mouseleave', () => { if (this.anim && this.anim.playState === 'paused') this.anim.play(); });
        // 재생기를 전체화면으로 보면 그 안에서도 보이게 (전체화면 요소 밖은 안 보인다)
        document.addEventListener('fullscreenchange', () => {
            (document.fullscreenElement || document.body).appendChild(this.el);
        });
        document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') this.poll(); });
        this.poll();
    }

    get off() {
        return !!storeGet(TICKER_OFF_KEY, false);
    }

    async poll() {
        clearTimeout(this.timer);
        let wait = POLL_MS;
        if (document.visibilityState === 'visible') {
            try {
                const p = new URLSearchParams();
                if (this.after != null) { p.set('after', this.after); p.set('boot', this.boot); }
                const d = await api(`/api/ticker?${p}`);
                if (this.boot != null && d.boot !== this.boot) this.after = null;   // 서버를 다시 켬
                this.boot = d.boot;
                this.after = d.latest;
                this.repeat = d.repeat || 2;
                this.speed = d.speed || 90;
                if (!d.enabled) wait = POLL_OFF_MS;
                if (d.items.length && !this.off) {
                    this.queue.push(...d.items);
                    this.queue = this.queue.slice(-30);   // 한꺼번에 너무 많이 오면 최근 것만
                    if (!this.running) this.next();
                    else this.syncCount();
                }
            } catch (e) {
                wait = POLL_OFF_MS;   // 로그인이 풀렸거나 서버가 꺼짐
            }
        }
        this.timer = setTimeout(() => this.poll(), wait);
    }

    syncCount() {
        this.count.textContent = `+${this.queue.length}`;
        this.count.classList.toggle('hidden', !this.queue.length);
    }

    next() {
        const item = this.queue.shift();
        if (!item || this.off) {
            this.running = false;
            this.el.classList.remove('show');
            document.body.classList.remove('ticker-on');
            return;
        }
        this.running = true;
        this.syncCount();
        this.label.textContent = item.channel || '';
        this.text.replaceChildren(
            item.author ? h('b', { class: 'ticker-author', text: item.author }) : '',
            h('span', { text: item.text }));
        this.el.classList.add('show');
        document.body.classList.add('ticker-on');
        // 오른쪽 끝에서 들어와 왼쪽 끝으로 다 나갈 때까지, 정한 횟수만큼
        requestAnimationFrame(() => {
            const from = this.track.clientWidth, to = -this.text.scrollWidth;
            const dur = ((from - to) / this.speed) * 1000;
            if (this.anim) this.anim.cancel();
            this.anim = this.text.animate([{ transform: `translateX(${from}px)` }, { transform: `translateX(${to}px)` }],
                { duration: dur, iterations: this.repeat, easing: 'linear' });
            this.anim.onfinish = () => this.next();
        });
    }

    skip() {
        this.queue = [];
        if (this.anim) this.anim.cancel();
        this.next();
    }

    /** 설정 > 화면에서 끄고 켤 때 */
    refresh() {
        if (this.off) this.skip();
    }
}

let ticker = null;
export function initTicker() {
    if (!ticker) ticker = new Ticker();
    return ticker;
}
export function refreshTicker() {
    if (ticker) ticker.refresh();
}
