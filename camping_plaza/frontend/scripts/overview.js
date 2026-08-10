/**
 * 露营广场 · 营地总览
 * 真实只读网页入口：从 /api/state 读取后端状态并渲染，
 * 从 /api/actions 读取人类操作目录。无假数据、无 demo mode。
 */

(function () {
    'use strict';

    // 锚点坐标（百分比，以地图左上角为原点）
    const ANCHORS = {
        entrance: { top: 82, left: 28 },
        tent1: { top: 23, left: 35 },
        tent2: { top: 13, left: 48 },
        tent3: { top: 29, left: 57 },
        tent4: { top: 17, left: 68 },
        tent5: { top: 35, left: 78 },
        tent6: { top: 49, left: 84 },
        campsite: { top: 57, left: 48 },
        bonfire: { top: 48, left: 50 },
        onsenLocked: { top: 30, left: 14 },
        entertainment: { top: 76, left: 45 },
        dining: { top: 82, left: 70 }
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
    let actionsConnected = false;
    let isAdvancing = false;
    let currentMode = null;
    let currentActions = null;
    let currentState = null;
    let selectedFreeActions = [];
    let selectedDecisionActions = [];
    let selectedDayEndActions = [];

    function init() {
        cacheElements();
        setDisconnected();
        fetchState();
    }

    function cacheElements() {
        els.connectionBanner = document.getElementById('connectionBanner');
        els.day = document.getElementById('day');
        els.turn = document.getElementById('turn');
        els.turnLabel = document.getElementById('turnLabel');
        els.turnDisplay = document.getElementById('turnDisplay');
        els.turnTotal = document.getElementById('turnTotal');
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
        els.reviewList = document.getElementById('reviewList');
        els.npcLayer = document.getElementById('npcLayer');
        els.playerMarker = document.getElementById('playerMarker');
        els.playerLabel = document.getElementById('playerLabel');
        els.operationsHeading = document.querySelector('.operations-heading h3');
        els.actionMessage = document.getElementById('actionMessage');
        els.actionGrid = document.getElementById('actionGrid');
        els.campsiteSlots = {};
        for (let i = 1; i <= 10; i++) {
            els.campsiteSlots[i] = document.querySelector(`.campsite-slot[data-slot="${i}"]`);
        }
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
            currentState = state;
            renderConnected();
            renderAll(state);
            await fetchActions();
        } catch (err) {
            apiConnected = false;
            actionsConnected = false;
            console.warn('无法连接游戏后端：', err);
            setDisconnected();
        }
    }

    async function fetchActions() {
        if (!apiConnected) {
            renderActionsDisconnected();
            return;
        }
        try {
            const res = await fetch('/api/actions', { method: 'GET' });
            if (!res.ok) {
                throw new Error('HTTP ' + res.status);
            }
            const actions = await res.json();
            if (actions.mode === 'day_end_pending') {
                try {
                    const growthRes = await fetch('/api/growth', { method: 'GET' });
                    if (!growthRes.ok) {
                        throw new Error('HTTP ' + growthRes.status);
                    }
                    actions.growth = await growthRes.json();
                } catch (growthErr) {
                    console.warn('无法读取成长目录：', growthErr);
                    actions.growth = null;
                }
            }
            actionsConnected = true;
            renderActions(actions);
        } catch (err) {
            actionsConnected = false;
            console.warn('无法读取动作目录：', err);
            renderActionsDisconnected();
        }
    }

    function setDisconnected() {
        if (els.connectionBanner) {
            els.connectionBanner.classList.remove('hidden');
        }
        clearState();
        renderActionsDisconnected();
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
        if (els.playerLabel) els.playerLabel.textContent = '小克';
    }

    function renderAll(state) {
        renderTopCards(state);
        renderMap(state);
        renderNPCs(state.active_npcs || []);
        renderIncome(state);
        renderReviewBook(state.review_history || []);
        renderOverview(state);
        renderReminders(state);
        renderEvents(state.event_history || []);
        showPlayerMarker();
    }

    function renderTopCards(state) {
        if (els.day) els.day.textContent = state.day ?? '--';
        if (els.balance) els.balance.textContent = state.balance ?? '--';
        if (els.reputation) {
            const averageRating = state.average_rating;
            els.reputation.textContent = typeof averageRating === 'number'
                ? averageRating.toFixed(1) + ' ★'
                : '-- ★';
        }

        const turn = state.turn ?? 1;
        if (els.turn) els.turn.textContent = turn;

        if (turn >= 1 && turn <= 5) {
            if (els.turnLabel) els.turnLabel.textContent = '经营轮次';
            if (els.turnTotal) els.turnTotal.textContent = ' / 5';
            if (els.turnTotal) els.turnTotal.style.display = '';
            if (els.phase) els.phase.textContent = getPhaseLabel(turn);
        } else if (turn === 6) {
            if (els.turnLabel) els.turnLabel.textContent = '当前阶段';
            if (els.turnTotal) els.turnTotal.style.display = 'none';
            if (els.phase) els.phase.textContent = '';
        }
    }

    function getPhaseLabel(turn) {
        if (turn === 1) return '迎客准备';
        if (turn >= 2 && turn <= 5) return '营业中';
        return '';
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
            const campsitePos = campsitePositionForNpc(npc);
            const pos = campsitePos || ANCHORS[anchorId];
            const sameSpot = byLocation[anchorId];
            const index = sameSpot.indexOf(npc);
            const offsetX = campsitePos ? 0 : (index % 2 === 1 ? 1 : -1) * Math.ceil(index / 2) * 14;
            const offsetY = campsitePos ? 0 : index * -10;

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

    function campsitePositionForNpc(npc) {
        if (npc.location !== 'campsite') return null;
        const slot = Number(npc.campsite_slot);
        if (!Number.isInteger(slot) || slot < 1 || slot > 10) return null;

        const slotElement = els.campsiteSlots && els.campsiteSlots[slot];
        if (!slotElement || !els.npcLayer) return null;

        const layerRect = els.npcLayer.getBoundingClientRect();
        const slotRect = slotElement.getBoundingClientRect();
        if (!layerRect.width || !layerRect.height || !slotRect.width || !slotRect.height) return null;

        return {
            left: ((slotRect.left - layerRect.left + slotRect.width / 2) / layerRect.width) * 100,
            top: ((slotRect.top - layerRect.top + slotRect.height / 2) / layerRect.height) * 100,
        };
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

    function reviewStars(rating) {
        const value = Number(rating);
        const stars = Number.isInteger(value) && value >= 1 && value <= 5 ? value : 0;
        return '★'.repeat(stars) + '☆'.repeat(5 - stars);
    }

    function renderReviewBook(reviews) {
        if (!els.reviewList) return;
        els.reviewList.innerHTML = '';
        const visibleReviews = Array.isArray(reviews) ? reviews.slice(-5).reverse() : [];
        if (visibleReviews.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'review-entry';
            empty.textContent = '暂无评价';
            els.reviewList.appendChild(empty);
            return;
        }
        visibleReviews.forEach(review => {
            const entry = document.createElement('div');
            entry.className = 'review-entry';
            const header = document.createElement('div');
            header.className = 'review-entry-header';
            const summary = document.createElement('div');
            summary.textContent = `Day ${review.created_day} · ${review.group_size}人客组`;
            const stars = document.createElement('div');
            stars.className = 'review-entry-stars';
            stars.textContent = reviewStars(review.rating);
            header.append(summary, stars);
            entry.appendChild(header);
            if (typeof review.comment === 'string' && review.comment.trim()) {
                const comment = document.createElement('div');
                comment.className = 'review-entry-comment';
                comment.textContent = review.comment;
                entry.appendChild(comment);
            }
            els.reviewList.appendChild(entry);
        });
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
            empty.textContent = '暂无经营日志';
            els.logList.appendChild(empty);
        } else {
            events.slice().reverse().forEach(event => {
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.textContent = `Day ${event.day} · Turn ${event.turn}\n${event.text}`;
                els.logList.appendChild(entry);
            });
        }

        if (els.noticeList) {
            const latest = Array.isArray(events) && events.length > 0
                ? events[events.length - 1]
                : null;
            if (!latest) {
                els.noticeList.innerHTML = '<span class="notice-chip">暂无经营事件</span>';
            } else {
                els.noticeList.innerHTML =
                    `<span class="notice-chip">${escapeHtml(String(latest.text || ''))}</span>`;
            }
        }
    }

    // ========================================================================
    // 操作面板：基于 /api/actions
    // ========================================================================

    function renderActionsDisconnected() {
        if (els.operationsHeading) els.operationsHeading.textContent = '经营操作';
        if (els.actionMessage) {
            els.actionMessage.textContent = '暂时无法读取经营操作';
            els.actionMessage.className = 'action-message action-warning';
        }
        if (els.actionGrid) els.actionGrid.innerHTML = '';
        clearGrowthCatalog();
    }

    function renderActions(actions) {
        if (!actions || !actions.success) {
            renderActionsDisconnected();
            return;
        }

        clearGrowthCatalog();
        currentMode = actions.mode;
        currentActions = actions;

        if (els.operationsHeading) {
            els.operationsHeading.textContent = actions.panel_title || '经营操作';
        }

        if (els.actionMessage) {
            els.actionMessage.textContent = '';
            els.actionMessage.className = 'action-message';
        }

        if (els.actionGrid) els.actionGrid.innerHTML = '';

        switch (actions.mode) {
            case 'opening':
                renderOpeningAction(actions.primary_action);
                break;
            case 'planning':
                renderPlanningActions(actions);
                break;
            case 'ready_to_advance':
                renderReadyToAdvance(actions);
                break;
            case 'day_end_pending':
                renderDayEndActions(actions);
                break;
            case 'day_end_completed':
                selectedDayEndActions = [];
                renderDayEndCompleted();
                break;
            default:
                renderPlaceholder('暂时无法读取经营操作');
        }
    }

    function renderOpeningAction(primary) {
        if (!els.actionGrid) return;
        const btn = document.createElement('button');
        btn.className = 'btn-action primary';
        btn.type = 'button';
        btn.textContent = primary && primary.label ? primary.label : '开始营业';
        btn.disabled = !(primary && primary.enabled);
        btn.addEventListener('click', advanceTurn);
        els.actionGrid.appendChild(btn);
    }

    function actionKey(candidate) {
        return JSON.stringify({
            action: candidate.action,
            params: candidate.params || {}
        });
    }

    function toRequestAction(candidate) {
        return {
            action: candidate.action,
            params: candidate.params || {}
        };
    }

    function renderPlanningActions(actions) {
        if (!els.actionGrid) return;
        els.actionGrid.innerHTML = '';

        const freeCandidates = actions.free_action_candidates || [];
        const decisionCandidates = actions.decision_action_candidates || [];
        const freeKeys = new Set(freeCandidates.map(actionKey));
        const decisionKeys = new Set(decisionCandidates.map(actionKey));
        selectedFreeActions = selectedFreeActions.filter(item => freeKeys.has(actionKey(item)));
        selectedDecisionActions = selectedDecisionActions.filter(item => decisionKeys.has(actionKey(item)));

        appendActionSection('免费操作', freeCandidates, selectedFreeActions, true);
        appendActionSection(
            `决策操作（已选 ${selectedDecisionActions.length}/${actions.max_decision_actions || 3}）`,
            decisionCandidates,
            selectedDecisionActions,
            false,
            actions.max_decision_actions || 3
        );

        const submit = document.createElement('button');
        submit.className = 'btn-action primary';
        submit.type = 'button';
        submit.dataset.role = 'submit-turn-plan';
        submit.textContent = (actions.primary_action && actions.primary_action.label) || '提交本轮计划';
        submit.disabled = !(actions.primary_action && actions.primary_action.enabled);
        submit.addEventListener('click', submitTurnPlan);
        els.actionGrid.appendChild(submit);
    }

    function appendActionSection(title, candidates, selected, isFree, maxDecisionActions) {
        if (!candidates.length) {
            return;
        }

        candidates.forEach(candidate => {
            const requestAction = toRequestAction(candidate);
            const selectedIndex = selected.findIndex(item => actionKey(item) === actionKey(requestAction));
            const isSelected = selectedIndex !== -1;
            const btn = document.createElement('button');
            btn.className = 'btn-action' + (isSelected ? ' primary' : '');
            btn.type = 'button';
            const detail = candidate.detail ? ` · ${candidate.detail}` : '';
            btn.textContent = `${isSelected ? '已选：' : ''}${candidate.label || candidate.action}${detail}`;
            btn.disabled = !candidate.enabled;
            btn.title = candidate.reason || '';
            btn.addEventListener('click', () => {
                if (isSelected) {
                    selected.splice(selectedIndex, 1);
                } else if (!isFree && requestAction.action === 'buy_food_package') {
                    selected.splice(
                        0,
                        selected.length,
                        ...selected.filter(item => item.action !== 'buy_food_package')
                    );
                    selected.push(requestAction);
                } else if (isFree || selected.length < maxDecisionActions) {
                    selected.push(requestAction);
                } else {
                    setActionMessage(`决策操作最多选择 ${maxDecisionActions} 项`, 'action-warning');
                }
                renderPlanningActions(currentActions);
            });
            els.actionGrid.appendChild(btn);
        });
    }

    function renderReadyToAdvance(actions) {
        if (!els.actionGrid) return;
        const plan = actions.turn_plan || {};
        const freeCount = (plan.free_actions || []).length;
        const decisionCount = (plan.decision_actions || []).length;
        const summary = document.createElement('div');
        summary.className = 'action-message';
        summary.textContent = `本轮计划已提交：${freeCount} 项免费操作，${decisionCount} 项决策操作`;
        els.actionGrid.appendChild(summary);

        const submittedActions = [
            ...(plan.free_actions || []),
            ...(plan.decision_actions || [])
        ];
        const details = document.createElement('div');
        details.className = 'action-message';
        details.textContent = submittedActions.length
            ? `已提交：${submittedActions.map(item => item.action).join('、')}`
            : '已提交空计划';
        els.actionGrid.appendChild(details);

        const primary = actions.primary_action;
        const btn = document.createElement('button');
        btn.className = 'btn-action primary';
        btn.type = 'button';
        btn.textContent = (primary && primary.label) || '推进经营轮次';
        btn.disabled = !(primary && primary.enabled);
        btn.addEventListener('click', advanceTurn);
        els.actionGrid.appendChild(btn);
    }

    function renderDayEndActions(actions) {
        if (!els.actionGrid) return;
        els.actionGrid.innerHTML = '';
        const candidates = (actions.day_end_action_candidates || [])
            .filter(candidate => candidate.action !== 'purchase_growth_project');
        const candidateKeys = new Set(candidates.map(actionKey));
        selectedDayEndActions = selectedDayEndActions.filter(item => candidateKeys.has(actionKey(item)));

        candidates.forEach(candidate => {
            const requestAction = toRequestAction(candidate);
            const selectedIndex = selectedDayEndActions.findIndex(
                item => actionKey(item) === actionKey(requestAction)
            );
            const isSelected = selectedIndex !== -1;
            const btn = document.createElement('button');
            btn.className = 'btn-action' + (isSelected ? ' primary' : '');
            btn.type = 'button';
            btn.textContent = `${isSelected ? '已选：' : ''}${candidate.label || candidate.action}`;
            btn.disabled = !candidate.enabled;
            btn.title = candidate.reason || '';
            btn.addEventListener('click', () => {
                if (isSelected) {
                    selectedDayEndActions.splice(selectedIndex, 1);
                } else if (candidate.action === 'buy_food_package') {
                    selectedDayEndActions = selectedDayEndActions.filter(
                        item => item.action !== 'buy_food_package'
                    );
                    selectedDayEndActions.push(requestAction);
                } else {
                    selectedDayEndActions.push(requestAction);
                }
                renderDayEndActions(currentActions);
            });
            els.actionGrid.appendChild(btn);
        });

        const submit = document.createElement('button');
        submit.className = 'btn-action primary';
        submit.type = 'button';
        submit.dataset.role = 'submit-day-end-actions';
        submit.textContent = '提交日终清单';
        submit.addEventListener('click', submitDayEndActions);
        els.actionGrid.appendChild(submit);
        renderGrowthCatalog(actions.growth);
    }

    function renderDayEndCompleted() {
        if (!els.actionGrid) return;
        const btn = document.createElement('button');
        btn.className = 'btn-action primary';
        btn.type = 'button';
        btn.dataset.role = 'start-next-day';
        btn.textContent = '开始新的一天';
        btn.addEventListener('click', startNextDay);
        els.actionGrid.appendChild(btn);
    }

    function clearGrowthCatalog() {
        const panel = els.actionGrid && els.actionGrid.parentElement;
        const existing = panel && panel.querySelector('[data-role="growth-catalog"]');
        if (existing) existing.remove();
    }

    function renderGrowthCatalog(growth) {
        clearGrowthCatalog();
        const panel = els.actionGrid && els.actionGrid.parentElement;
        if (!panel) return;
        const section = document.createElement('section');
        section.dataset.role = 'growth-catalog';
        const title = document.createElement('h4');
        title.textContent = '建设与升级';
        section.appendChild(title);
        const grid = document.createElement('div');
        grid.className = 'action-grid';
        section.appendChild(grid);

        const projects = growth && Array.isArray(growth.projects) ? growth.projects : [];
        const purchaseCandidates = (currentActions && currentActions.day_end_action_candidates || [])
            .filter(candidate => candidate.action === 'purchase_growth_project');
        const routes = [
            ['tent', '帐篷'],
            ['dining', '餐饮'],
            ['entertainment', '娱乐'],
            ['greenery', '绿化']
        ];
        routes.forEach(([category, label]) => {
            const routeProjects = projects.filter(project => project.category === category);
            const current = routeProjects.find(project => !project.completed);
            if (current) {
                appendGrowthProject(grid, label, current, purchaseCandidates);
            } else if (routeProjects.length) {
                appendGrowthStatus(grid, label, '已完成');
            }
        });

        const hotSpring = projects.find(project => project.project_id === 'hot_spring');
        if (hotSpring) {
            appendGrowthProject(grid, '温泉', hotSpring, purchaseCandidates);
        }
        panel.appendChild(section);
    }

    function appendGrowthStatus(container, label, status) {
        const btn = document.createElement('button');
        btn.className = 'btn-action';
        btn.type = 'button';
        btn.disabled = true;
        btn.textContent = `${label} ${status}`;
        container.appendChild(btn);
    }

    function appendGrowthProject(container, label, project, purchaseCandidates) {
        const status = project.completed
            ? '已完成'
            : project.can_purchase_now
                ? '可购买'
                : '条件未满足';
        const details = [
            `${label}：${project.display_name}`,
            `价格：${project.price}金币`,
            `状态：${status}`,
            ...growthProgressLines(project),
            !project.completed && project.affordable === false
                ? `金币不足：当前 ${currentState && currentState.balance != null ? currentState.balance : '--'} / 需要 ${project.price}`
                : ''
        ].filter(Boolean).join('\n');
        const wrapper = document.createElement('span');
        wrapper.style.width = '100%';
        wrapper.title = details;
        const candidate = purchaseCandidates.find(item => (
            item.params && item.params.project_id === project.project_id
        ));
        const btn = document.createElement('button');
        btn.className = 'btn-action' + (candidate ? '' : ' locked');
        btn.type = 'button';
        btn.style.width = '100%';
        btn.textContent = project.display_name + (project.completed ? ' 已完成' : '');
        if (!candidate) {
            btn.disabled = true;
        } else {
            const requestAction = toRequestAction(candidate);
            const selectedIndex = selectedDayEndActions.findIndex(
                item => actionKey(item) === actionKey(requestAction)
            );
            const isSelected = selectedIndex !== -1;
            if (isSelected) {
                btn.classList.add('primary');
                btn.textContent = `已选：${project.display_name}`;
            }
            btn.addEventListener('click', () => {
                if (isSelected) {
                    selectedDayEndActions.splice(selectedIndex, 1);
                } else {
                    selectedDayEndActions.push(requestAction);
                }
                renderDayEndActions(currentActions);
            });
        }
        wrapper.appendChild(btn);
        container.appendChild(wrapper);
    }

    function growthProgressLines(project) {
        const progress = project.progress || {};
        const lines = [];
        if (progress.current_operating_day != null && progress.required_operating_day != null) {
            lines.push(`经营天数：${progress.current_operating_day} / ${progress.required_operating_day}`);
        }
        if (progress.current_served_groups != null && progress.required_served_groups != null) {
            const served = `累计接待：${progress.current_served_groups} / ${progress.required_served_groups}`;
            if (progress.fallback_operating_day != null) {
                lines.push(`${served} 或 经营天数：${progress.current_operating_day} / ${progress.fallback_operating_day}`);
            } else {
                lines.push(served);
            }
        }
        if (progress.current_successful_dining_groups != null && progress.required_successful_dining_groups != null) {
            lines.push(`成功用餐：${progress.current_successful_dining_groups} / ${progress.required_successful_dining_groups}`);
        }
        if (progress.current_successful_paid_entertainment_groups != null && progress.required_successful_paid_entertainment_groups != null) {
            lines.push(`收费娱乐：${progress.current_successful_paid_entertainment_groups} / ${progress.required_successful_paid_entertainment_groups}`);
        }
        if (progress.current_successful_greenery_maintenance_count != null && progress.required_successful_greenery_maintenance_count != null) {
            lines.push(`绿化维护：${progress.current_successful_greenery_maintenance_count} / ${progress.required_successful_greenery_maintenance_count}`);
        }
        if (progress.current_completed_growth_nodes != null && progress.required_completed_growth_nodes != null) {
            lines.push(`成长节点：${progress.current_completed_growth_nodes} / ${progress.required_completed_growth_nodes}`);
        }
        return lines;
    }

    async function submitDayEndActions() {
        const btn = els.actionGrid && els.actionGrid.querySelector('[data-role="submit-day-end-actions"]');
        if (btn) btn.disabled = true;
        setActionMessage('正在提交日终清单…');

        try {
            const res = await fetch('/api/day/end', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ day_end_actions: selectedDayEndActions })
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            selectedDayEndActions = [];
            await fetchState();
            setActionMessage(result.message || '日终清单已提交');
        } catch (err) {
            console.warn('提交日终清单失败：', err);
            setActionMessage('提交日终清单失败：' + err.message, 'action-error');
            if (btn) btn.disabled = false;
        }
    }

    async function startNextDay() {
        const btn = els.actionGrid && els.actionGrid.querySelector('[data-role="start-next-day"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        setActionMessage('正在开启新的一天…');

        try {
            const res = await fetch('/api/day/start', {
                method: 'POST'
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            await fetchState();
        } catch (err) {
            console.warn('开启新的一天失败：', err);
            setActionMessage('开启新的一天失败：' + err.message, 'action-error');
            btn.disabled = false;
        }
    }

    async function submitTurnPlan() {
        const btn = els.actionGrid && els.actionGrid.querySelector('[data-role="submit-turn-plan"]');
        if (btn) btn.disabled = true;
        setActionMessage('正在提交本轮计划…');

        try {
            const res = await fetch('/api/turn/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    free_actions: selectedFreeActions,
                    actions: selectedDecisionActions
                })
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            selectedFreeActions = [];
            selectedDecisionActions = [];
            await fetchState();
            setActionMessage(
                result.message || `计划已提交：目标 Day ${result.target_day} Turn ${result.target_turn}，` +
                `${result.free_action_count} 项免费操作，${result.action_count} 项决策操作`
            );
        } catch (err) {
            console.warn('提交 Turn Plan 失败：', err);
            setActionMessage('提交计划失败：' + err.message, 'action-error');
            if (btn) btn.disabled = false;
        }
    }

    function renderPlaceholder(text) {
        if (!els.actionMessage) return;
        els.actionMessage.textContent = text;
        els.actionMessage.className = 'action-message action-placeholder';
    }

    function setActionMessage(text, type) {
        if (!els.actionMessage) return;
        els.actionMessage.textContent = text;
        els.actionMessage.className = 'action-message ' + (type || '');
    }

    async function advanceTurn() {
        if (isAdvancing) return;
        isAdvancing = true;

        const btn = els.actionGrid && els.actionGrid.querySelector('button');
        if (btn) btn.disabled = true;
        setActionMessage('正在进入营业…');

        try {
            const res = await fetch('/api/turn/advance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            setActionMessage('已进入营业阶段');
            await fetchState();
            await fetchActions();
        } catch (err) {
            console.warn('推进回合失败：', err);
            setActionMessage('进入营业失败：' + err.message, 'action-error');
            if (btn) btn.disabled = false;
        } finally {
            isAdvancing = false;
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
