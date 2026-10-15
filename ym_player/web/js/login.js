// 로그인 화면. 사용자가 아직 없으면 "관리자 계정 만들기"로 바뀐다.
const $ = (id) => document.getElementById(id);
const form = $('loginForm');
const errorEl = $('loginError');
let setup = false;

function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.toggle('hidden', !msg);
}

async function post(path, body) {
    const res = await fetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* 빈 본문 */ }
    if (!res.ok || data.success === false) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

async function init() {
    try {
        const res = await fetch('/api/auth/state', { headers: { Accept: 'application/json' } });
        const st = await res.json();
        if (st.user) { location.replace('/'); return; }
        setup = !!st.setup;
    } catch (e) {
        showError('서버에 연결할 수 없습니다.');
    }
    if (setup) {
        $('loginTitle').textContent = '관리자 계정 만들기';
        $('setupNote').classList.remove('hidden');
        $('confirmField').classList.remove('hidden');
        $('password').autocomplete = 'new-password';
        $('loginBtn').textContent = '계정 만들고 시작하기';
    }
    $('username').focus();
}

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    showError('');
    const username = $('username').value.trim();
    const password = $('password').value;
    if (!username || !password) { showError('아이디와 비밀번호를 넣어 주세요.'); return; }
    if (setup && password !== $('confirm').value) { showError('비밀번호 확인이 다릅니다.'); return; }
    const btn = $('loginBtn');
    btn.disabled = true;
    try {
        await post(setup ? '/api/auth/setup' : '/api/auth/login', { username, password, remember: $('remember').checked });
        location.replace('/');
    } catch (err) {
        showError(err.message);
        $('password').select();
    } finally {
        btn.disabled = false;
    }
});

init();
