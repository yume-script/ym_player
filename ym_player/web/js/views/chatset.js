// 북마크 [채팅 주소 보내기]가 여는 화면 (#/chat-set?sid=소스&url=채팅주소) - 관리자의 브라우저가 연 사이트에서 찾은
// 채팅 주소를 서버에 저장한다 (서버가 그 사이트를 열 수 없을 때). 저장하면 그 IPTV 소스의 채팅 패널이 이 주소를 쓴다.
import { h, icon, api } from '../util.js';
import { registry } from '../providers/index.js';

export async function renderChatSet(main, _params, query) {
    const sid = query.get('sid') || '';
    const url = query.get('url') || '';
    const box = h('div', { class: 'page chat-set' }, h('h1', {}, icon('comments'), ' 채팅 주소 받기'));
    main.appendChild(box);
    const say = (cls, text) => box.appendChild(h('p', { class: cls, text }));
    if (!sid || !url) { say('muted error', '보낼 채팅 주소가 없습니다. 사이트 페이지에서 북마크를 눌러 주세요.'); return; }
    box.appendChild(h('p', { class: 'chat-set-url', text: url }));
    try {
        const r = await api(`/api/iptv/${encodeURIComponent(sid)}/chat-url`, { method: 'POST', json: { url } });
        say('chat-set-ok', `"${r.source}"의 채팅 주소로 저장했습니다. 이제 그 채널을 틀면 이 채팅이 뜹니다. 이 창은 닫아도 됩니다.`);
        const p = registry.byId(sid);
        if (p) box.appendChild(h('a', { class: 'btn btn-accent', href: `#/live/${encodeURIComponent(sid)}` }, icon('tv'), ` ${p.name} 채널 보기`));
    } catch (e) {
        say('muted error', `저장하지 못했습니다: ${e.message}`);
    }
}
