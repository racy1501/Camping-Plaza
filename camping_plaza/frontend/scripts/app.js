/**
 * 露营广场 - 主应用逻辑
 * 连接后端API，驱动地图和状态面板
 */

// API地址（本地开发用，部署后换成合集站分配的地址）
const API_BASE = 'http://localhost:8000';

// 全局状态
let campMap = null;
let gameState = null;
let aiPosition = { x: 300, y: 450 }; // AI角色当前位置
let aiTarget = null; // AI角色移动目标
let logEntries = [];

// =============================================================================
// 初始化
// =============================================================================

window.addEventListener('DOMContentLoaded', () => {
    campMap = new CampMap('campMap');

    // 初始渲染（默认状态）
    const defaultTents = {
        1: { status: 'available', capacity: 1 },
        2: { status: 'available', capacity: 2 },
        3: { status: 'available', capacity: 2 },
        4: { status: 'available', capacity: 3 },
        5: { status: 'available', capacity: 3 },
        6: { status: 'available', capacity: 5 }
    };

    campMap.draw(defaultTents, []);
    renderTentsPanel(defaultTents);

    // 如果有后端，加载真实数据
    loadGameState();

    // 启动AI角色动画
    animateAI();
});

// =============================================================================
// 数据加载
// =============================================================================

async function loadGameState() {
    try {
        const response = await fetch(`${API_BASE}/api/state`);
        if (!response.ok) throw new Error('API不可用');

        gameState = await response.json();
        updateUI(gameState);
        addLog('📡 已连接游戏服务器');
    } catch (e) {
        // 没有后端时用演示模式
        addLog('⚠️ 未连接服务器，显示初始状态');
        startDemoMode();
    }
}

// =============================================================================
// UI更新
// =============================================================================

function updateUI(state) {
    // 状态栏
    document.getElementById('day').textContent = state.day;
    document.getElementById('turn').textContent = state.turn;
    document.getElementById('balance').textContent = state.balance;
    document.getElementById('reputation').textContent = Math.round(state.reputation_rate);
    document.getElementById('decisions').textContent = state.decisions_left;

    // 收入
    if (state.today_income) {
        document.getElementById('income-accommodation').textContent = state.today_income.accommodation || 0;
        document.getElementById('income-campsite').textContent = state.today_income.campsite || 0;
        document.getElementById('income-dining').textContent = state.today_income.dining || 0;
        document.getElementById('income-entertainment').textContent = state.today_income.entertainment || 0;
    }

    // 帐篷面板
    renderTentsPanel(state.tents);

    // 地图
    campMap.draw(state.tents, state.active_npcs || []);

    // AI角色位置更新
    updateAIPosition(state);
}

function renderTentsPanel(tents) {
    const grid = document.getElementById('tentsGrid');
    grid.innerHTML = '';

    for (const [id, tent] of Object.entries(tents)) {
        const statusIcons = {
            'available': '🟢',
            'occupied': '🔴',
            'cleaning': '🟡',
            'broken': '⚠️',
            'reserved': '🔵'
        };

        const card = document.createElement('div');
        card.className = `tent-card ${tent.status}`;
        card.innerHTML = `
            <div class="tent-id">${id}号帐篷</div>
            <div class="tent-status-icon">${statusIcons[tent.status] || '❓'}</div>
            <div class="tent-info">${tent.capacity}人</div>
        `;
        grid.appendChild(card);
    }
}

// =============================================================================
// AI角色
// =============================================================================

/**
 * 根据AI当前操作更新位置
 */
function updateAIPosition(state) {
    // AI默认站在管理中心（地图中间偏下）
    let targetX = 300;
    let targetY = 450;

    // 如果正在维修某帐篷，移动到那个帐篷
    if (state.repairing_tent) {
        const tentPos = campMap.positions.tents[state.repairing_tent];
        if (tentPos) {
            targetX = tentPos.x - 25;
            targetY = tentPos.y;
        }
    }
    // 如果在管理设施，移动到对应设施
    else if (state.managing_facility) {
        const facilityPos = campMap.positions[state.managing_facility];
        if (facilityPos) {
            targetX = facilityPos.x;
            targetY = facilityPos.y - 35;
        }
    }

    aiTarget = { x: targetX, y: targetY };
}

/**
 * AI角色平滑移动动画
 */
function animateAI() {
    if (aiTarget) {
        // 缓慢移向目标
        const dx = aiTarget.x - aiPosition.x;
        const dy = aiTarget.y - aiPosition.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist > 2) {
            const speed = Math.min(dist, 1.5);
            aiPosition.x += (dx / dist) * speed;
            aiPosition.y += (dy / dist) * speed;
        }
    }

    // 重绘地图（带上AI角色）
    if (gameState) {
        campMap.draw(gameState.tents, gameState.active_npcs || []);
    } else {
        const defaultTents = {
            1: { status: 'available', capacity: 1 },
            2: { status: 'available', capacity: 2 },
            3: { status: 'available', capacity: 2 },
            4: { status: 'available', capacity: 3 },
            5: { status: 'available', capacity: 3 },
            6: { status: 'available', capacity: 5 }
        };
        campMap.draw(defaultTents, []);
    }

    // 绘制AI角色
    drawAICharacter();

    requestAnimationFrame(animateAI);
}

/**
 * 绘制AI角色（像素风机器人）
 */
function drawAICharacter() {
    const ctx = campMap.ctx;
    const x = aiPosition.x;
    const y = aiPosition.y;
    const size = 10;

    // 身体阴影
    ctx.fillStyle = 'rgba(0,0,0,0.15)';
    ctx.beginPath();
    ctx.ellipse(x, y + size + 2, size * 0.8, 3, 0, 0, Math.PI * 2);
    ctx.fill();

    // 身体（莫兰迪蓝灰）
    ctx.fillStyle = '#7a95a8';
    ctx.fillRect(x - size * 0.7, y - size * 0.3, size * 1.4, size * 1.2);

    // 头部（稍浅）
    ctx.fillStyle = '#8fa8b8';
    ctx.fillRect(x - size * 0.6, y - size, size * 1.2, size * 0.8);

    // 眼睛（两个发光点）
    ctx.fillStyle = '#e8f0f5';
    ctx.fillRect(x - size * 0.3, y - size * 0.7, 4, 4);
    ctx.fillRect(x + size * 0.1, y - size * 0.7, 4, 4);

    // 眼睛高光
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(x - size * 0.2, y - size * 0.6, 2, 2);
    ctx.fillRect(x + size * 0.2, y - size * 0.6, 2, 2);

    // 天线
    ctx.strokeStyle = '#7a95a8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y - size);
    ctx.lineTo(x, y - size - 8);
    ctx.stroke();

    // 天线顶端小球
    ctx.fillStyle = '#c9a96a';
    ctx.beginPath();
    ctx.arc(x, y - size - 10, 3, 0, Math.PI * 2);
    ctx.fill();

    // 名字标签
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.fillRect(x - 20, y + size + 4, 40, 14);
    ctx.strokeStyle = '#7a95a8';
    ctx.lineWidth = 1;
    ctx.strokeRect(x - 20, y + size + 4, 40, 14);
    ctx.fillStyle = '#5a5249';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('🤖 AI管家', x, y + size + 11);
}

// =============================================================================
// 游戏操作
// =============================================================================

async function advanceTurn() {
    try {
        const response = await fetch(`${API_BASE}/api/turn/advance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        gameState = await fetch(`${API_BASE}/api/state`).then(r => r.json());
        updateUI(gameState);

        // 添加事件日志
        if (result.events) {
            result.events.forEach(event => {
                const type = event.includes('⚠️') ? 'warning' :
                             event.includes('收入') || event.includes('入住') ? 'income' : '';
                addLog(event, type);
            });
        }

        // AI位置更新
        if (result.ai_action) {
            aiTarget = getAIActionPosition(result.ai_action);
        }

    } catch (e) {
        addLog('❌ 操作失败：' + e.message, 'warning');
    }
}

async function doAction(action, params) {
    try {
        const response = await fetch(`${API_BASE}/api/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, params })
        });

        const result = await response.json();

        // 重新加载状态
        gameState = await fetch(`${API_BASE}/api/state`).then(r => r.json());
        updateUI(gameState);

        addLog(result.message || `执行: ${action}`, result.success ? 'income' : 'warning');

    } catch (e) {
        addLog('❌ 操作失败：' + e.message, 'warning');
    }
}

// =============================================================================
// 日志
// =============================================================================

function addLog(text, type = '') {
    logEntries.unshift({ text, type, time: new Date() });
    if (logEntries.length > 50) logEntries.pop();
    renderLog();
}

function renderLog() {
    const list = document.getElementById('logList');
    list.innerHTML = logEntries.map(entry =>
        `<div class="log-entry ${entry.type}">${entry.text}</div>`
    ).join('');
}

// =============================================================================
// 演示模式（无后端时）
// =============================================================================

function startDemoMode() {
    let demoDay = 1;
    let demoTurn = 1;
    let demoBalance = 1000;

    setInterval(() => {
        // 模拟回合推进
        demoTurn++;
        if (demoTurn > 5) {
            demoTurn = 1;
            demoDay++;
        }

        // 模拟收入
        const income = Math.floor(Math.random() * 200) + 50;
        demoBalance += income;

        // 模拟NPC
        const npcCount = Math.floor(Math.random() * 4) + 1;
        const locations = ['tent_1', 'tent_2', 'tent_3', 'tent_4', 'tent_5', 'tent_6', 'dining', 'entertainment'];
        const demoNPCs = [];
        for (let i = 0; i < npcCount; i++) {
            demoNPCs.push({
                id: i + 1,
                group_size: Math.floor(Math.random() * 3) + 1,
                location: locations[Math.floor(Math.random() * locations.length)]
            });
        }

        // 随机帐篷状态
        const statuses = ['available', 'occupied', 'available', 'occupied', 'cleaning', 'available'];
        const demoTents = {};
        for (let i = 1; i <= 6; i++) {
            demoTents[i] = {
                status: statuses[Math.floor(Math.random() * statuses.length)],
                capacity: [1, 2, 2, 3, 3, 5][i - 1]
            };
        }

        // 更新状态栏
        document.getElementById('day').textContent = demoDay;
        document.getElementById('turn').textContent = demoTurn;
        document.getElementById('balance').textContent = demoBalance;

        // 更新帐篷面板
        renderTentsPanel(demoTents);

        // 更新地图
        campMap.draw(demoTents, demoNPCs);

        // AI随机移动
        const targetFacilities = ['hotSpring', 'dining', 'entertainmentA', 'entertainmentB'];
        const targetKey = targetFacilities[Math.floor(Math.random() * targetFacilities.length)];
        aiTarget = campMap.positions[targetKey];

    }, 3000);
}

// =============================================================================
// 辅助
// =============================================================================

function getAIActionPosition(action) {
    if (action.includes('repair') || action.includes('upgrade')) {
        // 移动到对应帐篷
        const tentId = parseInt(action.match(/\d+/)?.[0] || '1');
        return campMap.positions.tents[tentId] || { x: 300, y: 450 };
    }
    if (action.includes('greenery')) {
        return { x: 300, y: 690 };
    }
    if (action.includes('dining')) {
        return campMap.positions.dining;
    }
    return { x: 300, y: 450 };
}
