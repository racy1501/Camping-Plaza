/**
 * 露营广场 · 营地总览
 * 真实只读网页入口：从 /api/state 读取后端状态并渲染，
 * 从 /api/actions 读取人类操作目录。无假数据、无 demo mode。
 */

(function () {
    'use strict';

    // 锚点坐标（百分比，以地图左上角为原点）
    const ANCHORS = {
        entrance: { top: 90, left: 35 },
        tent1: { top: 18, left: 41 },
        tent2: { top: 11, left: 54 },
        tent3: { top: 28, left: 51 },
        tent4: { top: 20, left: 59 },
        tent5: { top: 17, left: 69 },
        tent6: { top: 27, left: 79 },
        campsite: { top: 60, left: 50 },
        bonfire: { top: 52, left: 50 },
        onsenLocked: { top: 24, left: 17 },
        entertainment: { top: 83, left: 51 },
        dining: { top: 83, left: 78 }
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

    const NPC_BADGE_IMAGES = {
        day: 'assets/npc_badge_day.png',
        overnight: 'assets/npc_badge_overnight.png'
    };
    const PLAYER_NAME_RULES = '中文名限 2-3 个汉字；英文名限 2-6 个英文字母或数字。';

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
    let selectedConflictChoice = null;
    let lastSeenEventSequence = null;
    let pendingPlayerEvents = [];
    let statePollTimer = null;
    let statePollInFlight = false;
    let isReplayingPlayerEvent = false;
    let latestPolledState = null;
    let replayStatePending = false;
    let eventsRenderDeferred = false;
    let visibilityPollingBound = false;
    let playerAnchorId = 'entrance';
    let onboardingSubmitting = false;
    const SESSION_STORAGE_KEY = 'camping_plaza_session_id';
    let sessionId = '';

    function sessionUrl(path, id = sessionId) {
        const url = new URL(path, window.location.origin);
        url.searchParams.set('session_id', id);
        return url.pathname + url.search;
    }

    function sessionBody(payload = {}) {
        return { ...payload, session_id: sessionId };
    }

    async function createSession() {
        const response = await fetch('/api/session', { method: 'POST' });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.session_id) {
            throw new Error(payload.message || '无法创建新的游戏存档。');
        }
        sessionId = payload.session_id;
        window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
        const url = new URL(window.location.href);
        url.searchParams.set('session_id', sessionId);
        window.history.replaceState(null, '', url);
    }

    function isSessionNotFoundResponse(response, payload) {
        return response.status === 404
            && payload.detail?.error_code === 'session_not_found';
    }

    async function initializeSession() {
        const searchParams = new URLSearchParams(window.location.search);
        const urlSessionId = searchParams.get('session_id');
        const hasExplicitUrlSession = urlSessionId !== null;
        const savedSessionId = window.localStorage.getItem(SESSION_STORAGE_KEY);
        const requestedSessionId = hasExplicitUrlSession ? urlSessionId : savedSessionId;
        if (hasExplicitUrlSession || requestedSessionId) {
            const response = await fetch(sessionUrl('/api/state', requestedSessionId));
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (!hasExplicitUrlSession && isSessionNotFoundResponse(response, payload)) {
                    window.localStorage.removeItem(SESSION_STORAGE_KEY);
                    await createSession();
                    return;
                }
                throw new Error('指定存档不存在或 session_id 无效。');
            }
            sessionId = requestedSessionId;
            window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
            return;
        }
        await createSession();
    }

    async function init() {
        cacheElements();
        setDisconnected();
        bindVisibilityPolling();
        try {
            await initializeSession();
            fetchState();
        } catch (err) {
            console.warn('无法打开游戏存档：', err);
            setActionMessage(err.message, 'action-error');
        }
    }

    function cacheElements() {
        els.onboardingScreen = document.getElementById('onboardingScreen');
        els.playerNameForm = document.getElementById('playerNameForm');
        els.playerNameInput = document.getElementById('playerNameInput');
        els.onboardingRules = document.getElementById('onboardingRules');
        els.onboardingMessage = document.getElementById('onboardingMessage');
        els.playerNameSubmit = document.getElementById('playerNameSubmit');
        els.connectionBanner = document.getElementById('connectionBanner');
        els.day = document.getElementById('day');
        els.turn = document.getElementById('turn');
        els.turnLabel = document.getElementById('turnLabel');
        els.turnDisplay = document.getElementById('turnDisplay');
        els.turnTotal = document.getElementById('turnTotal');
        els.phase = document.getElementById('phase');
        els.balance = document.getElementById('balance');
        els.debtRemaining = document.getElementById('debtRemaining');
        els.reputation = document.getElementById('reputation');
        els.achievementCatalogButton = document.getElementById('achievementCatalogButton');
        els.achievementUnlockedCount = document.getElementById('achievementUnlockedCount');
        els.achievementModal = document.getElementById('achievementModal');
        els.achievementModalClose = document.getElementById('achievementModalClose');
        els.achievementGrid = document.getElementById('achievementGrid');
        els.noticeList = document.getElementById('noticeList');
        els.logList = document.getElementById('logList');
        els.reminderList = document.getElementById('reminderList');
        els.openSlots = document.getElementById('openSlots');
        els.activeGroups = document.getElementById('activeGroups');
        els.tomorrowReservationGroups = document.getElementById('tomorrowReservationGroups');
        els.decisionsResource = document.getElementById('decisionsResource');
        els.foodStock = document.getElementById('foodStock');
        els.greeneryState = document.getElementById('greeneryState');
        els.bonfireKitchenState = document.getElementById('bonfireKitchenState');
        els.gameHouseState = document.getElementById('gameHouseState');
        els.hotSpringState = document.getElementById('hotSpringState');
        els.incomeAccommodation = document.getElementById('income-accommodation');
        els.incomeCampsite = document.getElementById('income-campsite');
        els.incomeDining = document.getElementById('income-dining');
        els.incomeEntertainment = document.getElementById('income-entertainment');
        els.incomeTotal = document.getElementById('income-total');
        els.incomeList = document.querySelector('.panel-card.income .income-list');
        els.reviewList = document.getElementById('reviewList');
        els.npcLayer = document.getElementById('npcLayer');
        els.playerMarker = document.getElementById('playerMarker');
        els.playerLabel = document.getElementById('playerLabel');
        els.operationsHeading = document.querySelector('.operations-heading h3');
        els.actionMessage = document.getElementById('actionMessage');
        els.actionGrid = document.getElementById('actionGrid');
        els.temporaryEventModal = document.getElementById('temporaryEventModal');
        els.temporaryEventDescription = document.getElementById('temporaryEventDescription');
        els.temporaryEventChoices = document.getElementById('temporaryEventChoices');
        els.morningReview = document.getElementById('morningReview');
        els.morningGuestGroups = document.getElementById('morningGuestGroups');
        els.morningNetIncome = document.getElementById('morningNetIncome');
        els.morningNewReviews = document.getElementById('morningNewReviews');
        els.morningRating = document.getElementById('morningRating');
        els.morningReservations = document.getElementById('morningReservations');
        els.morningFoodStock = document.getElementById('morningFoodStock');
        els.campsiteSlots = {};
        for (let i = 1; i <= 10; i++) {
            els.campsiteSlots[i] = document.querySelector(`.campsite-slot[data-slot="${i}"]`);
        }
        els.tentAnchors = {};
        for (let i = 1; i <= 6; i++) {
            els.tentAnchors[i] = document.querySelector(`.anchor-tent${i}`);
        }
        if (els.achievementCatalogButton) {
            els.achievementCatalogButton.addEventListener('click', openAchievementCatalog);
        }
        if (els.achievementModalClose) {
            els.achievementModalClose.addEventListener('click', closeAchievementCatalog);
        }
        if (els.achievementModal) {
            els.achievementModal.addEventListener('click', event => {
                if (event.target === els.achievementModal) closeAchievementCatalog();
            });
        }
        if (els.playerNameForm) {
            els.playerNameForm.addEventListener('submit', submitPlayerName);
        }
    }

    function maxEventSequence(state) {
        const history = Array.isArray(state && state.event_history) ? state.event_history : [];
        return history.reduce(
            (max, event) => Math.max(max, Number(event && event.sequence) || 0),
            0,
        );
    }

    async function fetchState(options = {}) {
        const skipEvents = options.skipEvents === true;
        const candidates = ensureRepairCandidate(currentActions && currentActions.day_end_action_candidates || []);
        const budget = dayEndBudgetInfo(candidates);
        if (budget.shortfall > 0) {
            setActionMessage(`金币不足，还差${budget.shortfall}金币`, 'action-error');
            if (btn) btn.disabled = false;
            return;
        }

        try {
            const res = await fetch(sessionUrl('/api/state'), { method: 'GET' });
            if (!res.ok) {
                throw new Error('HTTP ' + res.status);
            }
            const state = await res.json();
            apiConnected = true;
            currentState = state;
            renderConnected();
            if (state.player_name == null) {
                showPlayerNameOnboarding();
                return;
            }
            hidePlayerNameOnboarding();
            renderAll(state, { skipEvents });
            if (skipEvents) eventsRenderDeferred = true;
            await fetchActions();
            initializeEventPolling(state);
        } catch (err) {
            apiConnected = false;
            actionsConnected = false;
            console.warn('无法连接游戏后端：', err);
            setDisconnected();
        }
    }

    function showPlayerNameOnboarding() {
        if (!els.onboardingScreen) return;
        els.onboardingScreen.classList.remove('hidden');
        if (els.onboardingRules) els.onboardingRules.textContent = PLAYER_NAME_RULES;
        if (!onboardingSubmitting && els.playerNameInput) els.playerNameInput.focus();
    }

    function hidePlayerNameOnboarding() {
        if (els.onboardingScreen) els.onboardingScreen.classList.add('hidden');
        if (els.onboardingMessage) els.onboardingMessage.textContent = '';
    }

    async function submitPlayerName(event) {
        event.preventDefault();
        if (onboardingSubmitting || !els.playerNameInput) return;

        onboardingSubmitting = true;
        if (els.playerNameSubmit) els.playerNameSubmit.disabled = true;
        if (els.onboardingMessage) {
            els.onboardingMessage.className = 'onboarding-message';
            els.onboardingMessage.textContent = '正在保存名称…';
        }

        try {
            const res = await fetch('/api/player/name', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionBody({ name: els.playerNameInput.value }))
            });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok || payload.success === false) {
                const detail = payload.detail || {};
                throw new Error(detail.message || payload.message || `请求失败 (${res.status})`);
            }
            await fetchState({ skipEvents: true });
        } catch (err) {
            console.warn('设置玩家名称失败：', err);
            if (els.onboardingMessage) {
                els.onboardingMessage.className = 'onboarding-message onboarding-message-error';
                els.onboardingMessage.textContent = err.message || '名称保存失败，请重试。';
            }
        } finally {
            onboardingSubmitting = false;
            if (els.playerNameSubmit) els.playerNameSubmit.disabled = false;
        }
    }

    function initializeEventPolling(state) {
        if (lastSeenEventSequence === null) {
            lastSeenEventSequence = maxEventSequence(state);
        }
        if (document.hidden || statePollTimer !== null) return;
        statePollTimer = window.setInterval(pollForPlayerEvents, 2000);
    }

    function stopEventPolling() {
        if (statePollTimer !== null) {
            window.clearInterval(statePollTimer);
            statePollTimer = null;
        }
    }

    function bindVisibilityPolling() {
        if (visibilityPollingBound) return;
        visibilityPollingBound = true;
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                stopEventPolling();
                return;
            }
            fetchState();
        });
    }

    async function pollForPlayerEvents() {
        if (statePollInFlight) return;
        statePollInFlight = true;
        try {
            const res = await fetch(sessionUrl('/api/state'), { method: 'GET' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const state = await res.json();
            const history = Array.isArray(state.event_history) ? state.event_history : [];
            const newEvents = history
                .filter(event => (Number(event && event.sequence) || 0) > lastSeenEventSequence)
                .sort((left, right) => (Number(left.sequence) || 0) - (Number(right.sequence) || 0));
            const newPlayerEvents = newEvents.filter(event => event && event.actor === 'player');
            latestPolledState = state;
            if (newPlayerEvents.length) {
                pendingPlayerEvents.push(...newPlayerEvents);
                replayStatePending = true;
                pumpPlayerEventReplay();
            } else if (
                !isReplayingPlayerEvent
                && pendingPlayerEvents.length === 0
                && (newEvents.length || eventsRenderDeferred)
            ) {
                applyPolledState(state);
            }
            lastSeenEventSequence = history.reduce(
                (max, event) => Math.max(max, Number(event && event.sequence) || 0),
                lastSeenEventSequence,
            );
        } catch (err) {
            console.warn('轮询事件失败：', err);
        } finally {
            statePollInFlight = false;
        }
    }

    function renderPlayerReplayEvent(event) {
        if (els.logList) {
            els.logList.innerHTML = '';
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.textContent = `Day ${event.day} 路 Turn ${event.turn}\n${event.text}`;
            els.logList.appendChild(entry);
        }
        if (els.noticeList) {
            els.noticeList.innerHTML =
                `<span class="notice-chip">${escapeHtml(String(event.text || ''))}</span>`;
        }
    }

    function movePlayerMarkerToAnchor(anchorId) {
        const anchor = ANCHORS[anchorId];
        if (!els.playerMarker || !anchor) return false;
        playerAnchorId = anchorId;
        els.playerMarker.style.left = anchor.left + '%';
        els.playerMarker.style.top = anchor.top + '%';
        els.playerMarker.style.bottom = 'auto';
        els.playerMarker.style.right = 'auto';
        return true;
    }

    function tentAnchorId(target) {
        const rawId = target && target.id;
        const match = String(rawId == null ? '' : rawId).match(/(\d+)$/);
        const tentId = match ? Number(match[1]) : NaN;
        return tentId >= 1 && tentId <= 6 ? `tent${tentId}` : null;
    }

    function playerReplayAnchorIds(event) {
        const targets = Array.isArray(event && event.targets) ? event.targets : [];
        switch (event && event.action) {
            case 'clean_tents':
                return targets
                    .filter(target => target && target.type === 'tent')
                    .map(tentAnchorId)
                    .filter(Boolean);
            case 'repair_tent': {
                const target = targets.find(item => item && item.type === 'tent');
                const anchorId = tentAnchorId(target);
                return anchorId ? [anchorId] : [];
            }
            case 'buy_food_package':
                return ['dining'];
            case 'make_post':
            case 'improve_service':
                return ['entrance'];
            case 'campfire':
                return ['bonfire'];
            case 'clean_campsite':
                return ['campsite'];
            case 'purchase_growth_project': {
                const target = targets[0] || {};
                if (target.type === 'tent') {
                    const anchorId = tentAnchorId(target);
                    return anchorId ? [anchorId] : [];
                }
                return {
                    dining: ['dining'],
                    entertainment: ['entertainment'],
                    hot_spring: ['onsenLocked']
                }[target.type] || [];
            }
            default:
                return [];
        }
    }

    async function playPlayerReplayMovement(event, duration = 700) {
        const anchorIds = playerReplayAnchorIds(event);
        if (anchorIds.length === 0) {
            await new Promise(resolve => window.setTimeout(resolve, duration));
            return;
        }
        const interval = duration / anchorIds.length;
        for (let index = 0; index < anchorIds.length; index += 1) {
            movePlayerMarkerToAnchor(anchorIds[index]);
            await new Promise(resolve => window.setTimeout(resolve, interval));
        }
    }

    function applyPolledState(state) {
        if (!state) return;
        currentState = state;
        apiConnected = true;
        renderConnected();
        renderAll(state);
        eventsRenderDeferred = false;
        fetchActions();
    }

    async function pumpPlayerEventReplay() {
        if (isReplayingPlayerEvent || pendingPlayerEvents.length === 0) return;
        isReplayingPlayerEvent = true;
        const event = pendingPlayerEvents.shift();
        try {
            renderPlayerReplayEvent(event);
            await playPlayerReplayMovement(event);
        } catch (err) {
            console.warn('播放玩家事件失败：', err);
        } finally {
            isReplayingPlayerEvent = false;
        }

        if (pendingPlayerEvents.length) {
            pumpPlayerEventReplay();
            return;
        }
        if (replayStatePending) {
            replayStatePending = false;
            applyPolledState(latestPolledState);
        }
    }

    async function fetchActions() {
        if (!apiConnected) {
            renderActionsDisconnected();
            return;
        }
        try {
            const res = await fetch(sessionUrl('/api/actions'), { method: 'GET' });
            if (!res.ok) {
                throw new Error('HTTP ' + res.status);
            }
            const actions = await res.json();
            if (actions.mode === 'day_end_pending') {
                try {
                    const growthRes = await fetch(sessionUrl('/api/growth'), { method: 'GET' });
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
            updateAchievementUnlockedCount(actions.achievement_unlocked_count);
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

    function showPlayerMarker(playerName) {
        const displayName = String(playerName || '玩家');
        const anchor = ANCHORS[playerAnchorId] || ANCHORS.entrance;
        if (els.playerMarker) {
            els.playerMarker.style.display = '';
            els.playerMarker.style.left = anchor.left + '%';
            els.playerMarker.style.top = anchor.top + '%';
            els.playerMarker.style.bottom = 'auto';
            els.playerMarker.style.right = 'auto';
            els.playerMarker.title = displayName;
        }
        if (els.playerLabel) els.playerLabel.textContent = displayName;
    }

    function renderAll(state, options = {}) {
        renderTopCards(state);
        renderMorningReview(state);
        renderMap(state);
        renderNPCs(state.active_npcs || [], state.tents || {});
        renderIncome(state);
        renderReviewBook(state.review_history || []);
        renderOverview(state);
        renderReminders(state);
        if (options.skipEvents !== true) {
            renderEvents(state.event_history || [], state.today_events || []);
        }
        showPlayerMarker(state.player_name);
    }

    function renderMorningReview(state) {
        if (!els.morningReview) return;
        const isTurnOne = Number(state.turn) === 1;
        els.morningReview.style.display = isTurnOne ? '' : 'none';
        if (!isTurnOne) return;
        const previous = state.previous_day_summary || {};
        const previousDay = Number(state.day) - 1;
        const newReviews = Array.isArray(state.review_history)
            ? state.review_history.filter(review => Number(review.created_day) === previousDay).length
            : 0;
        const confirmedReservations = Array.isArray(state.reservations)
            ? state.reservations.filter(reservation => (
                reservation &&
                reservation.status === 'accepted' &&
                Number(reservation.arrival_day) === Number(state.day)
            ))
            : [];
        const overnight = confirmedReservations.filter(
            reservation => reservation.visit_type === 'overnight'
        ).length;
        const day = confirmedReservations.filter(
            reservation => reservation.visit_type === 'day'
        ).length;
        const reservations = [];
        if (overnight) reservations.push(`帐篷 ${overnight} 顶`);
        if (day) reservations.push(`营位 ${day} 组`);
        els.morningGuestGroups.textContent = previous.guest_groups_served ?? '--';
        els.morningNetIncome.textContent = previous.net_income ?? '--';
        els.morningNewReviews.textContent = state.previous_day_summary ? newReviews : '--';
        els.morningRating.textContent = typeof state.average_rating === 'number' ? state.average_rating.toFixed(1) + ' ★' : '-- ★';
        els.morningReservations.textContent = reservations.length ? '今日预约：' + reservations.join('｜') : '今日预约：无';
        els.morningFoodStock.textContent = (state.food_stock ?? '--') + '份';
    }

    function renderTopCards(state) {
        if (els.day) els.day.textContent = state.day ?? '--';
        if (els.balance) {
            const balance = state.balance;
            els.balance.textContent = balance == null ? '--' : Number(balance).toLocaleString('zh-CN');
        }
        if (els.debtRemaining) {
            const debtRemaining = Number(state.debt_remaining);
            const hasDebt = Number.isFinite(debtRemaining) && debtRemaining > 0;
            els.debtRemaining.textContent = hasDebt
                ? `启动资金待还：${debtRemaining.toLocaleString('zh-CN')}`
                : '';
            els.debtRemaining.classList.toggle('hidden', !hasDebt);
        }
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
            const lock = anchor.querySelector('.tent-lock');
            if (tent && tent.unlocked) {
                anchor.classList.remove('locked');
                if (lock) lock.hidden = true;
                anchor.title = `${i}号帐篷 · ${tent.status || 'unknown'} · 容量${tent.capacity ?? '?'}`;
            } else {
                anchor.classList.add('locked');
                if (lock) lock.hidden = false;
                anchor.title = `${i}号帐篷 · 未解锁`;
            }
            const damageIndicator = anchor.querySelector('.tent-damaged-indicator');
            if (tent && tent.unlocked && tent.status === 'broken') {
                if (!damageIndicator) {
                    const indicator = document.createElement('span');
                    indicator.className = 'tent-damaged-indicator';
                    indicator.setAttribute('aria-label', '帐篷损坏');
                    indicator.textContent = '⚠️';
                    anchor.appendChild(indicator);
                }
            } else if (damageIndicator) {
                damageIndicator.remove();
            }
        }
        const hotSpring = state.hot_spring || {};
        const onsen = document.querySelector('.anchor-onsen-locked');
        if (onsen) {
            const lock = onsen.querySelector('.onsen-lock');
            const label = onsen.querySelector('.anchor-label');
            const built = hotSpring.built === true;
            onsen.classList.toggle('built', built);
            if (lock) lock.hidden = built;
            if (label) label.hidden = built;
        }
    }

    function lockAllTents() {
        for (let i = 1; i <= 6; i++) {
            const anchor = els.tentAnchors[i];
            if (!anchor) continue;
            anchor.classList.add('locked');
            const lock = anchor.querySelector('.tent-lock');
            if (lock) lock.hidden = false;
            anchor.title = `${i}号帐篷 · 未解锁`;
        }
    }

    function renderNPCs(activeNpcs, tents) {
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
            const isTent = /^tent\d+$/.test(anchorId);
            const isCampsite = Boolean(campsitePos);
            const campsiteBadgeOffsets = {
                1: { x: 0, y: -4 },
                2: { x: 0, y: -4 },
                3: { x: 0, y: -4 },
                4: { x: 0, y: -4 },
                5: { x: 0, y: 10 },
                6: { x: 0, y: -4 },
                7: { x: 0, y: -4 },
                8: { x: 0, y: -4 },
                9: { x: 0, y: -4 },
                10: { x: 0, y: -4 }
            };
            const campsiteSlot = Number(npc.campsite_slot);
            const campsiteBadge = campsiteBadgeOffsets[campsiteSlot] || { x: 0, y: -4 };
            const tentBadgeOffsets = {
                tent1: { x: 0, y: -4 },
                tent2: { x: 0, y: -4 },
                tent3: { x: 0, y: -4 },
                tent4: { x: 0, y: -4 },
                tent5: { x: 0, y: -4 },
                tent6: { x: 0, y: -4 }
            };
            const baseOffset = tentBadgeOffsets[anchorId];
            const offsetX = isTent
                ? baseOffset.x + index * 8
                : isCampsite
                    ? campsiteBadge.x + index * 8
                : (index % 2 === 1 ? 1 : -1) * Math.ceil(index / 2) * 14;
            const offsetY = isTent
                ? baseOffset.y - index * 8
                : isCampsite
                    ? campsiteBadge.y - index * 8
                : index * -10;

            const marker = document.createElement('div');
            marker.className = 'npc-marker';
            marker.style.left = pos.left + '%';
            marker.style.top = pos.top + '%';
            marker.style.transform = `translate(calc(-50% + ${offsetX}px), calc(-50% + ${offsetY}px))`;
            marker.title = `${npc.group_size}人 · ${npc.visit_type || 'unknown'} · ${npc.location}`;

            const badge = document.createElement('div');
            badge.className = 'npc-badge';
            const badgeImage = document.createElement('img');
            badgeImage.className = 'npc-badge-image';
            badgeImage.src = NPC_BADGE_IMAGES[npc.visit_type] || NPC_BADGE_IMAGES.day;
            badgeImage.alt = '';
            const badgeText = document.createElement('div');
            badgeText.className = 'npc-badge-text';
            const badgeNumber = document.createElement('span');
            badgeNumber.className = 'npc-badge-number';
            const fixedNumber = fixedBadgeNumberForNpc(npc, tents);
            badgeNumber.textContent = Number.isInteger(fixedNumber) ? `${fixedNumber}号` : '—号';
            const badgeSize = document.createElement('span');
            badgeSize.className = 'npc-badge-size';
            badgeSize.textContent = `${npc.group_size}人`;
            badgeText.append(badgeNumber, badgeSize);
            badge.append(badgeImage, badgeText);
            marker.appendChild(badge);

            els.npcLayer.appendChild(marker);
        });
    }

    function fixedBadgeNumberForNpc(npc, tents) {
        if (npc.visit_type === 'day') {
            const campsiteSlot = Number(npc.campsite_slot);
            return Number.isInteger(campsiteSlot) && campsiteSlot > 0 ? campsiteSlot : null;
        }
        if (npc.visit_type !== 'overnight') return null;

        const matchingTent = Object.entries(tents || {}).find(([, tent]) =>
            Number(tent && tent.occupied_by) === Number(npc.id)
        );
        if (!matchingTent) return null;

        const [tentKey, tent] = matchingTent;
        const tentId = Number(tent && tent.id != null ? tent.id : tentKey);
        return Number.isInteger(tentId) && tentId > 0 ? tentId : null;
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
        const expenses = state.today_expenses || {};
        const rows = (items, sign) => items.map(([label, key]) => `<div class="income-row"><span class="income-label">${label}</span><span class="income-value ${sign === '+' ? 'income-in' : 'income-out'}">${sign}${Number(items && (sign === '+' ? income : expenses)[key] || 0)}</span></div>`).join('');
        const incomeItems = [['住宿', 'accommodation'], ['营位', 'campsite'], ['餐饮', 'dining'], ['娱乐', 'entertainment'], ['温泉', 'hot_spring'], ['小费', 'tip']];
        const expenseItems = [['食材', 'food'], ['绿化', 'greenery'], ['维修', 'repair'], ['建设 / 升级', 'growth']];
        const incomeTotal = incomeItems.reduce((sum, [, key]) => sum + Number(income[key] || 0), 0);
        const expenseTotal = expenseItems.reduce((sum, [, key]) => sum + Number(expenses[key] || 0), 0);
        if (els.incomeList) {
            els.incomeList.innerHTML = `<div class="income-columns"><div class="income-column"><h4>今日收入</h4>${rows(incomeItems, '+')}</div><div class="income-column"><h4>今日支出</h4>${rows(expenseItems, '-')}</div></div><div class="income-summary"><div><span>收入合计</span><strong>+${incomeTotal}</strong></div><div><span>支出合计</span><strong>-${expenseTotal}</strong></div><div><span>今日净收益</span><strong>${incomeTotal - expenseTotal >= 0 ? '+' : ''}${incomeTotal - expenseTotal}</strong></div></div>`;
        }
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
        const greenery = state.greenery || {};
        const facilities = state.facilities || {};
        const hotSpring = state.hot_spring || {};
        const tomorrowDay = Number(state.day) + 1;
        const tomorrowReservationGroups = (Array.isArray(state.reservations) ? state.reservations : [])
            .filter(reservation => (
                reservation &&
                reservation.status === 'accepted' &&
                Number(reservation.arrival_day) === tomorrowDay
            )).length;
        const facilityLevel = key => facilities[key] && facilities[key].level;

        if (els.openSlots) els.openSlots.textContent = `${unlocked}/6`;
        if (els.activeGroups) els.activeGroups.textContent = active;
        if (els.tomorrowReservationGroups) {
            els.tomorrowReservationGroups.textContent = tomorrowReservationGroups;
        }
        const selectedDecisionCost = selectedDecisionActions.length;
        const visibleDecisionsLeft = Math.max(
            0,
            Number(state.decisions_left ?? 0) - selectedDecisionCost
        );
        if (els.decisionsResource) {
            if (Number(state.turn) === 6) {
                els.decisionsResource.textContent = state.day_end_completed === true
                    ? ''
                    : '本轮日终操作：无上限';
            } else {
                els.decisionsResource.textContent = `本轮剩余决策点：${visibleDecisionsLeft}`;
            }
        }
        if (els.foodStock) els.foodStock.textContent = `${state.food_stock ?? '--'}份`;
        if (els.greeneryState) {
            const value = Number.isFinite(Number(greenery.value)) ? Number(greenery.value).toFixed(1) : '--';
            const max = Number.isFinite(Number(greenery.max)) ? Number(greenery.max).toFixed(1) : '--';
            const maintenance = Number(greenery.level) >= 2
                ? '已满级'
                : (greenery.maintained_today ? '已维护' : '未维护');
            els.greeneryState.textContent = `${value} / ${max}｜${maintenance}`;
        }
        if (els.bonfireKitchenState) els.bonfireKitchenState.textContent = `Lv.${facilityLevel('dining') ?? '--'}`;
        if (els.gameHouseState) els.gameHouseState.textContent = `Lv.${facilityLevel('entertainment') ?? '--'}`;
        if (els.hotSpringState) els.hotSpringState.textContent = hotSpring.built === true ? '已开放' : '待建造';
    }

    function renderReminders(state) {
        if (!els.reminderList) return;
        if (state.turn === 6 && state.day_end_completed === true) {
            els.reminderList.innerHTML = '<li>暂无紧急提醒</li>';
            return;
        }
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
        if (state.turn === 6 && !state.day_end_completed && greenery.level < 2 && !greenery.maintained_today && greenery.value > 0) {
            reminders.push('如果今日不维护绿化，明天绿化值会下降 0.5。');
        }
        if (state.food_stock === 0) {
            reminders.push('食材库存为空');
        }
        if (!reminders.length) {
            reminders.push('暂无紧急提醒');
        }
        els.reminderList.innerHTML = reminders.map(r => `<li>${escapeHtml(r)}</li>`).join('');
    }

    function renderEvents(events, todayEvents) {
        if (!els.logList) return;
        const visibleEvents = (Array.isArray(events) ? events.slice() : []).concat(
            (Array.isArray(todayEvents) ? todayEvents : []).map((text, index) => ({
                day: currentState && currentState.day,
                turn: currentState && currentState.turn,
                text,
                sequence: Number.MAX_SAFE_INTEGER - ((Array.isArray(todayEvents) ? todayEvents.length : 0) - index),
            }))
        );
        visibleEvents.sort((left, right) => (
            Number(left.day || 0) - Number(right.day || 0)
            || Number(left.turn || 0) - Number(right.turn || 0)
            || Number(left.sequence || 0) - Number(right.sequence || 0)
        ));
        els.logList.innerHTML = '';
        if (visibleEvents.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'log-entry';
            empty.textContent = '暂无经营日志';
            els.logList.appendChild(empty);
        } else {
            visibleEvents.slice().reverse().forEach(event => {
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.textContent = `Day ${event.day} · Turn ${event.turn}\n${event.text}`;
                els.logList.appendChild(entry);
            });
        }

        if (els.noticeList) {
            const latest = visibleEvents.length > 0
                ? visibleEvents[visibleEvents.length - 1]
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
        renderTemporaryEventModal(null);
        clearGrowthCatalog();
    }

    function renderActions(actions) {
        if (!actions || !actions.success) {
            renderActionsDisconnected();
            return;
        }

        clearGrowthCatalog();
        clearDayEndSubmit();
        currentMode = actions.mode;
        currentActions = actions;
        if (actions.mode !== 'planning') renderTemporaryEventModal(null);

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
        const action = candidate.action;
        return JSON.stringify({
            action,
            params: action === 'repay_debt' ? {} : (candidate.params || {})
        });
    }

    function toRequestAction(candidate) {
        return {
            action: candidate.action,
            params: candidate.params || {}
        };
    }

    function dayEndBudgetInfo(candidates, selections = selectedDayEndActions) {
        const byKey = new Map((candidates || []).map(candidate => [actionKey(candidate), candidate]));
        const greeneryUpgradeSelected = selections.some(item => {
            const candidate = byKey.get(actionKey(item));
            return candidate && candidate.action === 'purchase_growth_project'
                && String(candidate.params && candidate.params.project_id || '').startsWith('greenery_');
        });
        const total = selections.reduce((sum, item) => {
            const candidate = byKey.get(actionKey(item));
            if (!candidate || (greeneryUpgradeSelected && candidate.action === 'manage_greenery')) return sum;
            const cost = candidate.action === 'repay_debt'
                ? Number(item.params && item.params.amount) || 0
                : Number(candidate.cost) || 0;
            return sum + Math.max(0, cost);
        }, 0);
        const balance = Math.max(0, Number(currentState && currentState.balance) || 0);
        return { total, balance, shortfall: Math.max(0, total - balance), byKey, greeneryUpgradeSelected };
    }

    function displayActionLabel(candidate) {
        if (candidate && candidate.action === 'clean_tents') {
            return '清洁帐篷';
        }
        const label = candidate && (candidate.label || candidate.action);
        if (
            candidate
            && Number.isFinite(candidate.remaining_today)
            && Number.isFinite(candidate.daily_limit)
        ) {
            return `${label}（${candidate.remaining_today}/${candidate.daily_limit}）`;
        }
        return label;
    }

    function updateAchievementUnlockedCount(count) {
        if (!els.achievementUnlockedCount || !Number.isFinite(count)) return;
        els.achievementUnlockedCount.textContent = `已解锁 ${count}`;
    }

    function closeAchievementCatalog() {
        if (els.achievementModal) els.achievementModal.classList.add('hidden');
    }

    function achievementStatusLabel(status) {
        return {
            locked: '未解锁',
            unlocked: '已解锁',
            hidden: '隐藏成就',
            alternative: '另一结果'
        }[status] || '';
    }

    function renderAchievementCatalog(payload) {
        if (!els.achievementGrid) return;
        els.achievementGrid.innerHTML = '';
        (payload.achievements || []).forEach(achievement => {
            const card = document.createElement('article');
            const status = String(achievement.status || 'locked');
            card.className = `achievement-item ${status}`;
            const title = document.createElement('h3');
            title.textContent = achievement.title || '隐藏成就';
            const description = document.createElement('p');
            description.textContent = achievement.description || '';
            const badge = document.createElement('span');
            badge.className = 'achievement-status';
            badge.textContent = achievementStatusLabel(status);
            card.append(title, description, badge);
            els.achievementGrid.appendChild(card);
        });
    }

    async function openAchievementCatalog() {
        if (!sessionId) return;
        try {
            const res = await fetch(sessionUrl('/api/achievements'), { method: 'GET' });
            const payload = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(payload.message || `请求失败 (${res.status})`);
            updateAchievementUnlockedCount(payload.unlocked_count);
            renderAchievementCatalog(payload);
            if (els.achievementModal) els.achievementModal.classList.remove('hidden');
        } catch (err) {
            setActionMessage('无法读取成就图鉴：' + err.message, 'action-error');
        }
    }

    function ensureRepairCandidate(candidates) {
        const result = Array.isArray(candidates) ? candidates.slice() : [];
        if (!result.some(candidate => candidate && candidate.action === 'repair_tent')) {
            result.push({
                action: 'repair_tent',
                params: {},
                label: '维修帐篷',
                enabled: false,
                reason: '当前没有待维修帐篷'
            });
        }
        return result;
    }

    function renderPlanningActions(actions) {
        if (!els.actionGrid) return;
        els.actionGrid.innerHTML = '';
        renderTemporaryEventModal(actions.temporary_event);

        const freeCandidates = actions.free_action_candidates || [];
        const decisionCandidates = ensureRepairCandidate(actions.decision_action_candidates || []);
        const maxDecisionActions = Math.max(0, actions.max_decision_actions ?? 3);
        const freeKeys = new Set(freeCandidates.map(actionKey));
        const decisionKeys = new Set(decisionCandidates.map(actionKey));
        selectedFreeActions = selectedFreeActions.filter(item => freeKeys.has(actionKey(item)));
        selectedDecisionActions = selectedDecisionActions
            .filter(item => decisionKeys.has(actionKey(item)))
            .slice(0, maxDecisionActions);

        appendActionSection('免费操作', freeCandidates, selectedFreeActions, true);
        appendActionSection(
            `决策操作（已选 ${selectedDecisionActions.length}/${maxDecisionActions}）`,
            decisionCandidates,
            selectedDecisionActions,
            false,
            maxDecisionActions
        );

        const submit = document.createElement('button');
        submit.className = 'btn-action primary';
        submit.type = 'button';
        submit.dataset.role = 'submit-turn-plan';
        submit.textContent = (actions.primary_action && actions.primary_action.label) || '提交本轮计划';
        submit.disabled = !(actions.primary_action && actions.primary_action.enabled);
        submit.addEventListener('click', submitTurnPlan);
        els.actionGrid.appendChild(submit);
        if (currentState) renderOverview(currentState);
    }

    function renderTemporaryEventModal(event) {
        if (!els.temporaryEventModal) return;
        if (!event) {
            selectedConflictChoice = null;
            els.temporaryEventModal.classList.add('hidden');
            return;
        }
        if (els.temporaryEventDescription) {
            els.temporaryEventDescription.textContent = event.description || '临时事件需要处理。';
        }
        if (!els.temporaryEventChoices) return;
        els.temporaryEventChoices.innerHTML = '';
        (event.choices || []).forEach(choice => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn-action compact';
            button.textContent = choice.label;
            const detail = document.createElement('small');
            detail.textContent = `消耗${choice.decision_cost}个决策点 · ${choice.effect}`;
            button.appendChild(detail);
            button.addEventListener('click', async () => {
                const buttons = els.temporaryEventChoices.querySelectorAll('button');
                buttons.forEach(item => { item.disabled = true; });
                try {
                    const res = await fetch('/api/action', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(sessionBody({
                            action: 'resolve_temporary_conflict',
                            params: { choice: choice.value }
                        }))
                    });
                    const result = await res.json().catch(() => ({}));
                    if (!res.ok || result.success === false) {
                        throw new Error(result.message || `请求失败 (${res.status})`);
                    }
                    selectedConflictChoice = null;
                    await fetchState({ skipEvents: true });
                    setActionMessage(result.message || '临时事件已处理。');
                } catch (err) {
                    buttons.forEach(item => { item.disabled = false; });
                    setActionMessage('处理临时事件失败：' + err.message, 'action-error');
                }
            });
            els.temporaryEventChoices.appendChild(button);
        });
        els.temporaryEventModal.classList.remove('hidden');
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
            btn.className = 'btn-action compact' + (isSelected ? ' primary' : '');
            btn.type = 'button';
            const detail = candidate.detail ? ` · ${candidate.detail}` : '';
            btn.textContent = `${isSelected ? '已选：' : ''}${displayActionLabel(candidate)}${detail}`;
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
        clearDayEndSubmit();
        const allCandidates = Array.isArray(actions.day_end_action_candidates)
            ? actions.day_end_action_candidates
            : [];
        const candidates = allCandidates
            .filter(candidate => candidate.action !== 'purchase_growth_project');
        const greeneryUpgradeSelected = selectedDayEndActions.some(item => (
            item.action === 'purchase_growth_project'
            && String(item.params && item.params.project_id || '').startsWith('greenery_')
        ));
        const candidateKeys = new Set(allCandidates.map(actionKey));
        selectedDayEndActions = selectedDayEndActions.filter(item => candidateKeys.has(actionKey(item)));
        const budget = dayEndBudgetInfo(allCandidates);
        renderDayEndSelectionSummary(actions);

        candidates.forEach(candidate => {
            let requestAction = toRequestAction(candidate);
            const selectedIndex = selectedDayEndActions.findIndex(
                item => actionKey(item) === actionKey(requestAction)
            );
            const isSelected = selectedIndex !== -1;
            const btn = document.createElement('button');
            btn.className = 'btn-action compact' + (isSelected ? ' primary' : '');
            btn.type = 'button';
            btn.textContent = `${isSelected ? '已选：' : ''}${displayActionLabel(candidate)}`;
            const blockedByGreeneryUpgrade = candidate.action === 'manage_greenery' && greeneryUpgradeSelected;
            const candidateCost = Math.max(0, Number(candidate.cost) || 0);
            const overBudget = !isSelected && candidate.enabled
                && budget.total + candidateCost > budget.balance;
            btn.disabled = !isSelected && (!candidate.enabled || blockedByGreeneryUpgrade || overBudget);
            if (overBudget) {
                btn.title = `金币不足，还差${budget.total + candidateCost - budget.balance}金币`;
            }
            btn.title = candidate.action === 'manage_greenery' && candidate.reason === '已满级'
                ? '已满级'
                : (blockedByGreeneryUpgrade
                ? '绿化升级已包含当日维护，无需重复打理。'
                : (candidate.reason || ''));
            btn.addEventListener('click', () => {
                if (isSelected) {
                    selectedDayEndActions.splice(selectedIndex, 1);
                } else if (candidate.action === 'repay_debt') {
                    const amountText = window.prompt(
                        `请输入还款金额（1-${candidate.max_amount}）`,
                        String(candidate.max_amount || '')
                    );
                    if (amountText === null) return;
                    const amount = Number(amountText);
                    if (
                        !Number.isInteger(amount)
                        || amount < candidate.min_amount
                        || amount > candidate.max_amount
                    ) {
                        setActionMessage(`还款金额必须是 ${candidate.min_amount}-${candidate.max_amount} 的整数`, 'action-error');
                        return;
                    }
                    requestAction = {
                        action: candidate.action,
                        params: { amount }
                    };
                    selectedDayEndActions.push(requestAction);
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

        renderGrowthCatalog(actions.growth);
        renderDayEndSubmit(actions.day_end_budget_hint);
    }

    function renderDayEndSelectionSummary(actions) {
        if (!els.actionMessage) return;
        const candidates = actions.day_end_action_candidates || [];
        const labels = selectedDayEndActions.map(item => {
            const candidate = candidates.find(entry => actionKey(entry) === actionKey(item));
            return candidate ? displayActionLabel(candidate) : item.action;
        });
        els.actionMessage.className = 'action-message';
        els.actionMessage.textContent = labels.length
            ? `本轮已选日终操作：${labels.join('、')}`
            : '';
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

    function clearDayEndSubmit() {
        const panel = els.actionGrid && els.actionGrid.parentElement;
        const existing = panel && panel.querySelector('[data-role="day-end-submit"]');
        if (existing) existing.remove();
    }

    function renderDayEndSubmit(budgetHint) {
        const panel = els.actionGrid && els.actionGrid.parentElement;
        if (!panel) return;
        const section = document.createElement('div');
        section.className = 'day-end-submit';
        section.dataset.role = 'day-end-submit';
        if (budgetHint) {
            const hint = document.createElement('p');
            hint.className = 'day-end-budget-hint';
            hint.textContent = budgetHint;
            section.appendChild(hint);
        }
        const submit = document.createElement('button');
        submit.className = 'btn-action confirm';
        submit.type = 'button';
        submit.dataset.role = 'submit-day-end-actions';
        submit.textContent = '提交日终清单并开启新一天';
        submit.addEventListener('click', submitDayEndActions);
        section.appendChild(submit);
        panel.appendChild(section);
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
        const allCandidates = Array.isArray(currentActions && currentActions.day_end_action_candidates)
            ? currentActions.day_end_action_candidates
            : [];
        const budget = dayEndBudgetInfo(allCandidates);
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
                appendGrowthProject(grid, label, current, purchaseCandidates, budget);
            } else if (routeProjects.length) {
                appendGrowthStatus(grid, label, growthCompletionStatus(category));
            }
        });

        const hotSpring = projects.find(project => project.project_id === 'hot_spring');
        if (hotSpring) {
            if (hotSpring.completed) {
                appendGrowthStatus(grid, '温泉', '已建设');
            } else {
                appendGrowthProject(grid, '温泉', hotSpring, purchaseCandidates, budget);
            }
        }
        panel.appendChild(section);
    }

    function growthCompletionStatus(category) {
        return {
            tent: '已购完',
            dining: '已满级',
            entertainment: '已满级',
            greenery: '已满级'
        }[category] || '已完成';
    }

    function appendGrowthStatus(container, label, status) {
        const btn = document.createElement('button');
        btn.className = 'btn-action compact';
        btn.type = 'button';
        btn.disabled = true;
        btn.textContent = `${label} ${status}`;
        container.appendChild(btn);
    }

    function appendGrowthProject(container, label, project, purchaseCandidates, budget) {
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
        btn.className = 'btn-action compact' + (candidate ? '' : ' locked');
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
            const candidateCost = Math.max(0, Number(candidate.cost) || 0);
            const overBudget = !isSelected && candidate.enabled
                && budget.total + candidateCost > budget.balance;
            btn.disabled = !isSelected && (!candidate.enabled || overBudget);
            if (overBudget) btn.title = `金币不足，还差${budget.total + candidateCost - budget.balance}金币`;
            if (isSelected) {
                btn.classList.add('primary');
                btn.textContent = `已选：${project.display_name}`;
            }
            btn.addEventListener('click', () => {
                if (isSelected) {
                    selectedDayEndActions.splice(selectedIndex, 1);
                } else {
                    selectedDayEndActions = selectedDayEndActions.filter(item => item.action !== 'manage_greenery');
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

    function dayEndActionResultLabel(item) {
        const params = item.params || {};
        switch (item.action) {
            case 'repay_debt':
                return '偿还债务';
            case 'clean_tents':
                return '清洁帐篷';
            case 'repair_tent':
                return params.tent_id != null ? `维修${params.tent_id}号帐篷` : '维修帐篷';
            case 'buy_food_package':
                return '补充食材';
            case 'manage_greenery':
                return '维护绿化';
            case 'purchase_growth_project':
                return '购买成长项目';
            default:
                return item.action || '日终行动';
        }
    }

    function formatDayEndResultSummary(result) {
        const items = Array.isArray(result.results) ? result.results : [];
        const succeeded = items.filter(item => item && item.success === true);
        const failed = items.filter(item => item && item.success === false);
        const successes = succeeded.map(dayEndActionResultLabel).join('、');
        const failures = failed.map(item => {
            const reason = item.message || item.error_code || '执行失败';
            return `${dayEndActionResultLabel(item)}失败：${reason}`;
        }).join('；');
        const balance = Number.isFinite(result.balance) ? `当前余额 ${result.balance}。` : '';

        switch (result.action_execution_status) {
            case 'all_succeeded':
                return `日终处理完成：${successes}。${balance}请确认进入新的一天。`;
            case 'partial_success':
                return `日终处理完成。已执行：${successes}；未执行：${failures}。${balance}请确认进入新的一天。`;
            case 'all_failed':
                return `本次日终行动均未执行：${failures}。${balance}请确认进入新的一天。`;
            case 'no_actions':
                return `日终处理已完成。${balance}请确认进入新的一天。`;
            default:
                return `日终处理已完成。${balance}请确认进入新的一天。`;
        }
    }

    function formatTurnPlanResultSummary(result) {
        const failures = Array.isArray(result.action_failures) ? result.action_failures : [];
        if (!failures.length) return '本轮计划已执行，已进入下一经营轮次。';
        const details = failures.map(item => {
            const action = item.action || '经营动作';
            const reason = item.message || item.error_code || '执行失败';
            return `${action}失败：${reason}`;
        }).join('；');
        return `本轮计划已执行，但部分动作未完成：${details}`;
    }

    async function submitDayEndActions() {
        const btn = els.actionGrid && els.actionGrid.querySelector('[data-role="submit-day-end-actions"]');
        if (btn) btn.disabled = true;
        setActionMessage('正在提交日终清单…');

        try {
            const res = await fetch('/api/day/end', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionBody({ day_end_actions: selectedDayEndActions }))
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            selectedDayEndActions = [];
            await fetchState({ skipEvents: true });
            const feedbackType = result.action_execution_status === 'partial_success'
                || result.action_execution_status === 'all_failed'
                ? 'action-warning'
                : '';
            setActionMessage(formatDayEndResultSummary(result), feedbackType);
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
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionBody())
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            await fetchState({ skipEvents: true });
            setActionMessage('已进入新的一天。');
        } catch (err) {
            console.warn('开启新的一天失败：', err);
            setActionMessage('开启新的一天失败：' + err.message, 'action-error');
            btn.disabled = false;
        }
    }

    async function submitTurnPlan() {
        const btn = els.actionGrid && els.actionGrid.querySelector('[data-role="submit-turn-plan"]');
        const temporaryEvent = currentActions && currentActions.temporary_event;
        if (temporaryEvent) {
            setActionMessage('请先处理临时事件。', 'action-warning');
            return;
        }
        if (btn) btn.disabled = true;
        setActionMessage('正在提交本轮计划…');

        try {
            const res = await fetch('/api/turn/plan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionBody({
                    free_actions: selectedFreeActions,
                    actions: selectedDecisionActions
                }))
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            selectedFreeActions = [];
            selectedDecisionActions = [];
            selectedConflictChoice = null;
            await fetchState({ skipEvents: true });
            setActionMessage(formatTurnPlanResultSummary(result), result.action_failures?.length ? 'action-warning' : '');
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
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sessionBody())
            });
            const result = await res.json().catch(() => ({}));
            if (!res.ok || result.success === false) {
                throw new Error(result.message || `请求失败 (${res.status})`);
            }
            setActionMessage('已进入营业阶段');
            await fetchState({ skipEvents: true });
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
