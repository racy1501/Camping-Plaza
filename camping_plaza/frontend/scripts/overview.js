/**
 * 露营广场 · 营地总览
 * 真实只读网页入口：从 /api/state 读取后端状态并渲染，无假数据、无 demo mode。
 */

(function () {
    'use strict';

    // 锚点坐标（百分比，以地图左上角为原点）
    const ANCHORS = {
        entrance: { top: 92, left: 14 },
        tent1: { top: 18, left: 46 },
        tent2: { top: 18, left: 54 },
        tent3: { top: 18, left: 62 },
        tent4: { top: 34, left: 46 },
        tent5: { top: 34, left: 54 },
        tent6: { top: 34, left: 62 },
        campsite: { top: 45, left: 95 },
        bonfire: { top: 48, left: 50 },
        onsenLocked: { top: 30, left: 14 },
        entertainment: { top: 82, left: 28 },
        dining: { top: 82, left: 72 }
    };

    // 后端 NPC location → 前端锚点标识
    const LOCATION_TO_ANCHOR = {
        gate: 'entrance',
        campsite: 'campsite',
        dining: 'dining',
        entertainment: 'entertainment',
        hot_spring: 'onsenLocked',
        bonfire: 'bonfire'
    };
    for (let i = 1; i <= 6; i++) {
        LOCATION_TO_ANCHOR[`tent_${i}`] = `tent${i}`;
    }

    const els = {};
    let apiConnected = false;

    function init() {
        cacheElements();
        setDisconnected();
        fetchState();
    }

    function cacheElements() {
        els.connectionBanner = document.getElementById('connectionBanner');
        els.day = document.getElementById('day');
        els.turn = document.getElementById('turn');
        els.phase = document.getElementById('phase');
        els.balance = document.getElementById('balance');
        els.reputation = document.getElementById('reputation');
        els.noticeList = document.getElementById('noticeList');
        els.logList = document.getElementById('logList');
        els.reminderList = document.getElementById('reminderList');
        els.openSlots = document.getElementById('openSlots');
        els.activeGroups = document.getElementById('activeGroups');
        els.todoCount = document.getElementById('todoCount');
        els.facilityState = document.getElementById('facilityState');
        els.incomeAccommodation = document.getElementById('income-accommodation');
        els.incomeCampsite = document.getElementById('income-campsite');
        els.incomeDining = document.getElementById('income-dining');
        els.incomeEntertainment = document.getElementById('income-entertainment');
        els.incomeTotal = document.getElementById('income-total');
        els.npcLayer = document.getElementById('npcLayer');
        els.playerMarker = document.getElementById('playerMarker');
        els.playerLabel = document.getElementById('playerLabel');
        els.turnHint = document.getElementById('turnHint');
        els.dayEndHint = document.getElementById('dayEndHint');
        els.actionButtons = document.querySelectorAll('.btn-action');
        els.tentAnchors = {};
        for (let i = 1; i <= 6; i++) {
            els.tentAnchors[i] = document.querySelector(`.anchor-tent${i}`);
        }
    }

    async function fetchState() {
        try {
            const res = await fetch('/api/state', { method: 'GET' });
            if (!res.ok) {
                throw new Error('HTTP ' + res.status);
            }
            const state = await res.json();
            apiConnected = true;
            renderConnected();
            renderAll(state);
        } catch (err) {
            apiConnected = false;
            console.warn('无法连接游戏后端：', err);
            setDisconnected();
        }
    }

    function setDisconnected() {
        if (els.connectionBanner) {
            els.connectionBanner.classList.remove('hidden');
        }
        clearState();
        disableAllActions();
        if (els.turnHint) els.turnHint.textContent = '等待后端…';
        if (els.dayEndHint) els.dayEndHint.textContent = '等待后端…';
        if (els.noticeList) {
            els.noticeList.innerHTML = '<span class="notice-chip">等待游戏后端连接</span>';
        }
        hidePlayerMarker();
    }

    function renderConnected() {
        if (els.connectionBanner) {
            els.connectionBanner.classList.add('hidden');
        }
    }

    function clearState() {
        if (els.npcLayer) els.npcLayer.innerHTML = '';
        if (els.logList) els.logList.innerHTML = '';
        if (els.reminderList) els.reminderList.innerHTML = '';
        lockAllTents();
    }

    function hidePlayerMarker() {
        if (els.playerMarker) els.playerMarker.style.display = 'none';
    }

    function showPlayerMarker() {
        if (els.playerMarker) {
            els.playerMarker.style.display = '';
            els.playerMarker.style.left = ANCHORS.entrance.left + '%';
            els.playerMarker.style.top = ANCHORS.entrance.top + '%';
            els.playerMarker.style.bottom = 'auto';
            els.playerMarker.style.right = 'auto';
        }
        if (els.playerLabel) els.playerLabel.textContent = '小克 待命';
    }

    function disableAllActions() {
        if (!els.actionButtons) return;
        els.actionButtons.forEach(btn => {
            btn.disabled = true;
            if (!apiConnected) {
                btn.title = '真实操作尚未接入';
            }
        });
    }

    function renderAll(state) {
        renderTopCards(state);
        renderMap(state);
        renderNPCs(state.active_npcs || []);
        renderIncome(state);
        renderOverview(state);
        renderReminders(state);
        renderEvents(state.today_events || []);
        renderHints(state);
        showPlayerMarker();
        disableAllActions();
    }

    function renderTopCards(state) {
        if (els.day) els.day.textContent = state.day ?? '--';
        if (els.turn) els.turn.textContent = state.turn ?? '--';
        if (els.phase) {
            els.phase.textContent = getPhaseLabel(state.turn) || '--';
        }
        if (els.balance) els.balance.textContent = state.balance ?? '--';
        if (els.reputation) {
            const rep = state.reputation_rate;
            els.reputation.textContent = (typeof rep === 'number' ? rep.toFixed(1) : rep) + '%';
        }
    }

    function getPhaseLabel(turn) {
        if (turn === 1) return '迎客准备';
        if (turn >= 2 && turn <= 5) return '营业中';
        if (turn === 6) return '日终管理';
        return '未知阶段';
    }

    function renderMap(state) {
        const tents = state.tents || {};
        for (let i = 1; i <= 6; i++) {
            const tent = tents[String(i)];
            const anchor = els.tentAnchors[i];
            if (!anchor) continue;
            if (tent && tent.unlocked) {
                anchor.classList.remove('locked');
                anchor.style.opacity = '';
                anchor.style.filter = '';
                anchor.title = `${i}号帐篷 · ${tent.status || 'unknown'} · 容量${tent.capacity ?? '?'}`;
            } else {
                anchor.classList.add('locked');
                anchor.style.opacity = '0.35';
                anchor.style.filter = 'grayscale(0.6)';
                anchor.title = `${i}号帐篷 · 未解锁`;
            }
        }
    }

    function lockAllTents() {
        for (let i = 1; i <= 6; i++) {
            const anchor = els.tentAnchors[i];
            if (!anchor) continue;
            anchor.classList.add('locked');
            anchor.style.opacity = '0.35';
            anchor.style.filter = 'grayscale(0.6)';
            anchor.title = `${i}号帐篷 · 未解锁`;
        }
    }

    function renderNPCs(activeNpcs) {
        if (!els.npcLayer) return;
        els.npcLayer.innerHTML = '';

        const visible = (activeNpcs || []).filter(npc => npc.location !== 'leaving');

        // 按位置分组，用于同一地点错开
        const byLocation = {};
        visible.forEach(npc => {
            const anchorId = anchorIdForNpc(npc.location);
            if (!anchorId) return;
            if (!byLocation[anchorId]) byLocation[anchorId] = [];
            byLocation[anchorId].push(npc);
        });

        visible.forEach(npc => {
            const anchorId = anchorIdForNpc(npc.location);
            if (!anchorId) {
                console.warn('未识别的 NPC location：', npc.location, npc);
                return;
            }
            const pos = ANCHORS[anchorId];
            const sameSpot = byLocation[anchorId];
            const index = sameSpot.indexOf(npc);
            const offsetX = (index % 2 === 1 ? 1 : -1) * Math.ceil(index / 2) * 14;
            const offsetY = index * -10;

            const marker = document.createElement('div');
            marker.className = 'npc-marker';
            marker.style.left = pos.left + '%';
            marker.style.top = pos.top + '%';
            marker.style.transform = `translate(calc(-50% + ${offsetX}px), calc(-50% + ${offsetY}px))`;
            marker.title = `${npc.group_size}人 · ${npc.visit_type || 'unknown'} · ${npc.location}`;
            marker.innerHTML = `<span class="npc-body">${npc.group_size}人</span>`;

            els.npcLayer.appendChild(marker);
        });
    }

    function anchorIdForNpc(location) {
        if (!location) return null;
        return LOCATION_TO_ANCHOR[location] || null;
    }

    function renderIncome(state) {
        const income = state.today_income || {};
        const accommodation = income.accommodation ?? 0;
        const campsite = income.campsite ?? 0;
        const dining = income.dining ?? 0;
        const entertainment = income.entertainment ?? 0;
        const total = accommodation + campsite + dining + entertainment;

        if (els.incomeAccommodation) els.incomeAccommodation.textContent = '+' + accommodation;
        if (els.incomeCampsite) els.incomeCampsite.textContent = '+' + campsite;
        if (els.incomeDining) els.incomeDining.textContent = '+' + dining;
        if (els.incomeEntertainment) els.incomeEntertainment.textContent = '+' + entertainment;
        if (els.incomeTotal) els.incomeTotal.textContent = '+' + total;
    }

    function renderOverview(state) {
        const tents = state.tents || {};
        const unlocked = Object.values(tents).filter(t => t && t.unlocked).length;
        const active = (state.active_npcs || []).filter(n => n.location !== 'leaving').length;

        if (els.openSlots) els.openSlots.textContent = `${unlocked}/6`;
        if (els.activeGroups) els.activeGroups.textContent = active;
        if (els.todoCount) els.todoCount.textContent = state.decisions_left ?? '--';
        if (els.facilityState) els.facilityState.textContent = state.food_stock ?? '--';
    }

    function renderReminders(state) {
        if (!els.reminderList) return;
        const reminders = [];
        const tents = state.tents || {};
        Object.entries(tents).forEach(([id, t]) => {
            if (t && t.unlocked && t.status === 'cleaning') {
                reminders.push(`${id}号帐篷待清洁`);
            }
            if (t && t.unlocked && t.status === 'broken') {
                reminders.push(`${id}号帐篷损坏待修`);
            }
        });
        const greenery = state.greenery || {};
        if (greenery.level < 2 && !greenery.maintained_today && greenery.value > 0) {
            reminders.push('绿化将在次日衰减，建议日终维护');
        }
        if (state.food_stock === 0) {
            reminders.push('食材库存为空');
        }
        if (!reminders.length) {
            reminders.push('暂无紧急提醒');
        }
        els.reminderList.innerHTML = reminders.map(r => `<li>${escapeHtml(r)}</li>`).join('');
    }

    function renderEvents(events) {
        if (!els.logList) return;
        els.logList.innerHTML = '';
        if (!Array.isArray(events) || events.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'log-entry';
            empty.textContent = '今日暂无事件';
            els.logList.appendChild(empty);
        } else {
            events.slice().reverse().forEach(text => {
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.textContent = text;
                els.logList.appendChild(entry);
            });
        }

        if (els.noticeList) {
            const latest = events.slice(-3).reverse();
            if (latest.length === 0) {
                els.noticeList.innerHTML = '<span class="notice-chip">今日暂无事件</span>';
            } else {
                els.noticeList.innerHTML = latest.map(e =>
                    `<span class="notice-chip">${escapeHtml(String(e))}</span>`
                ).join('');
            }
        }
    }

    function renderHints(state) {
        if (els.turnHint) {
            els.turnHint.textContent = `自由行动 + 经营动作 0/${state.decisions_left ?? 3}`;
        }
        if (els.dayEndHint) {
            els.dayEndHint.textContent = state.turn === 6 ? '日终：可提交不限数量' : '日终：等待日终阶段';
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
