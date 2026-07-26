/**
 * 露营广场 - 营地地图渲染
 * Canvas绘制：帐篷、设施、NPC、绿化
 */

class CampMap {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.width = 600;
        this.height = 800;

        // 莫兰迪配色
        this.colors = {
            grass: '#c8d4bc',
            grassDark: '#a8b5a0',
            path: '#d4cec4',
            water: '#9fb4c7',
            tentBase: '#e8e2db',
            tentStroke: '#8a7e72',
            tentAvailable: '#a8b5a0',
            tentOccupied: '#c9a9a0',
            tentCleaning: '#d4c5a0',
            tentBroken: '#b8908a',
            tentReserved: '#9fb4c7',
            facilityFill: '#d4b5a0',
            facilityStroke: '#8a7e72',
            npcDot: '#7a6e62',
            npcText: '#5a5249',
            gate: '#b8a898',
            greenery: '#a8b5a0',
            hotSpring: '#9fb4c7',
            dining: '#d4b5a0',
            entertainment: '#d4c5a0',
            text: '#5a5249'
        };

        // 设施位置（基于草图）
        this.positions = {
            tents: {
                1: { x: 120, y: 160, capacity: 1 },
                2: { x: 300, y: 110, capacity: 2 },
                3: { x: 470, y: 180, capacity: 2 },
                4: { x: 140, y: 400, capacity: 3 },
                5: { x: 350, y: 330, capacity: 3 },
                6: { x: 470, y: 460, capacity: 5 }
            },
            hotSpring: { x: 80, y: 280 },
            dining: { x: 300, y: 530 },
            entertainmentA: { x: 170, y: 620 },
            entertainmentB: { x: 430, y: 620 },
            gate: { x: 300, y: 730 }
        };

        this.npcs = [];
        this.tents = {};
    }

    /**
     * 绘制整个地图
     */
    draw(tentsData, npcsData) {
        this.tents = tentsData || {};
        this.npcs = npcsData || [];

        const ctx = this.ctx;

        // 清除画布
        ctx.clearRect(0, 0, this.width, this.height);

        // 绘制背景（草地）
        this._drawBackground();

        // 绘制边界
        this._drawBoundary();

        // 绘制路径
        this._drawPaths();

        // 绘制设施
        this._drawHotSpring();
        this._drawDining();
        this._drawEntertainment();
        this._drawGate();

        // 绘制绿化区域
        this._drawGreenery();

        // 绘制帐篷
        this._drawTents();

        // 绘制NPC
        this._drawNPCs();

        // 绘制标题
        this._drawTitle();
    }

    /**
     * 绘制草地背景
     */
    _drawBackground() {
        const ctx = this.ctx;

        // 主草地
        ctx.fillStyle = this.colors.grass;
        ctx.fillRect(0, 0, this.width, this.height);

        // 添加像素噪点纹理
        ctx.fillStyle = this.colors.grassDark;
        for (let i = 0; i < 200; i++) {
            const x = Math.floor(Math.random() * this.width / 4) * 4;
            const y = Math.floor(Math.random() * this.height / 4) * 4;
            ctx.fillRect(x, y, 4, 4);
        }
    }

    /**
     * 绘制营地边界
     */
    _drawBoundary() {
        const ctx = this.ctx;
        ctx.strokeStyle = '#8a7e72';
        ctx.lineWidth = 3;
        ctx.setLineDash([]);

        // 不规则边界（模拟手绘）
        ctx.beginPath();
        ctx.moveTo(40, 50);
        ctx.lineTo(560, 50);
        ctx.lineTo(570, 700);
        ctx.lineTo(520, 760);
        ctx.lineTo(80, 760);
        ctx.lineTo(30, 700);
        ctx.closePath();
        ctx.stroke();
    }

    /**
     * 绘制小径
     */
    _drawPaths() {
        const ctx = this.ctx;
        ctx.strokeStyle = this.colors.path;
        ctx.lineWidth = 12;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.setLineDash([]);

        // 主干道（从入口向上分叉）
        ctx.beginPath();
        ctx.moveTo(300, 730);
        ctx.lineTo(300, 530);
        ctx.stroke();

        // 分叉到左侧帐篷
        ctx.beginPath();
        ctx.moveTo(300, 450);
        ctx.lineTo(140, 400);
        ctx.stroke();

        // 分叉到右侧帐篷
        ctx.beginPath();
        ctx.moveTo(300, 450);
        ctx.lineTo(470, 460);
        ctx.stroke();

        // 中部分叉
        ctx.beginPath();
        ctx.moveTo(300, 350);
        ctx.lineTo(350, 330);
        ctx.stroke();

        // 左上分叉
        ctx.beginPath();
        ctx.moveTo(300, 250);
        ctx.lineTo(120, 160);
        ctx.stroke();

        // 右上分叉
        ctx.beginPath();
        ctx.moveTo(300, 250);
        ctx.lineTo(470, 180);
        ctx.stroke();

        // 到温泉
        ctx.lineWidth = 8;
        ctx.beginPath();
        ctx.moveTo(200, 280);
        ctx.lineTo(80, 280);
        ctx.stroke();

        // 到娱乐区
        ctx.lineWidth = 10;
        ctx.beginPath();
        ctx.moveTo(300, 580);
        ctx.lineTo(170, 620);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(300, 580);
        ctx.lineTo(430, 620);
        ctx.stroke();

        ctx.lineWidth = 1;
    }

    /**
     * 绘制帐篷
     */
    _drawTents() {
        const ctx = this.ctx;

        for (const [id, tent] of Object.entries(this.tents)) {
            const pos = this.positions.tents[parseInt(id)];
            if (!pos) continue;

            const radius = 16 + pos.capacity * 4;
            const statusColor = this._getTentColor(tent.status);

            // 帐篷底座（阴影）
            ctx.fillStyle = 'rgba(0,0,0,0.08)';
            ctx.beginPath();
            ctx.arc(pos.x + 2, pos.y + 2, radius, 0, Math.PI * 2);
            ctx.fill();

            // 帐篷主体
            ctx.fillStyle = statusColor;
            ctx.beginPath();
            ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2);
            ctx.fill();

            // 帐篷边框
            ctx.strokeStyle = this.colors.tentStroke;
            ctx.lineWidth = 2;
            ctx.stroke();

            // 帐篷编号
            ctx.fillStyle = this.colors.text;
            ctx.font = 'bold 14px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(id, pos.x, pos.y - 4);

            // 容量标记
            ctx.font = '10px monospace';
            ctx.fillStyle = '#8a7e72';
            ctx.fillText(`${pos.capacity}人`, pos.x, pos.y + 10);

            // 等级标记
            if (tent.level > 0) {
                ctx.fillStyle = '#c9a96a';
                ctx.font = '10px monospace';
                const stars = '★'.repeat(tent.level);
                ctx.fillText(stars, pos.x, pos.y + 22);
            }

            // 故障标记
            if (tent.status === 'broken') {
                ctx.fillStyle = '#b8908a';
                ctx.font = '16px monospace';
                ctx.fillText('⚠', pos.x + radius - 6, pos.y - radius + 6);
            }
        }
    }

    /**
     * 绘制温泉
     */
    _drawHotSpring() {
        const ctx = this.ctx;
        const pos = this.positions.hotSpring;

        // 水面
        ctx.fillStyle = this.colors.hotSpring;
        ctx.beginPath();
        ctx.ellipse(pos.x, pos.y, 50, 35, 0, 0, Math.PI * 2);
        ctx.fill();

        // 边框
        ctx.strokeStyle = '#7a9ab5';
        ctx.lineWidth = 2;
        ctx.stroke();

        // 蒸汽效果（像素风小圆点）
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        const time = Date.now() / 1000;
        for (let i = 0; i < 5; i++) {
            const sx = pos.x - 20 + i * 10 + Math.sin(time + i) * 3;
            const sy = pos.y - 20 - i * 5 + Math.cos(time + i * 0.7) * 2;
            ctx.beginPath();
            ctx.arc(sx, sy, 3, 0, Math.PI * 2);
            ctx.fill();
        }

        // 标签
        ctx.fillStyle = this.colors.text;
        ctx.font = '12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('♨ 温泉', pos.x, pos.y + 5);
    }

    /**
     * 绘制餐饮区
     */
    _drawDining() {
        const ctx = this.ctx;
        const pos = this.positions.dining;

        // 建筑底座
        ctx.fillStyle = this.colors.dining;
        ctx.fillRect(pos.x - 40, pos.y - 25, 80, 50);
        ctx.strokeStyle = this.colors.facilityStroke;
        ctx.lineWidth = 2;
        ctx.strokeRect(pos.x - 40, pos.y - 25, 80, 50);

        // 篝火图标
        ctx.fillStyle = '#c97a5a';
        ctx.font = '18px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('🔥', pos.x, pos.y - 2);

        // 标签
        ctx.fillStyle = this.colors.text;
        ctx.font = '11px monospace';
        ctx.fillText('餐饮区', pos.x, pos.y + 18);
    }

    /**
     * 绘制娱乐区
     */
    _drawEntertainment() {
        const ctx = this.ctx;

        // A区
        const posA = this.positions.entertainmentA;
        ctx.fillStyle = this.colors.entertainment;
        ctx.fillRect(posA.x - 30, posA.y - 20, 60, 40);
        ctx.strokeStyle = this.colors.facilityStroke;
        ctx.lineWidth = 2;
        ctx.strokeRect(posA.x - 30, posA.y - 20, 60, 40);
        ctx.fillStyle = this.colors.text;
        ctx.font = '11px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('🎮 娱乐A', posA.x, posA.y + 3);

        // B区
        const posB = this.positions.entertainmentB;
        ctx.fillStyle = this.colors.entertainment;
        ctx.fillRect(posB.x - 30, posB.y - 20, 60, 40);
        ctx.strokeStyle = this.colors.facilityStroke;
        ctx.lineWidth = 2;
        ctx.strokeRect(posB.x - 30, posB.y - 20, 60, 40);
        ctx.fillStyle = this.colors.text;
        ctx.fillText('🎯 娱乐B', posB.x, posB.y + 3);
    }

    /**
     * 绘制入口
     */
    _drawGate() {
        const ctx = this.ctx;
        const pos = this.positions.gate;

        ctx.fillStyle = this.colors.gate;
        ctx.fillRect(pos.x - 35, pos.y - 12, 70, 24);
        ctx.strokeStyle = this.colors.facilityStroke;
        ctx.lineWidth = 2;
        ctx.strokeRect(pos.x - 35, pos.y - 12, 70, 24);

        ctx.fillStyle = this.colors.text;
        ctx.font = 'bold 12px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('⛺ 入口', pos.x, pos.y);
    }

    /**
     * 绘制绿化区域
     */
    _drawGreenery() {
        const ctx = this.ctx;

        // 底部绿化装饰
        ctx.fillStyle = this.colors.greenery;
        for (let i = 0; i < 8; i++) {
            const x = 60 + i * 70;
            const y = 690 + Math.sin(i * 0.8) * 10;
            // 像素风小树
            ctx.fillRect(x - 4, y, 8, 12);
            ctx.fillStyle = '#8a9e80';
            ctx.fillRect(x - 8, y - 8, 16, 12);
            ctx.fillStyle = this.colors.greenery;
        }

        // 绿化标签
        ctx.fillStyle = this.colors.text;
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('🌿 绿化带', 300, 710);
    }

    /**
     * 绘制NPC
     */
    _drawNPCs() {
        const ctx = this.ctx;

        for (const npc of this.npcs) {
            const pos = this._getNPCPosition(npc);
            if (!pos) continue;

            // NPC圆点
            ctx.fillStyle = this.colors.npcDot;
            ctx.beginPath();
            ctx.arc(pos.x, pos.y, 8, 0, Math.PI * 2);
            ctx.fill();

            // 白色边框
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 2;
            ctx.stroke();

            // 人数标签
            if (npc.group_size > 1) {
                ctx.fillStyle = '#fff';
                ctx.font = 'bold 9px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(npc.group_size, pos.x, pos.y);
            }
        }
    }

    /**
     * 绘制标题
     */
    _drawTitle() {
        const ctx = this.ctx;
        ctx.fillStyle = this.colors.text;
        ctx.font = 'bold 14px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText('🏕️ 露营广场', 12, 12);
    }

    /**
     * 获取NPC在地图上的位置
     */
    _getNPCPosition(npc) {
        if (!npc.location) return null;

        if (npc.location.startsWith('tent_')) {
            const tentId = parseInt(npc.location.split('_')[1]);
            const tentPos = this.positions.tents[tentId];
            if (tentPos) {
                // 偏移避免重叠
                return {
                    x: tentPos.x + 20,
                    y: tentPos.y + 15
                };
            }
        }

        const facilityPositions = {
            'dining': this.positions.dining,
            'entertainment': this.positions.entertainmentA,
            'gate': this.positions.gate
        };

        const pos = facilityPositions[npc.location];
        if (pos) {
            return { x: pos.x + 45, y: pos.y };
        }

        return null;
    }

    /**
     * 根据帐篷状态返回颜色
     */
    _getTentColor(status) {
        const map = {
            'available': this.colors.tentAvailable,
            'occupied': this.colors.tentOccupied,
            'cleaning': this.colors.tentCleaning,
            'broken': this.colors.tentBroken,
            'reserved': this.colors.tentReserved
        };
        return map[status] || this.colors.tentBase;
    }
}
