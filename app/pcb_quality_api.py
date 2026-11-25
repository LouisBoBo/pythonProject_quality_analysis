# -*- coding: utf-8 -*-
"""
PCB智能品质分析系统 v2.0 - API服务
====================================
功能：
  1. 多维品质体检报告（报废/检验/过程/客诉/原材料）
  2. 8D改善报告智能生成与管理
  3. SPC统计过程控制分析
  4. 智能预警管理
  5. 自主学习引擎（自动积累知识，进化分析能力）
  
数据源：ERP(atomLive 192.168.8.10) + MES(CIMOM 192.168.49.10)
端口：8765
"""

import sys
import os
import json
import sqlite3
import decimal
import logging
import threading
import hashlib
import hmac
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from socketserver import ThreadingMixIn
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional

# ─── 路径设置 ───────────────────────────────────────
_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from config.settings import (  # noqa: E402
    PROJECT_ROOT,
    WEB_DIR,
    DATA_DIR,
    LOG_DIR,
    OUTPUT_DIR,
    KB_V2_PATH,
    API_HOST,
    API_PORT,
    API_TOKEN,
    DEBUG_ERRORS,
    VERSION,
    CORS_ORIGIN,
    PROD_ALLOW_BRIDGE,
    GRAPHRAG_BASE_URL,
    TARGET_SCRAP_RATE,
    WARN_SCRAP_RATE,
    CRIT_SCRAP_RATE,
    TARGET_PASS_RATE,
    TARGET_PROCESS_YIELD,
    TARGET_IQC_PASS,
    TARGET_FQC_PASS,
    TARGET_DPPM,
    TARGET_REWORK_RATE,
    ALERT_OVERDUE_HOURS,
    EIGHT_D_OVERDUE_DAYS,
)
from graphrag_client import get_graphrag_client, reset_graphrag_client  # noqa: E402
from app.project_env import (  # noqa: E402
    describe_for_api,
    get_active_context,
    resolve_paths,
)

BASE_DIR = PROJECT_ROOT
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
KB_PATH = KB_V2_PATH

# ─── 日志配置 ────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_handlers: list = [logging.StreamHandler()]
try:
    _log_handlers.append(
        logging.FileHandler(str(LOG_DIR / "pcb_quality_api.log"), encoding="utf-8")
    )
except Exception:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PCB-QA] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_log_handlers,
    force=True,
)
logger = logging.getLogger("PCB_Quality")

# ═══════════════════════════════════════════════════
# JSON编码器
# ═══════════════════════════════════════════════════

class SmartEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime,)):
            return obj.isoformat()
        return super().default(obj)


# ═══════════════════════════════════════════════════
# 数据库：知识库 + 8D + 预警 + 学习历史
# ═══════════════════════════════════════════════════

class QualityKnowledgeDB:
    """品质知识库数据库（SQLite本地存储；可按环境切换文件，不删除旧库）"""

    def __init__(self, db_path: Optional[Path] = None):
        self._conn = None
        self._lock = threading.Lock()
        self.db_path = Path(db_path) if db_path else Path(KB_PATH)
        self._init()

    def rebind(self, db_path) -> None:
        """切换到另一知识库文件；关闭旧连接，不删除任何文件。"""
        new_path = Path(db_path)
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            self.db_path = new_path
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _get_conn(self):
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init(self):
        conn = self._get_conn()
        c = conn.cursor()

        # 8D报告表
        c.execute("""CREATE TABLE IF NOT EXISTS eightd_reports (
            id TEXT PRIMARY KEY,
            report_no TEXT UNIQUE,
            part_number TEXT,
            customer TEXT DEFAULT '',
            problem TEXT,
            d1 TEXT DEFAULT '',
            d2 TEXT DEFAULT '',
            d3 TEXT DEFAULT '',
            d4 TEXT DEFAULT '',
            d5 TEXT DEFAULT '',
            d6 TEXT DEFAULT '',
            d7 TEXT DEFAULT '',
            d8 TEXT DEFAULT '',
            defects_json TEXT DEFAULT '[]',
            status TEXT DEFAULT 'open',
            severity INTEGER DEFAULT 3,
            created_by TEXT DEFAULT 'System',
            created_at TEXT,
            updated_at TEXT,
            closed_at TEXT
        )""")

        # 不良模式知识表
        c.execute("""CREATE TABLE IF NOT EXISTS defect_patterns (
            id TEXT PRIMARY KEY,
            defect_type TEXT NOT NULL,
            process_name TEXT DEFAULT '',
            root_causes TEXT DEFAULT '[]',
            solutions TEXT DEFAULT '[]',
            occurrence INTEGER DEFAULT 0,
            effectiveness REAL DEFAULT 0.0,
            created_at TEXT,
            updated_at TEXT
        )""")

        # 预警记录表（仅存 ERP/MES 实算结果，禁止编造指标）
        c.execute("""CREATE TABLE IF NOT EXISTS quality_alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT,
            level TEXT DEFAULT 'warning',
            part_number TEXT DEFAULT '',
            process_name TEXT DEFAULT '',
            message TEXT,
            metric_value REAL DEFAULT 0,
            threshold REAL DEFAULT 0,
            suggestion TEXT DEFAULT '',
            data_source TEXT DEFAULT '',
            evidence TEXT DEFAULT '',
            auto_generated INTEGER DEFAULT 1,
            acknowledged INTEGER DEFAULT 0,
            ack_by TEXT DEFAULT '',
            ack_at TEXT DEFAULT '',
            created_at TEXT
        )""")
        # 兼容旧库补列
        for col, typ in (("data_source", "TEXT DEFAULT ''"), ("evidence", "TEXT DEFAULT ''")):
            try:
                c.execute(f"ALTER TABLE quality_alerts ADD COLUMN {col} {typ}")
            except Exception:
                pass
        for col, typ in (
            ("evidence_json", "TEXT DEFAULT '{}'"),
            ("history_refs_json", "TEXT DEFAULT '[]'"),
            ("knowledge_refs_json", "TEXT DEFAULT '[]'"),
            ("data_policy", "TEXT DEFAULT ''"),
        ):
            try:
                c.execute(f"ALTER TABLE eightd_reports ADD COLUMN {col} {typ}")
            except Exception:
                pass
        for col, typ in (
            ("verified", "INTEGER DEFAULT 0"),
            ("source_8d", "TEXT DEFAULT ''"),
        ):
            try:
                c.execute(f"ALTER TABLE defect_patterns ADD COLUMN {col} {typ}")
            except Exception:
                pass

        # 品质体检报告记录（须含 ERP/MES 实据快照，禁止只存空壳评分）
        c.execute("""CREATE TABLE IF NOT EXISTS health_reports (
            id TEXT PRIMARY KEY,
            report_no TEXT,
            part_number TEXT,
            date_range TEXT,
            overall_score REAL DEFAULT 0,
            grade TEXT DEFAULT '',
            dimensions_json TEXT DEFAULT '{}',
            recommendations_json TEXT DEFAULT '[]',
            created_at TEXT
        )""")
        for col, typ in (
            ("fiscal_period", "TEXT DEFAULT ''"),
            ("scope", "TEXT DEFAULT ''"),
            ("verdict", "TEXT DEFAULT ''"),
            ("evidence_json", "TEXT DEFAULT '{}'"),
            ("findings_json", "TEXT DEFAULT '[]'"),
            ("score_json", "TEXT DEFAULT '{}'"),
            ("data_policy", "TEXT DEFAULT ''"),
        ):
            try:
                c.execute(f"ALTER TABLE health_reports ADD COLUMN {col} {typ}")
            except Exception:
                pass

        # AI自主学习历史
        c.execute("""CREATE TABLE IF NOT EXISTS learning_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            learn_type TEXT,
            source TEXT DEFAULT '',
            content TEXT,
            insight TEXT DEFAULT '',
            confidence REAL DEFAULT 0,
            applied INTEGER DEFAULT 0,
            created_at TEXT
        )""")

        # SPC控制图数据
        c.execute("""CREATE TABLE IF NOT EXISTS spc_charts (
            id TEXT PRIMARY KEY,
            chart_name TEXT,
            process_name TEXT,
            characteristic TEXT,
            data_points TEXT DEFAULT '[]',
            ucl REAL, cl REAL, lcl REAL,
            usl REAL, lsl REAL,
            cp REAL, cpk REAL,
            status TEXT DEFAULT 'normal',
            created_at TEXT,
            updated_at TEXT
        )""")

        conn.commit()
        logger.info(f"知识库初始化完成: {self.db_path}")

    def execute(self, sql, params=()):
        with self._lock:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(sql, params)
            conn.commit()
            return c

    def query(self, sql, params=()):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute(sql, params)
        rows = c.fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql, params=()):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute(sql, params)
        row = c.fetchone()
        return dict(row) if row else None

    # ── 8D报告 ──────────────────────
    def save_8d(self, data: dict):
        now = datetime.now().isoformat()
        data.setdefault("created_at", now)
        data["updated_at"] = now
        cols = list(data.keys())
        placeholders = ",".join(["?"] * len(cols))
        updates = ",".join([f"{c}=excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO eightd_reports ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        self.execute(sql, list(data.values()))

    def get_8d_list(self, part_number=None, status=None, limit=50):
        conds = ["1=1"]
        params = []
        if part_number:
            conds.append("part_number LIKE ?")
            params.append(f"%{part_number}%")
        if status:
            conds.append("status=?")
            params.append(status)
        return self.query(f"SELECT * FROM eightd_reports WHERE {' AND '.join(conds)} ORDER BY created_at DESC LIMIT ?", params + [limit])

    def get_8d(self, report_id):
        return self.query_one("SELECT * FROM eightd_reports WHERE id=?", (report_id,))

    def delete_8d(self, report_id):
        self.execute("DELETE FROM eightd_reports WHERE id=?", (report_id,))

    def purge_invalid_8d(self):
        """清理无效/编造稿：无料号、报告号当料号、无缺陷实据、通用模板冒充根因的旧稿"""
        rows = self.query(
            "SELECT id, part_number, problem, defects_json, d4, d5, data_policy, status FROM eightd_reports"
        )
        purged = []
        template_marks = ("油墨粘度偏高", "调整油墨粘度至规范值", "日常检查/参数管控不到位")
        for r in rows:
            if (r.get("status") or "") == "closed":
                continue  # 已关闭人工案保留
            pid = (r.get("part_number") or "").strip()
            problem = (r.get("problem") or "").strip()
            d4 = r.get("d4") or ""
            d5 = r.get("d5") or ""
            policy = r.get("data_policy") or ""
            bad = False
            if not pid:
                bad = True
            elif pid.startswith("QHC-8D-") or pid.startswith("8D-"):
                bad = True
            elif not problem and not (r.get("defects_json") or "").strip("[] "):
                bad = True
            elif "主要不良：待确认" in problem or "报废率0.00%，主要不良：待确认" in problem:
                bad = True
            elif not policy.startswith("metrics=") and any(m in d4 for m in template_marks) and any(
                m in d5 for m in template_marks
            ):
                # 旧版用通用模板填满 D4/D5 且无实据政策标记
                bad = True
            if bad:
                self.delete_8d(r["id"])
                purged.append(r["id"])
        return purged

    def find_closed_8d_history(self, part_number=None, defect_types=None, process_names=None, limit=8):
        """仅取已关闭 8D 作为真实历史方案（人工确认后的对策）"""
        rows = self.query(
            "SELECT * FROM eightd_reports WHERE status='closed' ORDER BY closed_at DESC, updated_at DESC LIMIT 80"
        )
        defect_types = [str(x).strip() for x in (defect_types or []) if str(x).strip()]
        process_names = [str(x).strip() for x in (process_names or []) if str(x).strip()]
        part_number = (part_number or "").strip()
        scored = []
        for r in rows:
            score = 0
            blob = " ".join([
                str(r.get("part_number") or ""),
                str(r.get("problem") or ""),
                str(r.get("d2") or ""),
                str(r.get("d4") or ""),
                str(r.get("d5") or ""),
                str(r.get("defects_json") or ""),
            ])
            if part_number and part_number in (r.get("part_number") or ""):
                score += 5
            for dt in defect_types:
                if dt and dt in blob:
                    score += 3
            for pn in process_names:
                if pn and pn in blob:
                    score += 2
            if score <= 0:
                continue
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, r in scored[:limit]:
            out.append({
                "id": r.get("id"),
                "report_no": r.get("report_no"),
                "part_number": r.get("part_number"),
                "problem": r.get("problem"),
                "d4": r.get("d4") or "",
                "d5": r.get("d5") or "",
                "closed_at": r.get("closed_at") or r.get("updated_at"),
                "match_score": score,
            })
        return out

    def get_verified_patterns(self, defect_type=None, process_name=None, limit=10):
        """仅返回经关闭 8D 回写的已验证对策"""
        rows = self.query(
            "SELECT * FROM defect_patterns WHERE IFNULL(verified,0)=1 ORDER BY occurrence DESC, updated_at DESC LIMIT 80"
        )
        out = []
        for r in rows:
            try:
                r["root_causes"] = json.loads(r.get("root_causes") or "[]")
            except Exception:
                r["root_causes"] = []
            try:
                r["solutions"] = json.loads(r.get("solutions") or "[]")
            except Exception:
                r["solutions"] = []
            ok = True
            if defect_type and defect_type not in (r.get("defect_type") or ""):
                ok = False
            if process_name and process_name not in (r.get("process_name") or ""):
                # 宽松：过程名也可用作二次匹配，不强制
                pass
            if ok or (process_name and process_name in (r.get("process_name") or "")):
                out.append(r)
            if len(out) >= limit:
                break
        return out[:limit]

    def bump_defect_occurrence(self, defect_type, process_name):
        """只累计不良出现次数，不写入通用模板根因/对策"""
        pid = hashlib.md5(f"{defect_type}|{process_name}".encode()).hexdigest()[:16]
        now = datetime.now().isoformat()
        existing = self.query_one("SELECT * FROM defect_patterns WHERE id=?", (pid,))
        if existing:
            self.execute(
                "UPDATE defect_patterns SET occurrence=occurrence+1, updated_at=? WHERE id=?",
                (now, pid),
            )
        else:
            self.execute(
                "INSERT INTO defect_patterns (id,defect_type,process_name,root_causes,solutions,occurrence,effectiveness,verified,source_8d,created_at,updated_at) VALUES (?,?,?,?,?,1,0,0,'',?,?)",
                (pid, defect_type, process_name, "[]", "[]", now, now),
            )

    def promote_8d_knowledge(self, report: dict):
        """关闭 8D 时，将人工确认的根因/对策写入已验证知识库"""
        if not report:
            return 0
        defects = []
        try:
            defects = json.loads(report.get("defects_json") or "[]")
        except Exception:
            defects = report.get("defects") or []
        # 从 d4/d5 抽取「→ 」或编号行作为条目（人工填写内容）
        def _lines(text):
            items = []
            for line in str(text or "").splitlines():
                s = line.strip()
                if not s or s.startswith("【") or s.startswith("- 完成") or s.startswith("- 责任"):
                    continue
                if s.startswith("→"):
                    items.append(s.lstrip("→ ").strip())
                elif re.match(r"^\d+[\.、]", s):
                    items.append(re.sub(r"^\d+[\.、]\s*", "", s))
                elif s.startswith("【确定根因】") or s.startswith("确定根因"):
                    items.append(re.sub(r"^【?确定根因】?[:：]?\s*", "", s))
            return [x for x in items if x and "待排查" not in x and "待制定" not in x and "草稿" not in x]

        causes = _lines(report.get("d4"))
        solutions = _lines(report.get("d5"))
        if not causes and not solutions:
            return 0
        n = 0
        now = datetime.now().isoformat()
        seeds = defects[:3] or [{"defect_type": "综合不良", "process": ""}]
        for d in seeds:
            dtype = str(d.get("defect_type") or d.get("DefectType") or "综合不良")
            proc = str(d.get("process") or d.get("Process") or "")
            pid = hashlib.md5(f"{dtype}|{proc}|verified".encode()).hexdigest()[:16]
            self.execute(
                """INSERT INTO defect_patterns
                   (id,defect_type,process_name,root_causes,solutions,occurrence,effectiveness,verified,source_8d,created_at,updated_at)
                   VALUES (?,?,?,?,?,1,1.0,1,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     root_causes=excluded.root_causes,
                     solutions=excluded.solutions,
                     effectiveness=1.0,
                     verified=1,
                     source_8d=excluded.source_8d,
                     occurrence=occurrence+1,
                     updated_at=excluded.updated_at""",
                (
                    pid, dtype, proc,
                    json.dumps(causes[:8], ensure_ascii=False),
                    json.dumps(solutions[:8], ensure_ascii=False),
                    report.get("report_no") or report.get("id") or "",
                    now, now,
                ),
            )
            n += 1
        return n

    # ── 预警 ─────────────────────────
    def save_alert(self, data: dict):
        """写入预警；若已确认则保留确认状态，仅刷新指标与文案。"""
        data.setdefault("created_at", datetime.now().isoformat())
        existing = self.query_one(
            "SELECT acknowledged, ack_by, ack_at, created_at FROM quality_alerts WHERE id=?",
            (data.get("id"),),
        )
        if existing and int(existing.get("acknowledged") or 0) == 1:
            data["acknowledged"] = 1
            data["ack_by"] = existing.get("ack_by") or ""
            data["ack_at"] = existing.get("ack_at") or ""
            data["created_at"] = existing.get("created_at") or data["created_at"]
        else:
            data.setdefault("acknowledged", 0)
        cols = list(data.keys())
        placeholders = ",".join(["?"] * len(cols))
        # 不覆盖 id；已确认时 acknowledged/ack_* 已在 data 中保留
        updates = ",".join([f"{c}=excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO quality_alerts ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        self.execute(sql, list(data.values()))

    def get_alerts(self, acknowledged=None, alert_type=None, level=None, limit=100):
        conds = ["1=1"]
        params = []
        if acknowledged is not None:
            conds.append("acknowledged=?")
            params.append(1 if acknowledged else 0)
        if alert_type:
            conds.append("alert_type=?")
            params.append(alert_type)
        if level:
            conds.append("level=?")
            params.append(level)
        return self.query(
            f"SELECT * FROM quality_alerts WHERE {' AND '.join(conds)} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        )

    def ack_alert(self, alert_id, ack_by="User"):
        self.execute("UPDATE quality_alerts SET acknowledged=1, ack_by=?, ack_at=? WHERE id=?",
                     (ack_by, datetime.now().isoformat(), alert_id))

    def ack_alerts_batch(self, alert_ids, ack_by="User"):
        now = datetime.now().isoformat()
        n = 0
        for aid in alert_ids or []:
            if not aid:
                continue
            self.execute(
                "UPDATE quality_alerts SET acknowledged=1, ack_by=?, ack_at=? WHERE id=? AND acknowledged=0",
                (ack_by, now, aid),
            )
            n += 1
        return n

    def delete_alert(self, alert_id):
        self.execute("DELETE FROM quality_alerts WHERE id=?", (alert_id,))

    def purge_unreliable_alerts(self):
        """清除不可靠/历史误报（低样本良率、0%→跳变趋势等），不编造只删除。"""
        removed = []
        for a in self.get_alerts(limit=500):
            aid = a.get("id") or ""
            msg = a.get("message") or ""
            atype = a.get("alert_type") or ""
            bad = False
            if atype == "process_yield":
                m = re.search(r"检验(\d+)笔", msg)
                if m and int(m.group(1)) < 15:
                    bad = True
                # 旧版无检验笔数、且文案含 0.00%/50.00% 的低可信条目
                if not m and re.search(r"(0\.00%|50\.00%)", msg):
                    bad = True
            if atype == "trend_deterioration" and re.search(r"（0\.00%→", msg):
                bad = True
            if bad and aid:
                self.delete_alert(aid)
                removed.append(aid)
        return removed

    def purge_stale_alerts(self, fiscal_period=None):
        """删除非当月预警，避免 4 月等历史记录混入当前列表。"""
        if not fiscal_period:
            fiscal_period = datetime.now().strftime("%Y%m")
        ym = f"{fiscal_period[:4]}-{fiscal_period[4:6]}"
        removed = []
        for a in self.get_alerts(limit=500):
            aid = a.get("id") or ""
            created = str(a.get("created_at") or "")
            stale = False
            if created and len(created) >= 7 and not created.startswith(ym):
                stale = True
            m = re.search(r"_(\d{8})$", aid)
            if m and m.group(1)[:6] != fiscal_period:
                stale = True
            if stale and aid:
                self.delete_alert(aid)
                removed.append(aid)
        return removed

    # ── 知识/学习 ────────────────────
    def add_learning(self, learn_type, source, content, insight="", confidence=0.5):
        self.execute("""INSERT INTO learning_history (learn_type,source,content,insight,confidence,created_at)
                        VALUES (?,?,?,?,?,?)""",
                     (learn_type, source, content, insight, confidence, datetime.now().isoformat()))

    def get_learning_stats(self):
        total = self.query_one("SELECT COUNT(*) as c FROM learning_history")["c"]
        applied = self.query_one("SELECT COUNT(*) as c FROM learning_history WHERE applied=1")["c"]
        patterns = self.query_one("SELECT COUNT(*) as c FROM defect_patterns")["c"]
        reports_8d = self.query_one("SELECT COUNT(*) as c FROM eightd_reports")["c"]
        alerts = self.query_one("SELECT COUNT(*) as c FROM quality_alerts")["c"]
        return {"total_learning": total, "applied_learning": applied,
                "defect_patterns": patterns, "reports_8d": reports_8d, "alerts_total": alerts}

    # ── 健康报告 ─────────────────────
    def save_health_report(self, data: dict):
        data.setdefault("created_at", datetime.now().isoformat())
        cols = list(data.keys())
        placeholders = ",".join(["?"] * len(cols))
        updates = ",".join([f"{c}=excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO health_reports ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        self.execute(sql, list(data.values()))

    def get_health_reports(self, part_number=None, grade=None, limit=50):
        """列表：默认不带超大 JSON 全文，便于前端筛选；完整快照走 get_health_report。"""
        conds = ["1=1"]
        params = []
        if part_number:
            conds.append("part_number LIKE ?")
            params.append(f"%{part_number}%")
        if grade:
            conds.append("grade=?")
            params.append(grade)
        limit = max(1, min(int(limit or 50), 200))
        rows = self.query(
            f"""SELECT id, report_no, part_number, date_range, fiscal_period, scope,
                       overall_score, grade, verdict, created_at, data_policy,
                       evidence_json, dimensions_json
                FROM health_reports
                WHERE {' AND '.join(conds)}
                ORDER BY created_at DESC LIMIT ?""",
            params + [limit],
        )
        for r in rows:
            ev = {}
            try:
                ev = json.loads(r.get("evidence_json") or "{}") or {}
            except Exception:
                ev = {}
            r["has_evidence"] = bool(ev.get("scrap_pcs") is not None or ev.get("has_scrap") or ev.get("has_mes"))
            r["scrap_rate"] = ev.get("scrap_rate")
            r["scrap_pcs"] = ev.get("scrap_pcs")
            r["stock_pcs"] = ev.get("stock_pcs")
            r["mes_avg_yield"] = ev.get("mes_avg_yield")
            r["mes_inspect_count"] = ev.get("mes_inspect_count")
            r["data_sources"] = ev.get("data_sources") or ""
            # 列表不回传大字段原文
            r.pop("evidence_json", None)
            r.pop("dimensions_json", None)
        return rows

    def get_health_report(self, report_id: str):
        r = self.query_one("SELECT * FROM health_reports WHERE id=?", (report_id,))
        if not r:
            # 兼容用 report_no 查（同号多份时取最新）
            rows = self.query(
                "SELECT * FROM health_reports WHERE report_no=? ORDER BY created_at DESC LIMIT 1",
                (report_id,),
            )
            r = rows[0] if rows else None
        if not r:
            return None
        for jk, default in (
            ("dimensions_json", {}),
            ("recommendations_json", []),
            ("evidence_json", {}),
            ("findings_json", []),
            ("score_json", {}),
        ):
            key = jk.replace("_json", "") if jk.endswith("_json") else jk
            # dimensions / recommendations / evidence / findings / score
            out_key = {
                "dimensions_json": "dimensions",
                "recommendations_json": "recommendations",
                "evidence_json": "evidence",
                "findings_json": "findings",
                "score_json": "score",
            }[jk]
            try:
                r[out_key] = json.loads(r.get(jk) or ("[]" if isinstance(default, list) else "{}"))
            except Exception:
                r[out_key] = default
        return r

    def purge_empty_health_reports(self):
        """清理无实据快照、无评分意义的空壳历史（旧数据或异常写入）。"""
        rows = self.query(
            "SELECT id, overall_score, evidence_json, dimensions_json, date_range FROM health_reports"
        )
        purged = []
        for r in rows:
            ev = {}
            try:
                ev = json.loads(r.get("evidence_json") or "{}") or {}
            except Exception:
                ev = {}
            has_ev = bool(
                ev.get("has_scrap")
                or ev.get("has_mes")
                or int(ev.get("scrap_pcs") or 0) > 0
                or int(ev.get("stock_pcs") or 0) > 0
                or int(ev.get("mes_inspect_count") or 0) > 0
            )
            # 旧报告无 evidence_json：若维度全空且评分为 0，视为空壳
            dims = {}
            try:
                dims = json.loads(r.get("dimensions_json") or "{}") or {}
            except Exception:
                dims = {}
            all_null_dims = (not dims) or all(v is None for v in dims.values())
            score = float(r.get("overall_score") or 0)
            if (not has_ev and not ev and all_null_dims and score <= 0) or (
                not has_ev and not ev and (r.get("date_range") or "").strip() in ("", "不限 ~ 不限") and score <= 0
            ):
                self.execute("DELETE FROM health_reports WHERE id=?", (r["id"],))
                purged.append(r["id"])
        return purged

    # ── 不良模式 ─────────────────────
    def upsert_defect_pattern(self, defect_type, process_name, root_causes, solutions):
        pid = hashlib.md5(f"{defect_type}_{process_name}".encode()).hexdigest()[:16]
        now = datetime.now().isoformat()
        existing = self.query_one("SELECT * FROM defect_patterns WHERE id=?", (pid,))
        if existing:
            occ = existing["occurrence"] + 1
            self.execute(
                "UPDATE defect_patterns SET occurrence=?, updated_at=? WHERE id=?",
                (occ, now, pid)
            )
        else:
            self.execute(
                "INSERT INTO defect_patterns (id,defect_type,process_name,root_causes,solutions,occurrence,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (pid, defect_type, process_name, json.dumps(root_causes, ensure_ascii=False),
                 json.dumps(solutions, ensure_ascii=False), now, now)
            )

    def get_defect_patterns(self, defect_type=None):
        if defect_type:
            rows = self.query("SELECT * FROM defect_patterns WHERE defect_type LIKE ? ORDER BY occurrence DESC",
                              (f"%{defect_type}%",))
        else:
            rows = self.query("SELECT * FROM defect_patterns ORDER BY occurrence DESC LIMIT 50")
        for r in rows:
            r["root_causes"] = json.loads(r.get("root_causes") or "[]")
            r["solutions"] = json.loads(r.get("solutions") or "[]")
        return rows


# ═══════════════════════════════════════════════════
# 自主学习引擎
# ═══════════════════════════════════════════════════

class SelfLearningEngine:
    """
    自主学习引擎
    基于实时数据持续进化知识库：
    - 自动识别高频不良模式
    - 更新工序风险权重
    - 生成智能建议
    - 预警阈值自适应
    """

    # 工序根因/对策权威源：GraphRAG 生产 Neo4j（禁止本地模板充数）
    # 历史内置 PROCESS_KNOWLEDGE 已下线，统一走 get_graphrag_client()

    # 品质评分权重（仅品质维度；不含设备稼动）
    SCORE_WEIGHTS = {
        "scrap": 0.30,       # 报废 30%
        "inspection": 0.25,  # 检验 25%
        "process": 0.25,     # 过程 25%
        "complaint": 0.10,   # 客诉 10%
        "material": 0.10,    # 来料 IQC 10%
    }

    def __init__(self, db: QualityKnowledgeDB):
        self.db = db
        self._last_learn_time = 0

    def learn_from_defects(self, defect_top10: list):
        """从 ERP 缺陷实数累计出现频次；禁止把通用模板写入对策库"""
        insights = []
        for d in defect_top10[:10]:
            defect_type = str(d.get("defect_type", d.get("DefectType", "")))
            process = str(d.get("process", d.get("Process", "")))
            scrap_pcs = int(d.get("scrap_pcs", d.get("ScrapPcs", 0)))

            if not defect_type:
                continue

            self.db.bump_defect_occurrence(defect_type, process)
            insight = f"累计不良频次：{process}-{defect_type}（本期 {scrap_pcs} PCS，来源 ERP）"
            self.db.add_learning(
                "defect_occurrence",
                f"{process}/{defect_type}",
                f"scrap_pcs={scrap_pcs}",
                insight,
                0.9,
            )
            insights.append(insight)

        self._last_learn_time = time.time()
        return insights

    # 预警阈值（可后续做成配置表）
    ALERT_SCRAP_WARN = 5.0
    ALERT_SCRAP_CRIT = 10.0
    ALERT_YIELD_WARN = 90.0
    ALERT_YIELD_CRIT = 80.0
    ALERT_MIN_INSPECT = 15  # MES 检验样本过少不报警，抑制 0% 误报

    def generate_alerts(self, scrap_rate: float, part_number: str,
                        process_data: list, monthly_trend: list,
                        scrap_pcs: float = 0, persist: bool = True) -> list:
        """智能生成预警（可落库）"""
        alerts = []
        now = datetime.now().isoformat()
        day = datetime.now().strftime('%Y%m%d')
        part_key = (part_number or "全厂").replace(" ", "")[:32]

        # 1. 报废率预警：必须有 ERP 报废实数，样本过小不报
        if scrap_rate > self.ALERT_SCRAP_WARN and scrap_pcs >= 50:
            aid = f"ALERT_{part_key}_SCRAP_{day}"
            level = "critical" if scrap_rate > self.ALERT_SCRAP_CRIT else "warning"
            alert = {
                "id": aid,
                "alert_type": "scrap_rate",
                "level": level,
                "part_number": part_number or "全厂",
                "process_name": "",
                "message": f"{'全厂' if not part_number or part_number == '全厂' else '料号' + part_number}"
                           f"报废率{scrap_rate:.2f}%，超过{self.ALERT_SCRAP_WARN:g}%阈值"
                           f"（报废 {int(scrap_pcs)} PCS）",
                "metric_value": scrap_rate,
                "threshold": self.ALERT_SCRAP_WARN,
                "suggestion": "建议立即启动8D分析，重点排查报废率最高工序与缺陷项",
                "data_source": "ERP:View_P_Inspection+FGI",
                "evidence": f"scrap_pcs={int(scrap_pcs)},scrap_rate={scrap_rate}",
                "auto_generated": 1,
                "acknowledged": 0,
                "created_at": now,
            }
            if persist:
                self.db.save_alert(alert)
            alerts.append(alert)

        # 2. 工序良率预警：必须来自 MES 检验实数，且检验笔数达标
        for p in (process_data or []):
            rate = float(p.get("PassRate", p.get("yield_rate", 100)) or 100)
            pname = str(p.get("ProcessName", p.get("process_name", "")) or "")
            inspect_n = int(p.get("InspectCount", p.get("total_qty", 0)) or 0)
            if not pname or rate >= self.ALERT_YIELD_WARN:
                continue
            if inspect_n < self.ALERT_MIN_INSPECT:
                continue  # 样本不足，禁止生成（避免包装 0%/焊接 50% 低样本误报）
            safe_pn = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", pname)[:24]
            aid = f"ALERT_{part_key}_{safe_pn}_YIELD_{day}"
            gr_tips = []
            try:
                gr_tips = get_graphrag_client().suggest_for_process(pname, max_items=2)
            except Exception:
                gr_tips = []
            suggestion = (
                "；".join(gr_tips)[:120]
                if gr_tips
                else f"请在 GraphRAG 知识图谱核查「{pname}」工序缺陷根因与对策"
            )
            alert = {
                "id": aid,
                "alert_type": "process_yield",
                "level": "critical" if rate < self.ALERT_YIELD_CRIT else "warning",
                "part_number": part_number or "全厂",
                "process_name": pname,
                "message": f"{pname}工序良率{rate:.2f}%（检验{inspect_n}笔），低于{self.ALERT_YIELD_WARN:g}%警戒线",
                "metric_value": rate,
                "threshold": self.ALERT_YIELD_WARN,
                "suggestion": suggestion,
                "data_source": "MES:TBL_QM_INSPECT_RECORD+GraphRAG",
                "evidence": f"process={pname},inspect={inspect_n},pass_rate={rate},graphrag_tips={len(gr_tips)}",
                "auto_generated": 1,
                "acknowledged": 0,
                "created_at": now,
            }
            if persist:
                self.db.save_alert(alert)
            alerts.append(alert)

        # 3. 趋势恶化：两期均需有有效报废率（禁止 0%→跳变），取自 ERP 日/月趋势实数
        if monthly_trend and len(monthly_trend) >= 2:
            sorted_trend = sorted(
                monthly_trend,
                key=lambda x: str(x.get("Day") or x.get("Month") or x.get("month") or ""),
            )
            last = sorted_trend[-1]
            prev = sorted_trend[-2]
            last_rate = float(last.get("ScrapRate", last.get("scrap_rate", 0)) or 0)
            prev_rate = float(prev.get("ScrapRate", prev.get("scrap_rate", 0)) or 0)
            last_pcs = float(last.get("TotalScrap", last.get("scrap_pcs", 0)) or 0)
            prev_pcs = float(prev.get("TotalScrap", prev.get("scrap_pcs", 0)) or 0)
            if (
                prev_rate > 0
                and last_rate > prev_rate * 1.2
                and last_rate > 3
                and last_pcs >= 50
                and prev_pcs >= 20
            ):
                aid = f"ALERT_{part_key}_TREND_{day}"
                alert = {
                    "id": aid,
                    "alert_type": "trend_deterioration",
                    "level": "critical" if last_rate > self.ALERT_SCRAP_CRIT else "warning",
                    "part_number": part_number or "全厂",
                    "process_name": "",
                    "message": f"报废率上升趋势（{prev_rate:.2f}%→{last_rate:.2f}%），需关注",
                    "metric_value": last_rate,
                    "threshold": prev_rate,
                    "suggestion": "建议对比两期工艺/物料批次变化，追查根因并复核首件",
                    "data_source": "ERP:View_P_Inspection日趋势",
                    "evidence": f"prev={prev_rate}%/{int(prev_pcs)}pcs,last={last_rate}%/{int(last_pcs)}pcs",
                    "auto_generated": 1,
                    "acknowledged": 0,
                    "created_at": now,
                }
                if persist:
                    self.db.save_alert(alert)
                alerts.append(alert)

        return alerts

    def calculate_quality_score(self, metrics: dict) -> dict:
        """
        计算品质综合评分（严禁用默认值充数）
        - 仅品质维度：报废 / 检验 / 过程 / 客诉 / 来料IQC
        - 不含设备 OEE、产能稼动等非品质指标
        - 未接入维度标记缺失，不给满分、不进总分；总分按已接入满分归一化
        """
        scores = {}  # 有数据为 int，无数据为 None
        # 原 equipment 15 分并入品质主维度，本系统不做设备稼动分析
        dim_max = {"scrap": 30, "inspection": 25, "process": 25,
                   "complaint": 10, "material": 10}
        data_gaps = []
        available_max = 0
        raw = 0

        scrap_rate = metrics.get("scrap_rate")
        has_scrap = bool(metrics.get("has_scrap_data"))
        avg_yield = metrics.get("avg_process_yield")
        min_yield = metrics.get("min_process_yield")
        has_mes = bool(metrics.get("has_mes_yield"))

        # 1. 报废（ERP）
        if has_scrap and scrap_rate is not None:
            sr = float(scrap_rate)
            if sr <= 1.0:
                scores["scrap"] = 30
            elif sr <= 2.0:
                scores["scrap"] = 26
            elif sr <= 3.5:
                scores["scrap"] = 22
            elif sr <= 5.0:
                scores["scrap"] = 16
            elif sr <= 8.0:
                scores["scrap"] = 10
            else:
                scores["scrap"] = 4
            available_max += dim_max["scrap"]
            raw += scores["scrap"]
        else:
            scores["scrap"] = None
            data_gaps.append("报废：本期无 ERP 报废/入库实测，请在 ERP 补充检验报废与生产入库")

        # 2. 检验 + 3. 过程（MES 工序良率）
        if has_mes and avg_yield is not None and min_yield is not None:
            ay = float(avg_yield)
            my = float(min_yield)
            if ay >= 99:
                scores["inspection"] = 25
            elif ay >= 97:
                scores["inspection"] = 22
            elif ay >= 95:
                scores["inspection"] = 18
            elif ay >= 90:
                scores["inspection"] = 12
            else:
                scores["inspection"] = 6
            if my >= 98:
                scores["process"] = 25
            elif my >= 95:
                scores["process"] = 21
            elif my >= 90:
                scores["process"] = 15
            elif my >= 85:
                scores["process"] = 9
            else:
                scores["process"] = 4
            available_max += dim_max["inspection"] + dim_max["process"]
            raw += scores["inspection"] + scores["process"]
        else:
            scores["inspection"] = None
            scores["process"] = None
            data_gaps.append("检验/过程：无 MES 工序检验实绩，请在 MES 补充 TBL_QM_INSPECT_RECORD")

        # 4. 客诉 / COPQ（ERP customer_complaints 实表）
        if metrics.get("has_complaint_data"):
            open_cnt = int(metrics.get("complaint_open") or 0)
            copq = float(metrics.get("copq_amount") or 0)
            sev = metrics.get("complaint_severity_avg")
            cscore = 10
            if open_cnt >= 5:
                cscore -= 4
            elif open_cnt >= 3:
                cscore -= 2
            elif open_cnt >= 1:
                cscore -= 1
            if copq >= 5000:
                cscore -= 3
            elif copq >= 2000:
                cscore -= 2
            elif copq >= 500:
                cscore -= 1
            if sev is not None and float(sev) >= 4:
                cscore -= 2
            scores["complaint"] = max(0, cscore)
            available_max += dim_max["complaint"]
            raw += scores["complaint"]
        else:
            scores["complaint"] = None
            data_gaps.append("客诉：未读到 customer_complaints 实据")

        # 5. 原材料 IQC（quality_inspection_records incoming）—— 不含设备 OEE
        if metrics.get("has_iqc_data") and metrics.get("iqc_pass_rate") is not None:
            iqc_v = float(metrics["iqc_pass_rate"])
            iqc_n = int(metrics.get("iqc_count") or 0)
            if not metrics.get("iqc_sample_ok"):
                # 样本过少：给一半上限并记缺口，避免虚高
                scores["material"] = 5 if iqc_v >= 95 else 3
                data_gaps.append(f"原材料：IQC 仅 {iqc_n} 单，样本不足按保守计分")
            elif iqc_v >= 99:
                scores["material"] = 10
            elif iqc_v >= 98:
                scores["material"] = 9
            elif iqc_v >= 95:
                scores["material"] = 7
            elif iqc_v >= 90:
                scores["material"] = 4
            else:
                scores["material"] = 2
            available_max += dim_max["material"]
            raw += scores["material"]
        else:
            scores["material"] = None
            data_gaps.append("原材料：未读到 incoming（IQC）检验实据")

        total = round(raw * 100.0 / available_max) if available_max > 0 else 0

        if total >= 90:
            grade, grade_name, grade_color = "A", "优秀", "#3fb950"
        elif total >= 75:
            grade, grade_name, grade_color = "B", "良好", "#58a6ff"
        elif total >= 60:
            grade, grade_name, grade_color = "C", "一般", "#d29922"
        elif total >= 45:
            grade, grade_name, grade_color = "D", "需改善", "#f97316"
        else:
            grade, grade_name, grade_color = "E", "严重", "#f85149"

        return {
            "scores": scores,
            "dim_max": dim_max,
            "raw_score": raw,
            "available_max": available_max,
            "total": total,
            "grade": grade,
            "grade_name": grade_name,
            "grade_color": grade_color,
            "data_gaps": data_gaps,
            "scoring_policy": (
                "仅品质维度：ERP报废 + MES工序良率 + ERP客诉/COPQ + IQC incoming；"
                "不含设备OEE/产能稼动；无实据维度不计分、不编造"
            ),
            "out_of_scope": ["equipment_oee", "capacity", "utilization"],
            "details": {
                "scrap_rate": float(scrap_rate) if scrap_rate is not None else None,
                "avg_yield": float(avg_yield) if avg_yield is not None else None,
                "min_yield": float(min_yield) if min_yield is not None else None,
            }
        }

    def generate_8d_from_evidence(self, part_number: str, evidence: dict,
                                   history: list, verified_patterns: list) -> dict:
        """
        基于 ERP/MES 实据 + 已关闭历史 8D / 已验证对策 + GraphRAG 知识库 生成报告。
        D4/D5 可引用知识库，须现场复核；禁止把通用模板写成「已确定根因」。
        """
        now = datetime.now().strftime("%Y-%m-%d")
        safe_part = (part_number or "UNKNOWN")[:32]
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        report_id = f"8D-{safe_part[:8]}-{ts}"
        # report_no 含时分秒，避免同料号同日 UNIQUE 冲突
        report_no = f"QHC-8D-{ts}-{hashlib.md5(safe_part.encode()).hexdigest()[:4].upper()}"

        scrap_rate = float(evidence.get("scrap_rate") or 0)
        scrap_pcs = int(evidence.get("scrap_pcs") or 0)
        stock_pcs = int(evidence.get("stock_pcs") or 0)
        fiscal = evidence.get("fiscal_period") or ""
        top_defects = evidence.get("defects") or []
        process_scrap = evidence.get("process_scrap") or []
        wo_list = evidence.get("work_orders") or []
        mes_yield = evidence.get("mes_process_yield") or []
        mes_defects = evidence.get("mes_process_defects") or []

        defect_desc = "、".join([
            f"{d.get('defect_type', '')}({int(d.get('scrap_pcs') or 0)}PCS,{float(d.get('ratio') or 0):.1f}%)"
            for d in top_defects[:5] if d.get("defect_type")
        ]) or "（本期 ERP 未检出缺陷明细，请核对料号/周期）"

        top_process = ""
        if top_defects:
            top_process = str(top_defects[0].get("process") or "")
        elif process_scrap:
            top_process = str(process_scrap[0].get("process") or "")

        wo_nos = [str(w.get("WONumber") or w.get("woNumber") or w.get("wo_number") or "")
                  for w in wo_list[:8]]
        wo_nos = [x for x in wo_nos if x]
        wo_line = "、".join(wo_nos) if wo_nos else "（本期无关联工单列表）"

        mes_lines = []
        for p in mes_yield[:6]:
            pname = p.get("ProcessName") or p.get("process_name") or ""
            rate = p.get("PassRate", p.get("yield_rate"))
            n = int(p.get("InspectCount") or 0)
            if pname and rate is not None:
                mes_lines.append(f"- {pname}：良率 {float(rate):.2f}%（检验 {n} 笔）")
        mes_block = "\n".join(mes_lines) if mes_lines else "- （该料号本期无 MES 工序检验实绩）"

        mes_ng = []
        for d in mes_defects[:6]:
            mes_ng.append(
                f"- {d.get('ProcessName') or ''} / {d.get('DefectItem') or ''} ×{int(d.get('DefectCount') or 0)}"
            )
        mes_ng_block = "\n".join(mes_ng) if mes_ng else "- （无 MES NG 明细）"

        # —— 历史真实方案（已关闭 8D + verified patterns）——
        hist_cause_lines, hist_sol_lines, hist_refs = [], [], []
        for h in (history or [])[:5]:
            ref = h.get("report_no") or h.get("id")
            hist_refs.append({
                "report_no": ref,
                "part_number": h.get("part_number"),
                "closed_at": h.get("closed_at"),
                "match_score": h.get("match_score"),
            })
            if (h.get("d4") or "").strip():
                hist_cause_lines.append(f"【引用 {ref} / {h.get('part_number') or ''}】\n{(h.get('d4') or '')[:500]}")
            if (h.get("d5") or "").strip():
                hist_sol_lines.append(f"【引用 {ref}】\n{(h.get('d5') or '')[:500]}")
        for vp in (verified_patterns or [])[:5]:
            src = vp.get("source_8d") or "已验证知识"
            for c in (vp.get("root_causes") or [])[:3]:
                hist_cause_lines.append(f"【已验证·{src}】{c}")
            for s in (vp.get("solutions") or [])[:3]:
                hist_sol_lines.append(f"【已验证·{src}】{s}")

        # —— 主责工序：按报废占比排序，禁止把次要工序（如包装 0.4%）当成根因入口 ——
        process_rank = []
        for p in process_scrap:
            pname = str(p.get("process") or "").strip()
            if not pname:
                continue
            process_rank.append({
                "process": pname,
                "scrap_pcs": int(p.get("scrap_pcs") or 0),
                "ratio": float(p.get("ratio") or 0),
            })
        process_rank.sort(key=lambda x: -x["scrap_pcs"])
        primary_process = (process_rank[0]["process"] if process_rank else top_process) or ""
        primary_ratio = process_rank[0]["ratio"] if process_rank else 0.0
        # 仅取累计占比前 95% 或至少主责工序，作为知识库工序种子
        seed_processes = []
        acc = 0.0
        for pr in process_rank:
            seed_processes.append(pr["process"])
            acc += float(pr["ratio"] or 0)
            if acc >= 95.0 or len(seed_processes) >= 2:
                break
        if primary_process and primary_process not in seed_processes:
            seed_processes.insert(0, primary_process)

        seed_defects = [
            str(d.get("defect_type") or d.get("DefectType") or "").strip()
            for d in top_defects[:5]
            if (d.get("defect_type") or d.get("DefectType"))
        ]

        # MES：主责工序良率
        mes_primary_line = "（本期 MES 无该工序检验实绩）"
        for p in mes_yield:
            pname = str(p.get("ProcessName") or p.get("process_name") or "")
            if primary_process and (primary_process in pname or pname in primary_process):
                rate = p.get("PassRate", p.get("yield_rate"))
                n = int(p.get("InspectCount") or 0)
                if rate is not None:
                    mes_primary_line = f"{pname} 良率 {float(rate):.2f}%（检验 {n} 笔）"
                break

        # 工单集中度
        wo_focus = ""
        if wo_list:
            top_wos = wo_list[:3]
            bits = []
            for w in top_wos:
                wn = w.get("WONumber") or w.get("woNumber") or w.get("wo_number") or ""
                scrap_w = w.get("ScrapQty") or w.get("scrap_pcs") or w.get("RejectQty") or ""
                bits.append(f"{wn}" + (f"(报废{scrap_w})" if scrap_w != "" else ""))
            wo_focus = "、".join([b for b in bits if b])

        knowledge_pack = {
            "source": "GraphRAG/FMEA",
            "source_url": GRAPHRAG_BASE_URL,
            "refs": [],
            "hit_count": 0,
            "cause_count": 0,
            "measure_count": 0,
            "defect_alignment": {},
        }
        gr_cause_by_cat: Dict[str, List[str]] = {}
        gr_cause_lines, gr_sol_lines = [], []
        defect_alignment: Dict[str, Any] = {}
        try:
            knowledge_pack = get_graphrag_client().lookup_for_8d(
                defect_names=seed_defects[:5],
                process_names=seed_processes[:2],
            )
            defect_alignment = knowledge_pack.get("defect_alignment") or {}
            for ref in knowledge_pack.get("refs") or []:
                gname = ref.get("matched") or ref.get("query") or ""
                qname = ref.get("query") or ""
                mtype = ref.get("match_type") or ""
                for rc in ref.get("root_causes") or []:
                    cause = (rc.get("cause") or "").strip()
                    cat = (rc.get("category") or "其他").strip() or "其他"
                    if not cause:
                        continue
                    line = f"→ [{qname}→{gname}/{cat}] {cause}" if qname != gname else f"→ [{gname}/{cat}] {cause}"
                    gr_cause_lines.append(line)
                    gr_cause_by_cat.setdefault(cat, []).append(f"[{gname}] {cause}")
                for cm in ref.get("countermeasures") or []:
                    measure = (cm.get("measure") or "").strip()
                    standard = (cm.get("standard") or "").strip()
                    if measure:
                        tag = f"{qname}→{gname}" if qname and qname != gname else gname
                        gr_sol_lines.append(
                            f"→ [{tag}] {measure}"
                            + (f"｜验证/标准：{standard}" if standard else "")
                            + (f"｜匹配方式：{mtype}" if mtype else "")
                        )
        except Exception as e:
            logger.warning("8D 引用 GraphRAG 失败: %s", e)
            knowledge_pack["error"] = str(e)

        # —— D4：数据定位 + 缺陷对齐 + 鱼骨根因 + 5Why ——
        align_lines = defect_alignment.get("summary_lines") or []
        if not align_lines and seed_defects:
            try:
                defect_alignment = get_graphrag_client().align_erp_defects(seed_defects[:5])
                align_lines = defect_alignment.get("summary_lines") or []
                knowledge_pack["defect_alignment"] = defect_alignment
            except Exception:
                pass
        d4_parts = ["【D4 根因分析 · 企业实战版（候选根因须现场核实后才能关闭）】"]
        d4_parts.append(
            "一、数据定位（ERP/MES 实据，非推测）\n"
            f"- 料号：{part_number}｜周期：{fiscal or evidence.get('date_range') or '—'}\n"
            f"- 报废率：{scrap_rate:.2f}%（报废 {scrap_pcs} PCS / 入库 {stock_pcs} PCS）\n"
            f"- 缺陷帕累托（ERP 原名）：{defect_desc}\n"
            f"- 工序集中度：主责「{primary_process or '—'}」占报废 "
            f"{primary_ratio:.1f}%"
            + (
                f"；其余：" + "、".join(
                    f"{pr['process']}({pr['ratio']:.1f}%)" for pr in process_rank[1:4]
                ) if len(process_rank) > 1 else ""
            )
            + "\n"
            f"- MES 主责工序：{mes_primary_line}\n"
            f"- MES NG：\n{mes_ng_block}\n"
            f"- 优先核查工单：{wo_focus or wo_line}"
        )
        d4_parts.append(
            "二、缺陷名对齐（ERP 保留原文 → GraphRAG 图谱名，用于检索根因/对策）\n"
            + (
                "\n".join(f"- {ln}" for ln in align_lines)
                if align_lines
                else "- （无缺陷可对齐；请在 config/defect_mapping.json 补充映射）"
            )
            + "\n- 说明：不修改 ERP 源数据；未对齐项不会编造根因"
        )
        if hist_cause_lines:
            d4_parts.append(
                "三、历史关闭 8D / 已验证知识（同类问题曾用方案）\n"
                + "\n\n".join(hist_cause_lines[:4])
            )
        fishbone_order = ["方法", "物料", "设备", "人员", "环境", "检测", "其他"]
        section_rc = "四" if hist_cause_lines else "三"
        if gr_cause_by_cat:
            blocks = []
            for cat in fishbone_order:
                lines = gr_cause_by_cat.get(cat) or []
                if not lines:
                    continue
                blocks.append(f"【{cat}】\n" + "\n".join(f"  - {x}" for x in lines[:4]))
            for cat, lines in gr_cause_by_cat.items():
                if cat not in fishbone_order and lines:
                    blocks.append(f"【{cat}】\n" + "\n".join(f"  - {x}" for x in lines[:3]))
            matched = "、".join(
                f"{r.get('query')}→{r.get('matched')}"
                for r in (knowledge_pack.get("refs") or [])[:5]
                if r.get("matched")
            )
            d4_parts.append(
                f"{section_rc}、根因候选（按对齐后的图谱缺陷拉取 FMEA"
                + (f"；{matched}" if matched else "")
                + "）\n"
                + "\n".join(blocks)
            )
        elif gr_cause_lines:
            d4_parts.append(
                f"{section_rc}、根因候选（GraphRAG/FMEA）\n" + "\n".join(gr_cause_lines[:10])
            )
        else:
            unmapped = "、".join(defect_alignment.get("unmapped") or seed_defects[:3]) or "当前缺陷"
            d4_parts.append(
                f"{section_rc}、根因候选\n"
                f"- 图谱暂未命中可用根因（未对齐或无 FMEA）：{unmapped}\n"
                "- 请补 config/defect_mapping.json 映射，或按下列清单现场取证，禁止空模板充数。\n"
                f"- 建议优先查：{primary_process or '主责工序'} 的首件/参数记录、油墨/药水批次、"
                "网板/菲林状态、曝光/显影参数、IPQC 漏失。"
            )

        top1 = (top_defects[0].get("defect_type") if top_defects else "") or "TOP不良"
        top1_ratio = float(top_defects[0].get("ratio") or 0) if top_defects else 0
        why2_hint = (
            gr_cause_lines[0].replace("→ ", "") if gr_cause_lines
            else f"{primary_process or '主责工序'} 工艺参数/物料批次待核对"
        )
        section_why = "五" if hist_cause_lines else "四"
        d4_parts.append(
            f"{section_why}、5Why（数据驱动假设链 · 每步须留证据）\n"
            f"Why1：为何报废率达 {scrap_rate:.2f}%？\n"
            f"  → 因「{top1}」等不良集中，TOP1 占 {top1_ratio:.1f}%；"
            f"工序「{primary_process or '—'}」占报废 {primary_ratio:.1f}%（ERP）。\n"
            f"Why2：为何 {primary_process or '该工序'} 输出 {top1}？\n"
            f"  → 候选：{why2_hint}\n"
            "Why3：为何该因素失控？\n"
            "  → 查：SOP/作业指导符合性、参数记录、换型点检、来料批次（待现场勾证据）。\n"
            "Why4：为何未在流出前拦截？\n"
            f"  → 查：首件/巡检频次与判定标准、MES {mes_primary_line}、IPQC 漏检。\n"
            "Why5：体系哪一环失效？\n"
            "  → 待确认：工艺文件/培训/设备点检/供应商变更中的失效点，并落实到 D7。"
        )
        priority_causes = []
        for lines in gr_cause_by_cat.values():
            priority_causes.extend(lines)
        priority_causes = priority_causes[:3] or (
            [f"现场锁定 {primary_process or '主责工序'} 导致 {top1} 的关键参数/物料"]
        )
        section_judge = "六" if hist_cause_lines else "五"
        d4_parts.append(
            f"{section_judge}、初步判定（待现场确认后改写为【确定根因】）\n"
            + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(priority_causes))
            + "\n【确定根因】□ 已现场确认（勾选并改写成立项）　"
            "未核实项不得作为关闭结论；关闭后回写已验证知识库"
        )
        d4 = "\n\n".join(d4_parts)
        # 对齐结果写入 evidence，便于前端/追溯
        evidence["defect_alignment"] = defect_alignment

        deadline = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        d5_parts = ["【D5 永久纠正措施 · 对准根因、可执行、可验证】"]
        if hist_sol_lines:
            d5_parts.append(
                "一、历史关闭 8D / 已验证对策\n" + "\n\n".join(hist_sol_lines[:4])
            )
        if gr_sol_lines:
            numbered = []
            for i, line in enumerate(gr_sol_lines[:8], 1):
                numbered.append(
                    f"{i}. {line.lstrip('→ ')}\n"
                    f"   - 责任角色：工艺工程 / 生产 / 品质（待点名）\n"
                    f"   - 完成时限：{deadline}\n"
                    f"   - 验证方式：首件+过程抽检；对照 D6 报废率/工序良率"
                )
            d5_parts.append(
                "二、知识库对策（GraphRAG/FMEA，须按本厂 SOP 裁剪后执行）\n"
                + "\n".join(numbered)
            )
        if not hist_sol_lines and not gr_sol_lines:
            d5_parts.append(
                "二、对策待制定（知识库未命中可用条目）\n"
                f"1. 针对主责工序「{primary_process or '—'}」与 TOP 不良「{top1}」"
                "制定参数窗口/点检/来料管制（禁止空话）\n"
                "2. 每条对策必须写清：做什么、谁做、何时完成、用哪项 ERP/MES 指标验收"
            )
        d5_parts.append(
            "三、实施与验收计划\n"
            f"- 总体完成时限：{deadline}\n"
            f"- 验收基线：报废率 {scrap_rate:.2f}%（报废 {scrap_pcs} / 入库 {stock_pcs}）"
            f"{(' · ' + fiscal) if fiscal else ''}\n"
            f"- 验收目标：连续 3 批报废率 ≤ 2.0%，且「{primary_process or '主责工序'}」"
            "相关不良占比显著下降\n"
            "- 跟踪：品质部每周用 ERP View_P_Inspection + MES 检验实绩复算，结果写入 D6\n"
            "- 说明：未完成现场根因确认前，对策仅作试行；确认后固化到作业指导并进入 D7"
        )
        d5 = "\n\n".join(d5_parts)

        target_scrap = 2.0
        d6 = (
            "【验证方案（对照本单 ERP/MES 基线，验证结果待实测回填）】\n"
            f"1. 基线报废率 {scrap_rate:.2f}%（报废 {scrap_pcs} / 入库 {stock_pcs} PCS）"
            f"{' · 周期 ' + fiscal if fiscal else ''}\n"
            f"2. 目标：连续 3 个生产批次报废率 ≤ {target_scrap:.1f}%（或较基线下降 ≥50%）\n"
            f"3. 主责工序「{top_process or '相关工序'}」首件/巡检加严，并回写 MES 检验实绩\n"
            "4. 品质部跟踪 2 周，用 ERP View_P_Inspection + FGI 复算报废率验收\n"
            "【验收状态】未验证（关闭前须填写实测结果）"
        )

        problem = (
            f"料号{part_number}品质异常：报废率{scrap_rate:.2f}%"
            f"（报废{scrap_pcs}PCS/入库{stock_pcs}PCS）"
            f"{('，周期'+fiscal) if fiscal else ''}，主要不良：{defect_desc}"
        )
        kb_hits = int(knowledge_pack.get("hit_count") or 0)

        return {
            "id": report_id,
            "report_no": report_no,
            "part_number": part_number,
            "customer": evidence.get("customer") or "",
            "problem": problem,
            "d1": (
                f"【成立团队 · 待任命确认】\n"
                f"建议牵头：品质部；协同：{top_process or '相关'}工序工程/生产\n"
                f"成立日期：{now}\n"
                f"目标：7 个工作日内完成根因确认与永久对策落地\n"
                f"关联证据：ERP 报废 {scrap_pcs} PCS，MES 工序行 {len(mes_yield)} 条"
                f"{f'，知识库命中 {kb_hits} 条' if kb_hits else ''}"
            ),
            "d2": (
                f"【问题描述 · ERP/MES 实据】\n"
                f"【产品】料号：{part_number}\n"
                f"【周期】{fiscal or evidence.get('date_range') or '见查询条件'}\n"
                f"【现象】报废率 {scrap_rate:.2f}% = 报废 {scrap_pcs} / (报废+入库 {scrap_pcs + stock_pcs}) PCS\n"
                f"【主要不良 TOP】{defect_desc}\n"
                f"【主责工序线索】{top_process or '—'}\n"
                f"【关联工单】{wo_line}\n"
                f"【MES 工序良率】\n{mes_block}\n"
                f"【MES NG 明细】\n{mes_ng_block}\n"
                f"【5W】\n"
                f"- What：{defect_desc}\n"
                f"- Where：{top_process or '见缺陷工序'}\n"
                f"- When：{fiscal or '见 CreateDate/检验时间'}\n"
                f"- Who：以工单/检验责任人为准（系统不编造人名）\n"
                f"- How much：报废 {scrap_pcs} PCS\n"
                f"【数据源】{evidence.get('data_sources') or 'ERP+MES'}"
            ),
            "d3": (
                "【临时遏制 · 按实据执行，完成情况待勾选】\n"
                f"1. 隔离料号 {part_number} 相关在制/库存，优先核查工单：{wo_line}\n"
                f"2. 对主责工序「{top_process or '相关工序'}」加严自检/首件，暂停可疑批出货\n"
                f"3. 以 ERP 报废明细复核 TOP 不良是否持续扩大（当前报废 {scrap_pcs} PCS）\n"
                "4. 客户通知：仅在确认已出货风险后由业务执行（系统不默认已通知）\n"
                f"【完成时限】{(datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')}\n"
                "【责任人】待指定"
            ),
            "d4": d4,
            "d5": d5,
            "d6": d6,
            "d7": (
                "【预防再发 · 关闭前落实】\n"
                f"1. 针对「{top_process or '主责工序'} / TOP 不良」更新作业指导与检验要点（有变更才落档）\n"
                "2. 本单关闭后系统将根因/对策写入「已验证知识库」，并与 GraphRAG 知识库对照沉淀\n"
                "3. 同类料号/工序做预防排查（范围由工程确认）\n"
                "【状态】未完成"
            ),
            "d8": (
                "【关闭确认 · 未完成前勿勾选】\n"
                "- [ ] 根因已现场确认（对照历史 8D / GraphRAG 知识库）\n"
                "- [ ] 永久对策已实施\n"
                "- [ ] ERP/MES 验证指标达标（见 D6）\n"
                "- [ ] 已验证知识已回写\n"
                f"\n报告生成时间：{now}\n系统：{VERSION}\n"
                "政策：指标仅 ERP/MES；D4/D5 可引用历史关闭 8D + GraphRAG 知识库，须现场复核"
            ),
            "defects_json": json.dumps(top_defects[:10], ensure_ascii=False),
            "evidence_json": json.dumps(evidence, ensure_ascii=False, default=str),
            "history_refs_json": json.dumps(hist_refs, ensure_ascii=False),
            "knowledge_refs_json": json.dumps(knowledge_pack, ensure_ascii=False),
            "data_policy": (
                "metrics=ERP/MES; "
                "actions=closed_8D+verified+GraphRAG_ref_or_pending; "
                "no_template_as_fact"
            ),
            "status": "open",
            "severity": 5 if scrap_rate >= 10 else (4 if scrap_rate >= 5 else 3),
            "created_by": "系统·实据+知识库",
            "created_at": datetime.now().isoformat(),
            "history_count": len(hist_refs),
            "verified_pattern_count": len(verified_patterns or []),
            "knowledge_hit_count": kb_hits,
            "knowledge_refs": knowledge_pack,
            "defect_alignment": defect_alignment,
        }

    def generate_8d_content(self, part_number: str, defects: list,
                             scrap_rate: float, process_data: list) -> dict:
        """兼容旧调用：转为 evidence 结构（无历史时 D4/D5 为待填写）"""
        evidence = {
            "scrap_rate": scrap_rate,
            "scrap_pcs": 0,
            "stock_pcs": 0,
            "defects": defects or [],
            "mes_process_yield": process_data or [],
            "mes_process_defects": [],
            "work_orders": [],
            "process_scrap": [],
            "data_sources": "legacy_call",
        }
        return self.generate_8d_from_evidence(part_number, evidence, [], [])

    def generate_recommendations(self, defects: list, process_data: list,
                                  scrap_rate: float, part_number: str) -> list:
        """生成改善建议列表"""
        recs = []

        # 基于报废率的建议
        if scrap_rate > 5:
            recs.append({
                "priority": "P0",
                "type": "立即行动",
                "title": f"报废率严重超标（{scrap_rate:.2f}%）",
                "action": "立即启动8D分析，停止不合格批次出货，100%检验",
                "owner": "品质部",
                "deadline": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            })
        elif scrap_rate > 2:
            recs.append({
                "priority": "P1",
                "type": "优先处理",
                "title": f"报废率偏高（{scrap_rate:.2f}%），需重点关注",
                "action": "开展专项分析，制定改善计划，增加该品种检验频率",
                "owner": "品质部",
                "deadline": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
            })

        # 基于工序数据的建议（对策来自 GraphRAG 生产图谱）
        gr = get_graphrag_client()
        for p in (process_data or [])[:5]:
            rate = float(p.get("PassRate", p.get("yield_rate", 100)))
            pname = str(p.get("ProcessName", p.get("process_name", "")))
            if rate < 90 and pname:
                solutions = []
                try:
                    solutions = gr.suggest_for_process(pname, max_items=2)
                except Exception:
                    solutions = []
                recs.append({
                    "priority": "P1" if rate < 85 else "P2",
                    "type": "工序改善",
                    "title": f"{pname}工序良率{rate:.1f}%低于警戒线",
                    "action": (
                        solutions[0]
                        if solutions
                        else f"排查{pname}工序异常；请对照 GraphRAG「{pname}」缺陷根因制定对策"
                    ),
                    "owner": "工程部",
                    "deadline": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
                    "source": "GraphRAG/FMEA" if solutions else "MES",
                })

        # 基于不良类型的建议（根因对策来自 GraphRAG，按缺陷名精确/模糊匹配）
        if defects:
            top_defect = defects[0]
            dtype = str(top_defect.get("defect_type", top_defect.get("DefectType", "")))
            proc = str(top_defect.get("process", top_defect.get("Process", "")))
            if dtype:
                solutions = []
                try:
                    solutions = gr.suggest_for_defect(dtype, max_items=2)
                    if not solutions and proc:
                        solutions = gr.suggest_for_process(proc, max_items=2)
                except Exception:
                    solutions = []
                if solutions:
                    recs.append({
                        "priority": "P1",
                        "type": "根因消除",
                        "title": f"TOP1不良：{proc}-{dtype}，需彻底根治",
                        "action": "；".join(solutions[:2]),
                        "owner": f"{proc}工序负责人" if proc else "工程部",
                        "deadline": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
                        "source": "GraphRAG/FMEA",
                    })

        # 通用改善建议
        if not recs:
            recs.append({
                "priority": "P3",
                "type": "持续改善",
                "title": "品质状况良好，保持持续改善",
                "action": "持续监控关键指标，定期开展QCC活动",
                "owner": "品质部",
                "deadline": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            })

        return recs[:8]

    def generate_findings(self, scrap: dict, process: dict, score_result: dict,
                          part_label: str) -> list:
        """体检问题清单（诊断结论，非总览KPI复述）"""
        findings = []
        scrap_rate = float(scrap.get("scrap_rate", 0) or 0)
        has_mes = bool(process.get("process_yield"))
        avg_yield = process.get("avg_yield")
        min_yield = process.get("min_yield")
        avg_yield_f = float(avg_yield) if avg_yield is not None else None
        min_yield_f = float(min_yield) if min_yield is not None else None
        defects = scrap.get("defect_top10") or []
        weak_procs = [
            p for p in (process.get("process_yield") or [])
            if float(p.get("PassRate", p.get("yield_rate", 100)) or 100) < 95
        ]
        for gap in (score_result.get("data_gaps") or []):
            findings.append({
                "level": "warning", "dim": "数据完整性",
                "title": "数据源缺失（不计分、不编造）",
                "detail": gap,
            })

        if scrap_rate > 5:
            findings.append({
                "level": "critical", "dim": "报废",
                "title": f"报废率 {scrap_rate:.2f}% 严重超标（目标≤2%）",
                "detail": f"{part_label} 本期报废 {fmt_num(scrap.get('total_scrap_pcs'))} PCS，需立即启动专项改善",
            })
        elif scrap_rate > 2:
            findings.append({
                "level": "warning", "dim": "报废",
                "title": f"报废率 {scrap_rate:.2f}% 高于目标",
                "detail": "建议结合 TOP 缺陷与责任工序做根因排查",
            })

        if defects:
            top = defects[0]
            dtype = top.get("defect_type") or top.get("DefectType") or "未知缺陷"
            proc = top.get("process") or top.get("Process") or "—"
            pcs = top.get("scrap_pcs") or top.get("ScrapPcs") or 0
            findings.append({
                "level": "warning", "dim": "缺陷",
                "title": f"TOP1 缺陷：{dtype}（{proc}）",
                "detail": f"报废 {fmt_num(pcs)} PCS，占比 {float(top.get('ratio') or 0):.1f}%",
            })

        if has_mes and min_yield_f is not None and min_yield_f < 90:
            worst = min(
                (process.get("process_yield") or [{"ProcessName": "—", "PassRate": min_yield_f}]),
                key=lambda x: float(x.get("PassRate", x.get("yield_rate", 100)) or 100),
            )
            findings.append({
                "level": "critical", "dim": "过程",
                "title": f"瓶颈工序 {(worst.get('ProcessName') or '—')} 良率 {min_yield_f:.1f}%",
                "detail": "低于 90% 警戒线，建议优先工程介入（数据来源 MES 检验）",
            })
        elif has_mes and avg_yield_f is not None and avg_yield_f < 95:
            findings.append({
                "level": "warning", "dim": "检验",
                "title": f"MES 工序平均良率 {avg_yield_f:.1f}% 偏低",
                "detail": f"共 {len(weak_procs)} 道工序良率 <95%，需关注过程稳定性",
            })

        scores = score_result.get("scores") or {}
        dim_max = score_result.get("dim_max") or {
            "scrap": 25, "inspection": 20, "process": 20,
        }
        dim_cn = {"scrap": "报废", "inspection": "检验", "process": "过程"}
        for k, mx in dim_max.items():
            if k not in dim_cn:
                continue
            s = scores.get(k)
            if s is None:
                continue
            if mx and int(s) / mx < 0.5:
                findings.append({
                    "level": "warning", "dim": dim_cn.get(k, k),
                    "title": f"{dim_cn.get(k, k)}维度得分偏低（{s}/{mx}）",
                    "detail": "该项基于 ERP/MES 实测算得，建议纳入本期改善重点",
                })

        real_findings = [f for f in findings if f.get("dim") != "数据完整性"]
        if not real_findings:
            findings.append({
                "level": "ok", "dim": "综合",
                "title": "本期未发现重大品质异常",
                "detail": "已接入维度指标处于可控范围；缺失数据源请在 MES/ERP 补充后再评",
            })
        return findings[:10]


def fmt_num(v):
    try:
        return f"{int(float(v or 0)):,}"
    except Exception:
        return str(v or 0)


# ═══════════════════════════════════════════════════
# 主系统（整合原有数据连接）
# ═══════════════════════════════════════════════════

class PCBQualitySystem:
    """
    PCB品质分析主系统
    整合ERP/MES数据连接 + 知识库 + 学习引擎
    支持按项目切换测试/生产环境数据源
    """

    def __init__(self):
        self.erp = None
        self.mes = None
        self.env_context = None
        # 按当前激活环境绑定知识库（默认 test → 现有 v2 库，数据不丢）
        try:
            ctx = get_active_context()
            paths = resolve_paths(ctx["config"])
            self.db = QualityKnowledgeDB(paths["kb"])
            self.env_context = ctx
            gr_url = (ctx["config"].get("graphrag_url") or "").strip()
            if gr_url:
                get_graphrag_client(gr_url)
        except Exception as e:
            logger.warning(f"加载项目环境失败，回退默认知识库: {e}")
            self.db = QualityKnowledgeDB()
        self.engine = SelfLearningEngine(self.db)
        logger.info("PCB品质分析系统 v2.0 初始化完成")

    def connect(self):
        """按当前激活环境连接 ERP/MES（本地 SQLite；不覆盖未激活环境数据）"""
        try:
            from app.quality_intelligence_main import QualityIntelligenceSystem
            ctx = get_active_context()
            paths = resolve_paths(ctx["config"])
            self.env_context = ctx
            # 知识库按环境文件隔离；切换不删旧文件
            if Path(self.db.db_path) != paths["kb"]:
                self.db.rebind(paths["kb"])
                self.engine = SelfLearningEngine(self.db)
            gr_url = (ctx["config"].get("graphrag_url") or "").strip()
            if gr_url:
                get_graphrag_client(gr_url)
            else:
                reset_graphrag_client()
                get_graphrag_client()

            qi = QualityIntelligenceSystem()
            logger.info(
                "正在连接ERP/MES… 项目=%s 环境=%s erp=%s mes=%s",
                ctx["project_id"],
                ctx["env"],
                paths["erp"],
                paths["mes"],
            )
            qi.connect(erp_db_path=str(paths["erp"]), mes_db_path=str(paths["mes"]))
            self.erp = qi.erp
            self.mes = qi.mes
            if self.erp and self.erp.connected:
                logger.info(f"ERP数据库连接成功: {getattr(self.erp, 'db_path', paths['erp'])}")
            else:
                logger.error("无法导入数据连接器" if not self.erp else "ERP未连接")
            if self.mes and self.mes.connected:
                logger.info(f"MES数据库连接成功: {getattr(self.mes, 'db_path', paths['mes'])}")
            else:
                logger.warning("MES数据库未连接")
        except Exception as e:
            logger.error(f"数据库连接失败: {e}")

    def is_connected(self):
        return bool(self.erp and self.erp.connected)

    def _get_default_dates(self, start_date=None, end_date=None):
        """获取默认日期（当月1号到今天）"""
        today = datetime.now().strftime('%Y-%m-%d')
        first_day = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        return (start_date or first_day, end_date or today)

    # ─── ERP数据查询 ─────────────────────────────────

    def get_scrap_analysis(self, part_number=None, start_date=None, end_date=None, period=None, fiscal_period=None):
        """报废数据分析（对应模板Sheet1）
        
        参数:
            part_number: 料号
            start_date: 开始日期（兼容旧接口）
            end_date: 结束日期（兼容旧接口）
            period: 周期（格式YYYY-MM，旧接口，兼容保留）
            fiscal_period: ERP周期码（格式YYYYMM，如202604）
        """
        result = {
            "total_scrap_pcs": 0,
            "total_in_stock_pcs": 0,   # 真实入库数量（FGI_StockFormItem）
            "scrap_rate": 0,
            "defect_top10": [],
            "monthly_trend": [],
            "process_scrap": [],
            "data_source": "ERP:View_P_Inspection+FGI_StockFormItemWO",
            "has_data": False,
        }

        if not self.erp or not self.erp.connected:
            result["data_gap"] = "ERP 未连接，无法取报废/入库实数"
            return result

        # 优先使用FiscalPeriod；无任何时间条件时默认当月周期（与仪表板一致，避免「截止今天」漏数）
        # fiscal_period格式：YYYYMM（如202611）
        if not fiscal_period and not period and not start_date and not end_date:
            fiscal_period = datetime.now().strftime("%Y%m")

        if fiscal_period:
            # 使用FiscalPeriod过滤
            # 同时设置日期范围用于入库查询（日历月，右开区间）
            year = int(fiscal_period[:4])
            month = int(fiscal_period[4:6])
            start_date = f"{year}-{month:02d}-01"
            if month == 12:
                end_date = f"{year+1}-01-01"
            else:
                end_date = f"{year}-{month+1:02d}-01"
            result["fiscal_period"] = fiscal_period
            result["date_filter"] = "fiscal_period+stockDate_month"
        elif period:
            year, month = period.split("-")
            fiscal_period = f"{year}{month}"
            return self.get_scrap_analysis(
                part_number, start_date, end_date, period=None, fiscal_period=fiscal_period
            )
        else:
            start_date, end_date = self._get_default_dates(start_date, end_date)
            result["date_filter"] = "createDate/stockDate_range"

        try:
            # 查询条件 - MRB报废包含所有检验类型的报废
            # ERP日报口径：排除以下非量产性报废
            # 1. 非生产工序: 样板组/品质部/工艺部/设备部/销售部/测试划线/FQC/FQC2
            # 2. 非生产项目: 实验板/备品/实验
            # 3. 测试工序和测试相关检验项目: ERP日报不统计入库前的测试报废
            non_production_processes = ['样板组', '品质部', '工艺部', '设备部', '销售部', '测试划线', 'FQC', 'FQC(内部客检)', 'FQC2']
            non_production_items = ['实验板', '备品', '实验']
            np_proc_list = "', '".join(non_production_processes)
            np_item_list = "', '".join(non_production_items)
            insp_conds = [
                "v.InspectionResult = 'Scrapped'",
                f"ISNULL(v.InspectionProcess, '') NOT IN ('{np_proc_list}')",
                f"ISNULL(v.InspectionItem, '') NOT IN ('{np_item_list}')",
                # 排除测试工序和测试相关检验项目(ERP日报不统计入库前的测试报废)
                "ISNULL(v.InspectionProcess, '') <> '测试'",
                # 排除检验项目包含"测试"的记录(如测试擦花/测试板损/测试压伤等)
                "ISNULL(v.InspectionItem, '') NOT LIKE '%测试%'"
            ]
            wo_conds = ["(wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)"]

            if part_number:
                insp_conds.append(f"v.MfgPartCode LIKE '%{part_number}%'")
                wo_conds.append(f"mp.mfgPartCode LIKE '%{part_number}%'")
            
            # 周期过滤：优先使用FiscalPeriod
            if fiscal_period:
                insp_conds.append(f"v.FiscalPeriod = '{fiscal_period}'")
                wo_conds.append(f"wo.FiscalPeriod = '{fiscal_period}'")
            else:
                # 兼容旧接口：使用日期范围
                if start_date:
                    insp_conds.append(f"v.CreateDate >= '{start_date}'")
                    wo_conds.append(f"wo.createDate >= '{start_date}'")
                if end_date:
                    insp_conds.append(f"v.CreateDate < '{end_date}'")
                    wo_conds.append(f"wo.createDate < '{end_date}'")

            insp_where = " AND ".join(insp_conds)
            wo_where = " AND ".join(wo_conds)

            # 总报废
            r = self.erp.execute(f"SELECT SUM(v.QtyOfUnit) AS TotalScrap FROM View_P_Inspection v WHERE {insp_where}")
            if r.get("success") and r.get("data"):
                result["total_scrap_pcs"] = int(r["data"][0].get("TotalScrap") or 0)

            # ── 真实入库数量：从FGI_StockFormItemWO（生产入库关联表）通过工单查询 ──
            # FGI_StockFormItemWO.woId → P_WO.recId → E_JobMfgParts.mfgPartCode
            # 关键：FiscalPeriod 模式下必须按 wo.FiscalPeriod 过滤，不能按日历 stockDate。
            # 例：202610 工单的 FGI 常在 11 月初入库，用 stockDate∈10月会得到入库=0。
            stock_conds = ["1=1"]
            if part_number:
                stock_conds.append(f"mp.mfgPartCode LIKE '%{part_number}%'")
            if fiscal_period:
                stock_conds.append(f"wo.FiscalPeriod = '{fiscal_period}'")
            else:
                if start_date:
                    stock_conds.append(f"sfi.stockDate >= '{start_date}'")
                if end_date:
                    stock_conds.append(f"sfi.stockDate < '{end_date}'")
            stock_where = " AND ".join(stock_conds)

            r_stock = self.erp.execute(f"""
                SELECT SUM(sfiwo.qtyOfPCS) AS TotalInStock
                FROM FGI_StockFormItemWO sfiwo
                INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {stock_where}
            """)
            if r_stock.get("success") and r_stock.get("data"):
                result["total_in_stock_pcs"] = int(r_stock["data"][0].get("TotalInStock") or 0)

            # 无 FGI 时回退工单产出 PCS（同周期），避免月趋势整段空白；并标记口径
            if result["total_in_stock_pcs"] <= 0 and fiscal_period:
                r_wo_qty = self.erp.execute(f"""
                    SELECT SUM(wo.qty_PCS_PRODUCED) AS Produced
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE wo.FiscalPeriod = '{fiscal_period}'
                      AND (wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)
                      {f"AND mp.mfgPartCode LIKE '%{part_number}%'" if part_number else ""}
                """)
                if r_wo_qty.get("success") and r_wo_qty.get("data"):
                    produced = int(r_wo_qty["data"][0].get("Produced") or 0)
                    if produced > 0:
                        result["total_in_stock_pcs"] = produced
                        result["stock_source"] = "P_WO.qty_PCS_PRODUCED"
                        result["data_gap"] = (result.get("data_gap") or "") + \
                            ("；" if result.get("data_gap") else "") + \
                            "本期无 FGI 入库记录，入库口径回退为工单产出 PCS"
                    else:
                        result["stock_source"] = "none"
                else:
                    result["stock_source"] = "none"
            elif result["total_in_stock_pcs"] > 0:
                result["stock_source"] = "FGI_StockFormItemWO"

            # 报废率 = 报废/(报废+入库)；仅报废无入库→100%；皆无→0 并标记无投入基数
            scrap_pcs = result["total_scrap_pcs"]
            stock_pcs = result["total_in_stock_pcs"]
            input_base = scrap_pcs + stock_pcs
            result["input_base_pcs"] = input_base
            if input_base > 0:
                result["scrap_rate"] = round(scrap_pcs * 100.0 / input_base, 2)
                result["has_data"] = True
            elif scrap_pcs > 0:
                result["scrap_rate"] = 100.0
                result["has_data"] = True
                result["data_gap"] = "有报废但无入库 PCS，请在 ERP 补充 FGI 生产入库"
            else:
                result["scrap_rate"] = 0
                result["has_data"] = False
                result["data_gap"] = "本期无报废且无入库，请确认 ERP 周期/料号筛选"

            # TOP10缺陷
            r = self.erp.execute(f"""
                SELECT TOP 10
                    ISNULL(v.InspectionItem, v.InspectionProcess) AS defect_type,
                    v.InspectionProcess AS process,
                    SUM(v.QtyOfUnit) AS scrap_pcs
                FROM View_P_Inspection v
                WHERE {insp_where}
                GROUP BY v.InspectionItem, v.InspectionProcess
                ORDER BY SUM(v.QtyOfUnit) DESC
            """)
            if r.get("success") and r.get("data"):
                total = result["total_scrap_pcs"] or 1
                for row in r["data"]:
                    sp = int(row.get("scrap_pcs") or 0)
                    result["defect_top10"].append({
                        "defect_type": row.get("defect_type", ""),
                        "process": row.get("process", ""),
                        "scrap_pcs": sp,
                        "ratio": round(sp * 100.0 / total, 1)
                    })

            # 按日趋势（查询区间内每日报废/入库/报废率）
            r = self.erp.execute(f"""
                SELECT
                    CONVERT(VARCHAR(10), v.CreateDate, 120) AS Day,
                    SUM(v.QtyOfUnit) AS TotalScrap
                FROM View_P_Inspection v
                WHERE {insp_where}
                GROUP BY CONVERT(VARCHAR(10), v.CreateDate, 120)
                ORDER BY Day DESC
            """)
            if r.get("success") and r.get("data"):
                # 每日入库（FGI_StockFormItemWO 通过工单关联）
                r2 = self.erp.execute(f"""
                    SELECT CONVERT(VARCHAR(10), sfi.stockDate, 120) AS Day,
                           SUM(sfiwo.qtyOfPCS) AS TotalInStock
                    FROM FGI_StockFormItemWO sfiwo
                    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {stock_where}
                    GROUP BY CONVERT(VARCHAR(10), sfi.stockDate, 120)
                """)
                inp_map = {}
                if r2.get("success") and r2.get("data"):
                    for row in r2["data"]:
                        inp_map[row.get("Day", "")] = int(row.get("TotalInStock") or 0)

                # 合并有报废或有入库的日期，按日升序，最多 62 天
                day_set = set()
                scrap_map = {}
                for row in r["data"]:
                    d = row.get("Day", "")
                    if not d:
                        continue
                    day_set.add(d)
                    scrap_map[d] = int(row.get("TotalScrap") or 0)
                day_set.update(inp_map.keys())
                for d in sorted(day_set)[-62:]:
                    sc = scrap_map.get(d, 0)
                    inp = inp_map.get(d, 0)
                    rate = round(sc * 100.0 / (sc + inp), 2) if (sc + inp) > 0 else 0
                    result["monthly_trend"].append({
                        "Day": d, "Month": d,  # Month 兼容旧前端字段
                        "TotalScrap": sc,
                        "TotalInStock": inp, "ScrapRate": rate
                    })

            # 按工序汇总报废
            r = self.erp.execute(f"""
                SELECT TOP 10
                    v.InspectionProcess AS process,
                    SUM(v.QtyOfUnit) AS scrap_pcs
                FROM View_P_Inspection v
                WHERE {insp_where} AND v.InspectionProcess IS NOT NULL
                GROUP BY v.InspectionProcess
                ORDER BY SUM(v.QtyOfUnit) DESC
            """)
            if r.get("success") and r.get("data"):
                total = result["total_scrap_pcs"] or 1
                for row in r["data"]:
                    sp = int(row.get("scrap_pcs") or 0)
                    result["process_scrap"].append({
                        "process": row.get("process", ""),
                        "scrap_pcs": sp,
                        "ratio": round(sp * 100.0 / total, 1)
                    })

            # 触发自主学习
            if result["defect_top10"]:
                self.engine.learn_from_defects(result["defect_top10"])

        except Exception as e:
            logger.error(f"报废分析失败: {e}")

        return result

    def get_process_yield(self, part_number=None, fiscal_period=None):
        """工序良率（MES）— 经 ERP 工单关联；可按 FiscalPeriod 限定当月工单。"""
        result = {
            "process_yield": [], "process_defects": [], "inspect_records": [],
            "inspect_count": 0, "min_yield": None, "avg_yield": None, "wo_matched": 0,
            "data_source": "MES:TBL_QM_INSPECT_RECORD",
            "has_data": False,
            "fiscal_period": fiscal_period or "",
        }

        if not self.mes or not self.mes.connected:
            result["data_gap"] = "MES 未连接，无法取工序良率"
            return result

        try:
            # MES CMO_LOT = 工单号：先从 ERP 取工单，再查 MES
            wo_list = []
            if self.erp and self.erp.connected and (part_number or fiscal_period):
                where_wo = ["1=1"]
                if part_number:
                    where_wo.append(f"mp.mfgPartCode LIKE '%{part_number}%'")
                if fiscal_period:
                    where_wo.append(f"wo.FiscalPeriod = '{fiscal_period}'")
                erp_r = self.erp.execute(f"""
                    SELECT DISTINCT wo.woNumber
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {' AND '.join(where_wo)}
                """)
                if erp_r.get("success") and erp_r.get("data"):
                    wo_list = [d.get("woNumber", "") for d in erp_r["data"] if d.get("woNumber")]
                    result["wo_matched"] = len(wo_list)
                    logger.info(
                        "工序查询 料号=%s 周期=%s 工单=%s",
                        part_number or "全厂", fiscal_period or "不限", len(wo_list),
                    )

            # 有料号/周期过滤但无工单：不回退全库，避免口径错乱
            if (part_number or fiscal_period) and not wo_list:
                tip = []
                if part_number:
                    tip.append(f"料号 {part_number}")
                if fiscal_period:
                    tip.append(f"周期 {fiscal_period}")
                result["data_gap"] = f"{' / '.join(tip)} 在 ERP 无关联工单，无法取 MES 工序良率"
                return result

            if wo_list:
                wo_filter = "','".join(wo_list[:200])
                where_clause = f"r.CRESULT IS NOT NULL AND r.CMO_LOT IN ('{wo_filter}')"
                where_clause2 = f"r.CRESULT = 2 AND i.CTEMPLATE_ITEM_NAME IS NOT NULL AND r.CMO_LOT IN ('{wo_filter}')"
                where_records = f"r.CMO_LOT IN ('{wo_filter}')"
            else:
                where_clause = "r.CRESULT IS NOT NULL"
                where_clause2 = "r.CRESULT = 2 AND i.CTEMPLATE_ITEM_NAME IS NOT NULL"
                where_records = "r.CRESULT IS NOT NULL"
                logger.info("无料号/周期过滤，返回全厂工序数据")

            r = self.mes.execute(f"""
                SELECT TOP 20
                    r.CPROCESS_ID AS ProcessID,
                    ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                    COUNT(*) AS InspectCount,
                    SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) AS PassCount,
                    SUM(CASE WHEN r.CRESULT = 2 THEN 1 ELSE 0 END) AS NgCount,
                    CAST(SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) AS DECIMAL(5,2)) AS PassRate
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE {where_clause}
                GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME
                ORDER BY InspectCount DESC
            """)
            if r.get("success") and r.get("data"):
                data = r["data"]
                for item in data:
                    item["yield_rate"] = item.get("PassRate", 0)
                result["process_yield"] = data

                rates = [float(d.get("PassRate", 0)) for d in data if d.get("PassRate") is not None]
                if rates:
                    result["min_yield"] = round(min(rates), 2)
                    result["avg_yield"] = round(sum(rates) / len(rates), 2)
                    result["has_data"] = True
                    result["inspect_count"] = sum(int(d.get("InspectCount") or 0) for d in data)
                else:
                    result["data_gap"] = "MES 有工序行但无有效良率，请检查 CRESULT 是否完整"

            # 不良项目明细
            r2 = self.mes.execute(f"""
                SELECT TOP 50
                    r.CPROCESS_ID AS ProcessID,
                    ISNULL(p.CPROCESS_NAME, '未知工序') AS ProcessName,
                    i.CTEMPLATE_ITEM_NAME AS DefectItem,
                    i.CINPUT_VALUE AS DefectValue,
                    COUNT(*) AS DefectCount
                FROM TBL_QM_INSPECT_RECORD r
                INNER JOIN TBL_QM_INSPECTION_RECORD_ITEM i ON r.CID = i.CINSPECTION_RECORD_ID
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE {where_clause2}
                GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME, i.CTEMPLATE_ITEM_NAME, i.CINPUT_VALUE
                ORDER BY DefectCount DESC
            """)
            if r2.get("success"):
                result["process_defects"] = r2.get("data", [])

            # 检验履历（工单/条码/工序/结果/时间）— 追溯页核心
            # 实际字段：CCREATE_DATE / CSUBMIT_QTY / CINSPECT_CODE（非 CINSPECT_TIME）
            r3 = self.mes.execute(f"""
                SELECT TOP 80
                    ISNULL(r.CMO_LOT, '') AS WoNumber,
                    ISNULL(r.CSCAN_BARCODE, '') AS Barcode,
                    ISNULL(r.CINSPECT_CODE, '') AS InspectCode,
                    ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                    r.CRESULT AS ResultCode,
                    CASE WHEN r.CRESULT = 1 THEN '合格' WHEN r.CRESULT = 2 THEN '不合格' ELSE '其他' END AS ResultName,
                    r.CCREATE_DATE AS InspectTime,
                    ISNULL(r.CSUBMIT_QTY, 0) AS InspectQty
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE {where_records}
                ORDER BY r.CCREATE_DATE DESC
            """)
            if r3.get("success"):
                result["inspect_records"] = r3.get("data", [])
                result["inspect_count"] = len(result["inspect_records"])
            else:
                logger.warning(f"MES检验履历查询失败: {r3.get('error')}")

        except Exception as e:
            logger.error(f"工序良率查询失败: {e}")

        return result

    def get_work_orders(self, part_number=None, start_date=None, end_date=None, fiscal_period=None):
        """工单数据
        
        参数:
            part_number: 料号
            start_date: 开始日期（兼容旧接口）
            end_date: 结束日期（兼容旧接口）
            fiscal_period: ERP周期码（格式YYYYMM，如202604）
        """
        result = {
            "total_wo": 0, "total_input_pcs": 0, "total_reject_pcs": 0,
            "reject_rate": 0, "work_orders": [], "daily_trend": []
        }

        if not self.erp or not self.erp.connected:
            return result

        try:
            where_parts = []
            if part_number:
                where_parts.append(f"mp.mfgPartCode LIKE '%{part_number}%'")
            
            # 优先使用FiscalPeriod
            if fiscal_period:
                where_parts.append(f"wo.FiscalPeriod = '{fiscal_period}'")
            else:
                # 兼容旧接口：使用日期范围
                if start_date:
                    where_parts.append(f"wo.createDate >= '{start_date}'")
                if end_date:
                    where_parts.append(f"wo.createDate <= '{end_date} 23:59:59'")

            where_sql = " AND ".join(where_parts) if where_parts else "1=1"

            r = self.erp.execute(f"""
                SELECT TOP 100
                    wo.woNumber, mp.mfgPartCode AS PartNumber, mp.mfgPartName AS PartName,
                    wo.qty_PCS_PRODUCED AS PcsProduced, wo.reject_PCS AS RejectPcs,
                    wo.qty_PNL_PRODUCED AS PnlProduced, wo.createDate, wo.endDate, wo.status
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql}
                ORDER BY wo.createDate DESC
            """)

            if r.get("success") and r.get("data"):
                wos = r["data"]
                result["total_wo"] = len(wos)
                result["work_orders"] = wos[:50]

                daily = defaultdict(lambda: {"input": 0, "reject": 0})
                for wo in wos:
                    result["total_input_pcs"] += int(wo.get("PcsProduced") or 0)
                    result["total_reject_pcs"] += int(wo.get("RejectPcs") or 0)
                    d = str(wo.get("createDate", ""))[:10]
                    if d:
                        daily[d]["input"] += int(wo.get("PcsProduced") or 0)
                        daily[d]["reject"] += int(wo.get("RejectPcs") or 0)

                if result["total_input_pcs"] > 0:
                    result["reject_rate"] = round(
                        result["total_reject_pcs"] * 100.0 / result["total_input_pcs"], 2
                    )
                result["daily_trend"] = [
                    {"date": k, "input_pcs": v["input"], "reject_pcs": v["reject"]}
                    for k, v in sorted(daily.items())
                ]

        except Exception as e:
            logger.error(f"工单查询失败: {e}")

        return result

    def get_comprehensive_health(self, part_number=None, start_date=None, end_date=None, period=None, fiscal_period=None):
        """
        品质全面体检（核心功能）
        整合所有维度 → 计算综合评分 → 生成建议 → 触发预警
        
        参数:
            part_number: 料号；为空时按全厂/当月汇总（ERP+MES）
            start_date: 开始日期（兼容旧接口）
            end_date: 结束日期（兼容旧接口）
            period: 周期（格式YYYY-MM，旧接口，兼容保留）
            fiscal_period: ERP周期码（格式YYYYMM，如202604）
        """
        part_number = (part_number or "").strip() or None
        part_label = part_number or "全厂"

        # 优先 FiscalPeriod；无日期条件时默认当月（禁止用假 IQC/客诉凑分）
        if fiscal_period:
            pass
        elif period:
            year, month = period.split("-")
            fiscal_period = f"{year}{month}"
        elif not start_date and not end_date:
            fiscal_period = datetime.now().strftime("%Y%m")
        else:
            start_date, end_date = self._get_default_dates(start_date, end_date)

        # 1. 各维度数据（无料号=全厂当月；MES 按同周期工单关联）
        scrap = self.get_scrap_analysis(part_number, start_date, end_date, fiscal_period=fiscal_period)
        process = self.get_process_yield(part_number, fiscal_period=fiscal_period)
        wo = self.get_work_orders(part_number, start_date, end_date, fiscal_period=fiscal_period)

        scrap_rate = scrap.get("scrap_rate", 0)
        avg_yield = process.get("avg_yield")  # None=无MES，禁止默认95
        min_yield = process.get("min_yield")
        has_scrap = bool(scrap.get("has_data")) or int(scrap.get("total_scrap_pcs") or 0) > 0 or int(scrap.get("total_in_stock_pcs") or 0) > 0
        has_mes = bool(process.get("has_data")) and bool(process.get("process_yield"))

        # 客诉 / COPQ（与体检周期对齐：FiscalPeriod→当月日历；否则用日期范围）
        if fiscal_period and len(str(fiscal_period)) >= 6:
            fp = str(fiscal_period)
            y, m = int(fp[:4]), int(fp[4:6])
            c_start = f"{y}-{m:02d}-01"
            if m == 12:
                c_end = f"{y + 1}-01-01"
            else:
                c_end = f"{y}-{m + 1:02d}-01"
        else:
            c_start, c_end = start_date, end_date
        copq = self.get_copq_analysis(c_start, c_end, part_number=part_number)
        has_complaint = bool(copq.get("has_data"))

        # IQC（有 ERP 实表则计分；不做设备 OEE）
        gates = self.get_inspection_gate_kpis(30)
        iqc_info = gates.get("iqc") or {}

        # 2. 评分计算（仅品质相关 ERP/MES 实测）
        metrics = {
            "scrap_rate": scrap_rate if has_scrap else None,
            "avg_process_yield": avg_yield,
            "min_process_yield": min_yield,
            "has_scrap_data": has_scrap,
            "has_mes_yield": has_mes,
            "has_complaint_data": has_complaint,
            "complaint_open": int((copq.get("kpi") or {}).get("open_count") or 0),
            "copq_amount": float((copq.get("kpi") or {}).get("copq_total") or 0),
            "complaint_severity_avg": (copq.get("kpi") or {}).get("severity_avg"),
            "has_iqc_data": bool(iqc_info.get("count")),
            "iqc_pass_rate": iqc_info.get("pass_rate"),
            "iqc_count": int(iqc_info.get("count") or 0),
            "iqc_sample_ok": bool(iqc_info.get("sample_ok")),
        }
        score_result = self.engine.calculate_quality_score(metrics)

        # 3. 问题清单 + 改善建议（体检核心，区别于总览KPI）
        findings = self.engine.generate_findings(scrap, process, score_result, part_label)
        recommendations = self.engine.generate_recommendations(
            scrap.get("defect_top10", []),
            process.get("process_yield", []),
            scrap_rate, part_label
        )

        # 4. 预警
        alerts = self.engine.generate_alerts(
            scrap_rate, part_label,
            process.get("process_yield", []),
            scrap.get("monthly_trend", []),
            scrap_pcs=float(scrap.get("total_scrap_pcs") or 0),
        )

        # 5. 存储报告（含 ERP/MES 实据快照，供历史页核验；无实据不落库）
        gaps = score_result.get("data_gaps") or []
        verdict = (
            f"{part_label} 本期综合评级 {score_result['grade']}（{score_result['grade_name']}），"
            f"得分 {score_result['total']}/100（已接入满分 {score_result.get('available_max', 0)} 归一化）；"
            f"检出 {len([f for f in findings if f.get('level') not in ('ok',) and f.get('dim') != '数据完整性'])} 项待改善；"
            f"数据缺口 {len(gaps)} 项（缺口不计分、不编造）。"
        )
        if fiscal_period:
            date_range = f"FiscalPeriod {fiscal_period}"
        elif start_date or end_date:
            date_range = f"{start_date or '不限'} ~ {end_date or '不限'}"
        else:
            date_range = "不限 ~ 不限"

        key = part_number or "ALL"
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        report_id = f"HR-{key[:12]}-{ts}"
        # report_no 含时分秒，避免同日同料号撞号无法区分
        report_no = (
            f"QHC-{ts}-"
            f"{hashlib.md5(f'{key}|{fiscal_period or date_range}|{ts}'.encode()).hexdigest()[:6].upper()}"
        )

        top_defects = (scrap.get("defect_top10") or [])[:8]
        evidence = {
            "fiscal_period": fiscal_period or "",
            "date_range": date_range,
            "part_number": part_label,
            "scope": "plant" if not part_number else "part",
            "has_scrap": has_scrap,
            "has_mes": has_mes,
            "has_complaint": has_complaint,
            "scrap_rate": float(scrap.get("scrap_rate") or 0) if has_scrap else None,
            "scrap_pcs": int(scrap.get("total_scrap_pcs") or 0),
            "stock_pcs": int(scrap.get("total_in_stock_pcs") or 0),
            "copq_total": float((copq.get("kpi") or {}).get("copq_total") or 0) if has_complaint else None,
            "complaint_open": int((copq.get("kpi") or {}).get("open_count") or 0) if has_complaint else None,
            "scrap_cost_est": float((copq.get("kpi") or {}).get("scrap_cost_est") or 0),
            "input_base_pcs": int(scrap.get("input_base_pcs") or 0),
            "mes_avg_yield": avg_yield,
            "mes_min_yield": min_yield,
            "mes_inspect_count": int(process.get("inspect_count") or 0),
            "defect_top": [
                {
                    "defect_type": d.get("defect_type"),
                    "process": d.get("process"),
                    "scrap_pcs": int(d.get("scrap_pcs") or 0),
                    "ratio": d.get("ratio"),
                }
                for d in top_defects if d.get("defect_type")
            ],
            "weak_processes": [
                {
                    "process": p.get("ProcessName") or p.get("process_name"),
                    "pass_rate": p.get("PassRate", p.get("yield_rate")),
                    "inspect_count": int(p.get("InspectCount") or p.get("total_qty") or 0),
                }
                for p in (process.get("process_yield") or [])
                if float(p.get("PassRate", p.get("yield_rate", 100)) or 100) < 98
            ][:10],
            "work_order_count": len(wo.get("work_orders") or []),
            "data_sources": "ERP:View_P_Inspection+FGI+P_WO; MES:TBL_QM_INSPECT_RECORD",
            "scrap_data_gap": scrap.get("data_gap"),
            "mes_data_gap": process.get("data_gap"),
            "data_gaps": gaps,
        }
        policy = "metrics=ERP/MES; snapshot=evidence+findings+score; no_fabricated_kpi"

        saved = False
        if has_scrap or has_mes:
            self.db.save_health_report({
                "id": report_id,
                "report_no": report_no,
                "part_number": part_label,
                "date_range": date_range,
                "fiscal_period": fiscal_period or "",
                "scope": "plant" if not part_number else "part",
                "verdict": verdict,
                "overall_score": score_result["total"],
                "grade": score_result["grade"],
                "dimensions_json": json.dumps(score_result.get("scores") or {}, ensure_ascii=False),
                "recommendations_json": json.dumps(recommendations, ensure_ascii=False),
                "evidence_json": json.dumps(evidence, ensure_ascii=False, default=str),
                "findings_json": json.dumps(findings, ensure_ascii=False),
                "score_json": json.dumps(score_result, ensure_ascii=False),
                "data_policy": policy,
            })
            saved = True
        else:
            logger.warning("体检无 ERP/MES 实据，跳过写入历史：%s %s", part_label, date_range)

        return {
            "report_id": report_id,
            "report_no": report_no,
            "part_number": part_label,
            "scope": "plant" if not part_number else "part",
            "fiscal_period": fiscal_period,
            "date_range": date_range,
            "generated_at": datetime.now().isoformat(),
            "verdict": verdict,
            "score": score_result,
            "data_gaps": gaps,
            "findings": findings,
            "scrap": scrap,
            "process": process,
            "work_orders": wo,
            "recommendations": recommendations,
            "new_alerts": len(alerts),
            "alerts": [{"id": a["id"], "message": a["message"],
                        "level": a["level"]} for a in alerts],
            "evidence": evidence,
            "saved_to_history": saved,
            "policy": policy,
        }

    @staticmethod
    def _prev_fiscal_period(fiscal_period: str) -> str:
        """YYYYMM → 上一会计月"""
        try:
            y, m = int(fiscal_period[:4]), int(fiscal_period[4:6])
            if m <= 1:
                return f"{y - 1}12"
            return f"{y}{m - 1:02d}"
        except Exception:
            return ""

    @staticmethod
    def _fiscal_period_window(end_fp: str, months: int) -> list:
        """生成以 end_fp 为终点的连续 FiscalPeriod 列表（升序，长度=months）。"""
        end_fp = str(end_fp or datetime.now().strftime("%Y%m"))
        if len(end_fp) < 6:
            end_fp = datetime.now().strftime("%Y%m")
        y, m = int(end_fp[:4]), int(end_fp[4:6])
        out = []
        for _ in range(months):
            out.append(f"{y}{m:02d}")
            m -= 1
            if m <= 0:
                m = 12
                y -= 1
        return list(reversed(out))

    def get_monthly_scrap_series(self, months: int = 12) -> list:
        """
        近 N 个连续 FiscalPeriod 报废/入库/报废率序列（领导 MoM/趋势）。
        轴固定 N 个月；ERP 无记录的月份以空点展示，不伪造报废率。
        口径与 get_scrap_analysis 一致：报废/(报废+FGI入库)。
        """
        months = max(3, min(int(months or 12), 24))
        if not (self.erp and self.erp.connected):
            return []
        try:
            # 终点：取「当前自然月」与「检验库最新周期」的较晚者，保证当月在轴上
            end_fp = datetime.now().strftime("%Y%m")
            r_fp = self.erp.execute("""
                SELECT TOP 1 FiscalPeriod
                FROM View_P_Inspection
                WHERE FiscalPeriod IS NOT NULL AND FiscalPeriod <> ''
                GROUP BY FiscalPeriod
                ORDER BY FiscalPeriod DESC
            """)
            if r_fp.get("success") and r_fp.get("data"):
                latest = str(r_fp["data"][0].get("FiscalPeriod") or "")
                if latest and latest > end_fp:
                    end_fp = latest

            fps = self._fiscal_period_window(end_fp, months)
            series = []
            for fp in fps:
                scrap = self.get_scrap_analysis(None, None, None, fiscal_period=fp)
                scrap_pcs = int(scrap.get("total_scrap_pcs") or 0)
                stock_pcs = int(scrap.get("total_in_stock_pcs") or 0)
                input_pcs = scrap_pcs + stock_pcs
                # 无入库仅有报废时，报废率=100% 会误导领导，标记为口径不全
                rate_reliable = stock_pcs > 0 and input_pcs > 0
                rate = round(scrap_pcs * 100.0 / input_pcs, 2) if rate_reliable else None
                has_data = bool(scrap.get("has_data") or scrap_pcs or stock_pcs)
                gap = scrap.get("data_gap")
                if scrap_pcs > 0 and stock_pcs <= 0:
                    gap = gap or "仅有报废PCS，无 FGI/工单入库口径，不计算报废率"
                elif not has_data:
                    gap = "本期无报废/入库记录"
                series.append({
                    "fiscal_period": fp,
                    "label": f"{fp[:4]}-{fp[4:6]}" if len(fp) >= 6 else fp,
                    "scrap_pcs": scrap_pcs,
                    "stock_pcs": stock_pcs,
                    "input_pcs": input_pcs,
                    "scrap_rate": rate,
                    "rate_reliable": rate_reliable,
                    "stock_source": scrap.get("stock_source") or ("FGI" if stock_pcs else "none"),
                    "data_gap": gap,
                    "has_data": has_data,
                })
            return series
        except Exception as e:
            logger.warning("月度报废序列失败: %s", e)
            return []

    @staticmethod
    def _parse_dt(val):
        """解析常见时间字符串，失败返回 None。"""
        if val is None or val == "":
            return None
        if isinstance(val, datetime):
            return val
        s = str(val).strip().replace("T", " ")
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(s[:26], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
        except Exception:
            return None

    def get_management_closure_stats(self) -> dict:
        """预警确认 / 8D 关闭等管理闭环指标（本地真实单据，含时效，禁止编造）。"""
        now = datetime.now()
        alerts_all = self.db.get_alerts(acknowledged=None, limit=500) or []
        active = [a for a in alerts_all if not int(a.get("acknowledged") or 0)]
        acked = [a for a in alerts_all if int(a.get("acknowledged") or 0)]
        critical_active = [
            a for a in active
            if str(a.get("level") or "").lower() in ("critical", "严重", "high")
        ]
        total_n = len(active) + len(acked)
        ack_rate = round(len(acked) * 100.0 / total_n, 1) if total_n else None

        # 未确认预警龄期（小时）
        active_ages_h = []
        for a in active:
            created = self._parse_dt(a.get("created_at"))
            if created:
                active_ages_h.append(max(0.0, (now - created).total_seconds() / 3600.0))
        alert_avg_age_h = round(sum(active_ages_h) / len(active_ages_h), 1) if active_ages_h else None
        alert_over_24h = sum(1 for h in active_ages_h if h >= float(ALERT_OVERDUE_HOURS))

        # 已确认响应时长（创建→确认）
        ack_hours = []
        for a in acked:
            created = self._parse_dt(a.get("created_at"))
            acked_at = self._parse_dt(a.get("ack_at"))
            if created and acked_at and acked_at >= created:
                ack_hours.append((acked_at - created).total_seconds() / 3600.0)
        alert_avg_ack_h = round(sum(ack_hours) / len(ack_hours), 1) if ack_hours else None

        open_8d = self.db.get_8d_list(status="open", limit=200) or []
        closed_8d = self.db.get_8d_list(status="closed", limit=200) or []
        cutoff = (now - timedelta(days=30)).isoformat()
        closed_30d = [
            r for r in closed_8d
            if str(r.get("closed_at") or r.get("updated_at") or "") >= cutoff
        ]

        open_ages_d = []
        for r in open_8d:
            created = self._parse_dt(r.get("created_at"))
            if created:
                open_ages_d.append(max(0.0, (now - created).total_seconds() / 86400.0))
        eight_d_avg_open_days = (
            round(sum(open_ages_d) / len(open_ages_d), 1) if open_ages_d else None
        )
        eight_d_overdue = sum(1 for d in open_ages_d if d >= float(EIGHT_D_OVERDUE_DAYS))

        close_days = []
        for r in closed_8d:
            created = self._parse_dt(r.get("created_at"))
            closed = self._parse_dt(r.get("closed_at") or r.get("updated_at"))
            if created and closed and closed >= created:
                close_days.append((closed - created).total_seconds() / 86400.0)
        eight_d_avg_close_days = (
            round(sum(close_days) / len(close_days), 1) if close_days else None
        )

        return {
            "active_alerts": len(active),
            "acked_alerts": len(acked),
            "critical_alerts": len(critical_active),
            "alert_ack_rate": ack_rate,
            "alert_avg_age_hours": alert_avg_age_h,
            "alert_over_24h": alert_over_24h,
            "alert_avg_ack_hours": alert_avg_ack_h,
            "pending_8d": len(open_8d),
            "closed_8d_total": len(closed_8d),
            "closed_8d_30d": len(closed_30d),
            "eight_d_avg_open_days": eight_d_avg_open_days,
            "eight_d_overdue": eight_d_overdue,
            "eight_d_avg_close_days": eight_d_avg_close_days,
            "eight_d_overdue_days_threshold": float(EIGHT_D_OVERDUE_DAYS),
            "alert_overdue_hours_threshold": float(ALERT_OVERDUE_HOURS),
        }

    def get_material_iqc_for_part(self, part_number: str = None, day_count: int = 180) -> dict:
        """
        追溯用：来料 IQC + 供应商质量（quality_inspection_records incoming + materials）。
        按料号/产品码模糊匹配；无匹配时仍返回近期 IQC 汇总（标注 scope）。
        """
        n = max(30, min(int(day_count or 180), 365))
        result = {
            "has_data": False,
            "data_source": "ERP:quality_inspection_records+materials",
            "window_days": n,
            "part_number": part_number or "",
            "scope": "part" if part_number else "plant",
            "kpi": {
                "count": 0, "done_count": 0, "pass_count": 0, "fail_count": 0,
                "pass_rate": None, "pending_count": 0,
            },
            "supplier_quality": [],
            "total_supplier": 0,
            "recent_records": [],
            "note": "",
            "data_gaps": [],
        }
        db = self.erp if (self.erp and self.erp.connected) else self.mes
        if not (db and db.connected):
            result["note"] = "ERP/MES 未连接"
            result["data_gaps"].append("ERP/MES 未连接")
            return result

        safe_part = str(part_number or "").replace("'", "''").strip()
        part_cond = ""
        if safe_part:
            part_cond = (
                f" AND (IFNULL(q.product_code,'') LIKE '%{safe_part}%'"
                f" OR IFNULL(q.material_code,'') LIKE '%{safe_part}%'"
                f" OR IFNULL(q.material_name,'') LIKE '%{safe_part}%')"
            )

        pass_expr = """(
            IFNULL(q.source_result,0)=1
            OR LOWER(IFNULL(q.inspection_result,'')) IN ('pass','ok','合格')
        )"""
        fail_expr = """(
            IFNULL(q.source_result,0)=2
            OR LOWER(IFNULL(q.inspection_result,'')) IN ('fail','ng','不合格','reject')
        )"""
        done_expr = """(
            LOWER(IFNULL(q.inspection_result,'')) NOT IN ('pending','待审','待处理','')
            OR IFNULL(q.source_result,0) IN (1,2)
        )"""

        try:
            r = db.execute(f"""
                SELECT COUNT(*) AS c,
                       SUM(CASE WHEN {done_expr} THEN 1 ELSE 0 END) AS done_c,
                       SUM(CASE WHEN {pass_expr} THEN 1 ELSE 0 END) AS pass_c,
                       SUM(CASE WHEN {fail_expr} THEN 1 ELSE 0 END) AS fail_c,
                       SUM(CASE WHEN LOWER(IFNULL(q.inspection_result,''))='pending'
                                 OR IFNULL(q.status,'')='pending' THEN 1 ELSE 0 END) AS pend_c
                FROM quality_inspection_records q
                WHERE q.inspection_type = 'incoming'
                  AND q.inspected_at IS NOT NULL
                  AND q.inspected_at >= datetime('now', '-{n} day')
                  {part_cond}
            """)
            matched_part = False
            if r.get("success") and r.get("data"):
                row = r["data"][0]
                c = int(row.get("c") or 0)
                if c > 0:
                    matched_part = True
                elif safe_part:
                    # 料号无 incoming 命中：放宽为全厂近期 IQC，避免追溯物料页空白
                    result["scope"] = "plant_fallback"
                    result["data_gaps"].append(
                        f"料号「{part_number}」近{n}日无 incoming 记录，已展示全厂来料 IQC"
                    )
                    part_cond = ""
                    r = db.execute(f"""
                        SELECT COUNT(*) AS c,
                               SUM(CASE WHEN {done_expr} THEN 1 ELSE 0 END) AS done_c,
                               SUM(CASE WHEN {pass_expr} THEN 1 ELSE 0 END) AS pass_c,
                               SUM(CASE WHEN {fail_expr} THEN 1 ELSE 0 END) AS fail_c,
                               SUM(CASE WHEN LOWER(IFNULL(q.inspection_result,''))='pending'
                                         OR IFNULL(q.status,'')='pending' THEN 1 ELSE 0 END) AS pend_c
                        FROM quality_inspection_records q
                        WHERE q.inspection_type = 'incoming'
                          AND q.inspected_at IS NOT NULL
                          AND q.inspected_at >= datetime('now', '-{n} day')
                    """)
                    row = (r.get("data") or [{}])[0]
                    c = int(row.get("c") or 0)
                done_c = int(row.get("done_c") or 0)
                pass_c = int(row.get("pass_c") or 0)
                fail_c = int(row.get("fail_c") or 0)
                pend_c = int(row.get("pend_c") or 0)
                result["kpi"] = {
                    "count": c,
                    "done_count": done_c,
                    "pass_count": pass_c,
                    "fail_count": fail_c,
                    "pending_count": pend_c,
                    "pass_rate": round(pass_c * 100.0 / done_c, 2) if done_c > 0 else None,
                }
                if matched_part:
                    result["scope"] = "part"
                result["has_data"] = c > 0

            # 供应商汇总：优先 materials 主数据
            rs = db.execute(f"""
                SELECT
                    IFNULL(NULLIF(TRIM(m.supplier_code), ''), '未知') AS supplier_code,
                    IFNULL(NULLIF(TRIM(m.supplier_name), ''), '未知供应商') AS supplier_name,
                    IFNULL(NULLIF(TRIM(q.material_code), ''), IFNULL(m.material_code, '')) AS material_code,
                    IFNULL(NULLIF(TRIM(q.material_name), ''), IFNULL(m.material_name, '')) AS material_name,
                    COUNT(*) AS inspect_count,
                    SUM(CASE WHEN {done_expr} THEN 1 ELSE 0 END) AS done_count,
                    SUM(CASE WHEN {pass_expr} THEN 1 ELSE 0 END) AS pass_count,
                    SUM(CASE WHEN {fail_expr} THEN 1 ELSE 0 END) AS fail_count
                FROM quality_inspection_records q
                LEFT JOIN materials m ON (
                    (q.material_id IS NOT NULL AND q.material_id = m.id)
                    OR (IFNULL(q.material_code,'') <> '' AND q.material_code = m.material_code)
                )
                WHERE q.inspection_type = 'incoming'
                  AND q.inspected_at IS NOT NULL
                  AND q.inspected_at >= datetime('now', '-{n} day')
                  {part_cond}
                GROUP BY 1, 2, 3, 4
                ORDER BY fail_count DESC, inspect_count DESC
                LIMIT 50
            """)
            suppliers = []
            if rs.get("success"):
                for row in (rs.get("data") or []):
                    done = int(row.get("done_count") or 0)
                    pas = int(row.get("pass_count") or 0)
                    fail = int(row.get("fail_count") or 0)
                    suppliers.append({
                        "supplier_code": row.get("supplier_code") or "未知",
                        "supplier_name": row.get("supplier_name") or "未知供应商",
                        "material_code": row.get("material_code") or "",
                        "material_name": row.get("material_name") or "",
                        "inspect_count": int(row.get("inspect_count") or 0),
                        "done_count": done,
                        "pass_count": pas,
                        "fail_count": fail,
                        "pass_rate": round(pas * 100.0 / done, 2) if done > 0 else None,
                    })
            result["supplier_quality"] = suppliers
            result["total_supplier"] = len({
                (s.get("supplier_code"), s.get("supplier_name")) for s in suppliers
            })

            rr = db.execute(f"""
                SELECT q.inspection_no, q.inspection_result, q.status,
                       q.product_code, q.material_code, q.material_name,
                       q.process_name, q.defect_reason, q.inspected_at,
                       q.inspected_qty, q.non_conforming_qty, q.inspector,
                       IFNULL(m.supplier_code, '') AS supplier_code,
                       IFNULL(m.supplier_name, '') AS supplier_name
                FROM quality_inspection_records q
                LEFT JOIN materials m ON (
                    (q.material_id IS NOT NULL AND q.material_id = m.id)
                    OR (IFNULL(q.material_code,'') <> '' AND q.material_code = m.material_code)
                )
                WHERE q.inspection_type = 'incoming'
                  AND q.inspected_at IS NOT NULL
                  AND q.inspected_at >= datetime('now', '-{n} day')
                  {part_cond}
                ORDER BY q.inspected_at DESC
                LIMIT 40
            """)
            if rr.get("success"):
                result["recent_records"] = rr.get("data") or []

            if not result["has_data"]:
                result["note"] = f"近{n}日无 incoming（IQC）检验记录"
                result["data_gaps"].append(result["note"])
            elif result["kpi"].get("done_count", 0) < 30:
                result["note"] = (
                    f"IQC 已出结论仅 {result['kpi'].get('done_count')} 单，样本不足仅供参考"
                )
                result["data_gaps"].append(result["note"])
            else:
                result["note"] = "来自 quality_inspection_records（incoming）实据"
            return result
        except Exception as e:
            result["note"] = f"IQC 查询失败: {e}"
            result["data_gaps"].append(str(e))
            return result

    def get_inspection_gate_kpis(self, day_count: int = 30) -> dict:
        """
        来料(IQC)/终检(FQC)/一次交验/复检返工 —— 来自 quality_inspection_records 实表。
        inspection_type: incoming / final / process；is_reinspection=1 视为复检。
        """
        n = max(7, min(int(day_count or 30), 90))
        result = {
            "has_data": False,
            "day_count": n,
            "data_source": "ERP/MES:quality_inspection_records",
            "iqc": {"count": 0, "pass_rate": None, "sample_ok": False},
            "fqc": {"count": 0, "pass_rate": None, "sample_ok": False},
            "fty": {"count": 0, "pass_rate": None},  # 一次交验：process 且非复检
            "rework": {"count": 0, "rate": None, "base_count": 0},
            "data_gaps": [],
        }
        db = self.mes if (self.mes and self.mes.connected) else self.erp
        if not (db and db.connected):
            result["data_gaps"].append("MES/ERP 未连接")
            return result
        try:
            # 口径：仅统计已出结论单据；pending 不进合格率分母
            pass_expr = """(
                IFNULL(source_result,0)=1
                OR LOWER(IFNULL(inspection_result,'')) IN ('pass','ok','合格')
            )"""
            done_expr = """(
                LOWER(IFNULL(inspection_result,'')) NOT IN ('pending','待审','待处理','')
                OR IFNULL(source_result,0) IN (1,2)
            )"""

            def _fetch(days):
                rr = db.execute(f"""
                    SELECT inspection_type,
                           IFNULL(is_reinspection,0) AS is_re,
                           COUNT(*) AS c,
                           SUM(CASE WHEN {done_expr} THEN 1 ELSE 0 END) AS done_c,
                           SUM(CASE WHEN {pass_expr} THEN 1 ELSE 0 END) AS pass_c
                    FROM quality_inspection_records
                    WHERE inspected_at IS NOT NULL
                      AND inspected_at >= datetime('now', '-{int(days)} day')
                    GROUP BY inspection_type, IFNULL(is_reinspection,0)
                """)
                return rr

            r = _fetch(n)
            rows = r.get("data") or []
            if not r.get("success"):
                result["data_gaps"].append(r.get("error") or "查询失败")
                return result

            def _agg(rows_):
                iqc_c = iqc_d = iqc_p = fqc_c = fqc_d = fqc_p = 0
                fty_c = fty_p = 0
                re_c = proc_base = 0
                for row in rows_:
                    t = str(row.get("inspection_type") or "").lower()
                    is_re = int(row.get("is_re") or 0)
                    c = int(row.get("c") or 0)
                    d = int(row.get("done_c") or 0)
                    p = int(row.get("pass_c") or 0)
                    if t == "incoming":
                        iqc_c += c
                        iqc_d += d
                        iqc_p += p
                    elif t == "final":
                        fqc_c += c
                        fqc_d += d
                        fqc_p += p
                    elif t == "process":
                        proc_base += c
                        if is_re:
                            re_c += c
                        else:
                            fty_c += d
                            fty_p += p
                return iqc_c, iqc_d, iqc_p, fqc_c, fqc_d, fqc_p, fty_c, fty_p, re_c, proc_base

            (iqc_c, iqc_d, iqc_p, fqc_c, fqc_d, fqc_p,
             fty_c, fty_p, re_c, proc_base) = _agg(rows)

            # IQC/FQC 近窗为空时，放宽到 180 天仍只展示实据，并标注窗口
            extended = False
            if iqc_c == 0 or fqc_c == 0:
                r2 = _fetch(180)
                if r2.get("success") and r2.get("data"):
                    (iqc_c2, iqc_d2, iqc_p2, fqc_c2, fqc_d2, fqc_p2,
                     _, _, _, _) = _agg(r2["data"])
                    if iqc_c == 0 and iqc_c2 > 0:
                        iqc_c, iqc_d, iqc_p = iqc_c2, iqc_d2, iqc_p2
                        extended = True
                    if fqc_c == 0 and fqc_c2 > 0:
                        fqc_c, fqc_d, fqc_p = fqc_c2, fqc_d2, fqc_p2
                        extended = True

            def _rate(pass_c, done_c):
                return round(pass_c * 100.0 / done_c, 2) if done_c > 0 else None

            result["iqc"] = {
                "count": iqc_c,
                "done_count": iqc_d,
                "pass_count": iqc_p,
                "pass_rate": _rate(iqc_p, iqc_d),
                "sample_ok": iqc_d >= 30,
                "window_days": 180 if extended and iqc_c else n,
            }
            result["fqc"] = {
                "count": fqc_c,
                "done_count": fqc_d,
                "pass_count": fqc_p,
                "pass_rate": _rate(fqc_p, fqc_d),
                "sample_ok": fqc_d >= 30,
                "window_days": 180 if extended and fqc_c else n,
            }
            result["fty"] = {
                "count": fty_c,
                "pass_count": fty_p,
                "pass_rate": _rate(fty_p, fty_c),
            }
            result["rework"] = {
                "count": re_c,
                "base_count": proc_base,
                "rate": round(re_c * 100.0 / proc_base, 2) if proc_base > 0 else None,
            }
            if iqc_c == 0:
                result["data_gaps"].append("近180日无 incoming（IQC）检验记录")
            elif iqc_d < 30:
                result["data_gaps"].append(
                    f"IQC 已出结论仅 {iqc_d} 单（共 {iqc_c}），样本不足仅供参考"
                )
            if fqc_c == 0:
                result["data_gaps"].append("近180日无 final（FQC/出货）检验记录")
            elif fqc_d < 30:
                result["data_gaps"].append(
                    f"FQC 已出结论仅 {fqc_d} 单（共 {fqc_c}），样本不足仅供参考"
                )
            if extended:
                result["data_gaps"].append("IQC/FQC 近30日无数据，已按最长180日实据展示")
            result["has_data"] = bool(iqc_c or fqc_c or fty_c or re_c)
            return result
        except Exception as e:
            result["data_gaps"].append(str(e))
            return result

    def get_quality_dppm_summary(self, day_count: int = 30) -> dict:
        """DPPM / 过程缺陷率 —— ERP quality_metrics 实表。"""
        n = max(7, min(int(day_count or 30), 90))
        empty = {
            "has_data": False, "day_count": n, "dppm": None,
            "defect_rate": None, "scrap_rate": None,
            "good": 0, "defect": 0, "scrap": 0, "total": 0,
            "data_source": "ERP:quality_metrics",
        }
        if not (self.erp and self.erp.connected):
            empty["data_gap"] = "ERP 未连接"
            return empty
        try:
            r = self.erp.execute(f"""
                SELECT IFNULL(SUM(good_count),0) AS g,
                       IFNULL(SUM(defect_count),0) AS d,
                       IFNULL(SUM(scrap_count),0) AS s,
                       IFNULL(SUM(total_inspected),0) AS t
                FROM quality_metrics
                WHERE record_date >= date('now', '-{n} day')
            """)
            if not (r.get("success") and r.get("data")):
                empty["data_gap"] = r.get("error") or "无 quality_metrics"
                return empty
            row = r["data"][0]
            g, d, s, t = (int(row.get("g") or 0), int(row.get("d") or 0),
                          int(row.get("s") or 0), int(row.get("t") or 0))
            if t <= 0:
                empty["data_gap"] = f"近{n}日 quality_metrics 无检验量"
                return empty
            return {
                "has_data": True,
                "day_count": n,
                "good": g, "defect": d, "scrap": s, "total": t,
                "dppm": round(d * 1_000_000.0 / t, 1),
                "defect_rate": round(d * 100.0 / t, 3),
                "scrap_rate": round(s * 100.0 / t, 3),
                "data_source": "ERP:quality_metrics",
            }
        except Exception as e:
            empty["data_gap"] = str(e)
            return empty

    def get_dashboard(self, fiscal_period=None):
        """仪表板总览（当月 FiscalPeriod）+ 领导决策层（目标/环比/月趋势/闭环）。"""
        if not fiscal_period:
            fiscal_period = datetime.now().strftime("%Y%m")

        result = {
            "timestamp": datetime.now().isoformat(),
            "erp_connected": bool(self.erp and self.erp.connected),
            "mes_connected": bool(self.mes and self.mes.connected),
            "fiscal_period": fiscal_period,
            "kpi": {
                "total_wo": 0, "total_input": 0, "total_reject": 0, "reject_rate": 0,
                "pass_qty": 0, "pass_rate": 0,
            },
            "top_defect_parts": [],
            "top_defect_items": [],
            "ranking_mode": "scrap_parts",
            "ranking_title": "TOP报废料号（当月）",
            "active_alerts": 0,
            "pending_8d": 0,
            "knowledge_stats": self.db.get_learning_stats(),
            "process_overview": [],
        }

        if self.erp and self.erp.connected:
            try:
                scrap_data = self.get_scrap_analysis(None, None, None, fiscal_period=fiscal_period)
                tr = scrap_data.get("total_scrap_pcs", 0)
                ti = scrap_data.get("total_in_stock_pcs", 0)

                # 工单数优先用 P_WO 当月，避免仅统计报废条码导致偏少
                wo_count = 0
                r_wo = self.erp.execute(
                    f"SELECT COUNT(*) AS TotalWo FROM P_WO WHERE FiscalPeriod = '{fiscal_period}'"
                )
                if r_wo.get("success") and r_wo.get("data"):
                    wo_count = int(r_wo["data"][0].get("TotalWo") or 0)
                if wo_count <= 0:
                    r_wo2 = self.erp.execute(f"""
                        SELECT COUNT(DISTINCT MOBarCode) AS TotalWo
                        FROM View_P_Inspection
                        WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = '{fiscal_period}'
                    """)
                    if r_wo2.get("success") and r_wo2.get("data"):
                        wo_count = int(r_wo2["data"][0].get("TotalWo") or 0)

                # 统一口径：
                #   合格数量 = 入库 PCS（良品）
                #   报废 PCS = MRB 报废
                #   投入 PCS = 合格 + 报废
                #   报废率 = 报废 / 投入
                #   合格率 = 合格 / 投入
                pass_qty = int(ti or 0)
                scrap_qty = int(tr or 0)
                input_qty = pass_qty + scrap_qty
                if input_qty > 0:
                    scrap_rate = round(scrap_qty * 100.0 / input_qty, 2)
                    pass_rate = round(pass_qty * 100.0 / input_qty, 2)
                else:
                    scrap_rate = float(scrap_data.get("scrap_rate", 0) or 0)
                    pass_rate = round(max(0.0, 100.0 - scrap_rate), 2)
                result["kpi"] = {
                    "total_wo": wo_count,
                    "total_input": input_qty,
                    "total_reject": scrap_qty,
                    "reject_rate": scrap_rate,
                    "pass_qty": pass_qty,
                    "pass_rate": pass_rate,
                }

                # TOP报废料号（当月）
                r_parts = self.erp.execute(f"""
                    SELECT TOP 10
                        v.MfgPartCode AS PartNumber,
                        IFNULL(MAX(mp.mfgPartName), v.MfgPartCode) AS PartName,
                        SUM(v.QtyOfUnit) AS TotalReject
                    FROM View_P_Inspection v
                    LEFT JOIN E_JobMfgParts mp ON mp.mfgPartCode = v.MfgPartCode
                    WHERE v.InspectionResult = 'Scrapped'
                      AND v.FiscalPeriod = '{fiscal_period}'
                      AND v.MfgPartCode IS NOT NULL AND v.MfgPartCode <> ''
                    GROUP BY v.MfgPartCode
                    ORDER BY SUM(v.QtyOfUnit) DESC
                """)
                stock_map = {}
                r_stock = self.erp.execute(f"""
                    SELECT mp.mfgPartCode AS PartNumber, SUM(sfiwo.qtyOfPCS) AS TotalInput
                    FROM FGI_StockFormItemWO sfiwo
                    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE wo.FiscalPeriod = '{fiscal_period}'
                    GROUP BY mp.mfgPartCode
                """)
                if r_stock.get("success") and r_stock.get("data"):
                    for row in r_stock["data"]:
                        stock_map[row.get("PartNumber", "")] = int(row.get("TotalInput") or 0)

                parts = []
                if r_parts.get("success") and r_parts.get("data"):
                    for row in r_parts["data"]:
                        pn = row.get("PartNumber") or ""
                        reject = int(float(row.get("TotalReject") or 0))
                        inp = int(stock_map.get(pn, 0))
                        rate = round(reject * 100.0 / (reject + inp), 2) if inp > 0 else None
                        parts.append({
                            "PartNumber": pn,
                            "PartName": row.get("PartName") or pn,
                            "TotalReject": reject,
                            "TotalInput": inp,
                            "RejectRate": rate,
                        })

                # 优先展示有入库口径的料号，避免大量「投入0 / 报废率—」
                with_stock = [p for p in parts if (p.get("TotalInput") or 0) > 0]
                without_stock = [p for p in parts if (p.get("TotalInput") or 0) <= 0]
                # 再补一轮：直接按入库 TOP 关联当月报废
                if len(with_stock) < 8 and stock_map:
                    existing = {p["PartNumber"] for p in with_stock}
                    for pn, inp in sorted(stock_map.items(), key=lambda x: -x[1])[:20]:
                        if pn in existing:
                            continue
                        r_one = self.erp.execute(f"""
                            SELECT IFNULL(SUM(QtyOfUnit),0) AS TotalReject,
                                   IFNULL(MAX(mp.mfgPartName), '{pn}') AS PartName
                            FROM View_P_Inspection v
                            LEFT JOIN E_JobMfgParts mp ON mp.mfgPartCode = v.MfgPartCode
                            WHERE v.InspectionResult='Scrapped'
                              AND v.FiscalPeriod='{fiscal_period}'
                              AND v.MfgPartCode='{pn}'
                        """)
                        reject = 0
                        pname = pn
                        if r_one.get("success") and r_one.get("data"):
                            reject = int(float(r_one["data"][0].get("TotalReject") or 0))
                            pname = r_one["data"][0].get("PartName") or pn
                        if reject <= 0:
                            continue
                        rate = round(reject * 100.0 / (reject + inp), 2)
                        with_stock.append({
                            "PartNumber": pn, "PartName": pname,
                            "TotalReject": reject, "TotalInput": inp, "RejectRate": rate,
                        })
                        existing.add(pn)
                        if len(with_stock) >= 10:
                            break
                with_stock.sort(key=lambda p: -(p.get("TotalReject") or 0))
                parts = (with_stock + without_stock)[:10]

                result["top_defect_parts"] = parts

                # 缺陷项：同周期 scrap TOP；若空则回退最近有数周期（避免底部图空白）
                result["top_defect_items"] = [
                    {
                        "defect_type": d.get("defect_type", ""),
                        "process": d.get("process", ""),
                        "scrap_pcs": d.get("scrap_pcs", 0),
                        "ratio": d.get("ratio", 0),
                    }
                    for d in (scrap_data.get("defect_top10") or [])[:10]
                ]
                if not parts or not result["top_defect_items"]:
                    r_fp = self.erp.execute("""
                        SELECT TOP 1 FiscalPeriod FROM View_P_Inspection
                        WHERE InspectionResult='Scrapped' AND FiscalPeriod IS NOT NULL
                        GROUP BY FiscalPeriod ORDER BY FiscalPeriod DESC
                    """)
                    alt_fp = None
                    if r_fp.get("success") and r_fp.get("data"):
                        alt_fp = r_fp["data"][0].get("FiscalPeriod")
                    if alt_fp and str(alt_fp) != str(fiscal_period):
                        scrap_alt = self.get_scrap_analysis(None, None, None, fiscal_period=alt_fp)
                        if not result["top_defect_items"]:
                            result["top_defect_items"] = [
                                {
                                    "defect_type": d.get("defect_type", ""),
                                    "process": d.get("process", ""),
                                    "scrap_pcs": d.get("scrap_pcs", 0),
                                    "ratio": d.get("ratio", 0),
                                }
                                for d in (scrap_alt.get("defect_top10") or [])[:10]
                            ]
                            result["ranking_title"] = f"TOP报废缺陷（{alt_fp}）"

            except Exception as e:
                logger.error(f"仪表板数据获取失败: {e}")

        if self.mes and self.mes.connected:
            try:
                r = self.mes.execute("""
                    SELECT TOP 8
                        ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                        COUNT(*) AS InspectCount,
                        CAST(SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END)*100.0/COUNT(*) AS DECIMAL(5,2)) AS PassRate
                    FROM TBL_QM_INSPECT_RECORD r
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT IS NOT NULL
                    GROUP BY p.CPROCESS_NAME
                    ORDER BY InspectCount DESC
                """)
                if r.get("success"):
                    result["process_overview"] = r.get("data", [])
            except Exception as e:
                logger.warning(f"MES仪表板数据: {e}")

        # 左侧排行榜：料号 → 缺陷项 → 低良率工序，避免空界面
        if result["top_defect_parts"]:
            result["ranking_mode"] = "scrap_parts"
            result["ranking_title"] = f"TOP报废料号"
        elif result["top_defect_items"]:
            result["ranking_mode"] = "defect_items"
            result["ranking_title"] = f"TOP报废缺陷"
        elif result["process_overview"]:
            # 按良率升序取关注工序
            low = sorted(
                [p for p in result["process_overview"] if p.get("ProcessName")],
                key=lambda x: float(x.get("PassRate") or 100),
            )[:8]
            result["ranking_mode"] = "low_yield"
            result["ranking_title"] = "关注工序（良率偏低）"
            result["top_defect_parts"] = [
                {
                    "PartNumber": p.get("ProcessName"),
                    "PartName": f"检验 {int(p.get('InspectCount') or 0)} 次",
                    "TotalReject": int(p.get("InspectCount") or 0) - int(
                        float(p.get("PassRate") or 0) / 100.0 * float(p.get("InspectCount") or 0)
                    ),
                    "TotalInput": int(p.get("InspectCount") or 0),
                    "RejectRate": round(100 - float(p.get("PassRate") or 0), 2),
                }
                for p in low
            ]
        else:
            result["ranking_mode"] = "empty"
            result["ranking_title"] = "品质关注项"

        result["yield_trends"] = self.get_yield_trends(day_count=30)
        scrap_trends = self.get_scrap_trends(day_count=30)
        result["scrap_trends"] = scrap_trends

        # ── 领导决策层：目标对标 · 环比 · 月趋势 · 管理闭环 ──
        targets = {
            "scrap_rate": TARGET_SCRAP_RATE,
            "warn_scrap_rate": WARN_SCRAP_RATE,
            "crit_scrap_rate": CRIT_SCRAP_RATE,
            "pass_rate": TARGET_PASS_RATE,
            "process_yield": TARGET_PROCESS_YIELD,
            "iqc_pass": TARGET_IQC_PASS,
            "fqc_pass": TARGET_FQC_PASS,
            "dppm": TARGET_DPPM,
            "rework_rate": TARGET_REWORK_RATE,
        }
        result["targets"] = targets

        cur_rate = float((result.get("kpi") or {}).get("reject_rate") or 0)
        cur_pass = float((result.get("kpi") or {}).get("pass_rate") or 0)
        cur_input = int((result.get("kpi") or {}).get("total_input") or 0)
        cur_scrap = int((result.get("kpi") or {}).get("total_reject") or 0)

        prev_fp = self._prev_fiscal_period(fiscal_period)
        mom = {
            "prev_fiscal_period": prev_fp,
            "scrap_rate_prev": None,
            "scrap_rate_delta": None,
            "pass_rate_prev": None,
            "pass_rate_delta": None,
            "input_prev": None,
            "input_delta_pct": None,
            "scrap_pcs_prev": None,
            "scrap_pcs_delta": None,
        }
        if prev_fp and self.erp and self.erp.connected:
            try:
                prev = self.get_scrap_analysis(None, None, None, fiscal_period=prev_fp)
                p_scrap = int(prev.get("total_scrap_pcs") or 0)
                p_stock = int(prev.get("total_in_stock_pcs") or 0)
                p_input = p_scrap + p_stock
                # 上月无入库口径时不做报废率环比，避免 100%→本月 造成虚假「大幅下降」
                prev_reliable = p_stock > 0 and p_input > 0
                p_rate = round(p_scrap * 100.0 / p_input, 2) if prev_reliable else None
                p_pass = round(p_stock * 100.0 / p_input, 2) if prev_reliable else None
                mom["scrap_rate_prev"] = p_rate
                mom["pass_rate_prev"] = p_pass
                mom["input_prev"] = p_input
                mom["scrap_pcs_prev"] = p_scrap
                mom["prev_rate_reliable"] = prev_reliable
                if p_rate is not None:
                    mom["scrap_rate_delta"] = round(cur_rate - p_rate, 2)
                if p_pass is not None:
                    mom["pass_rate_delta"] = round(cur_pass - p_pass, 2)
                if p_input > 0 and p_stock > 0:
                    mom["input_delta_pct"] = round((cur_input - p_input) * 100.0 / p_input, 1)
                mom["scrap_pcs_delta"] = cur_scrap - p_scrap
                if not prev_reliable:
                    mom["note"] = f"上月 {prev_fp} 缺 FGI 入库口径，报废率环比暂不展示"
            except Exception as e:
                logger.warning("环比计算失败: %s", e)
        result["mom"] = mom

        monthly = scrap_trends.get("monthly") or self.get_monthly_scrap_series(months=12)
        result["monthly_scrap_trend"] = monthly

        mgmt = self.get_management_closure_stats()
        result["management"] = mgmt
        result["active_alerts"] = mgmt.get("active_alerts", 0)
        result["pending_8d"] = mgmt.get("pending_8d", 0)
        result["critical_alerts"] = mgmt.get("critical_alerts", 0)

        # 目标达成状态
        vs_target = {
            "scrap_vs_target": round(cur_rate - TARGET_SCRAP_RATE, 2),
            "scrap_status": (
                "ok" if cur_rate <= TARGET_SCRAP_RATE
                else ("warn" if cur_rate <= WARN_SCRAP_RATE else "crit")
            ),
            "pass_vs_target": round(cur_pass - TARGET_PASS_RATE, 2),
            "pass_status": "ok" if cur_pass >= TARGET_PASS_RATE else "warn",
        }
        result["vs_target"] = vs_target

        # ── 领导一眼可读：指标达成看板（达标/关注/严重）──
        achieve_items = []
        # 1) 报废率
        scrap_st = vs_target["scrap_status"]
        achieve_items.append({
            "key": "scrap_rate",
            "name": "报废率",
            "value": cur_rate,
            "unit": "%",
            "target": TARGET_SCRAP_RATE,
            "target_text": f"≤{TARGET_SCRAP_RATE:g}%",
            "delta": vs_target["scrap_vs_target"],
            "status": scrap_st,
            "status_label": {"ok": "达标", "warn": "关注", "crit": "严重"}.get(scrap_st, "—"),
            "hint": (
                f"较目标 {vs_target['scrap_vs_target']:+.2f}pp"
                if vs_target.get("scrap_vs_target") is not None else "—"
            ),
        })
        # 2) 合格率
        pass_st = vs_target["pass_status"]
        achieve_items.append({
            "key": "pass_rate",
            "name": "合格率",
            "value": cur_pass,
            "unit": "%",
            "target": TARGET_PASS_RATE,
            "target_text": f"≥{TARGET_PASS_RATE:g}%",
            "delta": vs_target["pass_vs_target"],
            "status": pass_st,
            "status_label": {"ok": "达标", "warn": "未达标", "crit": "严重"}.get(pass_st, "—"),
            "hint": (
                f"较目标 {vs_target['pass_vs_target']:+.2f}pp"
                if vs_target.get("pass_vs_target") is not None else "—"
            ),
        })
        # 3) 报废率环比（改善=达标，恶化=关注/严重）
        mom_delta = mom.get("scrap_rate_delta")
        if mom_delta is None:
            mom_st = "na"
            mom_label = "暂无环比"
            mom_hint = mom.get("note") or "上月缺可靠入库口径"
        elif mom_delta <= 0:
            mom_st = "ok"
            mom_label = "改善/持平"
            mom_hint = f"环比 {mom_delta:+.2f}pp"
        elif mom_delta < 1:
            mom_st = "warn"
            mom_label = "小幅恶化"
            mom_hint = f"环比 {mom_delta:+.2f}pp"
        else:
            mom_st = "crit"
            mom_label = "明显恶化"
            mom_hint = f"环比 {mom_delta:+.2f}pp"
        achieve_items.append({
            "key": "mom_scrap",
            "name": "报废环比",
            "value": mom_delta,
            "unit": "pp",
            "target": 0,
            "target_text": "≤0pp（不恶化）",
            "delta": mom_delta,
            "status": mom_st,
            "status_label": mom_label,
            "hint": mom_hint,
        })
        # 4) 预警闭环
        crit_n = int(mgmt.get("critical_alerts") or 0)
        active_n = int(mgmt.get("active_alerts") or 0)
        ack = mgmt.get("alert_ack_rate")
        if crit_n > 0:
            alert_st, alert_label = "crit", "有严重未确认"
        elif active_n > 5:
            alert_st, alert_label = "warn", "积压偏多"
        else:
            alert_st, alert_label = "ok", "可控"
        achieve_items.append({
            "key": "alerts",
            "name": "预警闭环",
            "value": active_n,
            "unit": "条",
            "target": 0,
            "target_text": "严重未确认=0",
            "delta": crit_n,
            "status": alert_st,
            "status_label": alert_label,
            "hint": (
                f"未确认 {active_n}（严重 {crit_n}）"
                + (f" · 确认率 {ack}%" if ack is not None else "")
            ),
        })
        # 5) 8D 闭环
        open_8d = int(mgmt.get("pending_8d") or 0)
        closed_30 = int(mgmt.get("closed_8d_30d") or 0)
        if open_8d >= 5:
            d8_st, d8_label = "crit", "积压严重"
        elif open_8d > 0:
            d8_st, d8_label = "warn", "有待关闭"
        else:
            d8_st, d8_label = "ok", "无积压"
        achieve_items.append({
            "key": "eight_d",
            "name": "8D闭环",
            "value": open_8d,
            "unit": "份",
            "target": 0,
            "target_text": "待关闭趋向0",
            "delta": open_8d,
            "status": d8_st,
            "status_label": d8_label,
            "hint": f"待关闭 {open_8d} · 近30天已关 {closed_30}",
        })
        # 6) IPQC 合格率（近30日，有数才纳入）
        ipqc = {}
        ipqc_pass = None
        ipqc_pending = 0
        weak_process = None
        try:
            ipqc = self.get_ipqc_analysis(day_count=30)
            if ipqc.get("has_data"):
                ipqc_pass = (ipqc.get("kpi") or {}).get("pass_rate")
                ipqc_pending = int((ipqc.get("kpi") or {}).get("pending_count") or 0)
                prs = ipqc.get("process_rates") or []
                if prs:
                    weak_process = min(
                        prs,
                        key=lambda x: float(x.get("pass_rate")
                                            if x.get("pass_rate") is not None else 999),
                    )
        except Exception as e:
            logger.warning("总览拉取 IPQC 达成失败: %s", e)
        if ipqc_pass is not None:
            ipqc_target = float(TARGET_PROCESS_YIELD)
            if ipqc_pass >= ipqc_target:
                ipqc_st, ipqc_label = "ok", "达标"
            elif ipqc_pass >= ipqc_target - 2:
                ipqc_st, ipqc_label = "warn", "关注"
            else:
                ipqc_st, ipqc_label = "crit", "偏低"
            hint = f"近30日 · 待审 {ipqc_pending}"
            if weak_process:
                hint += (
                    f" · 最弱「{weak_process.get('process_name')}」"
                    f"{weak_process.get('pass_rate')}%"
                )
            achieve_items.append({
                "key": "ipqc_pass",
                "name": "IPQC合格率",
                "value": ipqc_pass,
                "unit": "%",
                "target": ipqc_target,
                "target_text": f"≥{ipqc_target:g}%",
                "delta": round(ipqc_pass - ipqc_target, 2),
                "status": ipqc_st,
                "status_label": ipqc_label,
                "hint": hint,
            })

        # 7) 客诉 / COPQ
        copq = {}
        try:
            # 与 FiscalPeriod 对齐：当月 1 日 → 下月 1 日
            y, m = int(str(fiscal_period)[:4]), int(str(fiscal_period)[4:6])
            start_m = f"{y:04d}-{m:02d}-01"
            if m == 12:
                end_m = f"{y + 1:04d}-01-01"
            else:
                end_m = f"{y:04d}-{m + 1:02d}-01"
            copq = self.get_copq_analysis(start_date=start_m, end_date=end_m)
        except Exception as e:
            logger.warning("总览拉取 COPQ 失败: %s", e)
        ck = copq.get("kpi") or {}
        if copq.get("has_data") or int(ck.get("open_count") or 0) or float(ck.get("copq_total") or 0):
            open_c = int(ck.get("open_count") or 0)
            copq_amt = float(ck.get("copq_total") or 0)
            if open_c >= 5 or copq_amt >= 5000:
                cq_st, cq_label = "crit", "客诉压力大"
            elif open_c >= 1 or copq_amt >= 1000:
                cq_st, cq_label = "warn", "有开案"
            else:
                cq_st, cq_label = "ok", "可控"
            top_cust = (copq.get("by_customer") or [{}])[0] if (copq.get("by_customer")) else {}
            achieve_items.append({
                "key": "copq",
                "name": "客诉COPQ",
                "value": round(copq_amt, 2),
                "unit": "元",
                "target": 0,
                "target_text": "开案趋向0",
                "delta": open_c,
                "status": cq_st,
                "status_label": cq_label,
                "hint": (
                    f"开案 {open_c} · 当月 COPQ ¥{copq_amt:,.0f}"
                    + (
                        f" · TOP {top_cust.get('customer_name') or top_cust.get('customer_code')}"
                        if top_cust else ""
                    )
                ),
            })
            scrap_cost = float(ck.get("scrap_cost_est") or 0)
            cov = ck.get("scrap_price_coverage")
            unpriced = int(ck.get("scrap_unpriced_pcs") or 0)
            if scrap_cost > 0 or cov is not None:
                achieve_items.append({
                    "key": "scrap_cost",
                    "name": "报废成本(已核价)",
                    "value": round(scrap_cost, 2),
                    "unit": "元",
                    "target": None,
                    "target_text": "仅 ERP 有单价部分",
                    "delta": None,
                    "status": "ok" if unpriced == 0 else "na",
                    "status_label": "已核价" if unpriced == 0 else "部分缺价",
                    "hint": (
                        f"覆盖 {cov if cov is not None else '—'}%"
                        + (f" · 缺价 {unpriced:,} PCS" if unpriced else "")
                        + (f" · {copq.get('scrap_cost_gap')}" if copq.get("scrap_cost_gap") else "")
                    ),
                })

        # 8) SPC 失控
        spc = {}
        try:
            spc = self.get_spc_analysis()
        except Exception as e:
            logger.warning("总览拉取 SPC 失败: %s", e)
        sp = spc.get("summary") or {}
        if spc.get("has_data"):
            alert_n = int(sp.get("alert") or 0)
            avg_cpk = sp.get("avg_cpk")
            if alert_n >= 2:
                sp_st, sp_label = "crit", "多图失控"
            elif alert_n >= 1:
                sp_st, sp_label = "warn", "有失控"
            else:
                sp_st, sp_label = "ok", "受控"
            achieve_items.append({
                "key": "spc",
                "name": "SPC失控",
                "value": alert_n,
                "unit": "图",
                "target": 0,
                "target_text": "失控=0",
                "delta": alert_n,
                "status": sp_st,
                "status_label": sp_label,
                "hint": (
                    f"共 {sp.get('total') or 0} 图 · 失控 {alert_n}"
                    + (f" · 平均Cpk {avg_cpk}" if avg_cpk is not None else "")
                ),
            })

        # 9) IQC / FQC / 一次交验 / 复检率 / DPPM（不含设备 OEE）
        gates = self.get_inspection_gate_kpis(30)
        dppm_info = self.get_quality_dppm_summary(30)
        iqc = gates.get("iqc") or {}
        if iqc.get("pass_rate") is not None and int(iqc.get("count") or 0) > 0:
            iv = float(iqc["pass_rate"])
            sample_ok = bool(iqc.get("sample_ok"))
            if not sample_ok:
                iqc_st, iqc_label = "na", f"样本不足({iqc.get('count')})"
            elif iv >= TARGET_IQC_PASS:
                iqc_st, iqc_label = "ok", "达标"
            elif iv >= TARGET_IQC_PASS - 2:
                iqc_st, iqc_label = "warn", "关注"
            else:
                iqc_st, iqc_label = "crit", "偏低"
            achieve_items.append({
                "key": "iqc",
                "name": "IQC来料合格率",
                "value": iv,
                "unit": "%",
                "target": TARGET_IQC_PASS,
                "target_text": f"≥{TARGET_IQC_PASS:g}%",
                "delta": round(iv - TARGET_IQC_PASS, 2),
                "status": iqc_st,
                "status_label": iqc_label,
                "hint": (
                    f"incoming 已出结论 {iqc.get('done_count', iqc.get('count'))} 单"
                    f" · 近{iqc.get('window_days') or 30}日"
                ),
            })
        fqc = gates.get("fqc") or {}
        if fqc.get("pass_rate") is not None and int(fqc.get("count") or 0) > 0:
            fv = float(fqc["pass_rate"])
            sample_ok = bool(fqc.get("sample_ok"))
            if not sample_ok:
                fqc_st, fqc_label = "na", f"样本不足({fqc.get('done_count') or fqc.get('count')})"
            elif fv >= TARGET_FQC_PASS:
                fqc_st, fqc_label = "ok", "达标"
            elif fv >= TARGET_FQC_PASS - 1:
                fqc_st, fqc_label = "warn", "关注"
            else:
                fqc_st, fqc_label = "crit", "偏低"
            achieve_items.append({
                "key": "fqc",
                "name": "FQC终检合格率",
                "value": fv,
                "unit": "%",
                "target": TARGET_FQC_PASS,
                "target_text": f"≥{TARGET_FQC_PASS:g}%",
                "delta": round(fv - TARGET_FQC_PASS, 2),
                "status": fqc_st,
                "status_label": fqc_label,
                "hint": (
                    f"final 已出结论 {fqc.get('done_count', fqc.get('count'))} 单"
                    f" · 近{fqc.get('window_days') or 30}日"
                ),
            })
        fty = gates.get("fty") or {}
        if fty.get("pass_rate") is not None and int(fty.get("count") or 0) > 0:
            fty_v = float(fty["pass_rate"])
            if fty_v >= TARGET_PROCESS_YIELD:
                fty_st, fty_label = "ok", "达标"
            elif fty_v >= TARGET_PROCESS_YIELD - 2:
                fty_st, fty_label = "warn", "关注"
            else:
                fty_st, fty_label = "crit", "偏低"
            achieve_items.append({
                "key": "fty",
                "name": "一次交验合格率",
                "value": fty_v,
                "unit": "%",
                "target": TARGET_PROCESS_YIELD,
                "target_text": f"≥{TARGET_PROCESS_YIELD:g}%（非复检）",
                "delta": round(fty_v - TARGET_PROCESS_YIELD, 2),
                "status": fty_st,
                "status_label": fty_label,
                "hint": f"process 非复检 {fty.get('count')} 单",
            })
        rew = gates.get("rework") or {}
        if rew.get("rate") is not None and int(rew.get("base_count") or 0) > 0:
            rr = float(rew["rate"])
            if rr <= TARGET_REWORK_RATE:
                rw_st, rw_label = "ok", "达标"
            elif rr <= TARGET_REWORK_RATE + 2:
                rw_st, rw_label = "warn", "关注"
            else:
                rw_st, rw_label = "crit", "偏高"
            achieve_items.append({
                "key": "rework",
                "name": "复检返工率",
                "value": rr,
                "unit": "%",
                "target": TARGET_REWORK_RATE,
                "target_text": f"≤{TARGET_REWORK_RATE:g}%",
                "delta": round(rr - TARGET_REWORK_RATE, 2),
                "status": rw_st,
                "status_label": rw_label,
                "hint": f"复检 {rew.get('count')}/{rew.get('base_count')} 单",
            })
        if dppm_info.get("has_data") and dppm_info.get("dppm") is not None:
            dv = float(dppm_info["dppm"])
            if dv <= TARGET_DPPM:
                dp_st, dp_label = "ok", "达标"
            elif dv <= TARGET_DPPM * 2:
                dp_st, dp_label = "warn", "关注"
            else:
                dp_st, dp_label = "crit", "偏高"
            achieve_items.append({
                "key": "dppm",
                "name": "过程DPPM",
                "value": dv,
                "unit": "",
                "target": TARGET_DPPM,
                "target_text": f"≤{TARGET_DPPM:g}",
                "delta": round(dv - TARGET_DPPM, 1),
                "status": dp_st,
                "status_label": dp_label,
                "hint": (
                    f"近30日 quality_metrics · 缺陷 {dppm_info.get('defect'):,} / "
                    f"检验 {dppm_info.get('total'):,}"
                ),
            })

        # 预警/8D 时效补强（有超期则覆盖提示）
        if int(mgmt.get("alert_over_24h") or 0) > 0:
            for it in achieve_items:
                if it.get("key") == "alerts":
                    it["hint"] += (
                        f" · 超{int(ALERT_OVERDUE_HOURS)}h未确认 "
                        f"{mgmt.get('alert_over_24h')} · 均龄 "
                        f"{mgmt.get('alert_avg_age_hours')}h"
                    )
                    if it.get("status") == "ok":
                        it["status"], it["status_label"] = "warn", "有超期未确认"
        if int(mgmt.get("eight_d_overdue") or 0) > 0:
            for it in achieve_items:
                if it.get("key") == "eight_d":
                    it["hint"] += (
                        f" · 超{int(EIGHT_D_OVERDUE_DAYS)}天 "
                        f"{mgmt.get('eight_d_overdue')} · 打开均 "
                        f"{mgmt.get('eight_d_avg_open_days')}天"
                    )
                    if it.get("status") == "ok":
                        it["status"], it["status_label"] = "warn", "有超期"

        # 焦点：最弱工序 + 风险客户（供前端/简报，不编造）
        focus = {
            "weak_process": (
                {
                    "process_name": weak_process.get("process_name"),
                    "pass_rate": weak_process.get("pass_rate"),
                    "inspect_count": weak_process.get("inspect_count")
                    or weak_process.get("total")
                    or weak_process.get("count"),
                } if weak_process else None
            ),
            "risk_customers": [],
        }
        for c in (copq.get("by_customer") or [])[:3]:
            focus["risk_customers"].append({
                "customer_code": c.get("customer_code"),
                "customer_name": c.get("customer_name"),
                "copq": c.get("amount"),
                "count": c.get("count"),
                "source": "客诉COPQ",
            })
        if not focus["risk_customers"]:
            for c in (ipqc.get("customer_ng_top") or [])[:3]:
                focus["risk_customers"].append({
                    "customer_code": c.get("customer_code"),
                    "customer_name": c.get("customer_name"),
                    "ng_count": c.get("ng_count"),
                    "source": "IPQC不合格",
                })

        rank = {"ok": 0, "na": 0, "warn": 1, "crit": 2}
        worst = max((rank.get(i.get("status"), 0) for i in achieve_items), default=0)
        overall_map = {0: ("ok", "整体达标"), 1: ("warn", "部分关注"), 2: ("crit", "需立即干预")}
        overall_st, overall_label = overall_map.get(worst, ("na", "—"))
        ok_n = sum(1 for i in achieve_items if i.get("status") == "ok")
        warn_n = sum(1 for i in achieve_items if i.get("status") == "warn")
        crit_n_items = sum(1 for i in achieve_items if i.get("status") == "crit")
        period_label = (
            f"{fiscal_period[:4]}年{fiscal_period[4:6]}月"
            if len(str(fiscal_period)) >= 6 else str(fiscal_period)
        )
        result["achievement"] = {
            "overall_status": overall_st,
            "overall_label": overall_label,
            "ok_count": ok_n,
            "warn_count": warn_n,
            "crit_count": crit_n_items,
            "items": achieve_items,
            "period": fiscal_period,
            "headline": (
                f"{period_label}核心指标达成：达标 {ok_n} · 关注 {warn_n} · 严重 {crit_n_items}"
                f" —— {overall_label}"
            ),
            "focus": focus,
            "extensions": {
                "gates": gates if gates.get("has_data") else None,
                "dppm": dppm_info if dppm_info.get("has_data") else None,
                "spc": sp.get("total") and sp or None,
                "copq_kpi": ck or None,
                "data_gaps": list(dict.fromkeys(
                    (gates.get("data_gaps") or [])
                    + ([dppm_info.get("data_gap")] if dppm_info.get("data_gap") else [])
                    + ([copq.get("scrap_cost_gap")] if copq.get("scrap_cost_gap") else [])
                )),
            },
        }

        # 决策简报（纯实数叙事，禁止编造）
        briefing_lines = []
        briefing_lines.append(
            f"【{period_label}】投入 {cur_input:,} PCS，报废 {cur_scrap:,} PCS，"
            f"报废率 {cur_rate:.2f}%（目标 ≤{TARGET_SCRAP_RATE:g}%），合格率 {cur_pass:.2f}%。"
        )
        if vs_target["scrap_status"] == "ok":
            briefing_lines.append(f"报废率低于目标 {TARGET_SCRAP_RATE:g}%，达标。")
        elif vs_target["scrap_status"] == "warn":
            briefing_lines.append(
                f"报废率超目标 {vs_target['scrap_vs_target']:+.2f}pp，已达关注线，建议组织专题分析。"
            )
        else:
            briefing_lines.append(
                f"报废率 {cur_rate:.2f}% 严重超标（警戒 {WARN_SCRAP_RATE:g}% / 严重 {CRIT_SCRAP_RATE:g}%），"
                f"建议立即启动 8D 并锁定 TOP 料号/缺陷。"
            )
        if mom.get("scrap_rate_delta") is not None:
            d = mom["scrap_rate_delta"]
            arrow = "上升" if d > 0 else ("下降" if d < 0 else "持平")
            briefing_lines.append(
                f"环比上月（{prev_fp}）报废率 {arrow} {abs(d):.2f}pp"
                f"（上月 {mom.get('scrap_rate_prev')}%）。"
            )
        elif mom.get("note"):
            briefing_lines.append(mom["note"] + "；可对比报废 PCS 绝对量。")
            if mom.get("scrap_pcs_delta") is not None:
                briefing_lines.append(
                    f"报废 PCS 较上月变化 {mom['scrap_pcs_delta']:+,} "
                    f"（上月 {mom.get('scrap_pcs_prev') or 0:,} → 本月 {cur_scrap:,}）。"
                )
        top_parts = result.get("top_defect_parts") or []
        if top_parts:
            p0 = top_parts[0]
            briefing_lines.append(
                f"TOP1 报废料号：{p0.get('PartNumber')}（{int(p0.get('TotalReject') or 0):,} PCS"
                + (f"，料号报废率 {p0.get('RejectRate')}%" if p0.get("RejectRate") is not None else "")
                + "）。"
            )
        top_items = result.get("top_defect_items") or []
        if top_items:
            d0 = top_items[0]
            briefing_lines.append(
                f"TOP1 缺陷：{d0.get('process') or ''}-{d0.get('defect_type')}（{int(d0.get('scrap_pcs') or 0):,} PCS）。"
            )
        briefing_lines.append(
            f"管理闭环：未确认预警 {mgmt.get('active_alerts', 0)} 条"
            f"（严重 {mgmt.get('critical_alerts', 0)}），"
            f"超{int(ALERT_OVERDUE_HOURS)}h未确认 {mgmt.get('alert_over_24h', 0)}，"
            f"待关闭 8D {mgmt.get('pending_8d', 0)} 份"
            f"（超{int(EIGHT_D_OVERDUE_DAYS)}天 {mgmt.get('eight_d_overdue', 0)}），"
            f"近30天已关闭 8D {mgmt.get('closed_8d_30d', 0)} 份"
            + (
                f"，预警确认率 {mgmt.get('alert_ack_rate')}%"
                if mgmt.get("alert_ack_rate") is not None else ""
            )
            + "。"
        )
        if focus.get("weak_process"):
            wp = focus["weak_process"]
            briefing_lines.append(
                f"最弱工序「{wp.get('process_name')}」合格率 {wp.get('pass_rate')}%。"
            )
        if focus.get("risk_customers"):
            rc0 = focus["risk_customers"][0]
            briefing_lines.append(
                f"风险客户 TOP1：{rc0.get('customer_name') or rc0.get('customer_code')}"
                + (
                    f"（COPQ ¥{rc0.get('copq'):,.0f}）"
                    if rc0.get("copq") is not None else
                    (f"（IPQC不合格 {rc0.get('ng_count')}）" if rc0.get("ng_count") else "")
                )
                + "。"
            )
        if ck.get("copq_total") is not None and (ck.get("open_count") or ck.get("copq_total")):
            briefing_lines.append(
                f"当月客诉开案 {ck.get('open_count', 0)}，COPQ ¥{float(ck.get('copq_total') or 0):,.1f}"
                + (
                    f"，报废已核价成本 ¥{float(ck.get('scrap_cost_est') or 0):,.1f}"
                    f"（覆盖 {ck.get('scrap_price_coverage')}%）"
                    if ck.get("scrap_price_coverage") is not None else ""
                )
                + "。"
            )
        if sp.get("total"):
            briefing_lines.append(
                f"SPC：{sp.get('total')} 图，失控 {sp.get('alert', 0)}"
                + (f"，平均 Cpk {sp.get('avg_cpk')}" if sp.get("avg_cpk") is not None else "")
                + "。"
            )
        if dppm_info.get("has_data"):
            briefing_lines.append(
                f"过程 DPPM {dppm_info.get('dppm')}（目标 ≤{TARGET_DPPM:g}）。"
            )
        gaps = result["achievement"].get("extensions", {}).get("data_gaps") or []
        if gaps:
            briefing_lines.append("数据缺口：" + "；".join(gaps[:4]) + "。")

        # 行动建议（基于实数阈值，不编造）
        actions = []
        if vs_target["scrap_status"] == "crit" or (mom.get("scrap_rate_delta") or 0) >= 1:
            actions.append("立即召开品质专题会，锁定 TOP3 料号与缺陷并下达 8D")
        if mgmt.get("critical_alerts", 0) > 0:
            actions.append(f"当日确认 {mgmt['critical_alerts']} 条严重预警并指定责任人")
        if mgmt.get("alert_over_24h", 0) > 0:
            actions.append(f"清理 {mgmt['alert_over_24h']} 条超{int(ALERT_OVERDUE_HOURS)}h未确认预警")
        if mgmt.get("pending_8d", 0) > 0:
            actions.append(f"跟踪 {mgmt['pending_8d']} 份进行中 8D 的 D4/D5 关闭节点")
        if mgmt.get("eight_d_overdue", 0) > 0:
            actions.append(f"优先关闭 {mgmt['eight_d_overdue']} 份超{int(EIGHT_D_OVERDUE_DAYS)}天 8D")
        if focus.get("weak_process"):
            actions.append(
                f"针对最弱工序「{focus['weak_process'].get('process_name')}」组织专项改善"
            )
        if int(sp.get("alert") or 0) > 0:
            actions.append(f"复核 {sp.get('alert')} 张 SPC 失控图并锁定过程参数")
        if vs_target["scrap_status"] == "ok" and (mom.get("scrap_rate_delta") or 0) <= 0:
            actions.append("维持现有管控，关注薄弱工序与知识库对策落地")
        if not actions:
            actions.append("持续监控日报废趋势，异常升幅 >20% 时触发预警扫描")

        # 简报开头突出达成总判
        briefing_lines.insert(1, result["achievement"]["headline"] + "。")
        result["decision_briefing"] = {
            "summary": " ".join(briefing_lines),
            "lines": briefing_lines,
            "actions": actions[:6],
            "focus_parts": [
                {"part": p.get("PartNumber"), "scrap_pcs": p.get("TotalReject"), "rate": p.get("RejectRate")}
                for p in top_parts[:3]
            ],
            "focus_defects": [
                {"defect": d.get("defect_type"), "process": d.get("process"), "scrap_pcs": d.get("scrap_pcs")}
                for d in top_items[:3]
            ],
            "focus": focus,
        }
        result["policy"] = (
            "仅品质指标：报废/合格/环比/预警·8D时效/IPQC/COPQ/SPC/IQC/FQC/一次交验/复检/DPPM；"
            "不含设备OEE与产能稼动；报废成本仅 ERP 精确单价；禁止编造"
        )
        return result

    def _qi_table_cols(self, db) -> set:
        """读取 quality_inspection_records 列名；失败返回空集。"""
        try:
            r = db.execute("PRAGMA table_info(quality_inspection_records)")
            cols = set()
            for row in (r.get("data") or []):
                name = row.get("name") or row.get("Name") or row.get("cid")
                # sqlite PRAGMA 经桥接后可能是 name / 第2列
                if not name:
                    name = row.get("1") or row.get("column_name")
                if name:
                    cols.add(str(name))
            if not cols and r.get("success") and r.get("data"):
                # 兜底：按 PRAGMA 常见字段序 name=第2项
                for row in r["data"]:
                    if isinstance(row, dict):
                        vals = list(row.values())
                        if len(vals) >= 2 and vals[1] is not None:
                            cols.add(str(vals[1]))
            return cols
        except Exception:
            return set()

    def _qi_process_expr(self, db) -> str:
        """工序名表达式：兼容仅有 process_name / process / 两者皆有（生产库常无 process 列）。"""
        cols = self._qi_table_cols(db)
        has_name = "process_name" in cols
        has_proc = "process" in cols
        if has_name and has_proc:
            return "COALESCE(NULLIF(process_name,''), NULLIF(process,''), '未知')"
        if has_name:
            return "COALESCE(NULLIF(process_name,''), '未知')"
        if has_proc:
            return "COALESCE(NULLIF(process,''), '未知')"
        return "'未知'"

    def get_yield_trends(self, day_count=30):
        """良率趋势：整体 / 工序 / 客户 / 关键指标（报废率、检验量）"""
        empty = {
            "days": [],
            "overall": [],
            "processes": {},
            "customers": {},
            "metrics": {"inspect_count": [], "scrap_rate": [], "ng_rate": []},
            "process_names": [],
            "customer_names": [],
        }
        db = None
        if self.mes and self.mes.connected:
            db = self.mes
        elif self.erp and self.erp.connected:
            db = self.erp
        if not db:
            return empty

        n = int(day_count)
        try:
            proc_expr = self._qi_process_expr(db)
            # 1) 检验记录（主源）
            r = db.execute(f"""
                SELECT strftime('%Y-%m-%d', inspected_at) AS day,
                       COUNT(*) AS total,
                       SUM(CASE WHEN IFNULL(source_result,0)=1
                                 OR LOWER(IFNULL(inspection_result,'')) IN ('pass','ok')
                            THEN 1 ELSE 0 END) AS pass_cnt,
                       SUM(CASE WHEN IFNULL(source_result,0)=2
                                 OR LOWER(IFNULL(inspection_result,'')) IN ('fail','ng')
                            THEN 1 ELSE 0 END) AS ng_cnt
                FROM quality_inspection_records
                WHERE inspected_at IS NOT NULL
                  AND inspected_at >= date('now', '-{n} days')
                GROUP BY strftime('%Y-%m-%d', inspected_at)
                ORDER BY day
            """)
            insp_map = {}
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    day = row.get("day")
                    if not day:
                        continue
                    total = int(row.get("total") or 0) or 1
                    pass_c = int(row.get("pass_cnt") or 0)
                    ng_c = int(row.get("ng_cnt") or 0)
                    insp_map[day] = {
                        "yield": round(pass_c * 100.0 / total, 2),
                        "total": total,
                        "ng_rate": round(ng_c * 100.0 / total, 2),
                        "source": "inspection",
                    }

            # 2) quality_metrics 补齐断档日（生产库检验常停在更早日期，metrics 仍持续更新）
            met_map = {}
            rm = db.execute(f"""
                SELECT record_date AS day,
                       SUM(COALESCE(total_inspected,0)) AS total,
                       SUM(COALESCE(good_count,0)) AS good_cnt,
                       SUM(COALESCE(defect_count,0)) AS defect_cnt
                FROM quality_metrics
                WHERE record_date IS NOT NULL
                  AND record_date >= date('now', '-{n} days')
                GROUP BY record_date
                ORDER BY day
            """)
            if rm.get("success") and rm.get("data"):
                for row in rm["data"]:
                    day = row.get("day")
                    if not day:
                        continue
                    total = int(row.get("total") or 0)
                    good = int(row.get("good_cnt") or 0)
                    defect = int(row.get("defect_cnt") or 0)
                    if total <= 0:
                        continue
                    met_map[day] = {
                        "yield": round(good * 100.0 / total, 2),
                        "total": total,
                        "ng_rate": round(defect * 100.0 / total, 2),
                        "source": "metrics",
                    }

            day_list = sorted(set(insp_map) | set(met_map))
            overall, inspect_count, ng_rate = [], [], []
            for day in day_list:
                # 有检验实据优先；断档日用 metrics 续上，避免趋势图停在过期日期
                row = insp_map.get(day) or met_map.get(day)
                overall.append(row["yield"])
                inspect_count.append(row["total"])
                ng_rate.append(row["ng_rate"])

            r_proc = db.execute(f"""
                SELECT {proc_expr} AS pname, COUNT(*) AS c
                FROM quality_inspection_records
                WHERE inspected_at >= date('now', '-{n} days')
                GROUP BY pname ORDER BY c DESC LIMIT 5
            """)
            process_names = [x.get("pname") for x in (r_proc.get("data") or []) if x.get("pname")]
            # metrics 工序补齐：检验侧工序不足时并入产量 metrics 的 TOP 工序
            if len(process_names) < 5:
                rpm = db.execute(f"""
                    SELECT process AS pname, SUM(COALESCE(total_inspected,0)) AS c
                    FROM quality_metrics
                    WHERE record_date >= date('now', '-{n} days')
                      AND process IS NOT NULL AND process <> ''
                    GROUP BY process ORDER BY c DESC LIMIT 5
                """)
                for x in (rpm.get("data") or []):
                    pname = x.get("pname")
                    if pname and pname not in process_names:
                        process_names.append(pname)
                    if len(process_names) >= 5:
                        break

            processes = {}
            for pname in process_names:
                safe = pname.replace("'", "''")
                rp = db.execute(f"""
                    SELECT strftime('%Y-%m-%d', inspected_at) AS day,
                           COUNT(*) AS total,
                           SUM(CASE WHEN IFNULL(source_result,0)=1
                                     OR LOWER(IFNULL(inspection_result,'')) IN ('pass','ok')
                                THEN 1 ELSE 0 END) AS pass_cnt
                    FROM quality_inspection_records
                    WHERE inspected_at >= date('now', '-{n} days')
                      AND {proc_expr} = '{safe}'
                    GROUP BY day ORDER BY day
                """)
                series = {row.get("day"): round(
                    int(row.get("pass_cnt") or 0) * 100.0 / (int(row.get("total") or 1)), 2
                ) for row in (rp.get("data") or [])}
                # metrics 同名工序补缺口
                rpm = db.execute(f"""
                    SELECT record_date AS day,
                           SUM(COALESCE(total_inspected,0)) AS total,
                           SUM(COALESCE(good_count,0)) AS good_cnt
                    FROM quality_metrics
                    WHERE record_date >= date('now', '-{n} days')
                      AND process = '{safe}'
                    GROUP BY record_date
                """)
                for row in (rpm.get("data") or []):
                    day = row.get("day")
                    total = int(row.get("total") or 0)
                    if not day or total <= 0 or day in series:
                        continue
                    series[day] = round(int(row.get("good_cnt") or 0) * 100.0 / total, 2)
                processes[pname] = [series.get(d) for d in day_list]

            r_cust = db.execute(f"""
                SELECT customer_code AS ccode, COUNT(*) AS c
                FROM quality_inspection_records
                WHERE inspected_at >= date('now', '-{n} days')
                  AND customer_code IS NOT NULL AND customer_code <> ''
                GROUP BY customer_code ORDER BY c DESC LIMIT 5
            """)
            customer_names = [x.get("ccode") for x in (r_cust.get("data") or []) if x.get("ccode")]
            customers = {}
            for ccode in customer_names:
                safe = str(ccode).replace("'", "''")
                rc = db.execute(f"""
                    SELECT strftime('%Y-%m-%d', inspected_at) AS day,
                           COUNT(*) AS total,
                           SUM(CASE WHEN IFNULL(source_result,0)=1
                                     OR LOWER(IFNULL(inspection_result,'')) IN ('pass','ok')
                                THEN 1 ELSE 0 END) AS pass_cnt
                    FROM quality_inspection_records
                    WHERE inspected_at >= date('now', '-{n} days')
                      AND customer_code = '{safe}'
                    GROUP BY day ORDER BY day
                """)
                series = {row.get("day"): round(
                    int(row.get("pass_cnt") or 0) * 100.0 / (int(row.get("total") or 1)), 2
                ) for row in (rc.get("data") or [])}
                customers[ccode] = [series.get(d) for d in day_list]

            # 报废率必须用 ERP 报废÷(报废+入库)，禁止用 MES 检验笔数当投入基数
            scrap_rate = []
            if self.erp and self.erp.connected and day_list:
                rs = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', CreateDate) AS day,
                           SUM(QtyOfUnit) AS scrap
                    FROM View_P_Inspection
                    WHERE InspectionResult='Scrapped'
                      AND CreateDate >= date('now', '-{n} days')
                    GROUP BY day ORDER BY day
                """)
                scrap_map = {row.get("day"): float(row.get("scrap") or 0)
                             for row in (rs.get("data") or [])}
                rst = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', sfi.stockDate) AS day,
                           SUM(sfiwo.qtyOfPCS) AS stock
                    FROM FGI_StockFormItemWO sfiwo
                    INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                    WHERE sfi.stockDate >= date('now', '-{n} days')
                    GROUP BY day ORDER BY day
                """)
                stock_map = {row.get("day"): float(row.get("stock") or 0)
                             for row in (rst.get("data") or [])}
                for d in day_list:
                    sc = scrap_map.get(d, 0)
                    st = stock_map.get(d, 0)
                    base = sc + st
                    scrap_rate.append(round(sc * 100.0 / base, 2) if base > 0 else 0)

            return {
                "days": day_list,
                "overall": overall,
                "processes": processes,
                "customers": customers,
                "metrics": {
                    "inspect_count": inspect_count,
                    "scrap_rate": scrap_rate,
                    "ng_rate": ng_rate,
                },
                "process_names": process_names,
                "customer_names": customer_names,
            }
        except Exception as e:
            logger.warning(f"良率趋势分析失败: {e}")
            return empty

    def _scrap_non_production_sql(self):
        """与 get_scrap_analysis 一致的非量产报废排除条件（View_P_Inspection 别名 v）。"""
        non_production_processes = [
            '样板组', '品质部', '工艺部', '设备部', '销售部', '测试划线', 'FQC', 'FQC(内部客检)', 'FQC2'
        ]
        non_production_items = ['实验板', '备品', '实验']
        np_proc = "', '".join(non_production_processes)
        np_item = "', '".join(non_production_items)
        return (
            "v.InspectionResult = 'Scrapped' "
            f"AND ISNULL(v.InspectionProcess, '') NOT IN ('{np_proc}') "
            f"AND ISNULL(v.InspectionItem, '') NOT IN ('{np_item}') "
            "AND ISNULL(v.InspectionProcess, '') <> '测试' "
            "AND ISNULL(v.InspectionItem, '') NOT LIKE '%测试%'"
        )

    def get_scrap_trends(self, day_count=30):
        """
        报废趋势分析（对齐良率趋势交互）：
        整体报废率 / 工序报废 / TOP料号 / 月度FiscalPeriod。
        日口径：报废/(报废+FGI入库)；无入库不伪造率。
        """
        empty = {
            "days": [],
            "overall_rate": [],
            "scrap_pcs": [],
            "stock_pcs": [],
            "processes": {},
            "parts": {},
            "process_names": [],
            "part_names": [],
            "monthly": [],
        }
        n = max(7, min(int(day_count or 30), 90))
        empty["monthly"] = self.get_monthly_scrap_series(months=12)
        if not (self.erp and self.erp.connected):
            return empty

        try:
            end = datetime.now().date()
            day_list = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]
            start_d = day_list[0]
            scrap_filter = self._scrap_non_production_sql()

            rs = self.erp.execute(f"""
                SELECT strftime('%Y-%m-%d', v.CreateDate) AS day,
                       SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {scrap_filter}
                  AND v.CreateDate >= '{start_d}'
                GROUP BY day ORDER BY day
            """)
            scrap_map = {
                row.get("day"): float(row.get("scrap") or 0)
                for row in (rs.get("data") or []) if row.get("day")
            }

            rst = self.erp.execute(f"""
                SELECT strftime('%Y-%m-%d', sfi.stockDate) AS day,
                       SUM(sfiwo.qtyOfPCS) AS stock
                FROM FGI_StockFormItemWO sfiwo
                INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                WHERE sfi.stockDate >= '{start_d}'
                GROUP BY day ORDER BY day
            """)
            stock_map = {
                row.get("day"): float(row.get("stock") or 0)
                for row in (rst.get("data") or []) if row.get("day")
            }

            overall_rate, scrap_pcs, stock_pcs = [], [], []
            for d in day_list:
                sc = scrap_map.get(d, 0.0)
                st = stock_map.get(d, 0.0)
                scrap_pcs.append(int(sc))
                stock_pcs.append(int(st))
                base = sc + st
                if st > 0 and base > 0:
                    overall_rate.append(round(sc * 100.0 / base, 2))
                else:
                    overall_rate.append(None)

            # TOP 工序（近 N 日报废量）
            r_proc = self.erp.execute(f"""
                SELECT TOP 5
                    ISNULL(NULLIF(v.InspectionProcess, ''), '未知') AS pname,
                    SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {scrap_filter}
                  AND v.CreateDate >= '{start_d}'
                GROUP BY ISNULL(NULLIF(v.InspectionProcess, ''), '未知')
                ORDER BY SUM(v.QtyOfUnit) DESC
            """)
            process_names = [x.get("pname") for x in (r_proc.get("data") or []) if x.get("pname")]
            processes = {}
            for pname in process_names:
                safe = str(pname).replace("'", "''")
                rp = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', v.CreateDate) AS day,
                           SUM(v.QtyOfUnit) AS scrap
                    FROM View_P_Inspection v
                    WHERE {scrap_filter}
                      AND v.CreateDate >= '{start_d}'
                      AND ISNULL(NULLIF(v.InspectionProcess, ''), '未知') = '{safe}'
                    GROUP BY day ORDER BY day
                """)
                series = {
                    row.get("day"): int(float(row.get("scrap") or 0))
                    for row in (rp.get("data") or []) if row.get("day")
                }
                processes[pname] = [series.get(d, 0) for d in day_list]

            # TOP 料号
            r_part = self.erp.execute(f"""
                SELECT TOP 5
                    v.MfgPartCode AS pn,
                    SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {scrap_filter}
                  AND v.CreateDate >= '{start_d}'
                  AND v.MfgPartCode IS NOT NULL AND v.MfgPartCode <> ''
                GROUP BY v.MfgPartCode
                ORDER BY SUM(v.QtyOfUnit) DESC
            """)
            part_names = [x.get("pn") for x in (r_part.get("data") or []) if x.get("pn")]
            parts = {}
            for pn in part_names:
                safe = str(pn).replace("'", "''")
                rp = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', v.CreateDate) AS day,
                           SUM(v.QtyOfUnit) AS scrap
                    FROM View_P_Inspection v
                    WHERE {scrap_filter}
                      AND v.CreateDate >= '{start_d}'
                      AND v.MfgPartCode = '{safe}'
                    GROUP BY day ORDER BY day
                """)
                series = {
                    row.get("day"): int(float(row.get("scrap") or 0))
                    for row in (rp.get("data") or []) if row.get("day")
                }
                parts[pn] = [series.get(d, 0) for d in day_list]

            return {
                "days": day_list,
                "overall_rate": overall_rate,
                "scrap_pcs": scrap_pcs,
                "stock_pcs": stock_pcs,
                "processes": processes,
                "parts": parts,
                "process_names": process_names,
                "part_names": part_names,
                "monthly": empty["monthly"],
            }
        except Exception as e:
            logger.warning("报废趋势分析失败: %s", e)
            return empty

    def get_copq_analysis(self, start_date=None, end_date=None, part_number=None, customer_code=None):
        """
        客诉 / COPQ 金额分析（ERP customer_complaints + materials 单价）。
        COPQ = 客诉台账 copq_amount 汇总；并估算本期报废×物料采购价作为内部质量成本参考。
        """
        result = {
            "has_data": False,
            "data_source": "ERP:customer_complaints",
            "date_filter": {},
            "kpi": {
                "total_count": 0, "open_count": 0, "closed_count": 0,
                "copq_total": 0.0, "affected_qty": 0, "severity_avg": None,
                "currency": "CNY",
                "scrap_cost_est": 0.0, "scrap_pcs": 0,
            },
            "by_category": [],
            "by_customer": [],
            "by_product": [],
            "monthly": [],
            "recent": [],
            "scrap_cost_by_part": [],
        }
        if not (self.erp and self.erp.connected):
            result["data_gap"] = "ERP 未连接"
            return result

        if not start_date and not end_date:
            # 默认近 90 天，覆盖当月客诉与环比
            end = datetime.now().date()
            start = end - timedelta(days=90)
            start_date = start.isoformat()
            end_date = (end + timedelta(days=1)).isoformat()

        result["date_filter"] = {"start_date": start_date, "end_date": end_date}
        conds = ["1=1"]
        if start_date:
            conds.append(f"complaint_date >= '{start_date}'")
        if end_date:
            conds.append(f"complaint_date < '{end_date}'")
        if customer_code:
            safe = str(customer_code).replace("'", "''")
            conds.append(f"customer_code LIKE '%{safe}%'")
        if part_number:
            safe = str(part_number).replace("'", "''")
            conds.append(f"(product_code LIKE '%{safe}%' OR product_name LIKE '%{safe}%')")
        where = " AND ".join(conds)

        try:
            r = self.erp.execute(f"""
                SELECT COUNT(*) AS total_count,
                       SUM(CASE WHEN LOWER(IFNULL(status,'')) IN ('open','processing','新建','处理中') THEN 1 ELSE 0 END) AS open_count,
                       SUM(CASE WHEN LOWER(IFNULL(status,'')) IN ('closed','已关闭','已回复') THEN 1 ELSE 0 END) AS closed_count,
                       IFNULL(SUM(copq_amount),0) AS copq_total,
                       IFNULL(SUM(affected_qty),0) AS affected_qty,
                       AVG(severity) AS severity_avg
                FROM customer_complaints
                WHERE {where}
            """)
            if r.get("success") and r.get("data"):
                row = r["data"][0]
                result["kpi"]["total_count"] = int(row.get("total_count") or 0)
                result["kpi"]["open_count"] = int(row.get("open_count") or 0)
                result["kpi"]["closed_count"] = int(row.get("closed_count") or 0)
                result["kpi"]["copq_total"] = round(float(row.get("copq_total") or 0), 2)
                result["kpi"]["affected_qty"] = int(row.get("affected_qty") or 0)
                sev = row.get("severity_avg")
                result["kpi"]["severity_avg"] = round(float(sev), 2) if sev is not None else None
                result["has_data"] = result["kpi"]["total_count"] > 0 or result["kpi"]["copq_total"] > 0

            r = self.erp.execute(f"""
                SELECT IFNULL(NULLIF(cost_category,''),'未分类') AS category,
                       COUNT(*) AS cnt,
                       IFNULL(SUM(copq_amount),0) AS amount,
                       IFNULL(SUM(affected_qty),0) AS qty
                FROM customer_complaints WHERE {where}
                GROUP BY IFNULL(NULLIF(cost_category,''),'未分类')
                ORDER BY SUM(copq_amount) DESC
            """)
            cat_label = {
                "claim": "索赔", "return": "退货", "rework": "返工",
                "scrap": "报废", "downtime": "停线", "other": "其他",
            }
            for row in (r.get("data") or []):
                cat = row.get("category") or "未分类"
                result["by_category"].append({
                    "category": cat,
                    "category_label": cat_label.get(str(cat).lower(), cat),
                    "count": int(row.get("cnt") or 0),
                    "amount": round(float(row.get("amount") or 0), 2),
                    "qty": int(row.get("qty") or 0),
                })

            r = self.erp.execute(f"""
                SELECT customer_code, customer_name,
                       COUNT(*) AS cnt,
                       IFNULL(SUM(copq_amount),0) AS amount
                FROM customer_complaints WHERE {where}
                GROUP BY customer_code, customer_name
                ORDER BY SUM(copq_amount) DESC
                LIMIT 10
            """)
            for row in (r.get("data") or []):
                result["by_customer"].append({
                    "customer_code": row.get("customer_code") or "",
                    "customer_name": row.get("customer_name") or "",
                    "count": int(row.get("cnt") or 0),
                    "amount": round(float(row.get("amount") or 0), 2),
                })

            r = self.erp.execute(f"""
                SELECT product_code, product_name,
                       COUNT(*) AS cnt,
                       IFNULL(SUM(copq_amount),0) AS amount,
                       IFNULL(SUM(affected_qty),0) AS qty
                FROM customer_complaints WHERE {where}
                GROUP BY product_code, product_name
                ORDER BY SUM(copq_amount) DESC
                LIMIT 10
            """)
            for row in (r.get("data") or []):
                result["by_product"].append({
                    "product_code": row.get("product_code") or "",
                    "product_name": row.get("product_name") or "",
                    "count": int(row.get("cnt") or 0),
                    "amount": round(float(row.get("amount") or 0), 2),
                    "qty": int(row.get("qty") or 0),
                })

            r = self.erp.execute(f"""
                SELECT strftime('%Y-%m', complaint_date) AS month,
                       COUNT(*) AS cnt,
                       IFNULL(SUM(copq_amount),0) AS amount
                FROM customer_complaints WHERE {where}
                GROUP BY strftime('%Y-%m', complaint_date)
                ORDER BY month
            """)
            for row in (r.get("data") or []):
                if not row.get("month"):
                    continue
                result["monthly"].append({
                    "month": row.get("month"),
                    "count": int(row.get("cnt") or 0),
                    "amount": round(float(row.get("amount") or 0), 2),
                })

            r = self.erp.execute(f"""
                SELECT complaint_no, customer_name, product_code, product_name,
                       complaint_date, complaint_type, severity, status,
                       cost_category, affected_qty, unit_cost, copq_amount,
                       problem_description, assigned_to
                FROM customer_complaints WHERE {where}
                ORDER BY complaint_date DESC, id DESC
                LIMIT 30
            """)
            for row in (r.get("data") or []):
                result["recent"].append({
                    "complaint_no": row.get("complaint_no"),
                    "customer_name": row.get("customer_name"),
                    "product_code": row.get("product_code"),
                    "product_name": row.get("product_name"),
                    "complaint_date": row.get("complaint_date"),
                    "complaint_type": row.get("complaint_type"),
                    "severity": row.get("severity"),
                    "status": row.get("status"),
                    "cost_category": row.get("cost_category"),
                    "affected_qty": int(row.get("affected_qty") or 0),
                    "unit_cost": float(row.get("unit_cost") or 0),
                    "copq_amount": round(float(row.get("copq_amount") or 0), 2),
                    "problem_description": row.get("problem_description") or "",
                    "assigned_to": row.get("assigned_to") or "",
                })

            # 内部报废成本：仅用 ERP 实有单价（materials.purchase_price /
            # customer_complaints.unit_cost），匹配不到的料号不计价、不编造。
            scrap = self.get_scrap_analysis(
                part_number, start_date, end_date, fiscal_period=None,
            )
            part_filter = ""
            if part_number:
                safe_pn = str(part_number).replace("'", "''")
                part_filter = f" AND v.MfgPartCode LIKE '%{safe_pn}%'"
            end_cond = f" AND v.CreateDate < '{end_date}'" if end_date else ""
            r_parts = self.erp.execute(f"""
                SELECT v.MfgPartCode AS pn, SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {self._scrap_non_production_sql()}
                  AND v.CreateDate >= '{start_date}'
                  {end_cond}
                  {part_filter}
                GROUP BY v.MfgPartCode
                ORDER BY SUM(v.QtyOfUnit) DESC
                LIMIT 30
            """)
            # 1) 物料主数据采购价
            price_map = {}
            price_src = {}
            r_price = self.erp.execute("""
                SELECT material_code, purchase_price FROM materials
                WHERE purchase_price IS NOT NULL AND purchase_price > 0
            """)
            for row in (r_price.get("data") or []):
                code = str(row.get("material_code") or "").strip()
                if not code:
                    continue
                price_map[code] = float(row.get("purchase_price") or 0)
                price_src[code] = "materials.purchase_price"
            # 2) 客诉台账同产品平均 unit_cost（ERP 实填）
            r_uc = self.erp.execute("""
                SELECT product_code, AVG(unit_cost) AS avg_uc, COUNT(*) AS c
                FROM customer_complaints
                WHERE IFNULL(unit_cost,0) > 0
                  AND IFNULL(product_code,'') <> ''
                GROUP BY product_code
            """)
            for row in (r_uc.get("data") or []):
                code = str(row.get("product_code") or "").strip()
                if not code or code in price_map:
                    continue
                price_map[code] = float(row.get("avg_uc") or 0)
                price_src[code] = "customer_complaints.unit_cost"

            scrap_cost = 0.0
            scrap_pcs = 0
            priced_pcs = 0
            unpriced_pcs = 0
            for row in (r_parts.get("data") or []):
                pn = str(row.get("pn") or "").strip()
                pcs = int(float(row.get("scrap") or 0))
                scrap_pcs += pcs
                price = price_map.get(pn)
                src = price_src.get(pn)
                # 仅允许精确料号匹配；禁止用无关物料均价冒充成品成本
                if price is None:
                    unpriced_pcs += pcs
                    result["scrap_cost_by_part"].append({
                        "part_number": pn,
                        "scrap_pcs": pcs,
                        "unit_price": None,
                        "cost_est": None,
                        "priced": False,
                        "price_source": None,
                    })
                    continue
                amt = round(pcs * price, 2)
                scrap_cost += amt
                priced_pcs += pcs
                result["scrap_cost_by_part"].append({
                    "part_number": pn,
                    "scrap_pcs": pcs,
                    "unit_price": price,
                    "cost_est": amt,
                    "priced": True,
                    "price_source": src,
                })

            total_scrap = scrap_pcs or int(scrap.get("total_scrap_pcs") or 0)
            result["kpi"]["scrap_pcs"] = total_scrap
            result["kpi"]["scrap_cost_est"] = round(scrap_cost, 2)
            result["kpi"]["scrap_priced_pcs"] = priced_pcs
            result["kpi"]["scrap_unpriced_pcs"] = unpriced_pcs
            result["kpi"]["scrap_price_coverage"] = (
                round(priced_pcs * 100.0 / scrap_pcs, 1) if scrap_pcs > 0 else None
            )
            result["kpi"]["quality_cost_total"] = round(
                float(result["kpi"]["copq_total"]) + scrap_cost, 2
            )
            if unpriced_pcs > 0:
                result["scrap_cost_gap"] = (
                    f"报废 {unpriced_pcs:,} PCS 无 materials/客诉单价，未计入成本"
                    "（禁止用假单价）"
                )
            if result["kpi"]["total_count"] or scrap_cost > 0 or total_scrap > 0:
                result["has_data"] = True
            return result
        except Exception as e:
            logger.warning("COPQ 分析失败: %s", e)
            result["data_gap"] = str(e)
            return result

    def get_spc_analysis(self, chart_id=None, chart_code=None, process_name=None):
        """
        SPC 控制图列表/详情（ERP spc_charts + spc_measurements）。
        列表不含超大 data_points；详情含测点与失控点。
        """
        result = {
            "has_data": False,
            "data_source": "ERP:spc_charts/spc_measurements",
            "charts": [],
            "detail": None,
            "summary": {"total": 0, "alert": 0, "normal": 0, "avg_cpk": None},
        }
        if not (self.erp and self.erp.connected):
            result["data_gap"] = "ERP 未连接"
            return result
        try:
            conds = ["1=1"]
            if process_name:
                safe = str(process_name).replace("'", "''")
                conds.append(f"process_name LIKE '%{safe}%'")
            where = " AND ".join(conds)
            r = self.erp.execute(f"""
                SELECT id, chart_code, chart_name, process_name, characteristic,
                       chart_type, subgroup_size, unit, usl, lsl, target,
                       ucl, cl, lcl, cp, cpk, status, updated_at
                FROM spc_charts
                WHERE {where}
                ORDER BY CASE WHEN LOWER(IFNULL(status,''))='alert' THEN 0 ELSE 1 END, id
            """)
            charts = []
            cpk_vals = []
            alert_n = 0
            for row in (r.get("data") or []):
                st = str(row.get("status") or "").lower()
                if st == "alert":
                    alert_n += 1
                cpk = row.get("cpk")
                if cpk is not None:
                    try:
                        cpk_vals.append(float(cpk))
                    except Exception:
                        pass
                charts.append({
                    "id": row.get("id"),
                    "chart_code": row.get("chart_code"),
                    "chart_name": row.get("chart_name"),
                    "process_name": row.get("process_name"),
                    "characteristic": row.get("characteristic"),
                    "chart_type": row.get("chart_type"),
                    "subgroup_size": row.get("subgroup_size"),
                    "unit": row.get("unit") or "",
                    "usl": row.get("usl"),
                    "lsl": row.get("lsl"),
                    "target": row.get("target"),
                    "ucl": row.get("ucl"),
                    "cl": row.get("cl"),
                    "lcl": row.get("lcl"),
                    "cp": row.get("cp"),
                    "cpk": row.get("cpk"),
                    "status": row.get("status") or "normal",
                    "updated_at": row.get("updated_at"),
                })
            result["charts"] = charts
            result["summary"] = {
                "total": len(charts),
                "alert": alert_n,
                "normal": len(charts) - alert_n,
                "avg_cpk": round(sum(cpk_vals) / len(cpk_vals), 3) if cpk_vals else None,
            }
            result["has_data"] = len(charts) > 0

            # 详情：指定图或默认第一张告警/第一张
            detail_id = chart_id
            if not detail_id and chart_code:
                for c in charts:
                    if str(c.get("chart_code")) == str(chart_code):
                        detail_id = c.get("id")
                        break
            if not detail_id and charts:
                alert_one = next((c for c in charts if str(c.get("status")).lower() == "alert"), None)
                detail_id = (alert_one or charts[0]).get("id")

            if detail_id:
                rd = self.erp.execute(f"""
                    SELECT * FROM spc_charts WHERE id = {int(detail_id)}
                """)
                if rd.get("success") and rd.get("data"):
                    d = rd["data"][0]
                    points = []
                    raw_dp = d.get("data_points") or "[]"
                    try:
                        points = json.loads(raw_dp) if isinstance(raw_dp, str) else (raw_dp or [])
                    except Exception:
                        points = []
                    measurements = []
                    rm = self.erp.execute(f"""
                        SELECT measured_at, value, subgroup_index, sample_label
                        FROM spc_measurements
                        WHERE chart_id = {int(detail_id)}
                        ORDER BY subgroup_index, measured_at
                    """)
                    for row in (rm.get("data") or []):
                        measurements.append({
                            "measured_at": row.get("measured_at"),
                            "value": row.get("value"),
                            "subgroup_index": row.get("subgroup_index"),
                            "sample_label": row.get("sample_label"),
                        })
                    ooc = [p for p in points if p.get("out_of_control")]
                    result["detail"] = {
                        "id": d.get("id"),
                        "chart_code": d.get("chart_code"),
                        "chart_name": d.get("chart_name"),
                        "process_name": d.get("process_name"),
                        "characteristic": d.get("characteristic"),
                        "chart_type": d.get("chart_type"),
                        "subgroup_size": d.get("subgroup_size"),
                        "unit": d.get("unit") or "",
                        "usl": d.get("usl"), "lsl": d.get("lsl"), "target": d.get("target"),
                        "ucl": d.get("ucl"), "cl": d.get("cl"), "lcl": d.get("lcl"),
                        "cp": d.get("cp"), "cpk": d.get("cpk"),
                        "status": d.get("status") or "normal",
                        "updated_at": d.get("updated_at"),
                        "data_points": points,
                        "measurements": measurements,
                        "out_of_control_count": len(ooc),
                        "out_of_control_points": ooc,
                    }
            return result
        except Exception as e:
            logger.warning("SPC 分析失败: %s", e)
            result["data_gap"] = str(e)
            return result

    def get_ipqc_analysis(self, day_count: int = 30, process_name: str = None):
        """
        IPQC 检验分析（领导决策向只读视图）。
        数据源：MES TBL_QM_INSPECT_RECORD + TBL_BD_PROCESS；
        待审/时效：quality_inspection_records（与中软 ERP IPQC 看板同口径）。
        输出：检验单量/合格率/不合格率/待审积压、工序合格率、日趋势、客户不合格 Top、积压时效、决策提示。
        """
        n = max(7, min(int(day_count or 30), 90))
        result = {
            "has_data": False,
            "data_source": "MES:TBL_QM_INSPECT_RECORD + quality_inspection_records",
            "day_count": n,
            "kpi": {
                "inspect_count": 0, "pass_count": 0, "ng_count": 0,
                "pass_rate": None, "ng_rate": None, "pending_count": 0,
            },
            "process_rates": [],
            "daily_trend": [],
            "customer_ng_top": [],
            "backlog": {
                "pending": 0, "audited": 0,
                "pending_avg_age_hours": None,
                "pending_over_24h": 0,
                "pending_over_24h_ratio": None,
                "pending_age_buckets": [],
                "avg_audit_hours": None,
                "audit_timing_reliable": False,
                "notes": [],
            },
            "decision_briefing": {"summary": "", "actions": []},
        }
        db = self.mes if (self.mes and self.mes.connected) else self.erp
        if not (db and db.connected):
            result["data_gap"] = "MES/ERP 未连接"
            return result

        try:
            proc_filter = ""
            if process_name:
                safe = str(process_name).replace("'", "''")
                proc_filter = f" AND IFNULL(p.CPROCESS_NAME,'待确认工序') LIKE '%{safe}%'"

            # KPI：近 N 日检验单
            r = db.execute(f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END) AS pass_c,
                       SUM(CASE WHEN r.CRESULT=2 THEN 1 ELSE 0 END) AS ng_c
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE r.CCREATE_DATE >= date('now', '-{n} days')
                  {proc_filter}
            """)
            if r.get("success") and r.get("data"):
                row = r["data"][0]
                total = int(row.get("total") or 0)
                pass_c = int(row.get("pass_c") or 0)
                ng_c = int(row.get("ng_c") or 0)
                result["kpi"]["inspect_count"] = total
                result["kpi"]["pass_count"] = pass_c
                result["kpi"]["ng_count"] = ng_c
                if total > 0:
                    result["kpi"]["pass_rate"] = round(pass_c * 100.0 / total, 2)
                    result["kpi"]["ng_rate"] = round(ng_c * 100.0 / total, 2)
                    result["has_data"] = True

            # 待审积压（全量未审，领导更关心当前积压而非窗口截断）
            r_pend = db.execute("""
                SELECT COUNT(*) AS pending FROM quality_inspection_records WHERE status='pending'
            """)
            pending = 0
            if r_pend.get("success") and r_pend.get("data"):
                pending = int(r_pend["data"][0].get("pending") or 0)
            result["kpi"]["pending_count"] = pending

            # 工序合格率
            r = db.execute(f"""
                SELECT IFNULL(p.CPROCESS_NAME, '待确认工序') AS process_name,
                       COUNT(*) AS total,
                       SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END) AS pass_c,
                       SUM(CASE WHEN r.CRESULT=2 THEN 1 ELSE 0 END) AS ng_c
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE r.CCREATE_DATE >= date('now', '-{n} days')
                  {proc_filter}
                GROUP BY IFNULL(p.CPROCESS_NAME, '待确认工序')
                ORDER BY COUNT(*) DESC
            """)
            for row in (r.get("data") or []):
                t = int(row.get("total") or 0) or 1
                pc = int(row.get("pass_c") or 0)
                nc = int(row.get("ng_c") or 0)
                result["process_rates"].append({
                    "process_name": row.get("process_name") or "待确认工序",
                    "total": t,
                    "pass_count": pc,
                    "ng_count": nc,
                    "pass_rate": round(pc * 100.0 / t, 2),
                    "ng_rate": round(nc * 100.0 / t, 2),
                })

            # 日不合格率趋势
            r = db.execute(f"""
                SELECT strftime('%Y-%m-%d', r.CCREATE_DATE) AS day,
                       COUNT(*) AS total,
                       SUM(CASE WHEN r.CRESULT=2 THEN 1 ELSE 0 END) AS ng_c
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE r.CCREATE_DATE >= date('now', '-{n} days')
                  {proc_filter}
                GROUP BY strftime('%Y-%m-%d', r.CCREATE_DATE)
                ORDER BY day
            """)
            for row in (r.get("data") or []):
                t = int(row.get("total") or 0)
                nc = int(row.get("ng_c") or 0)
                result["daily_trend"].append({
                    "day": row.get("day"),
                    "inspect_count": t,
                    "ng_count": nc,
                    "ng_rate": round(nc * 100.0 / t, 2) if t else None,
                })

            # 客户不合格 Top10（quality_inspection_records 含 customer_code）
            r = db.execute(f"""
                SELECT q.customer_code,
                       IFNULL(MAX(c.customer_name), q.customer_code) AS customer_name,
                       COUNT(*) AS total,
                       SUM(CASE WHEN q.status='failed' OR IFNULL(q.source_result,0)=2
                                 OR LOWER(IFNULL(q.inspection_result,'')) IN ('fail','ng')
                            THEN 1 ELSE 0 END) AS ng_c
                FROM quality_inspection_records q
                LEFT JOIN customers c ON c.customer_code = q.customer_code
                WHERE q.customer_code IS NOT NULL AND q.customer_code <> ''
                  AND IFNULL(q.inspected_at, q.created_at) >= date('now', '-{n} days')
                GROUP BY q.customer_code
                HAVING ng_c > 0
                ORDER BY ng_c DESC
                LIMIT 10
            """)
            # source_result may not exist - fallback query
            if not r.get("success"):
                r = db.execute(f"""
                    SELECT q.customer_code,
                           IFNULL(MAX(c.customer_name), q.customer_code) AS customer_name,
                           COUNT(*) AS total,
                           SUM(CASE WHEN q.status='failed' THEN 1 ELSE 0 END) AS ng_c
                    FROM quality_inspection_records q
                    LEFT JOIN customers c ON c.customer_code = q.customer_code
                    WHERE q.customer_code IS NOT NULL AND q.customer_code <> ''
                      AND IFNULL(q.inspected_at, q.created_at) >= date('now', '-{n} days')
                    GROUP BY q.customer_code
                    HAVING ng_c > 0
                    ORDER BY ng_c DESC
                    LIMIT 10
                """)
            for row in (r.get("data") or []):
                t = int(row.get("total") or 0) or 1
                nc = int(row.get("ng_c") or 0)
                result["customer_ng_top"].append({
                    "customer_code": row.get("customer_code") or "",
                    "customer_name": row.get("customer_name") or "",
                    "total": t,
                    "ng_count": nc,
                    "ng_rate": round(nc * 100.0 / t, 2),
                })

            # ① 待审龄期（与柱图同口径）：只用 source_created_at，避免旧种子 created_at 把龄期拉爆
            # ② 已审时效：创建→修改；若绝大多数时差≈0（同步特征），标记不可靠，避免与待审图打架
            notes = []
            r_cnt = db.execute("""
                SELECT
                  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status IN ('passed','failed') THEN 1 ELSE 0 END) AS audited
                FROM quality_inspection_records
            """)
            audited = 0
            if r_cnt.get("success") and r_cnt.get("data"):
                audited = int(r_cnt["data"][0].get("audited") or 0)
                pending = int(r_cnt["data"][0].get("pending") or pending or 0)
                result["kpi"]["pending_count"] = pending

            r_age = db.execute("""
                SELECT
                  COUNT(*) AS aged_n,
                  ROUND(AVG(age_h), 1) AS avg_age,
                  SUM(CASE WHEN age_h > 24 THEN 1 ELSE 0 END) AS over_24,
                  SUM(CASE WHEN age_h < 8 THEN 1 ELSE 0 END) AS h0_8,
                  SUM(CASE WHEN age_h >= 8 AND age_h < 24 THEN 1 ELSE 0 END) AS h8_24,
                  SUM(CASE WHEN age_h >= 24 AND age_h < 72 THEN 1 ELSE 0 END) AS h24_72,
                  SUM(CASE WHEN age_h >= 72 THEN 1 ELSE 0 END) AS h72p
                FROM (
                  SELECT (julianday('now') - julianday(source_created_at)) * 24 AS age_h
                  FROM quality_inspection_records
                  WHERE status = 'pending' AND source_created_at IS NOT NULL
                )
            """)
            buckets = [
                {"label": "<8h", "count": 0},
                {"label": "8-24h", "count": 0},
                {"label": "24-72h", "count": 0},
                {"label": ">72h", "count": 0},
            ]
            pending_avg_age = None
            pending_over_24 = 0
            pending_over_24_ratio = None
            aged_n = 0
            if r_age.get("success") and r_age.get("data"):
                row = r_age["data"][0]
                aged_n = int(row.get("aged_n") or 0)
                if row.get("avg_age") is not None:
                    pending_avg_age = float(row.get("avg_age"))
                pending_over_24 = int(row.get("over_24") or 0)
                if aged_n > 0:
                    pending_over_24_ratio = round(pending_over_24 * 100.0 / aged_n, 1)
                buckets[0]["count"] = int(row.get("h0_8") or 0)
                buckets[1]["count"] = int(row.get("h8_24") or 0)
                buckets[2]["count"] = int(row.get("h24_72") or 0)
                buckets[3]["count"] = int(row.get("h72p") or 0)

            skip_pending = max(0, pending - aged_n)
            if skip_pending:
                notes.append(f"{skip_pending} 条待审缺少 source_created_at，未计入龄期分层")

            r_audit = db.execute("""
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN (julianday(source_modified_at)-julianday(source_created_at))*24*60 >= 1
                       THEN 1 ELSE 0 END) AS meaningful,
                  AVG(CASE WHEN (julianday(source_modified_at)-julianday(source_created_at))*24*60 >= 1
                       THEN (julianday(source_modified_at)-julianday(source_created_at))*24 END) AS avg_h
                FROM quality_inspection_records
                WHERE status IN ('passed','failed')
                  AND source_created_at IS NOT NULL AND source_modified_at IS NOT NULL
            """)
            avg_h = None
            audit_reliable = False
            if r_audit.get("success") and r_audit.get("data"):
                row = r_audit["data"][0]
                total_a = int(row.get("total") or 0)
                meaningful = int(row.get("meaningful") or 0)
                # 有效样本过少（同步时创建=修改）则不展示已审均时，避免与待审图矛盾
                if total_a > 0 and meaningful * 100.0 / total_a >= 5:
                    audit_reliable = True
                    if row.get("avg_h") is not None:
                        avg_h = round(float(row.get("avg_h")), 1)
                else:
                    notes.append(
                        "已审记录创建/修改时间大多相同（数据同步特征），已审审核时效暂不可用；"
                        "本图以待审龄期为准"
                    )

            result["backlog"] = {
                "pending": pending,
                "audited": audited,
                "pending_with_age": aged_n,
                "pending_avg_age_hours": pending_avg_age,
                "pending_over_24h": pending_over_24,
                "pending_over_24h_ratio": pending_over_24_ratio,
                "pending_age_buckets": buckets,
                "avg_audit_hours": avg_h,
                "audit_timing_reliable": audit_reliable,
                "notes": notes,
            }

            # 领导决策提示（实数，不编造）
            actions = []
            weak = sorted(
                [p for p in result["process_rates"] if p.get("total", 0) >= 20],
                key=lambda x: float(x.get("pass_rate") or 100),
            )[:3]
            for p in weak:
                if float(p.get("pass_rate") or 100) < 95:
                    actions.append(
                        f"关注工序「{p['process_name']}」合格率 {p['pass_rate']}%（不合格 {p['ng_count']}）→ 下钻报废/SPC/开8D"
                    )
            if result["customer_ng_top"]:
                c0 = result["customer_ng_top"][0]
                actions.append(
                    f"客户 {c0.get('customer_name') or c0.get('customer_code')} 不合格 {c0['ng_count']} 单居首 → 客诉/COPQ与追溯联查"
                )
            if pending >= 50:
                aged = buckets[3]["count"] + buckets[2]["count"]
                actions.append(
                    f"待审积压 {pending} 单（≥24h {aged}）→ 督促审核闭环，避免漏检风险放大"
                )
            ng_rate = result["kpi"].get("ng_rate")
            summary = (
                f"近{n}日检验 {result['kpi']['inspect_count']} 单，合格率 "
                f"{result['kpi']['pass_rate'] if result['kpi']['pass_rate'] is not None else '—'}%，"
                f"不合格率 {ng_rate if ng_rate is not None else '—'}%，待审 {pending}。"
            )
            if weak and float(weak[0].get("pass_rate") or 100) < 90:
                summary += f" 最弱工序「{weak[0]['process_name']}」合格率仅 {weak[0]['pass_rate']}%。"
            result["decision_briefing"] = {"summary": summary, "actions": actions[:5]}
            if result["kpi"]["inspect_count"] or pending:
                result["has_data"] = True
            return result
        except Exception as e:
            logger.warning("IPQC 分析失败: %s", e)
            result["data_gap"] = str(e)
            return result

    def scan_quality_alerts(self, fiscal_period=None):
        """从 ERP/MES 扫描当月品质数据并生成/刷新预警（禁止编造，仅实数）"""
        if not fiscal_period:
            fiscal_period = datetime.now().strftime("%Y%m")

        # 先清：非当月旧预警 + 低样本/0%跳变等不可靠记录
        purged_stale = self.db.purge_stale_alerts(fiscal_period)
        purged_bad = self.db.purge_unreliable_alerts()
        purged = list(dict.fromkeys(purged_stale + purged_bad))

        created = []
        sources = {
            "plant_scrap": False, "plant_mes": False, "top_parts": 0,
            "purged": len(purged), "purged_stale": len(purged_stale),
        }

        # 1) 全厂报废 + 日趋势（ERP）
        scrap = self.get_scrap_analysis(None, fiscal_period=fiscal_period)
        sources["plant_scrap"] = True
        created.extend(self.engine.generate_alerts(
            float(scrap.get("scrap_rate") or 0),
            "全厂",
            [],
            scrap.get("monthly_trend") or [],
            scrap_pcs=float(scrap.get("total_scrap_pcs") or 0),
        ))

        # 2) 全厂 MES 工序良率（按工单关联有数时）
        proc = self.get_process_yield(None)
        sources["plant_mes"] = bool(proc.get("process_yield"))
        created.extend(self.engine.generate_alerts(
            0,
            "全厂",
            proc.get("process_yield") or [],
            [],
            scrap_pcs=0,
        ))

        # 3) TOP 报废料号逐一扫描（ERP 报废率 + 该料号 MES）
        dash = self.get_dashboard(fiscal_period)
        top_parts = dash.get("top_defect_parts") or []
        for row in top_parts[:8]:
            part = (row.get("PartNumber") or row.get("part") or "").strip()
            if not part:
                continue
            rate = float(row.get("RejectRate") or 0)
            reject = float(row.get("TotalReject") or row.get("scrap_pcs") or 0)
            # 料号报废预警
            created.extend(self.engine.generate_alerts(
                rate, part, [], [], scrap_pcs=reject,
            ))
            # 料号 MES 工序
            p_mes = self.get_process_yield(part)
            if p_mes.get("process_yield"):
                created.extend(self.engine.generate_alerts(
                    0, part, p_mes.get("process_yield") or [], [], scrap_pcs=0,
                ))
            sources["top_parts"] += 1

        # 去重（同 id 可能被多次 generate）
        uniq = {}
        for a in created:
            uniq[a["id"]] = a
        active = self.db.get_alerts(acknowledged=False, limit=200)
        critical = [a for a in active if a.get("level") == "critical"]
        return {
            "scanned_at": datetime.now().isoformat(),
            "fiscal_period": fiscal_period,
            "sources": sources,
            "generated": len(uniq),
            "purged": len(purged),
            "active_alerts": len(active),
            "critical_alerts": len(critical),
            "alerts": list(uniq.values())[:30],
            "erp_connected": bool(self.erp and self.erp.connected),
            "mes_connected": bool(self.mes and self.mes.connected),
            "policy": "指标仅来自 ERP/MES 实查，禁止编造；建议文案可为知识库模板",
        }

    def generate_8d_report(self, part_number, start_date=None, end_date=None, period=None, fiscal_period=None):
        """生成真实有效的 8D：ERP/MES 实据 + 已关闭历史方案 / 已验证知识"""
        part_number = (part_number or "").strip()
        if not part_number:
            return {"success": False, "error": "料号不能为空"}
        if part_number.startswith("QHC-8D-") or part_number.startswith("8D-"):
            return {"success": False, "error": "料号无效（请勿填报告编号）"}

        if fiscal_period:
            pass
        elif period:
            year, month = period.split("-")
            fiscal_period = f"{year}{month}"
        elif not start_date and not end_date:
            fiscal_period = datetime.now().strftime("%Y%m")

        # 清理历史无效稿
        purged = self.db.purge_invalid_8d()

        scrap = self.get_scrap_analysis(part_number, start_date, end_date, fiscal_period=fiscal_period)
        process = self.get_process_yield(part_number, fiscal_period=fiscal_period)
        wo = self.get_work_orders(part_number, start_date, end_date, fiscal_period=fiscal_period)

        scrap_pcs = int(scrap.get("total_scrap_pcs") or 0)
        stock_pcs = int(scrap.get("total_in_stock_pcs") or 0)
        defects = scrap.get("defect_top10") or []
        has_metric = scrap_pcs > 0 or stock_pcs > 0 or bool(defects) or bool(process.get("process_yield"))
        if not has_metric:
            return {
                "success": False,
                "error": (
                    f"料号 {part_number} 在"
                    f"{('周期 '+fiscal_period) if fiscal_period else '所选日期'}"
                    "无 ERP 报废/入库或 MES 检验实绩，请先在 MES/ERP 补充数据后再生成 8D"
                ),
                "purged_invalid": purged,
            }

        evidence = {
            "part_number": part_number,
            "fiscal_period": fiscal_period or scrap.get("fiscal_period") or "",
            "date_range": f"{start_date or ''} ~ {end_date or ''}".strip(" ~"),
            "scrap_rate": float(scrap.get("scrap_rate") or 0),
            "scrap_pcs": scrap_pcs,
            "stock_pcs": stock_pcs,
            "input_base_pcs": int(scrap.get("input_base_pcs") or (scrap_pcs + stock_pcs)),
            "defects": defects,
            "process_scrap": scrap.get("process_scrap") or [],
            "work_orders": (wo.get("work_orders") or [])[:20],
            "mes_process_yield": process.get("process_yield") or [],
            "mes_process_defects": process.get("process_defects") or [],
            "mes_inspect_count": process.get("inspect_count") or 0,
            "mes_avg_yield": process.get("avg_yield"),
            "mes_min_yield": process.get("min_yield"),
            "data_sources": "ERP:View_P_Inspection+FGI+P_WO; MES:TBL_QM_INSPECT_RECORD",
            "scrap_data_gap": scrap.get("data_gap"),
            "mes_data_gap": process.get("data_gap"),
        }

        defect_types = [d.get("defect_type") for d in defects if d.get("defect_type")]
        process_names = list({
            *(d.get("process") for d in defects if d.get("process")),
            *(p.get("process") for p in (scrap.get("process_scrap") or []) if p.get("process")),
        })
        history = self.db.find_closed_8d_history(part_number, defect_types, process_names, limit=6)
        verified = []
        for dt in defect_types[:3]:
            verified.extend(self.db.get_verified_patterns(defect_type=dt, limit=3))
        # 去重
        seen = set()
        verified_uniq = []
        for v in verified:
            vid = v.get("id")
            if vid in seen:
                continue
            seen.add(vid)
            verified_uniq.append(v)

        content = self.engine.generate_8d_from_evidence(
            part_number, evidence, history, verified_uniq
        )
        # DB 只存表字段
        db_row = {k: v for k, v in content.items()
                  if k in (
                      "id", "report_no", "part_number", "customer", "problem",
                      "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
                      "defects_json", "evidence_json", "history_refs_json",
                      "knowledge_refs_json", "data_policy",
                      "status", "severity", "created_by", "created_at",
                  )}
        self.db.save_8d(db_row)
        content["success"] = True
        content["purged_invalid"] = purged
        content["evidence"] = evidence
        content["history_refs"] = history
        content["verified_patterns"] = [
            {"defect_type": v.get("defect_type"), "process_name": v.get("process_name"),
             "solutions": v.get("solutions"), "source_8d": v.get("source_8d")}
            for v in verified_uniq
        ]
        return content


# ═══════════════════════════════════════════════════
# 请求参数校验（阻断 SQL 注入：只接受合法格式，拒绝引号/注释/控制字符）
# ═══════════════════════════════════════════════════

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_BAD_SQL_CHARS = re.compile(r"['\"`;\x00-\x1f]|--|/\*|\*/")
_FP_RE = re.compile(r"^(19|20)\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^(\d{4}-\d{2}|\d{6})$")
_DIGITS_RE = re.compile(r"^\d{1,6}$")


def _is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def validate_params(params: Dict[str, List[str]]) -> Optional[str]:
    """校验查询参数/生成参数；返回错误文案，None 表示通过。"""
    for key, values in (params or {}).items():
        for raw in values:
            if raw is None:
                continue
            value = str(raw)
            if key == "token":
                continue
            if key == "fiscal_period" and not _FP_RE.match(value):
                return f"fiscal_period 须为 6 位周期（如 202611）：{value[:20]}"
            if key in ("start", "end") and not _DATE_RE.match(value):
                return f"{key} 须为 YYYY-MM-DD：{value[:20]}"
            if key == "period" and not _PERIOD_RE.match(value):
                return f"period 须为 YYYY-MM 或 YYYYMM：{value[:20]}"
            if key in ("id", "report_no") and len(value) > 120:
                return f"{key} 过长：{value[:20]}…"
            if key in ("days", "limit") and not _DIGITS_RE.match(value):
                return f"{key} 须为数字：{value[:20]}"
            if key == "acknowledged" and value not in ("", "true", "false", "1", "0"):
                return "acknowledged 取值非法"
            if _BAD_SQL_CHARS.search(value):
                return f"参数 {key} 含非法字符（引号/分号/注释符）"
    return None


# ═══════════════════════════════════════════════════
# HTTP API 处理器
# ═══════════════════════════════════════════════════

class PCBQualityHandler(BaseHTTPRequestHandler):
    system = None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # 令牌引导：/pcb_quality_system.html?token=xxx → 校验通过后下发 Cookie 并重定向去掉令牌
        if path.endswith(".html") and params.get("token"):
            self._bootstrap_token(path, params["token"][0])
            return

        try:
            invalid = validate_params(params)
            if invalid:
                self._err(400, invalid)
                return
            if (path.startswith("/api/") or path.startswith("/download/")) and not self._authorized():
                if path == "/api/health":
                    self._health(public=True)
                else:
                    self._err(401, "需要访问令牌：请用 ?token= 打开页面，或携带 X-API-Token 头")
                return

            if path == "/api/health":
                self._health()
            elif path == "/api/project/env":
                self._project_env_get()
            elif path == "/api/defect_mapping":
                self._defect_mapping_get(params)
            elif path == "/api/dashboard":
                self._dashboard(params)
            elif path == "/api/health_check":
                self._health_report(params)
            elif path == "/api/scrap":
                self._scrap(params)
            elif path == "/api/process":
                self._process(params)
            elif path == "/api/wo":
                self._work_orders(params)
            elif path == "/api/trace":
                self._trace(params)
            elif path == "/api/8d/list":
                self._8d_list(params)
            elif path == "/api/8d/get":
                self._8d_get(params)
            elif path == "/api/8d/export":
                self._8d_export(params)
            elif path == "/api/8d/knowledge_lookup":
                self._8d_knowledge_lookup(params)
            elif path == "/api/alerts":
                self._alerts(params)
            elif path == "/api/knowledge":
                self._knowledge(params)
            elif path == "/api/periods":
                self._periods(params)
            elif path == "/api/history":
                self._history(params)
            elif path == "/api/history/get":
                self._history_get(params)
            elif path == "/api/copq":
                # 客诉 / COPQ 金额分析（ERP customer_complaints）
                self._copq(params)
            elif path == "/api/spc":
                # SPC 控制图列表与详情（ERP spc_charts）
                self._spc(params)
            elif path == "/api/ipqc":
                # IPQC 检验分析（领导决策向）
                self._ipqc(params)
            elif path == "/api/export/excel":
                # 分析页 Excel 导出：module=scrap|ipqc|spc|copq|health|process
                self._export_excel(params)
            elif path == "/" or path == "/index":
                # 重定向到HTML前端
                self.send_response(302)
                self.send_header("Location", "/pcb_quality_system.html")
                self.end_headers()
            elif path.startswith("/download/"):
                # 浏览器下载走 GET
                filename = path.split("/download/", 1)[1]
                self._download_file(filename)
            elif path.endswith(".html") or path.endswith(".css") or path.endswith(".js"):
                # 静态文件服务
                self._serve_static(path)
            else:
                self._err(404, "接口不存在")
        except Exception as e:
            self._err_internal(e, f"GET {path}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        try:
            if not self._authorized():
                self._err(401, "需要访问令牌：请用 ?token= 打开页面，或携带 X-API-Token 头")
                return
            params = {k: [v] for k, v in (body or {}).items()}
            # 仅对会进入 SQL 拼接的参数做校验；8D/学习正文走参数化存储，不限制标点
            if path in (
                "/api/8d/generate", "/api/8d/delete", "/api/8d/purge_invalid",
                "/api/alerts/scan", "/api/alerts/ack", "/api/alerts/ack_batch",
                "/api/history/purge_empty", "/api/export",
            ):
                invalid = validate_params(params)
                if invalid:
                    self._err(400, invalid)
                    return

            if path == "/api/8d/generate":
                self._8d_generate(params)
            elif path == "/api/8d/delete":
                self._8d_delete(params)
            elif path == "/api/8d/purge_invalid":
                self._8d_purge(params)
            elif path == "/api/8d/save":
                self._8d_save(body)
            elif path == "/api/8d/export":
                # POST body: { id } 或 { report_no }
                self._8d_export_body(body)
            elif path == "/api/alerts/scan":
                self._alerts_scan(params)
            elif path == "/api/alerts/ack":
                self._ack_alert(params)
            elif path == "/api/alerts/ack_batch":
                self._ack_alerts_batch(params)
            elif path == "/api/alerts/create":
                self._alert_create(body)
            elif path == "/api/history/purge_empty":
                self._history_purge(params)
            elif path == "/api/sync/bridge":
                self._sync_bridge()
            elif path == "/api/learn":
                self._manual_learn(body)
            elif path == "/api/export":
                self._export_report(body)
            else:
                self._err(404, "接口不存在")
        except Exception as e:
            self._err_internal(e, f"POST {path}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ─── 鉴权 ────────────────────────────────────────

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "qa_token":
                return value.strip()
        return ""

    def _authorized(self) -> bool:
        if not API_TOKEN:
            return True
        supplied = (self.headers.get("X-API-Token") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not supplied:
            supplied = self._cookie_token()
        return bool(supplied) and hmac.compare_digest(supplied, API_TOKEN)

    def _bootstrap_token(self, path: str, token: str) -> None:
        if not API_TOKEN:
            self._redirect(path)
            return
        if not hmac.compare_digest((token or "").strip(), API_TOKEN):
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<meta charset='utf-8'><h3>访问令牌无效</h3>"
                "<p>请输入 .env 中 QA_API_TOKEN 的值后重试。</p>".encode("utf-8")
            )
            return
        cookie = f"qa_token={API_TOKEN}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000"
        self.send_response(302)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Location", path)
        self.end_headers()

    def _redirect(self, path: str) -> None:
        self.send_response(302)
        self.send_header("Location", path)
        self.end_headers()

    # ─── 接口实现 ────────────────────────────────────

    def _health(self, public: bool = False):
        s = self.system
        env_info = {}
        try:
            env_info = describe_for_api()
        except Exception as e:
            env_info = {"error": str(e)}
        resolved = env_info.get("resolved") or {}
        erp_path = str(getattr(s.erp, "db_path", "") or "")

        if public:
            # 未授权访问：仅暴露连通性，不泄露库路径/项目信息
            self._ok({
                "connected": s.is_connected(),
                "erp_connected": bool(s.erp and s.erp.connected),
                "mes_connected": bool(s.mes and s.mes.connected),
                "version": VERSION,
                "timestamp": datetime.now().isoformat(),
            })
            return

        erp_mtime = None
        try:
            if erp_path and Path(erp_path).exists():
                erp_mtime = datetime.fromtimestamp(Path(erp_path).stat().st_mtime).isoformat(timespec="seconds")
        except Exception:
            pass
        try:
            from app.bridge_sync_worker import get_bridge_sync_status
            bridge_sync = get_bridge_sync_status()
        except Exception:
            bridge_sync = {}

        # 数据源诚实标注：另一个项目的 SQLite 副本 ≠ 生产库
        norm_path = erp_path.replace("\\", "/").lower()
        is_bridge_copy = (
            "/pythonproject_zr_aicoding/" in norm_path
            or "erp_quality_bridge" in norm_path
            or "erp_bridge" in norm_path
        )
        if is_bridge_copy:
            data_source = "桥接副本（pythonProject_zr_aicoding，非生产库）"
        elif (env_info.get("env") or "").lower() == "prod":
            data_source = "MES/ERP生产库"
        else:
            data_source = "配置库（测试环境）"

        warnings = list(env_info.get("warnings") or [])
        if is_bridge_copy and (env_info.get("env") or "").lower() == "prod":
            warnings.append(
                "当前 QA_ENV=prod，但 ERP/MES 指向 pythonProject_zr_aicoding 的 SQLite 副本；"
                "对外称生产环境前须改为生产库连接器"
            )

        self._ok({
            "connected": s.is_connected(),
            "erp_connected": bool(s.erp and s.erp.connected),
            "mes_connected": bool(s.mes and s.mes.connected),
            "erp_db": erp_path,
            "mes_db": str(getattr(s.mes, "db_path", "") or ""),
            "kb_path": str(getattr(s.db, "db_path", "") or ""),
            "erp_mtime": erp_mtime,
            "data_source": data_source,
            "bridge_sync": bridge_sync,
            "project_id": env_info.get("project_id"),
            "project_name": env_info.get("project_name"),
            "env": env_info.get("env"),
            "env_label": env_info.get("env_label"),
            "warnings": warnings,
            "using_bridge_fallback": bool(resolved.get("using_bridge_fallback")) or is_bridge_copy,
            "auth_enabled": bool(API_TOKEN),
            "version": VERSION,
            "timestamp": datetime.now().isoformat()
        })

    def _sync_bridge(self):
        """从生产 MES/ERP 业务表刷新品质桥接表（跟随动态数据）。"""
        s = self.system
        db_path = str(getattr(s.erp, "db_path", "") or getattr(s.mes, "db_path", "") or "")
        if not db_path:
            self._err(500, "未配置生产库路径")
            return
        try:
            from app.bridge_sync_worker import run_bridge_sync
            stats = run_bridge_sync(db_path)
            self._ok({
                "message": "生产桥接同步完成",
                "db": db_path,
                "stats": stats,
            })
        except Exception as e:
            logger.exception("桥接同步失败: %s", e)
            self._err(500, "桥接同步失败，请查看服务端日志")

    def _project_env_get(self):
        """只读：当前工程激活的测试/生产环境（改项目根 .env 的 QA_ENV）"""
        info = describe_for_api()
        s = self.system
        info["connected"] = s.is_connected()
        info["erp_connected"] = bool(s.erp and s.erp.connected)
        info["mes_connected"] = bool(s.mes and s.mes.connected)
        self._ok(info)

    def _defect_mapping_get(self, params=None):
        """
        ERP→GraphRAG 缺陷映射（只读）。
        查询参数 erp：可选，逗号分隔 ERP 缺陷名，返回对齐试算结果。
        维护方式：编辑 config/defect_mapping.json。
        """
        from config.defect_mapping import describe_mapping_for_api, map_erp_defects
        info = describe_mapping_for_api()
        params = params or {}
        erp_q = (params.get("erp", [""])[0] or "").strip()
        if erp_q:
            names = [x.strip() for x in erp_q.replace("，", ",").split(",") if x.strip()]
            try:
                client = get_graphrag_client()
                info["trial"] = client.align_erp_defects(names)
            except Exception:
                info["trial"] = map_erp_defects(names)
        self._ok(info)

    def _dashboard(self, params=None):
        """仪表板接口，支持fiscal_period参数"""
        params = params or {}
        fiscal_period = params.get("fiscal_period", [None])[0]
        self._ok(self.system.get_dashboard(fiscal_period))

    def _health_report(self, params):
        part = (params.get("part", [""])[0] or "").strip()
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        # 料号可空：默认全厂当月 FiscalPeriod 体检（ERP/MES 实数）
        self._ok(self.system.get_comprehensive_health(
            part or None, start, end, period=period, fiscal_period=fiscal_period
        ))

    def _scrap(self, params):
        part = params.get("part", [""])[0]
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        self._ok(self.system.get_scrap_analysis(
            part or None, start, end, period, fiscal_period=fiscal_period
        ))

    def _copq(self, params):
        """
        客诉 / COPQ 金额分析。
        查询参数：start/end 日期，part 料号/产品码，customer 客户编码。
        适用页面：客诉与质量成本（COPQ）。
        """
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        part = (params.get("part", [""])[0] or "").strip() or None
        customer = (params.get("customer", [""])[0] or "").strip() or None
        self._ok(self.system.get_copq_analysis(start, end, part_number=part, customer_code=customer))

    def _spc(self, params):
        """
        SPC 控制图列表与详情。
        查询参数：id 图主键，code 图编码，process 工序名（模糊）。
        适用页面：SPC 统计过程控制。
        """
        chart_id = params.get("id", [None])[0]
        code = (params.get("code", [""])[0] or "").strip() or None
        process = (params.get("process", [""])[0] or "").strip() or None
        try:
            chart_id = int(chart_id) if chart_id not in (None, "") else None
        except Exception:
            chart_id = None
        self._ok(self.system.get_spc_analysis(chart_id=chart_id, chart_code=code, process_name=process))

    def _ipqc(self, params):
        """
        IPQC 检验分析（领导决策向只读）。
        查询参数：days 近N日（默认30），process 工序名（模糊）。
        适用页面：IPQC检验分析；用于看清检验合格/积压并下钻改善。
        """
        days = params.get("days", ["30"])[0]
        process = (params.get("process", [""])[0] or "").strip() or None
        try:
            days = int(days or 30)
        except Exception:
            days = 30
        self._ok(self.system.get_ipqc_analysis(day_count=days, process_name=process))

    def _periods(self, params):
        """获取可用周期列表（返回FiscalPeriod周期码）"""
        part = params.get("part", [""])[0]
        result = {"periods": [], "fiscal_periods": [], "part": part}

        # 使用system的erp（因为handler中的self.erp可能未初始化）
        erp = getattr(self, 'erp', None) or getattr(self.system, 'erp', None)
        if not erp:
            # 返回默认周期
            from datetime import datetime
            now = datetime.now()
            result["periods"] = [now.strftime('%Y-%m')]  # 旧格式兼容
            result["fiscal_periods"] = [now.strftime('%Y%m')]  # ERP周期码格式
            self._ok(result)
            return

        try:
            where = "InspectionResult = 'Scrapped'"
            if part:
                where += f" AND MfgPartCode LIKE '%{part}%'"
            
            # 返回FiscalPeriod（ERP系统标准周期码，格式YYYYMM）
            r = erp.execute(f"SELECT DISTINCT TOP 24 FiscalPeriod FROM View_P_Inspection WHERE {where} ORDER BY FiscalPeriod DESC")
            if r.get("success"):
                fiscal_periods = [row["FiscalPeriod"] for row in r.get("data", []) if row.get("FiscalPeriod")]
                result["fiscal_periods"] = fiscal_periods
                # 转换旧格式YYYY-MM（兼容前端）
                result["periods"] = [fp[:4] + "-" + fp[4:6] for fp in fiscal_periods]
        except Exception as e:
            logger.error(f"获取周期列表失败: {e}")
        self._ok(result)

    def _export_report(self, body):
        """导出品质体检报告（PDF格式）"""
        part = body.get("part", "")
        start = body.get("start", "")
        end = body.get("end", "")
        period = body.get("period", "")
        fiscal_period = body.get("fiscal_period", "")

        if not part:
            self._err(400, "缺少料号")
            return

        # 获取体检报告
        health = self.system.get_comprehensive_health(part, start, end, period, fiscal_period)

        # 导入PDF生成器
        try:
            from app.pdf_generator import generate_health_check_pdf
        except ImportError:
            self._err(500, "PDF生成模块未找到")
            return

        # 生成PDF文件
        try:
            pdf_path = generate_health_check_pdf(
                health,
                part,
                fiscal_period=fiscal_period or period.replace('-', '') if period else None,
                output_dir=str(OUTPUT_DIR),
            )

            filename = os.path.basename(pdf_path)

            self._ok({
                "success": True,
                "format": "pdf",
                "filename": filename,
                "filepath": pdf_path,
                "download_url": f"/download/{filename}",
                "message": f"报告已导出: {filename}"
            })
        except Exception as e:
            logger.exception("PDF生成失败: %s", e)
            self._err(500, "PDF生成失败，请查看服务端日志")

    def _export_excel(self, params=None):
        """
        导出分析数据为 Excel。
        查询参数：module=scrap|ipqc|spc|copq|health|process，以及各模块原有筛选参数。
        适用页面：报废 / IPQC / SPC / 客诉COPQ / 体检 / 工序。
        """
        import urllib.parse
        params = params or {}
        module = (params.get("module", [""])[0] or "").strip().lower()
        if module not in ("scrap", "ipqc", "spc", "copq", "health", "process"):
            self._err(400, "module 须为 scrap|ipqc|spc|copq|health|process")
            return
        try:
            from app.excel_exporter import export_module_excel
        except ImportError as e:
            self._err(500, f"Excel 导出模块未找到: {e}")
            return

        part = (params.get("part", [""])[0] or "").strip()
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        extra = {"part": part, "start": start, "end": end, "period": period, "fiscal_period": fiscal_period}

        try:
            if module == "scrap":
                data = self.system.get_scrap_analysis(
                    part or None, start, end, period, fiscal_period=fiscal_period
                )
            elif module == "ipqc":
                days = params.get("days", ["30"])[0]
                process = (params.get("process", [""])[0] or "").strip() or None
                try:
                    days = int(days or 30)
                except Exception:
                    days = 30
                data = self.system.get_ipqc_analysis(days, process_name=process)
            elif module == "spc":
                chart_id = params.get("id", [None])[0]
                code = (params.get("code", [""])[0] or "").strip() or None
                process = (params.get("process", [""])[0] or "").strip() or None
                try:
                    chart_id = int(chart_id) if chart_id not in (None, "") else None
                except Exception:
                    chart_id = None
                data = self.system.get_spc_analysis(
                    chart_id=chart_id, chart_code=code, process_name=process
                )
            elif module == "copq":
                customer = (params.get("customer", [""])[0] or "").strip() or None
                data = self.system.get_copq_analysis(
                    start, end, part_number=part or None, customer_code=customer
                )
            elif module == "health":
                data = self.system.get_comprehensive_health(
                    part or None, start, end, period=period, fiscal_period=fiscal_period
                )
            else:  # process
                data = self.system.get_process_yield(part or None, fiscal_period=fiscal_period or None)

            xlsx_path = export_module_excel(module, data or {}, str(OUTPUT_DIR), extra=extra)
            filename = os.path.basename(xlsx_path)
            self._ok({
                "success": True,
                "format": "xlsx",
                "module": module,
                "filename": filename,
                "filepath": xlsx_path,
                "download_url": f"/download/{urllib.parse.quote(filename)}",
                "message": f"已导出 Excel: {filename}",
            })
        except Exception as e:
            logger.error(f"Excel 导出失败: {e}")
            self._err(500, f"Excel 导出失败: {e}")

    def _resolve_8d_report(self, report_id=None, report_no=None):
        """按 id 或 report_no 取 8D 报告。"""
        rid = (report_id or "").strip()
        rno = (report_no or "").strip()
        if rid:
            r = self.system.db.get_8d(rid)
            if r:
                return r
        if rno:
            rows = self.system.db.query(
                "SELECT * FROM eightd_reports WHERE report_no=? LIMIT 1", (rno,)
            )
            if rows:
                return rows[0]
        return None

    def _8d_export(self, params=None):
        """
        导出 8D 报告为 PDF（GET）。
        查询参数：id（优先）或 report_no。
        """
        params = params or {}
        rid = (params.get("id", [""])[0] or "").strip()
        rno = (params.get("report_no", [""])[0] or "").strip()
        self._do_8d_export(rid, rno)

    def _8d_export_body(self, body=None):
        """导出 8D 报告为 PDF（POST）。请求体：id 或 report_no。"""
        body = body or {}
        self._do_8d_export(
            (body.get("id") or "").strip(),
            (body.get("report_no") or "").strip(),
        )

    def _do_8d_export(self, report_id: str, report_no: str):
        """生成 8D PDF 并返回下载地址。"""
        if not report_id and not report_no:
            self._err(400, "缺少 id 或 report_no")
            return
        report = self._resolve_8d_report(report_id, report_no)
        if not report:
            self._err(404, "未找到该 8D 报告")
            return
        try:
            from app.pdf_generator import generate_8d_pdf
        except ImportError:
            self._err(500, "PDF生成模块未找到")
            return
        try:
            import urllib.parse
            pdf_path = generate_8d_pdf(report, output_dir=str(OUTPUT_DIR))
            filename = os.path.basename(pdf_path)
            self._ok({
                "format": "pdf",
                "filename": filename,
                "filepath": pdf_path,
                "download_url": f"/download/{urllib.parse.quote(filename)}",
                "report_no": report.get("report_no") or "",
                "part_number": report.get("part_number") or "",
                "message": f"8D 报告已导出: {filename}",
            })
        except Exception as e:
            logger.error(f"8D PDF生成失败: {e}")
            self._err(500, f"8D PDF生成失败: {e}")

    def _download_file(self, filename):
        """文件下载（output/ 下的 PDF / Excel）"""
        import urllib.parse
        import mimetypes

        filename_decoded = urllib.parse.unquote(filename)
        lower = filename_decoded.lower()
        if not (lower.endswith(".pdf") or lower.endswith(".xlsx") or lower.endswith(".csv")):
            self._err(403, "只允许下载 PDF / Excel / CSV")
            return

        output_root = str(OUTPUT_DIR.resolve())
        filepath = os.path.normpath(os.path.join(output_root, filename_decoded))

        # 安全检查：防止目录遍历（仅允许 output/ 下，比较带分隔符的绝对前缀）
        if not filepath.startswith(output_root + os.sep) or not os.path.exists(filepath):
            self._err(404, "文件不存在")
            return

        try:
            with open(filepath, "rb") as f:
                content = f.read()

            ctype = mimetypes.guess_type(filename_decoded)[0] or "application/octet-stream"
            if lower.endswith(".xlsx"):
                ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif lower.endswith(".pdf"):
                ctype = "application/pdf"

            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{urllib.parse.quote(filename_decoded)}",
            )
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.exception("文件下载失败: %s", e)
            self._err(500, "文件下载失败")

    def _process(self, params):
        """工序良率；可选 part、fiscal_period（按当月工单关联 MES）。"""
        part = params.get("part", [""])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        self._ok(self.system.get_process_yield(part or None, fiscal_period=fiscal_period or None))

    def _work_orders(self, params):
        part = params.get("part", [""])[0]
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        self._ok(self.system.get_work_orders(part or None, start, end))

    def _trace(self, params):
        """一键追溯：ERP 工单/MRB + MES 工序良率/不良/检验履历"""
        part = (params.get("part", [""])[0] or "").strip()
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        if not part:
            self._err(400, "缺少part参数")
            return

        # 周期：YYYY-MM → YYYYMM；优先 fiscal_period
        if not fiscal_period and period:
            fiscal_period = str(period).replace("-", "")
            if len(fiscal_period) == 6:
                start = end = None  # 用周期码时不再叠日期

        scrap = self.system.get_scrap_analysis(
            part, start, end, period=period, fiscal_period=fiscal_period
        )
        proc = self.system.get_process_yield(part, fiscal_period=fiscal_period)
        wo = self.system.get_work_orders(part, start, end, fiscal_period=fiscal_period)

        scrap_pcs = int(scrap.get("total_scrap_pcs") or 0)
        stock_pcs = int(scrap.get("total_in_stock_pcs") or 0)
        # 与总览统一：投入=入库+报废，报废率=报废/(入库+报废)
        input_base = scrap_pcs + stock_pcs
        scrap_rate = round(scrap_pcs * 100.0 / input_base, 2) if input_base > 0 else float(scrap.get("scrap_rate") or 0)
        pass_qty = stock_pcs
        pass_rate = round(100.0 - scrap_rate, 2) if input_base > 0 else 0

        self._ok({
            "success": True,
            "part_number": part,
            "date_range": f"{start or '不限'} ~ {end or '不限'}",
            "fiscal_period": fiscal_period,
            "overview": {
                "total_wo": wo.get("total_wo", 0),
                "total_input": input_base or wo.get("total_input_pcs", 0),
                "total_reject": scrap_pcs,
                "reject_rate": scrap_rate,
                "mrb_reject": scrap_pcs,
                "pass_qty": pass_qty,
                "pass_rate": pass_rate,
                "inspect_pcs": stock_pcs,
                "wo_reject_pcs": wo.get("total_reject_pcs", 0),
                "mes_process_count": len(proc.get("process_yield") or []),
                "mes_inspect_count": proc.get("inspect_count", 0),
                "mes_avg_yield": proc.get("avg_yield", 0),
            },
            "work_orders": (wo.get("work_orders") or [])[:50],
            "mrb": {
                "defect_top10": scrap.get("defect_top10") or [],
                "process_scrap": scrap.get("process_scrap") or [],
                "scrap_rate": scrap_rate,
                "total_in_stock_pcs": stock_pcs,
                "total_reject_pcs": scrap_pcs,
                "monthly_trend": scrap.get("monthly_trend") or [],
            },
            "mes": {
                "process_yield": proc.get("process_yield") or [],
                "process_defects": proc.get("process_defects") or [],
                "inspect_records": proc.get("inspect_records") or [],
                "total_process": len(proc.get("process_yield") or []),
                "inspect_count": proc.get("inspect_count", 0),
                "avg_yield": proc.get("avg_yield", 0),
                "min_yield": proc.get("min_yield", 0),
                "wo_matched": proc.get("wo_matched", 0),
            },
            "material": self.system.get_material_iqc_for_part(part, day_count=180),
        })

    def _8d_list(self, params):
        part = params.get("part", [""])[0]
        status = params.get("status", [""])[0]
        self._ok({
            "reports": self.system.db.get_8d_list(part or None, status or None),
            "policy": "D2 指标来自 ERP/MES；D4/D5 仅引用已关闭历史或标注待填写",
        })

    def _8d_get(self, params):
        """获取单份 8D；解析 evidence / history_refs / knowledge_refs 供编辑页引用知识库。"""
        rid = params.get("id", [""])[0]
        if not rid:
            self._err(400, "缺少id参数")
            return
        r = self.system.db.get_8d(rid)
        if r:
            for jk, ok in (
                ("defects_json", "defects"),
                ("evidence_json", "evidence"),
                ("history_refs_json", "history_refs"),
                ("knowledge_refs_json", "knowledge_refs"),
            ):
                if r.get(jk):
                    try:
                        r[ok] = json.loads(r[jk])
                    except Exception:
                        r[ok] = {} if ok in ("evidence", "knowledge_refs") else []
        self._ok({"report": r})

    def _serve_static(self, path):
        """静态文件服务（web/ 目录）"""
        import os
        web_root = str(WEB_DIR.resolve())
        safe_path = os.path.normpath(os.path.join(web_root, path.lstrip("/")))
        if not safe_path.startswith(web_root + os.sep):
            self._err(403, "禁止访问")
            return
        try:
            with open(safe_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            if path.endswith(".css"):
                self.send_header("Content-Type", "text/css; charset=utf-8")
            elif path.endswith(".js"):
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
            else:
                self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._err(404, "文件不存在")
        except Exception as e:
            logger.exception("静态文件错误 %s: %s", path, e)
            self._err(500, "静态文件读取失败")

    def _8d_generate(self, params):
        """
        按 ERP/MES 实据生成 8D，并自动引用自主学习知识库（GraphRAG）根因/对策。
        查询参数：part（必填）、start/end 或 fiscal_period/period。
        """
        part = params.get("part", [""])[0]
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        if not part:
            self._err(400, "缺少part参数")
            return
        r = self.system.generate_8d_report(
            part, start, end, period=period, fiscal_period=fiscal_period
        )
        if r.get("success") is False:
            self._err(400, r.get("error") or "无法生成8D")
            return
        hist_n = r.get("history_count") or len(r.get("history_refs") or [])
        kb_n = r.get("knowledge_hit_count") or 0
        parts = ["8D已基于 ERP/MES 实据生成"]
        align = r.get("defect_alignment") or {}
        if align.get("mapped_count"):
            parts.append(f"缺陷对齐 {align.get('mapped_count')} 项")
        if align.get("unmapped_count"):
            parts.append(f"未对齐 {align.get('unmapped_count')} 项（可补 defect_mapping.json）")
        if hist_n:
            parts.append(f"引用历史关闭方案 {hist_n} 条")
        if kb_n:
            parts.append(f"引用知识库 GraphRAG {kb_n} 条（D4/D5 须现场复核）")
        elif not hist_n:
            parts.append("暂无历史/知识库命中，D4/D5 待现场填写或手动检索知识库")
        self._ok({
            "report": r,
            "message": "；".join(parts),
            "purged_invalid": r.get("purged_invalid") or [],
            "defect_alignment": align,
        })

    def _8d_knowledge_lookup(self, params):
        """
        8D 编写时按需检索自主学习知识库（GraphRAG）。
        查询参数：
          - defect：不良类型（可逗号分隔多个）
          - process：工序名（可选，缺陷未命中时作补充）
        """
        defect_raw = (params.get("defect", [""])[0] or "").strip()
        process_raw = (params.get("process", [""])[0] or "").strip()
        defects = [x.strip() for x in re.split(r"[,，、;；\s]+", defect_raw) if x.strip()]
        processes = [x.strip() for x in re.split(r"[,，、;；\s]+", process_raw) if x.strip()]
        if not defects and not processes:
            self._err(400, "请提供 defect 或 process 参数")
            return
        try:
            pack = get_graphrag_client().lookup_for_8d(
                defect_names=defects, process_names=processes
            )
        except Exception as e:
            logger.exception("8D 知识库检索失败: %s", e)
            self._err(502, "无法连接知识库服务，请稍后重试（详见服务端日志）")
            return
        self._ok({
            "knowledge_refs": pack,
            "source": pack.get("source"),
            "source_url": pack.get("source_url") or GRAPHRAG_BASE_URL,
            "message": (
                f"命中 {pack.get('hit_count') or 0} 条知识"
                f"（根因 {pack.get('cause_count') or 0} / 对策 {pack.get('measure_count') or 0}）"
                if pack.get("hit_count")
                else "知识库未命中，请换用图谱中的精确缺陷名"
            ),
        })

    def _8d_save(self, body):
        if not body.get("id"):
            self._err(400, "缺少id字段")
            return
        if "defects" in body:
            body["defects_json"] = json.dumps(body.pop("defects"), ensure_ascii=False)
        if "evidence" in body and not body.get("evidence_json"):
            body["evidence_json"] = json.dumps(body.pop("evidence"), ensure_ascii=False, default=str)
        if "knowledge_refs" in body and not body.get("knowledge_refs_json"):
            body["knowledge_refs_json"] = json.dumps(
                body.pop("knowledge_refs"), ensure_ascii=False
            )
        # 关闭时回写已验证知识
        promoted = 0
        prev = self.system.db.get_8d(body["id"])
        status = body.get("status") or (prev or {}).get("status")
        if status == "closed":
            body["closed_at"] = body.get("closed_at") or datetime.now().isoformat()
            merged = {**(prev or {}), **body}
            promoted = self.system.db.promote_8d_knowledge(merged)
        # 只写表字段
        allow = {
            "id", "report_no", "part_number", "customer", "problem",
            "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
            "defects_json", "evidence_json", "history_refs_json",
            "knowledge_refs_json", "data_policy",
            "status", "severity", "created_by", "created_at", "updated_at", "closed_at",
        }
        row = {k: v for k, v in body.items() if k in allow}
        self.system.db.save_8d(row)
        self._ok({
            "message": "保存成功" + (f"；已回写验证知识 {promoted} 条" if promoted else ""),
            "promoted_knowledge": promoted,
        })

    def _8d_delete(self, params):
        rid = params.get("id", [""])[0]
        if not rid:
            self._err(400, "缺少id")
            return
        self.system.db.delete_8d(rid)
        self._ok({"message": "已删除", "id": rid})

    def _8d_purge(self, params=None):
        purged = self.system.db.purge_invalid_8d()
        self._ok({"message": f"已清理无效报告 {len(purged)} 条", "purged": purged})

    def _alerts(self, params):
        ack = params.get("acknowledged", [None])[0]
        acknowledged = None if ack is None or ack == "" else (ack == "true" or ack == "1")
        alert_type = params.get("type", [None])[0] or None
        level = params.get("level", [None])[0] or None
        alerts = self.system.db.get_alerts(
            acknowledged=acknowledged, alert_type=alert_type, level=level, limit=200
        )
        # 卡片汇总：含全部确认状态，不受 ack/level 影响（类型筛选仍生效）
        # 未确认+已确认=全部；严重+关注=全部
        base = self.system.db.get_alerts(
            acknowledged=None, alert_type=alert_type, level=None, limit=200
        )
        active = [a for a in base if not int(a.get("acknowledged") or 0)]
        acked = [a for a in base if int(a.get("acknowledged") or 0)]
        self._ok({
            "alerts": alerts,
            "list_total": len(alerts),
            "total": len(base),
            "active": len(active),
            "acked": len(acked),
            "critical": len([a for a in base if a.get("level") == "critical"]),
            "warning": len([a for a in base if a.get("level") == "warning"]),
        })

    def _alerts_scan(self, params):
        """从 ERP/MES 扫描并刷新预警"""
        fiscal_period = params.get("fiscal_period", [None])[0]
        result = self.system.scan_quality_alerts(fiscal_period)
        self._ok(result)

    def _ack_alert(self, params):
        aid = params.get("id", [""])[0]
        by = params.get("by", ["User"])[0]
        if not aid:
            self._err(400, "缺少id参数")
            return
        self.system.db.ack_alert(aid, by)
        self._ok({"message": "预警已确认", "acked": aid})

    def _ack_alerts_batch(self, params):
        ids_raw = params.get("ids", [""])[0] or ""
        by = params.get("by", ["User"])[0]
        ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
        if not ids:
            self._err(400, "缺少ids参数")
            return
        n = self.system.db.ack_alerts_batch(ids, by)
        self._ok({"message": f"已确认 {n} 条", "acked": n})

    def _alert_create(self, body):
        # 禁止无数据源的手工造数；预警须由 /api/alerts/scan 从 ERP/MES 生成
        self._err(403, "禁止手工编造预警指标，请使用 /api/alerts/scan 从 ERP/MES 扫描生成")

    def _knowledge(self, params):
        """
        自主学习知识库（权威数据源：GraphRAG 生产环境）

        查询参数：
          - defect：不良类型关键词（模糊匹配图谱缺陷名）
          - limit：返回条数，默认 40，最大 80

        返回：
          - patterns：缺陷模式（根因/对策来自 Neo4j；occurrence 为生产图谱查阅次数）
          - processes：FMEA 工序知识卡片
          - stats：图谱统计 + 本地 8D/预警数量
          - source / source_url / evidence_policy：溯源说明
        """
        defect_type = (params.get("defect", [""])[0] or "").strip()
        try:
            limit = int(params.get("limit", ["40"])[0] or 40)
        except Exception:
            limit = 40
        limit = max(1, min(limit, 80))

        local_stats = self.system.db.get_learning_stats() or {}
        gr = get_graphrag_client()
        try:
            bundle = gr.build_knowledge_bundle(search=defect_type, limit=limit)
        except Exception as e:
            logger.error("GraphRAG 知识库拉取失败: %s", e)
            self._ok({
                "patterns": [],
                "processes": [],
                "stats": {
                    "total_learning": local_stats.get("total_learning", 0),
                    "defect_patterns": 0,
                    "reports_8d": local_stats.get("reports_8d", 0),
                    "alerts_total": local_stats.get("alerts_total", 0),
                },
                "source": "GraphRAG/FMEA",
                "source_url": GRAPHRAG_BASE_URL,
                "connected": False,
                "error": f"无法连接 GraphRAG 生产环境（{GRAPHRAG_BASE_URL}）：{e}",
                "evidence_policy": "根因/对策仅来自 GraphRAG；连接失败时不返回本地编造条目",
            })
            return

        stats = dict(bundle.get("stats") or {})
        # 8D / 预警仍来自本系统落库（真实业务单据），与图谱并列展示
        stats["reports_8d"] = local_stats.get("reports_8d", 0)
        stats["alerts_total"] = local_stats.get("alerts_total", 0)
        stats["local_learning"] = local_stats.get("total_learning", 0)

        # 若本地 ERP 学习库有同名缺陷出现次数，叠加标注（不覆盖图谱查阅次数）
        local_patterns = {
            (p.get("defect_type") or ""): int(p.get("occurrence") or 0)
            for p in (self.system.db.get_defect_patterns(None) or [])
        }
        patterns = []
        for p in bundle.get("patterns") or []:
            item = dict(p)
            erp_occ = local_patterns.get(item.get("defect_type") or "", 0)
            if erp_occ:
                item["erp_occurrence"] = erp_occ
                item["evidence"] = (
                    f"{item.get('evidence', '')}; ERP学习累计出现={erp_occ}"
                )
            patterns.append(item)

        self._ok({
            "patterns": patterns,
            "processes": bundle.get("processes") or [],
            "stats": stats,
            "source": bundle.get("source"),
            "source_url": bundle.get("source_url") or GRAPHRAG_BASE_URL,
            "connected": True,
            "evidence_policy": bundle.get("evidence_policy"),
            "meta": bundle.get("meta") or {},
        })

    def _history(self, params=None):
        """
        体检报告历史列表（真实落库快照摘要）。
        查询参数：part（料号模糊）、grade（等级）、limit（默认50）。
        """
        params = params or {}
        part = (params.get("part", [""])[0] or "").strip()
        grade = (params.get("grade", [""])[0] or "").strip().upper()
        try:
            limit = int(params.get("limit", ["50"])[0] or 50)
        except Exception:
            limit = 50
        reports = self.system.db.get_health_reports(
            part_number=part or None, grade=grade or None, limit=limit
        )
        with_ev = sum(1 for r in reports if r.get("has_evidence"))
        self._ok({
            "reports": reports,
            "total": len(reports),
            "with_evidence": with_ev,
            "policy": "列表指标来自生成时 ERP/MES 快照；无快照的旧记录标记 has_evidence=false",
        })

    def _history_get(self, params):
        """
        体检报告详情（完整实据快照 + 发现项 + 建议）。
        查询参数：id（报告 id，或 report_no）。
        """
        rid = (params.get("id", [""])[0] or "").strip()
        if not rid:
            self._err(400, "缺少id参数")
            return
        r = self.system.db.get_health_report(rid)
        if not r:
            self._err(404, "未找到该体检报告")
            return
        ev = r.get("evidence") or {}
        self._ok({
            "report": r,
            "has_evidence": bool(
                ev.get("has_scrap") or ev.get("has_mes")
                or int(ev.get("scrap_pcs") or 0) > 0
                or int(ev.get("mes_inspect_count") or 0) > 0
            ),
            "policy": r.get("data_policy") or "snapshot from ERP/MES at generation time",
        })

    def _history_purge(self, params=None):
        """清理无实据空壳历史记录。"""
        purged = self.system.db.purge_empty_health_reports()
        self._ok({"message": f"已清理空壳历史 {len(purged)} 条", "purged": purged})

    def _manual_learn(self, body):
        learn_type = body.get("type", "manual")
        content = body.get("content", "")
        insight = body.get("insight", "")
        self.system.db.add_learning(learn_type, "manual", content, insight, 0.9)
        self._ok({"message": "学习记录已保存"})

    # ─── 响应工具 ────────────────────────────────────

    def _cors_headers(self):
        origin = CORS_ORIGIN
        if not origin:
            # 默认同源：不发送任何跨域头
            return
        if origin == "*":
            self.send_header("Access-Control-Allow-Origin", "*")
        else:
            req_origin = self.headers.get("Origin") or origin
            self.send_header("Access-Control-Allow-Origin", req_origin if origin == "reflect" else origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token, Authorization")

    def _ok(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-System-Version", VERSION)
        self.end_headers()
        self.wfile.write(json.dumps({"success": True, **data}, ensure_ascii=False,
                                    cls=SmartEncoder).encode("utf-8"))

    def _err(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({"success": False, "error": msg},
                                    ensure_ascii=False).encode("utf-8"))

    def _err_internal(self, exc: Exception, where: str = ""):
        """500：完整堆栈进日志；仅 QA_DEBUG_ERRORS=1 时回传原文（避免经反代泄露路径）。"""
        logger.exception("内部错误 %s: %s", where, exc)
        if DEBUG_ERRORS:
            self._err(500, f"服务内部错误: {exc}")
        else:
            self._err(500, "服务内部错误，请查看服务端日志")

    def log_message(self, format, *args):
        # 不把令牌写进访问日志
        msg = re.sub(r"(token=)[^&\s]+", r"\1***", str(args[0] if args else ""))
        logger.info(msg)


# ═══════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════

def _enforce_prod_guard():
    """生产环境：桥接库防护 + 端口黑名单（同机勿抢 MES/ERP）。"""
    try:
        ctx = describe_for_api()
    except Exception as e:
        logger.warning("环境描述失败: %s", e)
        ctx = {}
    env = ctx.get("env") or ""
    if env == "prod":
        resolved = ctx.get("resolved") or {}
        for w in list(ctx.get("warnings") or []):
            logger.warning("[ENV] %s", w)
        if resolved.get("using_bridge_fallback") and not PROD_ALLOW_BRIDGE:
            msg = (
                "生产环境仍指向桥接库，且未设置 QA_PROD_ALLOW_BRIDGE=1。"
                "请配置独立 QA_PROD_ERP_DB / QA_PROD_MES_DB 后重启。"
            )
            logger.error(msg)
            print(f"[FATAL] {msg}", flush=True)
            sys.exit(2)
    # 端口黑名单（prod 更严：须高位端口）
    try:
        from deploy.ports import is_forbidden_api_port, QA_DEFAULT_API_PORT
        reason = is_forbidden_api_port(API_PORT, strict_prod=(env == "prod"))
        if reason:
            logger.error("[PORT] %s", reason)
            print(f"[FATAL] {reason}", flush=True)
            print(f"        请在 .env 设置 QA_API_PORT={QA_DEFAULT_API_PORT}", flush=True)
            sys.exit(2)
    except Exception as e:
        logger.warning("端口策略检查跳过: %s", e)


def _enforce_bind_guard():
    """非回环监听必须配置 API 令牌；否则拒绝启动（避免无鉴权裸奔）。"""
    if _is_loopback(API_HOST):
        if API_TOKEN:
            print("[OK] API 令牌鉴权已启用（本机监听）", flush=True)
        return
    if not API_TOKEN:
        msg = (
            f"监听 {API_HOST} 但未设置 QA_API_TOKEN：拒绝启动。"
            "请在 .env 设置访问令牌，或改用 QA_API_HOST=127.0.0.1。"
        )
        logger.error(msg)
        print(f"[FATAL] {msg}", flush=True)
        sys.exit(2)
    print(f"[OK] API 令牌鉴权已启用（监听 {API_HOST}）", flush=True)


def main():
    print("=" * 60, flush=True)
    print("  PCB智能品质分析系统 v2.0", flush=True)
    print("  具备自主学习能力的品质管理平台", flush=True)
    print("=" * 60, flush=True)

    _enforce_prod_guard()
    _enforce_bind_guard()

    system = PCBQualitySystem()
    print("正在连接数据库...", flush=True)
    system.connect()

    if not system.is_connected():
        print("[警告] ERP数据库未连接，部分功能不可用", flush=True)
    else:
        print("[OK] ERP数据库连接成功", flush=True)

    if system.mes and system.mes.connected:
        print("[OK] MES数据库连接成功", flush=True)
    else:
        print("[警告] MES数据库未连接", flush=True)

    PCBQualityHandler.system = system
    # 生产并发：ThreadingHTTPServer
    try:
        server = ThreadingHTTPServer((API_HOST, API_PORT), PCBQualityHandler)
    except AttributeError:
        class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True
        server = _ThreadedHTTPServer((API_HOST, API_PORT), PCBQualityHandler)
    server.daemon_threads = True

    print(flush=True)
    print(f"API服务已启动: http://{API_HOST}:{API_PORT}", flush=True)
    print(f"Web界面:       http://127.0.0.1:{API_PORT}/pcb_quality_system.html", flush=True)
    print(flush=True)
    try:
        _ctx = describe_for_api()
        print(f"当前项目: {_ctx.get('project_name')} | 环境: {_ctx.get('env_label')}", flush=True)
        print(f"  ERP: {_ctx.get('resolved', {}).get('erp_db_path')}", flush=True)
        print(f"  MES: {_ctx.get('resolved', {}).get('mes_db_path')}", flush=True)
        print(f"  KB : {_ctx.get('resolved', {}).get('kb_path')}", flush=True)
        for w in (_ctx.get("warnings") or []):
            print(f"  [WARN] {w}", flush=True)
        # 生产：后台周期性从 MES/ERP 生产库刷新桥接表（动态数据）
        if (_ctx.get("env") or "").lower() == "prod":
            try:
                from app.bridge_sync_worker import start_bridge_sync_worker
                erp_live = (_ctx.get("resolved") or {}).get("erp_db_path")
                if start_bridge_sync_worker(erp_live):
                    print(f"  桥接同步: 已启用（跟生产库动态更新） db={erp_live}", flush=True)
            except Exception as e:
                print(f"  [WARN] 桥接同步线程未启动: {e}", flush=True)
    except Exception:
        pass
    print(flush=True)
    print("主要接口:", flush=True)
    print("  GET  /api/health          - 服务状态（含当前工程环境）", flush=True)
    print("  POST /api/sync/bridge     - 手动刷新生产桥接表", flush=True)
    print("  GET  /api/export/excel    - 分析页 Excel 导出", flush=True)
    print(f"  GraphRAG 数据源:         {GRAPHRAG_BASE_URL}", flush=True)
    if API_TOKEN:
        print("  访问方式: 打开 /pcb_quality_system.html?token=<QA_API_TOKEN> 一次以写入 Cookie", flush=True)
    print("完整部署说明见 deploy/DEPLOY.md", flush=True)
    print("按 Ctrl+C 停止服务", flush=True)
    print("=" * 60, flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止", flush=True)


if __name__ == "__main__":
    main()
