// ==================== 状态管理 ====================
const state = {
    sessionId: 'default',
    userId: null,
    username: '',
    isLoggedIn: false,
    messages: [],
    isProcessing: false,
};

// ==================== 初始化 ====================
document.addEventListener('DOMContentLoaded', () => {
    state.sessionId = generateSessionId();
    loadSessionFromStorage();
    setupAutoResize();
});

function generateSessionId() {
    return 'sess_' + Date.now().toString(36) + Math.random().toString(36).substr(2, 6);
}

function loadSessionFromStorage() {
    const saved = localStorage.getItem('tourism_user');
    if (saved) {
        try {
            const data = JSON.parse(saved);
            state.userId = data.userId;
            state.username = data.username;
            state.isLoggedIn = true;
            updateUserUI();
            loadPreferences();
        } catch (e) {
            localStorage.removeItem('tourism_user');
        }
    }
}

// ==================== 用户认证 ====================
function showAuthModal(tab = 'login') {
    if (state.isLoggedIn) {
        doLogout();
        return;
    }
    document.getElementById('authModal').style.display = 'flex';
    switchAuthTab(tab);
}

function hideAuthModal() {
    document.getElementById('authModal').style.display = 'none';
}

function switchAuthTab(tab) {
    document.getElementById('tabLogin').classList.toggle('active', tab === 'login');
    document.getElementById('tabRegister').classList.toggle('active', tab === 'register');
    document.getElementById('loginFormPanel').style.display = tab === 'login' ? 'flex' : 'none';
    document.getElementById('registerFormPanel').style.display = tab === 'register' ? 'flex' : 'none';
    document.getElementById('loginError').textContent = '';
    document.getElementById('regError').textContent = '';
}

// 点击遮罩关闭弹窗
document.addEventListener('click', (e) => {
    if (e.target.id === 'authModal') hideAuthModal();
});

async function doLogin(event) {
    event.preventDefault();
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    const errEl = document.getElementById('loginError');
    errEl.textContent = '';

    if (!username || !password) {
        errEl.textContent = '请填写用户名和密码';
        return;
    }

    const btn = document.querySelector('#loginFormPanel .btn-auth');
    btn.disabled = true;
    btn.textContent = '登录中...';

    try {
        const resp = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            errEl.textContent = data.detail || '登录失败';
            return;
        }
        setLoggedIn(data.user);
        hideAuthModal();
        document.getElementById('loginPassword').value = '';
        addSystemMessage(`欢迎回来，${state.username}！`);
    } catch (e) {
        errEl.textContent = '网络错误，请检查连接';
    } finally {
        btn.disabled = false;
        btn.textContent = '登 录';
    }
}

async function doRegister(event) {
    event.preventDefault();
    const username = document.getElementById('regUsername').value.trim();
    const nickname = document.getElementById('regNickname').value.trim();
    const password = document.getElementById('regPassword').value;
    const passwordConfirm = document.getElementById('regPasswordConfirm').value;
    const errEl = document.getElementById('regError');
    errEl.textContent = '';

    if (username.length < 2) {
        errEl.textContent = '用户名至少需要2个字符';
        return;
    }
    if (password.length < 6) {
        errEl.textContent = '密码至少需要6位字符';
        return;
    }
    if (password !== passwordConfirm) {
        errEl.textContent = '两次输入的密码不一致';
        return;
    }

    const btn = document.querySelector('#registerFormPanel .btn-auth');
    btn.disabled = true;
    btn.textContent = '注册中...';

    try {
        const resp = await fetch('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, nickname: nickname || username }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            errEl.textContent = data.detail || '注册失败';
            return;
        }
        setLoggedIn(data.user);
        hideAuthModal();
        clearRegForm();
        addSystemMessage(`注册成功，欢迎你，${state.username}！`);
    } catch (e) {
        errEl.textContent = '网络错误，请检查连接';
    } finally {
        btn.disabled = false;
        btn.textContent = '注 册';
    }
}

function clearRegForm() {
    document.getElementById('regUsername').value = '';
    document.getElementById('regNickname').value = '';
    document.getElementById('regPassword').value = '';
    document.getElementById('regPasswordConfirm').value = '';
}

function setLoggedIn(user) {
    state.userId = user.id;
    state.username = user.username;
    state.isLoggedIn = true;
    localStorage.setItem('tourism_user', JSON.stringify({
        userId: user.id,
        username: user.username,
    }));
    updateUserUI();
    loadPreferences();
}

function doLogout() {
    state.userId = null;
    state.username = '';
    state.isLoggedIn = false;
    localStorage.removeItem('tourism_user');
    updateUserUI();
    document.getElementById('preferencesList').innerHTML = '<p class="text-muted">登录后可查看偏好设置</p>';
    hideAuthModal();
    addSystemMessage('已退出登录，切换为游客模式。');
}

function updateUserUI() {
    document.getElementById('userName').textContent = state.isLoggedIn ? state.username : '未登录';
    document.getElementById('userStatus').textContent = state.isLoggedIn ? '已登录 · 个性化模式' : '游客模式';
    document.getElementById('userAvatar').textContent = state.isLoggedIn ? '🧑' : '👤';
    const loginBtn = document.getElementById('toggleLoginBtn');
    loginBtn.textContent = state.isLoggedIn ? '退出' : '登录';
}

async function loadPreferences() {
    if (!state.userId) return;
    try {
        const resp = await fetch(`/memory/preferences/${state.userId}`);
        const data = await resp.json();
        const panel = document.getElementById('preferencesList');
        if (data.preferences && data.preferences.length > 0) {
            panel.innerHTML = data.preferences.map(p =>
                `<div class="pref-item" style="margin-bottom:6px;font-size:12px;">
                    <span style="color:#818cf8;">[${p.category}]</span>
                    ${p.key}: ${p.value}
                    <span style="color:#94a3b8;">(${(p.confidence*100).toFixed(0)}%)</span>
                </div>`
            ).join('');
        } else {
            panel.innerHTML = '<p class="text-muted">暂无偏好，使用中自动积累</p>';
        }
    } catch (e) {
        console.error('Load preferences failed:', e);
    }
}

// ==================== 会话管理 ====================
function newSession() {
    state.sessionId = generateSessionId();
    state.messages = [];
    document.getElementById('chatMessages').innerHTML = `
        <div class="welcome-message">
            <div class="welcome-icon">🌍</div>
            <h2>新会话已创建</h2>
            <p>有什么可以帮您的？</p>
            <div class="quick-actions">
                <button class="quick-btn" onclick="sendQuick('帮我查北京到上海的机票')">✈️ 查机票</button>
                <button class="quick-btn" onclick="sendQuick('三亚今天天气怎么样')">🌤️ 查天气</button>
                <button class="quick-btn" onclick="sendQuick('推荐成都的景点')">🏔️ 景点推荐</button>
                <button class="quick-btn" onclick="sendQuick('帮我查北京的酒店')">🏨 查酒店</button>
            </div>
        </div>
    `;
    document.getElementById('detailContent').innerHTML = '<p class="text-muted">选择一条消息查看详情</p>';

    const sessionList = document.getElementById('sessionList');
    const item = document.createElement('div');
    item.className = 'session-item active';
    item.dataset.session = state.sessionId;
    item.innerHTML = `<span class="session-icon">💬</span><span class="session-title">会话 ${state.sessionId.slice(-6)}</span>`;
    item.onclick = () => switchSession(state.sessionId);
    sessionList.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
    sessionList.appendChild(item);
}

function switchSession(sessionId) {
    state.sessionId = sessionId;
    document.querySelectorAll('.session-item').forEach(el => {
        el.classList.toggle('active', el.dataset.session === sessionId);
    });
}

// ==================== 消息发送（流式） ====================
function sendQuick(text) {
    document.getElementById('chatInput').value = text;
    sendMessage();
}

async function sendMessage() {
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if (!text || state.isProcessing) return;

    state.isProcessing = true;
    input.value = '';
    input.style.height = 'auto';
    document.getElementById('sendBtn').disabled = true;

    // 移除欢迎消息
    const welcome = document.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    // 显示用户消息
    const userMsgId = addMessage('user', text);
    const startTime = Date.now();

    // 创建助手消息占位（流式填充）
    const assistantMsgId = addMessage('assistant', '', { streaming: true });

    try {
        const resp = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: text,
                session_id: state.sessionId,
                history: state.isLoggedIn ? null : state.messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let intent = '';
        let durationMs = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // 保留未完成的行

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6);
                if (data === '[DONE]') continue;

                try {
                    const event = JSON.parse(data);
                    switch (event.type) {
                        case 'intent':
                            intent = event.intent;
                            updateMessageIntent(assistantMsgId, intent);
                            break;
                        case 'tool':
                            // 显示工具执行进度
                            showToolProgress(assistantMsgId, event.tool_name);
                            break;
                        case 'token':
                            appendToMessage(assistantMsgId, event.content);
                            break;
                        case 'done':
                            intent = event.intent || intent;
                            durationMs = event.duration_ms || (Date.now() - startTime);
                            updateMessageIntent(assistantMsgId, intent);
                            hideToolProgress(assistantMsgId);
                            break;
                        case 'answer':
                            // 非流式回退（追问等场景）
                            setMessageContent(assistantMsgId, event.content);
                            intent = event.intent;
                            durationMs = event.duration_ms || (Date.now() - startTime);
                            updateMessageIntent(assistantMsgId, intent);
                            break;
                    }
                } catch (e) {
                    // 跳过解析失败的行
                }
            }
        }

        // 刷新最后一次渲染，确保内容完整
        flushRender(assistantMsgId);

        // 设置最终内容和耗时
        const msgEl = document.getElementById(assistantMsgId);
        if (msgEl) {
            const contentEl = msgEl.querySelector('.message-content');
            const finalContent = contentEl ? contentEl.textContent : '';
            state.messages.push({ role: 'assistant', content: finalContent, meta: { intent, durationMs } });
            // 更新耗时显示
            const timeEl = msgEl.querySelector('.msg-time');
            if (timeEl) {
                timeEl.textContent = formatDuration(durationMs || (Date.now() - startTime));
            }
        }

    } catch (e) {
        setMessageContent(assistantMsgId, `抱歉，请求处理失败：${e.message}。请检查服务是否正常运行。`);
        console.error('Send failed:', e);
    }

    state.isProcessing = false;
    document.getElementById('sendBtn').disabled = false;
    input.focus();
}

// ==================== 消息渲染 ====================
function addMessage(role, content, meta = {}) {
    const messagesDiv = document.getElementById('chatMessages');
    const msgId = 'msg_' + Date.now() + Math.random().toString(36).substr(2, 4);

    const avatarMap = {
        user: '🧑',
        assistant: '🤖',
        system: '💡',
    };

    const html = `
        <div class="message ${role}" id="${msgId}" data-intent="${meta.intent || ''}">
            <div class="message-avatar">${avatarMap[role] || '💬'}</div>
            <div>
                <div class="message-content">${formatContent(content)}</div>
                <div class="message-meta">
                    <span class="msg-time">${meta.streaming ? '⏳ 生成中...' : new Date().toLocaleTimeString()}</span>
                    ${meta.intent ? `<span class="intent-tag">${meta.intent}</span>` : ''}
                </div>
                ${role === 'assistant' ? `
                <div class="message-actions">
                    <button onclick="showDetail('${msgId}')" title="查看详情">📋</button>
                    <button onclick="copyMessageContent('${msgId}')" title="复制">📝</button>
                </div>` : ''}
            </div>
        </div>
    `;

    messagesDiv.insertAdjacentHTML('beforeend', html);

    if (role !== 'system' && !meta.streaming) {
        state.messages.push({ role, content, meta });
        if (state.messages.length > 40) {
            state.messages = state.messages.slice(-40);
        }
    }

    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    return msgId;
}

function addSystemMessage(content) {
    return addMessage('system', content);
}

let _renderQueue = new Map(); // msgId -> { rawText, rafId }

function appendToMessage(msgId, token) {
    const el = document.getElementById(msgId);
    if (!el) return;
    const contentEl = el.querySelector('.message-content');
    if (!contentEl) return;

    // 累积原始文本
    if (!contentEl.dataset.rawText) contentEl.dataset.rawText = '';
    contentEl.dataset.rawText += token;

    // 使用 RAF 节流渲染，避免每个 token 都触发一次完整渲染
    const entry = _renderQueue.get(msgId) || {};
    if (entry.rafId) cancelAnimationFrame(entry.rafId);

    entry.rawText = contentEl.dataset.rawText;
    entry.rafId = requestAnimationFrame(() => {
        contentEl.innerHTML = renderMarkdown(entry.rawText);
        const messagesDiv = document.getElementById('chatMessages');
        if (messagesDiv) messagesDiv.scrollTop = messagesDiv.scrollHeight;
        _renderQueue.delete(msgId);
    });
    _renderQueue.set(msgId, entry);
}

function flushRender(msgId) {
    const entry = _renderQueue.get(msgId);
    if (!entry) return;
    if (entry.rafId) cancelAnimationFrame(entry.rafId);
    const el = document.getElementById(msgId);
    if (el) {
        const contentEl = el.querySelector('.message-content');
        if (contentEl && entry.rawText) {
            contentEl.innerHTML = renderMarkdown(entry.rawText);
        }
    }
    _renderQueue.delete(msgId);
}

function setMessageContent(msgId, content) {
    const el = document.getElementById(msgId);
    if (!el) return;
    const contentEl = el.querySelector('.message-content');
    if (!contentEl) return;
    contentEl.dataset.rawText = content;
    contentEl.innerHTML = renderMarkdown(content);
    _renderQueue.delete(msgId);
}

function updateMessageIntent(msgId, intent) {
    const el = document.getElementById(msgId);
    if (!el || !intent) return;
    el.dataset.intent = intent;
    const tagEl = el.querySelector('.intent-tag');
    if (tagEl) {
        tagEl.textContent = intent;
    } else if (intent) {
        const metaEl = el.querySelector('.message-meta');
        if (metaEl) {
            const tag = document.createElement('span');
            tag.className = 'intent-tag';
            tag.textContent = intent;
            metaEl.appendChild(tag);
        }
    }
}

function formatDuration(ms) {
    if (!ms) return '';
    const seconds = (ms / 1000).toFixed(1);
    return `⏱️ ${seconds}s`;
}

const TOOL_CN_MAP = {
    train_ticket_query: '正在查询 12306 火车票...',
    weather_query: '正在查询天气...',
    tour_group_query: '正在查询旅行团...',
    hotel_query: '正在查询酒店...',
    car_rental_query: '正在查询租车信息...',
    insurance_query: '正在查询保险...',
    attraction_recommend: '正在推荐景点...',
    ticket_query: '正在查询票务...',
};

function showToolProgress(msgId, toolName) {
    const el = document.getElementById(msgId);
    if (!el) return;
    const timeEl = el.querySelector('.msg-time');
    const text = TOOL_CN_MAP[toolName] || `正在查询 ${toolName}...`;
    if (timeEl) timeEl.textContent = text;
}

function hideToolProgress(msgId) {
    const el = document.getElementById(msgId);
    if (!el) return;
    const timeEl = el.querySelector('.msg-time');
    if (timeEl) timeEl.textContent = '⏳ 生成中...';
}

function formatContent(text) {
    return renderMarkdown(text);
}

function renderMarkdown(text) {
    if (!text) return '';

    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // 代码块 (``` ... ```)
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        return '<pre><code>' + code.trim() + '</code></pre>';
    });

    // 表格 (| col1 | col2 | ...)
    html = html.replace(/((?:^\|.+\|\n?)+)/gm, (match) => {
        const lines = match.trim().split('\n');
        if (lines.length < 2) return match;
        let table = '<table>';
        lines.forEach((line, i) => {
            const cells = line.split('|').filter(c => c.trim() !== '');
            const tag = i === 1 && /^[\s\-:]+$/.test(cells.join('')) ? '' : (i === 0 ? 'th' : 'td');
            if (!tag) return; // 跳过分隔行
            table += '<tr>';
            cells.forEach(c => {
                table += '<' + tag + '>' + c.trim() + '</' + tag + '>';
            });
            table += '</tr>';
        });
        table += '</table>';
        return table;
    });

    // 内联代码
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // 加粗
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // 斜体
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // 标题
    html = html.replace(/^#### (.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');

    // 水平线
    html = html.replace(/^---$/gm, '<hr>');

    // 无序列表 —— 将连续的 - 行包在 <ul> 中
    html = html.replace(/((?:^- .+\n?)+)/gm, (match) => {
        const items = match.trim().split('\n')
            .map(line => '<li>' + line.replace(/^- /, '') + '</li>')
            .join('');
        return '<ul>' + items + '</ul>';
    });

    // 有序列表
    html = html.replace(/((?:^\d+\. .+\n?)+)/gm, (match) => {
        const items = match.trim().split('\n')
            .map(line => '<li>' + line.replace(/^\d+\. /, '') + '</li>')
            .join('');
        return '<ol>' + items + '</ol>';
    });

    // 段落（双换行）
    const parts = html.split(/\n\n+/);
    html = parts.map(p => {
        p = p.trim();
        if (!p) return '';
        // 已经是块级元素的不需要再包 <p>
        if (/^<(h[2-5]|ul|ol|table|pre|hr)/.test(p)) return p;
        return '<p>' + p + '</p>';
    }).join('');

    // 剩余单换行
    html = html.replace(/\n/g, '<br>');

    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function copyMessageContent(msgId) {
    const el = document.getElementById(msgId);
    if (!el) return;
    const contentEl = el.querySelector('.message-content');
    const text = contentEl ? contentEl.textContent : '';
    navigator.clipboard.writeText(text).then(() => {
        showToast('已复制到剪贴板');
    }).catch(() => {
        showToast('复制失败，请手动选择文字');
    });
}

function copyText(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    navigator.clipboard.writeText(div.textContent).then(() => {
        showToast('已复制到剪贴板');
    });
}

// ==================== 详情面板 ====================
function showDetail(msgId) {
    const el = document.getElementById(msgId);
    if (!el) return;

    const content = el.querySelector('.message-content').textContent;
    const intent = el.dataset.intent || 'N/A';
    const role = el.classList.contains('user') ? '用户' : '助手';
    const timeEl = el.querySelector('.msg-time');
    const time = timeEl ? timeEl.textContent : '';

    document.getElementById('detailContent').innerHTML = `
        <div class="detail-item">
            <div class="detail-label">角色</div>
            <div class="detail-value">${role}</div>
        </div>
        <div class="detail-item">
            <div class="detail-label">意图</div>
            <div class="detail-value"><span class="intent-tag">${intent}</span></div>
        </div>
        ${time ? `<div class="detail-item"><div class="detail-label">耗时</div><div class="detail-value">${time}</div></div>` : ''}
        <div class="detail-item">
            <div class="detail-label">内容</div>
            <div class="detail-value">${formatContent(content)}</div>
        </div>
    `;
}

function toggleDetailPanel() {
    document.getElementById('detailPanel').classList.toggle('collapsed');
}

// ==================== 侧边栏 ====================
document.getElementById('sidebarToggle').addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('collapsed');
});

// ==================== 输入框自动调整高度 ====================
function setupAutoResize() {
    const input = document.getElementById('chatInput');
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });
}

function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// ==================== Toast ====================
function showToast(message) {
    let toast = document.getElementById('toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.style.cssText = `
            position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
            padding: 8px 20px; background: #1e293b; color: white;
            border-radius: 20px; font-size: 13px; z-index: 9999;
            animation: fadeIn 0.3s;
        `;
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = '1';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 2000);
}
