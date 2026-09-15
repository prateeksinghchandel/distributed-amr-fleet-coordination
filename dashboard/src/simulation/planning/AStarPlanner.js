/**
 * 8-connected grid A* planner for static warehouse obstacles.
 * Mirrors backend/robot/planning/astar.py so browser simulation and Python AMRs
 * make compatible global-navigation decisions.
 */

export class PathNotFoundError extends Error {
    constructor(message) {
        super(message);
        this.name = 'PathNotFoundError';
    }
}

export class AStarPlanner {
    constructor(width, height, { resolution = 0.25, robotRadius = 0.4, safetyMargin = 0.1 } = {}) {
        if (width <= 0 || height <= 0 || resolution <= 0) throw new Error('Invalid planner dimensions/resolution');
        this.width = width;
        this.height = height;
        this.resolution = resolution;
        this.robotRadius = robotRadius;
        this.safetyMargin = safetyMargin;
        this.margin = robotRadius + safetyMargin;
        this.cols = Math.max(1, Math.ceil(width / resolution));
        this.rows = Math.max(1, Math.ceil(height / resolution));
    }

    plan(start, goal, obstacles = [], smooth = true) {
        this.validate(start, 'start');
        this.validate(goal, 'goal');
        if (Math.hypot(goal.x - start.x, goal.y - start.y) <= this.resolution * 0.5) return [{ ...goal }];
        const blocked = this.blockedCells(obstacles);
        const s = this.cellFor(start), g = this.cellFor(goal);
        if (blocked.has(key(s))) throw new PathNotFoundError('start lies inside an inflated obstacle');
        if (blocked.has(key(g))) throw new PathNotFoundError('goal lies inside an inflated obstacle');
        const cells = this.search(s, g, blocked);
        if (!cells) throw new PathNotFoundError(`no path from (${start.x},${start.y}) to (${goal.x},${goal.y})`);
        const raw = [{ ...start }, ...cells.slice(1, -1).map((c) => this.pointFor(c)), { ...goal }];
        return smooth ? this.smoothPath(raw, obstacles) : raw;
    }

    distance(path) {
        return path.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - path[i].x, p.y - path[i].y), 0);
    }

    smoothPath(points, obstacles) {
        if (points.length <= 2) return points;
        const result = [points[0]];
        let anchor = 0;
        while (anchor < points.length - 1) {
            let furthest = anchor + 1;
            for (let i = anchor + 2; i < points.length; i++) {
                if (this.segmentIsFree(points[anchor], points[i], obstacles)) furthest = i;
                else break;
            }
            result.push(points[furthest]);
            anchor = furthest;
        }
        return result;
    }

    segmentIsFree(a, b, obstacles) {
        for (const o of obstacles) {
            if (segmentRect(a, b, o.x - this.margin, o.y - this.margin,
                o.x + o.width + this.margin, o.y + o.height + this.margin)) return false;
        }
        return true;
    }

    search(start, goal, blocked) {
        const heap = [[this.heuristic(start, goal), 0, start]];
        const came = new Map(), gScore = new Map([[key(start), 0]]), closed = new Set();
        const moves = [[-1,0,1],[1,0,1],[0,-1,1],[0,1,1],[-1,-1,Math.SQRT2],[-1,1,Math.SQRT2],[1,-1,Math.SQRT2],[1,1,Math.SQRT2]];
        while (heap.length) {
            heap.sort((a,b) => a[0] - b[0] || a[1] - b[1]);
            const [, gc, cur] = heap.shift();
            const ck = key(cur);
            if (closed.has(ck)) continue;
            closed.add(ck);
            if (cur.x === goal.x && cur.y === goal.y) return reconstruct(came, cur);
            for (const [dx,dy,cost] of moves) {
                const n = { x: cur.x + dx, y: cur.y + dy };
                const nk = key(n);
                if (!this.inBounds(n) || blocked.has(nk) || closed.has(nk)) continue;
                if (dx && dy && (blocked.has(key({x:cur.x+dx,y:cur.y})) || blocked.has(key({x:cur.x,y:cur.y+dy})))) continue;
                const ng = gc + cost;
                if (ng < (gScore.get(nk) ?? Infinity)) {
                    gScore.set(nk, ng); came.set(nk, cur);
                    heap.push([ng + this.heuristic(n, goal), ng, n]);
                }
            }
        }
        return null;
    }

    blockedCells(obstacles) {
        const blocked = new Set();
        for (const o of obstacles) {
            const minC = Math.max(0, Math.floor((o.x - this.margin) / this.resolution));
            const maxC = Math.min(this.cols - 1, Math.floor((o.x + o.width + this.margin) / this.resolution));
            const minR = Math.max(0, Math.floor((o.y - this.margin) / this.resolution));
            const maxR = Math.min(this.rows - 1, Math.floor((o.y + o.height + this.margin) / this.resolution));
            for (let y = minR; y <= maxR; y++) for (let x = minC; x <= maxC; x++) {
                const p = this.pointFor({x,y});
                if (p.x >= o.x-this.margin && p.x <= o.x+o.width+this.margin && p.y >= o.y-this.margin && p.y <= o.y+o.height+this.margin) blocked.add(`${x},${y}`);
            }
        }
        return blocked;
    }

    cellFor(p) { return { x: Math.min(this.cols-1, Math.max(0, Math.floor(p.x/this.resolution))), y: Math.min(this.rows-1, Math.max(0, Math.floor(p.y/this.resolution))) }; }
    pointFor(c) { return { x: (c.x+0.5)*this.resolution, y: (c.y+0.5)*this.resolution }; }
    inBounds(c) { return c.x >= 0 && c.x < this.cols && c.y >= 0 && c.y < this.rows; }
    heuristic(a,b) { const dx=Math.abs(a.x-b.x), dy=Math.abs(a.y-b.y); return Math.max(dx,dy)+(Math.SQRT2-1)*Math.min(dx,dy); }
    validate(p,name) { if (!Number.isFinite(p.x)||!Number.isFinite(p.y)) throw new Error(`${name} must be finite`); if(p.x<0||p.x>this.width||p.y<0||p.y>this.height) throw new Error(`${name} outside bounds`); }
}

function key(c) { return `${c.x},${c.y}`; }
function reconstruct(came, current) { const path=[current]; while(came.has(key(current))){current=came.get(key(current));path.push(current);} return path.reverse(); }
function segmentRect(a,b,xmin,ymin,xmax,ymax){
    const dx=b.x-a.x,dy=b.y-a.y; let t0=0,t1=1;
    for(const [p,q] of [[-dx,a.x-xmin],[dx,xmax-a.x],[-dy,a.y-ymin],[dy,ymax-a.y]]){
        if(Math.abs(p)<1e-12){if(q<0)return false;continue;}
        const t=q/p;
        if(p<0){if(t>t1)return false;t0=Math.max(t0,t);}else{if(t<t0)return false;t1=Math.min(t1,t);}
    }
    return t0<=t1;
}
