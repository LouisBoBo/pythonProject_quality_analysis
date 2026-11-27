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
import uuid
import difflib
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
    MAX_BODY_BYTES,
    TRUST_PROXY,
    ADMIN_ROLES,
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
    EIGHT_D_API_BASE,
    EIGHT_D_API_KEY,
    EIGHT_D_API_TIMEOUT,
    EIGHT_D_API_ENV,
    ERP_AUTH_BASE,
    ERP_AUTH_TIMEOUT,
    REQUIRE_LOGIN,
    SESSION_TTL_SECONDS,
    COOKIE_SECURE_MODE,
    LOGIN_ENTERPRISES,
    OUTPUT_RETENTION_DAYS,
)
from config.process_catalog import (  # noqa: E402
    STANDARD_PROCESSES,
    aliases_including_self,
    catalog_payload as process_catalog_payload,
    merge_counts_by_process,
    normalize_process_name,
    process_sort_key,
)
from config.env_profiles import (  # noqa: E402
    ACTIVE_ENV,
    resolve_eight_d_api,
    resolve_erp_auth,
)
from graphrag_client import get_graphrag_client, reset_graphrag_client  # noqa: E402
from app.project_env import (  # noqa: E402
    describe_for_api,
    get_active_context,
    resolve_paths,
)
from app.auth_session import (  # noqa: E402
    DEFAULT_ENTERPRISES,
    check_login_rate_limit,
    clear_login_failures,
    erp_login,
    get_session_store,
    record_login_failure,
    safe_internal_next,
    session_cookie_header,
)
from app.date_window import (  # noqa: E402
    end_exclusive as _end_exclusive,
    month_end_inclusive as _month_end_inclusive,
)

BASE_DIR = PROJECT_ROOT
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
KB_PATH = KB_V2_PATH

# ─── 日志配置 ────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_handlers: list = [logging.StreamHandler()]
try:
    from logging.handlers import RotatingFileHandler

    _log_handlers.append(
        RotatingFileHandler(
            str(LOG_DIR / "pcb_quality_api.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
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


def _cleanup_output_dir() -> None:
    """按保留天数清理 output/ 下过期的导出文件（仅 .pdf/.xlsx/.csv）。"""
    if OUTPUT_RETENTION_DAYS <= 0:
        return
    cutoff = time.time() - OUTPUT_RETENTION_DAYS * 86400
    try:
        for path in Path(OUTPUT_DIR).iterdir():
            if path.suffix.lower() not in (".pdf", ".xlsx", ".csv"):
                continue
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError as e:
                logger.warning("清理过期导出失败 %s: %s", path.name, e)
    except OSError as e:
        logger.warning("扫描导出目录失败: %s", e)


def _period_parts(period: Any) -> Optional[tuple[str, str, str]]:
    """把 YYYY-MM / YYYYMM 统一成 (fiscal_period, year, month)；非法返回 None。"""
    text = str(period or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", text)
    if m:
        return f"{m.group(1)}{m.group(2)}", m.group(1), m.group(2)
    m = re.match(r"^(\d{4})(\d{2})$", text)
    if m:
        return text, m.group(1), m.group(2)
    return None


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

# 关闭校验：占位/未落实措辞（出现即不得关闭、不得回写知识）
_8D_PLACEHOLDER_MARKS = (
    "待现场确认", "待现场核实", "待排查", "待制定", "待补充", "待指定",
    "待点名", "草稿", "示例", "TODO", "XXX",
)
_8D_GENERIC_DEFECTS = {"综合不良", "待确认", "未知", "其他", "N/A", "-"}


def _8d_defect_identity(d: Any) -> tuple[str, str]:
    """统一缺陷字段命名（ERP/MES/外部分别用 defect_type/Process/process_name）。"""
    if not isinstance(d, dict):
        return "", ""
    dtype = str(
        d.get("defect_type") or d.get("DefectType") or d.get("defect_name")
        or d.get("DefectName") or ""
    ).strip()
    proc = str(
        d.get("process") or d.get("Process") or d.get("process_name")
        or d.get("ProcessName") or ""
    ).strip()
    return dtype, proc


def _8d_extract_items(text: Any) -> list[str]:
    """从 D4/D5 文本抽取人工条目；过滤占位、模板标题和未落实措辞。"""
    items = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("- 完成") or s.startswith("- 责任"):
            continue
        if s.startswith("→"):
            s = s.lstrip("→ ").strip()
        elif re.match(r"^\d+[\.、]", s):
            s = re.sub(r"^\d+[\.、]\s*", "", s)
        elif s.startswith("【确定根因】") or s.startswith("确定根因"):
            s = re.sub(r"^【?确定根因】?[:：]?\s*", "", s)
        elif s.startswith("【"):
            continue
        else:
            continue
        if not s or len(s) < 2:
            continue
        if any(m in s for m in _8D_PLACEHOLDER_MARKS) or "□" in s:
            continue
        items.append(s)
    return items


def _8d_parse_defects(report: dict) -> list[tuple[str, str]]:
    """解析报告中可用于回写知识的缺陷（去重、去通用桶、去占位）。"""
    raw = report.get("defects_json") or "[]"
    try:
        defects = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        defects = []
    out, seen = [], set()
    for d in defects if isinstance(defects, list) else []:
        dtype, proc = _8d_defect_identity(d)
        if not dtype or dtype in _8D_GENERIC_DEFECTS:
            continue
        if any(m in dtype for m in _8D_PLACEHOLDER_MARKS):
            continue
        key = (dtype, proc)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def validate_8d_closure(report: dict) -> list[str]:
    """关闭前确定性校验：根因已确认、对策可执行、D6 有实测、D8 全勾选、缺陷可追溯。"""
    blockers: list[str] = []
    part = str(report.get("part_number") or "").strip()
    if not part:
        blockers.append("缺少料号")
    elif part.startswith(("QHC-8D-", "8D-")):
        blockers.append("料号字段填了报告编号")
    if not str(report.get("problem") or "").strip():
        blockers.append("缺少问题描述")

    d4 = str(report.get("d4") or "")
    d5 = str(report.get("d5") or "")
    d6 = str(report.get("d6") or "")
    d8 = str(report.get("d8") or "")

    if "确定根因" not in d4:
        blockers.append("D4 缺少【确定根因】结论")
    if "□" in d4:
        blockers.append("D4 根因确认框未勾选")
    if any(m in d4 for m in ("待现场确认", "待排查", "待确认")):
        blockers.append("D4 仍含待确认/待排查措辞")
    if "对策待制定" in d5 or not _8d_extract_items(d5):
        blockers.append("D5 未填写可执行对策")
    if any(m in d5 for m in _8D_PLACEHOLDER_MARKS):
        blockers.append("D5 仍含占位措辞（待制定/待指定/草稿等）")
    if not ("验收" in d6 or "验证" in d6):
        blockers.append("D6 缺少验收说明")
    if re.search(r"未验证|待实测|待回填|未实测|待验证", d6):
        blockers.append("D6 验收状态仍为未验证/待实测")
    if not re.search(r"\d+(?:\.\d+)?\s*%", d6):
        blockers.append("D6 缺少实测数值（良率/报废率百分比）")
    if not re.search(r"\[[xX√✓]\]|☑", d8):
        blockers.append("D8 关闭确认项尚未勾选")
    if re.search(r"\[[ ]?\]", d8) or "□" in d8:
        blockers.append("D8 存在未勾选的确认项")
    if not _8d_parse_defects(report):
        blockers.append("缺陷明细为空或仅为通用占位（无法沉淀可追溯知识）")
    return blockers


# 关闭后再次编辑时，这些字段发生变化就必须重新通过关闭校验
_8D_CONTENT_FIELDS = (
    "part_number", "customer", "problem",
    "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
    "defects_json", "evidence_json",
)


def _8d_content_changed(body: dict, prev: Optional[dict]) -> bool:
    prev = prev or {}
    return any(
        key in body and body.get(key) != prev.get(key)
        for key in _8D_CONTENT_FIELDS
    )


# ── SPC：控制限/能力/状态重算（确定性纯函数，避免沿用 ERP 里的陈旧快照）──
_SPC_D2 = {
    2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326,
    6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078,
}


def _spc_compute(values, subgroup_size=1, subgroup_ids=None,
                 usl=None, lsl=None, ucl=None, cl=None, lcl=None) -> dict:
    """由 spc_measurements 原始测值重算控制限、Cp/Cpk 与状态。

    - 失控判定优先用 ERP 已配置的 UCL/CL/LCL；缺失时按测值重算
      （I-MR: 移动极差均值/1.128；Xbar-R: 子组极差均值/d2）。
    - 状态：样本 <5 点记 insufficient；任一点越控制限或越规格限记 alert；否则 normal。
    - Cp/Cpk 始终用当前测值重算，避免沿用过期值。
    """
    vals: list[float] = []
    ids: list[Any] = []
    for i, v in enumerate(values or []):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        vals.append(fv)
        ids.append(subgroup_ids[i] if subgroup_ids and i < len(subgroup_ids) else None)

    n_sub = max(1, int(subgroup_size or 1))
    point_groups: list[list[float]] = []
    point_ranges: list[Optional[float]] = []

    if n_sub > 1:
        groups: Dict[Any, list[float]] = {}
        order: list[Any] = []
        if len(ids) == len(vals) and any(x is not None for x in ids):
            for v, gid in zip(vals, ids):
                key = ("g", gid) if gid is not None else ("s", len(order))
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(v)
        else:
            for i in range(0, len(vals), n_sub):
                key = ("s", i // n_sub)
                groups[key] = vals[i:i + n_sub]
                order.append(key)
        point_groups = [groups[k] for k in order if groups[k]]
        points = [sum(g) / len(g) for g in point_groups]
        point_ranges = [(max(g) - min(g)) if len(g) > 1 else 0.0 for g in point_groups]
        d2 = _SPC_D2.get(min(max(n_sub, 2), 10), _SPC_D2[5])
        sigma = (sum(point_ranges) / len(point_ranges) / d2) if point_ranges else 0.0
    else:
        points = list(vals)
        point_groups = [[v] for v in vals]
        point_ranges = [None] * len(points)
        if len(vals) >= 2:
            mr = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
            sigma = (sum(mr) / len(mr)) / 1.128
        else:
            sigma = 0.0

    mean = (sum(points) / len(points)) if points else None
    if ucl is None or lcl is None or cl is None:
        limits_source = "recomputed"
        if mean is None or sigma <= 0:
            eff_ucl = eff_cl = eff_lcl = None
        else:
            se = sigma if n_sub <= 1 else sigma / (n_sub ** 0.5)
            eff_cl = mean
            eff_ucl = mean + 3.0 * se
            eff_lcl = mean - 3.0 * se
    else:
        limits_source = "stored"
        eff_ucl, eff_cl, eff_lcl = float(ucl), float(cl), float(lcl)

    cp = cpk = None
    if sigma > 0 and mean is not None and usl is not None and lsl is not None:
        cp = (float(usl) - float(lsl)) / (6.0 * sigma)
        cpk = min(
            (float(usl) - mean) / (3.0 * sigma),
            (mean - float(lsl)) / (3.0 * sigma),
        )

    ooc = sum(
        1 for p in points
        if (eff_ucl is not None and p > eff_ucl) or (eff_lcl is not None and p < eff_lcl)
    )
    out_spec = sum(
        1 for v in vals
        if (usl is not None and v > float(usl)) or (lsl is not None and v < float(lsl))
    )
    if not points:
        status = None
    elif len(points) < 5:
        status = "insufficient"
    elif ooc > 0 or out_spec > 0:
        status = "alert"
    else:
        status = "normal"

    return {
        "points": points,
        "point_groups": point_groups,
        "point_ranges": point_ranges,
        "sample_count": len(points),
        "measurement_count": len(vals),
        "mean": mean,
        "sigma": sigma,
        "ucl": eff_ucl,
        "cl": eff_cl,
        "lcl": eff_lcl,
        "limits_source": limits_source,
        "cp": cp,
        "cpk": cpk,
        "out_of_control": ooc,
        "out_of_spec": out_spec,
        "status": status,
    }


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
            # 并发读写（API 线程 + 桥接同步）下降低 database is locked 概率
            for pragma in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA busy_timeout=5000",
            ):
                try:
                    self._conn.execute(pragma)
                except sqlite3.Error:
                    pass
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
        for col, typ in (
            ("data_source", "TEXT DEFAULT ''"),
            ("evidence", "TEXT DEFAULT ''"),
            ("fiscal_period", "TEXT DEFAULT ''"),
            ("last_seen_at", "TEXT DEFAULT ''"),
            ("archived", "INTEGER DEFAULT 0"),
            ("resolved_at", "TEXT DEFAULT ''"),
        ):
            try:
                c.execute(f"ALTER TABLE quality_alerts ADD COLUMN {col} {typ}")
            except Exception:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_active "
            "ON quality_alerts(archived, fiscal_period)"
        )
        for col, typ in (
            ("evidence_json", "TEXT DEFAULT '{}'"),
            ("history_refs_json", "TEXT DEFAULT '[]'"),
            ("knowledge_refs_json", "TEXT DEFAULT '[]'"),
            ("data_policy", "TEXT DEFAULT ''"),
            ("deleted_at", "TEXT DEFAULT ''"),
            ("alert_id", "TEXT DEFAULT ''"),
            ("fiscal_period", "TEXT DEFAULT ''"),
        ):
            try:
                c.execute(f"ALTER TABLE eightd_reports ADD COLUMN {col} {typ}")
            except Exception:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_8d_active "
            "ON eightd_reports(status, deleted_at)"
        )
        for col, typ in (
            ("verified", "INTEGER DEFAULT 0"),
            ("source_8d", "TEXT DEFAULT ''"),
            ("last_bumped_date", "TEXT DEFAULT ''"),
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
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_daily "
            "ON learning_history(learn_type, source, created_at)"
        )

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

        # 操作审计（删除/清理/确认/保存/导出/同步等写操作留痕）
        c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            actor TEXT DEFAULT '',
            role TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            action TEXT,
            target TEXT DEFAULT '',
            detail TEXT DEFAULT ''
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)"
        )

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

    def get_8d_list(self, part_number=None, status=None, limit=50, fiscal_period=None):
        conds = ["IFNULL(deleted_at,'')=''"]
        params = []
        if part_number:
            conds.append("part_number LIKE ?")
            params.append(f"%{part_number}%")
        if status:
            conds.append("status=?")
            params.append(status)
        if fiscal_period:
            conds.append("fiscal_period=?")
            params.append(str(fiscal_period))
        return self.query(f"SELECT * FROM eightd_reports WHERE {' AND '.join(conds)} ORDER BY created_at DESC LIMIT ?", params + [limit])

    def get_8d(self, report_id, include_deleted=False):
        cond = "id=?"
        if not include_deleted:
            cond += " AND IFNULL(deleted_at,'')=''"
        return self.query_one(f"SELECT * FROM eightd_reports WHERE {cond}", (report_id,))

    def find_8d_by_report_no(self, report_no, exclude_id=""):
        if not (report_no or "").strip():
            return None
        return self.query_one(
            "SELECT id, report_no FROM eightd_reports "
            "WHERE report_no=? AND id<>? AND IFNULL(deleted_at,'')=''",
            ((report_no or "").strip(), exclude_id or ""),
        )

    def delete_8d(self, report_id):
        """软删除：保留审计痕迹；列表/历史/导出均排除，删除后释放原报告编号。"""
        now = datetime.now().isoformat()
        self.execute(
            "UPDATE eightd_reports SET deleted_at=?, updated_at=?, "
            "report_no=CASE WHEN IFNULL(report_no,'')='' THEN '' "
            "ELSE report_no || ' [已删除:' || ? || ']' END WHERE id=?",
            (now, now, now[:19], report_id),
        )

    def purge_invalid_8d(self):
        """清理无效/编造稿：无料号、报告号当料号、无缺陷实据、通用模板冒充根因的旧稿"""
        rows = self.query(
            "SELECT id, part_number, problem, defects_json, d4, d5, data_policy, status "
            "FROM eightd_reports WHERE IFNULL(deleted_at,'')=''"
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
            "SELECT * FROM eightd_reports WHERE status='closed' "
            "AND IFNULL(deleted_at,'')='' ORDER BY closed_at DESC, updated_at DESC LIMIT 80"
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
        """按自然日累计不良出现次数：同一天多次分析不重复加一。"""
        pid = hashlib.md5(f"{defect_type}|{process_name}".encode()).hexdigest()[:16]
        now = datetime.now().isoformat()
        today = datetime.now().strftime("%Y-%m-%d")
        existing = self.query_one("SELECT * FROM defect_patterns WHERE id=?", (pid,))
        if existing:
            if (existing.get("last_bumped_date") or "") == today:
                return
            self.execute(
                "UPDATE defect_patterns SET occurrence=occurrence+1, "
                "last_bumped_date=?, updated_at=? WHERE id=?",
                (today, now, pid),
            )
        else:
            self.execute(
                "INSERT INTO defect_patterns (id,defect_type,process_name,root_causes,"
                "solutions,occurrence,effectiveness,verified,source_8d,"
                "last_bumped_date,created_at,updated_at) "
                "VALUES (?,?,?,?,?,1,0,0,'',?,?,?)",
                (pid, defect_type, process_name, "[]", "[]", today, now, now),
            )

    def merge_duplicate_patterns(self):
        """合并同一（缺陷类型, 工序）的历史重复行（旧版 verified/非 verified 双主键并存）。

        以自然键 (defect_type, process_name) 归并：occurrence 求和，保留已验证内容与
        源 8D；幂等，可重复调用。
        """
        rows = self.query("SELECT * FROM defect_patterns")
        groups = {}
        for r in rows:
            key = (
                str(r.get("defect_type") or "").strip(),
                str(r.get("process_name") or "").strip(),
            )
            groups.setdefault(key, []).append(r)
        merged = 0
        for (dtype, proc), items in groups.items():
            if len(items) < 2:
                continue
            target_id = hashlib.md5(f"{dtype}|{proc}".encode()).hexdigest()[:16]
            verified_rows = [r for r in items if int(r.get("verified") or 0) == 1]
            base = verified_rows[-1] if verified_rows else max(
                items, key=lambda r: int(r.get("occurrence") or 0)
            )
            occurrence = sum(int(r.get("occurrence") or 0) for r in items)
            verified = 1 if verified_rows else 0
            source_8d = next(
                (r.get("source_8d") for r in reversed(verified_rows) if r.get("source_8d")),
                "",
            )
            created = min(
                (r.get("created_at") or "" for r in items if r.get("created_at")),
                default="",
            )
            updated = max(
                (r.get("updated_at") or "" for r in items if r.get("updated_at")),
                default=datetime.now().isoformat(),
            )
            last_bumped = max(
                (r.get("last_bumped_date") or "" for r in items), default=""
            )
            self.execute("DELETE FROM defect_patterns WHERE id<>? AND defect_type=? AND process_name=?",
                         (target_id, dtype, proc))
            self.execute(
                """INSERT INTO defect_patterns
                   (id,defect_type,process_name,root_causes,solutions,occurrence,
                    effectiveness,verified,source_8d,last_bumped_date,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     root_causes=excluded.root_causes,
                     solutions=excluded.solutions,
                     occurrence=excluded.occurrence,
                     effectiveness=excluded.effectiveness,
                     verified=excluded.verified,
                     source_8d=excluded.source_8d,
                     last_bumped_date=excluded.last_bumped_date,
                     updated_at=excluded.updated_at""",
                (
                    target_id, dtype, proc,
                    base.get("root_causes") or "[]",
                    base.get("solutions") or "[]",
                    occurrence,
                    float(base.get("effectiveness") or 0.0),
                    verified,
                    source_8d,
                    last_bumped,
                    created or datetime.now().isoformat(),
                    updated,
                ),
            )
            merged += 1
        return merged

    def promote_8d_knowledge(self, report: dict):
        """关闭 8D 时，将人工确认的根因/对策写入已验证知识库。

        仅沉淀可追溯缺陷（逐条 exact match），不写通用占位桶；调用方须先通过
        validate_8d_closure 且仅在 open→closed 迁移时调用，避免重复累计 occurrence。
        主键与 bump_defect_occurrence 使用同一自然键，保证「出现次数」与「已验证知识」
        落在同一行，不再产生 verified/非 verified 双行。
        """
        if not report:
            return 0
        causes = _8d_extract_items(report.get("d4"))
        solutions = _8d_extract_items(report.get("d5"))
        if not causes and not solutions:
            return 0
        seeds = _8d_parse_defects(report)[:3]
        if not seeds:
            return 0
        self.merge_duplicate_patterns()
        rows = []
        now = datetime.now().isoformat()
        causes_json = json.dumps(causes[:8], ensure_ascii=False)
        solutions_json = json.dumps(solutions[:8], ensure_ascii=False)
        source_8d = report.get("report_no") or report.get("id") or ""
        for dtype, proc in seeds:
            pid = hashlib.md5(f"{dtype}|{proc}".encode()).hexdigest()[:16]
            rows.append((
                pid, dtype, proc, causes_json, solutions_json,
                source_8d, now, now,
            ))
        if not rows:
            return 0
        # 单事务写入：要么全部沉淀成功，要么全部回滚，避免半套知识入库
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(
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
                    rows,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(rows)

    # ── 预警 ─────────────────────────
    def save_alert(self, data: dict):
        """写入预警（同 id 为同一问题的持续状态）。

        created_at = 首次发现时间（永不覆盖）；last_seen_at = 最近一次仍触发的扫描时间；
        已确认状态保留；问题复发时解除归档，但不重复要求确认。
        """
        now = datetime.now().isoformat()
        data.setdefault("created_at", now)
        data.setdefault("last_seen_at", now)
        data.setdefault("fiscal_period", "")
        existing = self.query_one(
            "SELECT acknowledged, ack_by, ack_at, created_at FROM quality_alerts WHERE id=?",
            (data.get("id"),),
        )
        if existing:
            data["created_at"] = existing.get("created_at") or data["created_at"]
            if int(existing.get("acknowledged") or 0) == 1:
                data["acknowledged"] = 1
                data["ack_by"] = existing.get("ack_by") or ""
                data["ack_at"] = existing.get("ack_at") or ""
            else:
                data.setdefault("acknowledged", 0)
            data["archived"] = 0
            data["resolved_at"] = ""
        else:
            data.setdefault("acknowledged", 0)
            data.setdefault("archived", 0)
        cols = list(data.keys())
        placeholders = ",".join(["?"] * len(cols))
        # 不覆盖 id；已确认时 acknowledged/ack_* 已在 data 中保留
        updates = ",".join([f"{c}=excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO quality_alerts ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        self.execute(sql, list(data.values()))

    def get_alerts(self, acknowledged=None, alert_type=None, level=None, limit=100,
                   include_archived=False, fiscal_period=None):
        conds = ["1=1"] if include_archived else ["IFNULL(archived,0)=0"]
        params = []
        if fiscal_period:
            fp = str(fiscal_period).strip()
            if re.match(r"^\d{4}-?\d{2}$", fp):
                fp = fp.replace("-", "")
                ym = f"{fp[:4]}-{fp[4:6]}"
                conds.append(
                    "(fiscal_period=? OR (IFNULL(fiscal_period,'')='' AND created_at LIKE ?))"
                )
                params.extend([fp, f"{ym}%"])
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

    def get_alert(self, alert_id, include_archived=True):
        if not (alert_id or "").strip():
            return None
        cond = "id=?"
        if not include_archived:
            cond += " AND IFNULL(archived,0)=0"
        return self.query_one(
            f"SELECT * FROM quality_alerts WHERE {cond}", (alert_id.strip(),)
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
        """归档旧版「按天生成」的预警 id（不再删除，保留审计历史）。

        旧 id 形如 ALERT_<part>_SCRAP_20261126，每天一条且确认状态无法延续；
        新方案为稳定指纹（不含日期），旧行归档后由重新扫描生成稳定记录。
        """
        removed = []
        for a in self.get_alerts(limit=500, include_archived=False):
            aid = a.get("id") or ""
            if aid and re.search(r"_\d{8}$", aid) and not re.search(r"_\d{8}_", aid):
                self.execute(
                    "UPDATE quality_alerts SET archived=1, resolved_at=? WHERE id=?",
                    (datetime.now().isoformat(), aid),
                )
                removed.append(aid)
        return removed

    def resolve_missing_alerts(self, fiscal_period, keep_ids, scopes=None):
        """本期扫描未再触发的自动预警 → 归档（问题已不再成立），保留历史与确认记录。

        scopes 限定本次真正扫描过的料号（含「全厂」），避免把未扫描料号的历史预警误归档。
        """
        keep = set(keep_ids or [])
        conds = [
            "IFNULL(archived,0)=0",
            "auto_generated=1",
            "fiscal_period=?",
        ]
        params = [str(fiscal_period or "")]
        if scopes:
            uniq = sorted({str(s) for s in scopes if str(s).strip()})
            conds.append("part_number IN (" + ",".join(["?"] * len(uniq)) + ")")
            params.extend(uniq)
        rows = self.query(
            f"SELECT id FROM quality_alerts WHERE {' AND '.join(conds)}", params
        )
        now = datetime.now().isoformat()
        resolved = []
        for r in rows:
            aid = r.get("id") or ""
            if aid and aid not in keep:
                self.execute(
                    "UPDATE quality_alerts SET archived=1, resolved_at=? WHERE id=?",
                    (now, aid),
                )
                resolved.append(aid)
        return resolved

    # ── 知识/学习 ────────────────────
    def add_learning(self, learn_type, source, content, insight="", confidence=0.5):
        self.execute("""INSERT INTO learning_history (learn_type,source,content,insight,confidence,created_at)
                        VALUES (?,?,?,?,?,?)""",
                     (learn_type, source, content, insight, confidence, datetime.now().isoformat()))

    def add_learning_once(self, learn_type, source, content, insight="", confidence=0.5):
        """同一学习类型+来源每天最多记一条（原子 upsert，避免并发重复写入膨胀历史表）。"""
        day = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()
        cur = self.execute(
            "INSERT INTO learning_history (learn_type,source,content,insight,confidence,created_at) "
            "SELECT ?,?,?,?,?,? WHERE NOT EXISTS ("
            "  SELECT 1 FROM learning_history "
            "  WHERE learn_type=? AND source=? AND substr(created_at,1,10)=?"
            ")",
            (learn_type, source, content, insight, confidence, now,
             learn_type, source, day),
        )
        return (cur.rowcount or 0) > 0

    def log_audit(self, actor, role, ip, action, target="", detail=""):
        """写一条操作审计；失败不影响主流程。"""
        try:
            self.execute(
                "INSERT INTO audit_log (ts,actor,role,ip,action,target,detail) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    str(actor or "")[:120],
                    str(role or "")[:120],
                    str(ip or "")[:64],
                    str(action or "")[:80],
                    str(target or "")[:200],
                    str(detail or "")[:2000],
                ),
            )
        except Exception as e:
            logger.warning("审计日志写入失败: %s", e)

    def get_learning_stats(self):
        total = self.query_one("SELECT COUNT(*) as c FROM learning_history")["c"]
        applied = self.query_one("SELECT COUNT(*) as c FROM learning_history WHERE applied=1")["c"]
        patterns = self.query_one("SELECT COUNT(*) as c FROM defect_patterns")["c"]
        reports_8d = self.query_one(
            "SELECT COUNT(*) as c FROM eightd_reports WHERE IFNULL(deleted_at,'')=''"
        )["c"]
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
            self.db.add_learning_once(
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
                        scrap_pcs: float = 0, persist: bool = True,
                        fiscal_period: str = "") -> list:
        """智能生成预警（可落库）。scrap_rate=None 表示无入库基数、口径不可靠，禁止据此报警。

        预警 id = 料号 + 周期 + 类型(+工序) 的稳定指纹，不含扫描日期：
        同一问题每天扫描只刷新指标/最近触发时间，确认状态与首次发现时间得以保留。
        """
        alerts = []
        now = datetime.now().isoformat()
        fp = str(fiscal_period or "").strip().replace("-", "")
        fp_key = fp or "ALL"
        part_key = (part_number or "全厂").replace(" ", "")[:32]

        # 1. 报废率预警：必须有可靠报废率（报废+入库基数）+ 报废实数，样本过小不报
        if scrap_rate is not None and scrap_rate > self.ALERT_SCRAP_WARN and scrap_pcs >= 50:
            aid = f"ALERT_{part_key}_{fp_key}_SCRAP"
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
                "fiscal_period": fp,
                "last_seen_at": now,
                "created_at": now,
            }
            if persist:
                self.db.save_alert(alert)
            alerts.append(alert)

        # 2. 工序良率预警：必须来自 MES 检验实数，且检验笔数达标
        for p in (process_data or []):
            raw_rate = p.get("PassRate", p.get("yield_rate"))
            if raw_rate is None:
                continue
            rate = float(raw_rate)
            pname = str(p.get("ProcessName", p.get("process_name", "")) or "")
            inspect_n = int(p.get("InspectCount", p.get("total_qty", 0)) or 0)
            if not pname or rate >= self.ALERT_YIELD_WARN:
                continue
            if inspect_n < self.ALERT_MIN_INSPECT:
                continue  # 样本不足，禁止生成（避免包装 0%/焊接 50% 低样本误报）
            safe_pn = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", pname)[:24]
            aid = f"ALERT_{part_key}_{fp_key}_{safe_pn}_YIELD"
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
                "fiscal_period": fp,
                "last_seen_at": now,
                "created_at": now,
            }
            if persist:
                self.db.save_alert(alert)
            alerts.append(alert)

        # 3. 趋势恶化：两期均需有有效报废率（禁止 0%→跳变），且只用「已结束的自然日」
        if monthly_trend and len(monthly_trend) >= 2:
            today = datetime.now().strftime("%Y-%m-%d")
            complete = [
                x for x in monthly_trend
                if str(x.get("Day") or x.get("Month") or x.get("month") or "")[:10] < today
            ]
            sorted_trend = sorted(
                complete,
                key=lambda x: str(x.get("Day") or x.get("Month") or x.get("month") or ""),
            )
            if len(sorted_trend) >= 2:
                last = sorted_trend[-1]
                prev = sorted_trend[-2]
                last_raw = last.get("ScrapRate", last.get("scrap_rate"))
                prev_raw = prev.get("ScrapRate", prev.get("scrap_rate"))
                last_pcs = float(last.get("TotalScrap", last.get("scrap_pcs", 0)) or 0)
                prev_pcs = float(prev.get("TotalScrap", prev.get("scrap_pcs", 0)) or 0)
                # 任一期无入库基数（None）不判趋势，避免 0→有数 或 无基数→100% 的伪恶化
                if (
                    last_raw is not None
                    and prev_raw is not None
                    and float(prev_raw) > 0
                    and float(last_raw) > float(prev_raw) * 1.2
                    and float(last_raw) > 3
                    and last_pcs >= 50
                    and prev_pcs >= 20
                ):
                    last_rate = float(last_raw)
                    prev_rate = float(prev_raw)
                    aid = f"ALERT_{part_key}_{fp_key}_TREND"
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
                        "evidence": f"prev={prev_rate}%/{int(prev_pcs)}pcs,last={last_rate}%/{int(last_pcs)}pcs,"
                                    f"days={last.get('Day') or ''}",
                        "auto_generated": 1,
                        "acknowledged": 0,
                        "fiscal_period": fp,
                        "last_seen_at": now,
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
            data_gaps.append(
                "报废：本期有报废但缺入库基数，报废率不计分"
                if has_scrap else
                "报废：本期无 ERP 报废/入库实测，请在 ERP 补充检验报废与生产入库"
            )

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

        rate_reliable = bool(evidence.get("rate_reliable", evidence.get("scrap_rate") is not None))
        scrap_rate = float(evidence.get("scrap_rate") or 0) if rate_reliable else 0.0
        # 无入库基数时不在正文写「0.00%」或「100%」，明确标注口径不可用
        scrap_rate_text = f"{scrap_rate:.2f}%" if rate_reliable else "—（缺入库基数，不计算）"
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
            f"- 报废率：{scrap_rate_text}（报废 {scrap_pcs} PCS / 入库 {stock_pcs} PCS）\n"
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
            f"Why1：为何报废率达 {scrap_rate_text}？\n"
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
            f"- 验收基线：报废率 {scrap_rate_text}（报废 {scrap_pcs} / 入库 {stock_pcs}）"
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
            f"1. 基线报废率 {scrap_rate_text}（报废 {scrap_pcs} / 入库 {stock_pcs} PCS）"
            f"{' · 周期 ' + fiscal if fiscal else ''}\n"
            f"2. 目标：连续 3 个生产批次报废率 ≤ {target_scrap:.1f}%（或较基线下降 ≥50%）\n"
            f"3. 主责工序「{top_process or '相关工序'}」首件/巡检加严，并回写 MES 检验实绩\n"
            "4. 品质部跟踪 2 周，用 ERP View_P_Inspection + FGI 复算报废率验收\n"
            "【验收状态】未验证（关闭前须填写实测结果）"
        )

        problem = (
            f"料号{part_number}品质异常：报废率{scrap_rate_text}"
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
                f"【现象】报废率 {scrap_rate_text} = 报废 {scrap_pcs} / (报废+入库 {scrap_pcs + stock_pcs}) PCS\n"
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
            "severity": (
                5 if (rate_reliable and scrap_rate >= 10)
                else 4 if ((rate_reliable and scrap_rate >= 5) or scrap_pcs >= 500)
                else 3
            ),
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
        """生成改善建议列表。scrap_rate=None（无入库基数）时不据此给报废建议。"""
        recs = []

        # 基于报废率的建议
        if scrap_rate is not None and scrap_rate > 5:
            recs.append({
                "priority": "P0",
                "type": "立即行动",
                "title": f"报废率严重超标（{scrap_rate:.2f}%）",
                "action": "立即启动8D分析，停止不合格批次出货，100%检验",
                "owner": "品质部",
                "deadline": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            })
        elif scrap_rate is not None and scrap_rate > 2:
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
            "rate_reliable": False,    # 无入库基数时不计算报废率（scrap_rate=None）
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
            result["end_exclusive"] = end_date
        elif period:
            parts = _period_parts(period)
            if not parts:
                return {"success": False, "error": f"周期格式无效：{period}"}
            fiscal_period = parts[0]
            return self.get_scrap_analysis(
                part_number, start_date, end_date, period=None, fiscal_period=fiscal_period
            )
        else:
            start_date, end_date = self._get_default_dates(start_date, end_date)
            # 统一右开区间：用户截止日含当天 → 次日 00:00（唯一转换点）
            end_date = _end_exclusive(end_date)
            result["date_filter"] = "createDate/stockDate_range"
            result["end_exclusive"] = end_date

        try:
            # 查询条件 - MRB报废包含所有检验类型的报废
            # 统一口径（与 get_scrap_trends / COPQ / 仪表板共用一个 SQL 片段）：
            # 排除非生产工序/项目、测试工序及项目、TEST- 非量产料号
            insp_conds = [self._scrap_non_production_sql()]
            wo_conds = ["(wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)",
                        self._scrap_part_excluded_sql("mp.mfgPartCode")]
            insp_params = []
            wo_params = []

            if part_number:
                insp_conds.append("v.MfgPartCode LIKE ?")
                insp_params.append(f"%{part_number}%")
                wo_conds.append("mp.mfgPartCode LIKE ?")
                wo_params.append(f"%{part_number}%")
            
            # 周期过滤：优先使用FiscalPeriod
            if fiscal_period:
                insp_conds.append("v.FiscalPeriod = ?")
                insp_params.append(fiscal_period)
                wo_conds.append("wo.FiscalPeriod = ?")
                wo_params.append(fiscal_period)
            else:
                # 兼容旧接口：使用日期范围
                if start_date:
                    insp_conds.append("v.CreateDate >= ?")
                    insp_params.append(start_date)
                    wo_conds.append("wo.createDate >= ?")
                    wo_params.append(start_date)
                if end_date:
                    insp_conds.append("v.CreateDate < ?")
                    insp_params.append(end_date)
                    wo_conds.append("wo.createDate < ?")
                    wo_params.append(end_date)

            insp_where = " AND ".join(insp_conds)
            wo_where = " AND ".join(wo_conds)

            # 总报废
            r = self.erp.execute(
                f"SELECT SUM(v.QtyOfUnit) AS TotalScrap FROM View_P_Inspection v WHERE {insp_where}",
                insp_params,
            )
            if r.get("success") and r.get("data"):
                result["total_scrap_pcs"] = int(r["data"][0].get("TotalScrap") or 0)

            # ── 真实入库数量：从FGI_StockFormItemWO（生产入库关联表）通过工单查询 ──
            # FGI_StockFormItemWO.woId → P_WO.recId → E_JobMfgParts.mfgPartCode
            # 关键：FiscalPeriod 模式下必须按 wo.FiscalPeriod 过滤，不能按日历 stockDate。
            # 例：202610 工单的 FGI 常在 11 月初入库，用 stockDate∈10月会得到入库=0。
            stock_conds = ["1=1", self._scrap_part_excluded_sql("mp.mfgPartCode")]
            stock_params = []
            if part_number:
                stock_conds.append("mp.mfgPartCode LIKE ?")
                stock_params.append(f"%{part_number}%")
            if fiscal_period:
                stock_conds.append("wo.FiscalPeriod = ?")
                stock_params.append(fiscal_period)
            else:
                if start_date:
                    stock_conds.append("sfi.stockDate >= ?")
                    stock_params.append(start_date)
                if end_date:
                    stock_conds.append("sfi.stockDate < ?")
                    stock_params.append(end_date)
            stock_where = " AND ".join(stock_conds)

            r_stock = self.erp.execute(f"""
                SELECT SUM(sfiwo.qtyOfPCS) AS TotalInStock
                FROM FGI_StockFormItemWO sfiwo
                INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {stock_where}
            """, stock_params)
            if r_stock.get("success") and r_stock.get("data"):
                result["total_in_stock_pcs"] = int(r_stock["data"][0].get("TotalInStock") or 0)

            # 无 FGI 时回退工单产出 PCS（同周期/同日期范围），避免月趋势整段空白；并标记口径
            if result["total_in_stock_pcs"] <= 0:
                fallback_params = []
                if fiscal_period:
                    wo_time_cond = "wo.FiscalPeriod = ?"
                    fallback_params.append(fiscal_period)
                else:
                    _wc = []
                    if start_date:
                        _wc.append("wo.createDate >= ?")
                        fallback_params.append(start_date)
                    if end_date:
                        _wc.append("wo.createDate < ?")
                        fallback_params.append(end_date)
                    wo_time_cond = " AND ".join(_wc) or "1=1"
                part_cond_wo = ""
                if part_number:
                    part_cond_wo = "AND mp.mfgPartCode LIKE ?"
                    fallback_params.append(f"%{part_number}%")
                r_wo_qty = self.erp.execute(f"""
                    SELECT SUM(wo.qty_PCS_PRODUCED) AS Produced
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {wo_time_cond}
                      AND (wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)
                      AND {self._scrap_part_excluded_sql('mp.mfgPartCode')}
                      {part_cond_wo}
                """, fallback_params)
                if r_wo_qty.get("success") and r_wo_qty.get("data"):
                    produced = int(r_wo_qty["data"][0].get("Produced") or 0)
                    if produced > 0:
                        result["total_in_stock_pcs"] = produced
                        result["stock_source"] = "P_WO.qty_PCS_PRODUCED"
                        result["data_gap"] = (result.get("data_gap") or "") + \
                            ("；" if result.get("data_gap") else "") + \
                            "本期无 FGI 入库记录，入库口径回退为工单产出 PCS（近似）"
                    else:
                        result["stock_source"] = "none"
                else:
                    result["stock_source"] = "none"
            elif result["total_in_stock_pcs"] > 0:
                result["stock_source"] = "FGI_StockFormItemWO"

            # 报废率 = 报废/(报废+入库)；无入库基数→不计算（None），避免 100% 误导
            scrap_pcs = result["total_scrap_pcs"]
            stock_pcs = result["total_in_stock_pcs"]
            input_base = scrap_pcs + stock_pcs
            result["input_base_pcs"] = input_base
            result["rate_reliable"] = bool(stock_pcs > 0 and input_base > 0)
            if result["rate_reliable"]:
                result["scrap_rate"] = round(scrap_pcs * 100.0 / input_base, 2)
                result["has_data"] = True
            elif scrap_pcs > 0:
                result["scrap_rate"] = None
                result["has_data"] = True
                result["data_gap"] = "有报废但无 FGI/工单入库基数，报废率不计算（请在 ERP 补充生产入库）"
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
            """, insp_params)
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
            """, insp_params)
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
                """, stock_params)
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
                    # 当日无入库基数时不给率（None），与 get_scrap_trends 一致
                    rate = round(sc * 100.0 / (sc + inp), 2) if inp > 0 else None
                    result["monthly_trend"].append({
                        "Day": d, "Month": d,  # Month 兼容旧前端字段
                        "TotalScrap": sc,
                        "TotalInStock": inp, "ScrapRate": rate
                    })

            # 按工序汇总报废（归一到标准 PCB 工序目录后再聚合）
            r = self.erp.execute(f"""
                SELECT
                    v.InspectionProcess AS process,
                    SUM(v.QtyOfUnit) AS scrap_pcs
                FROM View_P_Inspection v
                WHERE {insp_where} AND v.InspectionProcess IS NOT NULL
                GROUP BY v.InspectionProcess
                ORDER BY SUM(v.QtyOfUnit) DESC
            """, insp_params)
            if r.get("success") and r.get("data"):
                total = result["total_scrap_pcs"] or 1
                merged = merge_counts_by_process(
                    [
                        {
                            "process": row.get("process", ""),
                            "scrap_pcs": int(row.get("scrap_pcs") or 0),
                        }
                        for row in r["data"]
                    ],
                    name_key="process",
                    sum_keys=["scrap_pcs"],
                )
                for row in merged[:10]:
                    sp = int(row.get("scrap_pcs") or 0)
                    result["process_scrap"].append({
                        "process": row.get("process", ""),
                        "scrap_pcs": sp,
                        "ratio": round(sp * 100.0 / total, 1),
                    })

            # 触发自主学习
            if result["defect_top10"]:
                self.engine.learn_from_defects(result["defect_top10"])

        except Exception as e:
            logger.error(f"报废分析失败: {e}")

        return result

    def get_process_yield(self, part_number=None, fiscal_period=None,
                          start_date=None, end_date=None):
        """工序良率（MES）— 经 ERP 工单关联；支持 FiscalPeriod 或日期范围，
        工单清单按块聚合，避免 200 条截断导致覆盖不全。"""
        result = {
            "process_yield": [], "process_defects": [], "inspect_records": [],
            "inspect_count": 0, "min_yield": None, "avg_yield": None, "wo_matched": 0,
            "wo_without_mes": 0, "mes_process_total": 0, "mes_data_cutoff": None,
            "data_source": "MES:TBL_QM_INSPECT_RECORD",
            "has_data": False,
            "rate_basis": "检验通过率（合格检验笔数/检验笔数；非单板一次通过率）",
            "fiscal_period": fiscal_period or "",
            "date_filter": (
                f"fiscal_period={fiscal_period}" if fiscal_period
                else (f"{start_date or '不限'}~{end_date or '不限'}" if (start_date or end_date) else "all")
            ),
        }

        if not self.mes or not self.mes.connected:
            result["data_gap"] = "MES 未连接，无法取工序良率"
            return result

        try:
            # MES 数据新鲜度（全局截止时间），供前端提示「MES 数据截至…」
            r_cut = self.mes.execute(
                "SELECT MAX(CCREATE_DATE) AS latest FROM TBL_QM_INSPECT_RECORD"
            )
            if r_cut.get("success") and r_cut.get("data"):
                result["mes_data_cutoff"] = r_cut["data"][0].get("latest")

            # MES CMO_LOT = 工单号：先从 ERP 取工单，再查 MES
            wo_list = []
            if self.erp and self.erp.connected and (part_number or fiscal_period or start_date or end_date):
                where_wo = ["1=1"]
                wo_params = []
                if part_number:
                    where_wo.append("mp.mfgPartCode LIKE ?")
                    wo_params.append(f"%{part_number}%")
                where_wo.append(self._scrap_part_excluded_sql("mp.mfgPartCode"))
                if fiscal_period:
                    where_wo.append("wo.FiscalPeriod = ?")
                    wo_params.append(fiscal_period)
                else:
                    if start_date:
                        where_wo.append("wo.createDate >= ?")
                        wo_params.append(start_date)
                    if end_date:
                        where_wo.append("wo.createDate < ?")
                        wo_params.append(_end_exclusive(end_date))
                erp_r = self.erp.execute(f"""
                    SELECT DISTINCT wo.woNumber
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {' AND '.join(where_wo)}
                """, wo_params)
                if erp_r.get("success") and erp_r.get("data"):
                    wo_list = sorted({
                        d.get("woNumber", "") for d in erp_r["data"] if d.get("woNumber")
                    })
                    result["wo_matched"] = len(wo_list)
                    logger.info(
                        "工序查询 料号=%s 周期=%s 日期=%s 工单=%s",
                        part_number or "全厂", fiscal_period or "不限",
                        result["date_filter"], len(wo_list),
                    )

            # 有料号/周期/日期过滤但无工单：不回退全库，避免口径错乱
            if (part_number or fiscal_period or start_date or end_date) and not wo_list:
                tip = []
                if part_number:
                    tip.append(f"料号 {part_number}")
                if fiscal_period:
                    tip.append(f"周期 {fiscal_period}")
                if start_date or end_date:
                    tip.append(f"日期 {result['date_filter']}")
                result["data_gap"] = f"{' / '.join(tip)} 在 ERP 无关联工单，无法取 MES 工序良率"
                return result

            def _lot_sql(lots):
                return ",".join("?" for _ in lots), [str(x) for x in lots]

            # 按块查询并合并，避免 IN(...) 过长与 200 条截断
            proc_map = {}
            defect_map = {}
            records = []
            matched_lots = set()

            def _ingest(chunk):
                lot_ph, lot_params = _lot_sql(chunk)
                wc = f"r.CRESULT IS NOT NULL AND r.CMO_LOT IN ({lot_ph})"
                wc2 = (f"r.CRESULT = 2 AND i.CTEMPLATE_ITEM_NAME IS NOT NULL "
                       f"AND r.CMO_LOT IN ({lot_ph})")
                r = self.mes.execute(f"""
                    SELECT
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                        COUNT(*) AS InspectCount,
                        SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) AS PassCount,
                        SUM(CASE WHEN r.CRESULT = 2 THEN 1 ELSE 0 END) AS NgCount
                    FROM TBL_QM_INSPECT_RECORD r
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE {wc}
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME
                """, lot_params)
                for row in (r.get("data") or []) if r.get("success") else []:
                    key = (row.get("ProcessID"), row.get("ProcessName") or "")
                    slot = proc_map.get(key)
                    if slot is None:
                        slot = {
                            "ProcessID": row.get("ProcessID"),
                            "ProcessName": row.get("ProcessName") or "",
                            "InspectCount": 0, "PassCount": 0, "NgCount": 0,
                        }
                        proc_map[key] = slot
                    slot["InspectCount"] += int(row.get("InspectCount") or 0)
                    slot["PassCount"] += int(row.get("PassCount") or 0)
                    slot["NgCount"] += int(row.get("NgCount") or 0)

                r2 = self.mes.execute(f"""
                    SELECT
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '未知工序') AS ProcessName,
                        i.CTEMPLATE_ITEM_NAME AS DefectItem,
                        i.CINPUT_VALUE AS DefectValue,
                        COUNT(*) AS DefectCount
                    FROM TBL_QM_INSPECT_RECORD r
                    INNER JOIN TBL_QM_INSPECTION_RECORD_ITEM i ON r.CID = i.CINSPECTION_RECORD_ID
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE {wc2}
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME, i.CTEMPLATE_ITEM_NAME, i.CINPUT_VALUE
                """, lot_params)
                for row in (r2.get("data") or []) if r2.get("success") else []:
                    key = (row.get("ProcessID"), row.get("DefectItem"), row.get("DefectValue"))
                    if key in defect_map:
                        defect_map[key]["DefectCount"] = (
                            int(defect_map[key].get("DefectCount") or 0)
                            + int(row.get("DefectCount") or 0)
                        )
                    else:
                        defect_map[key] = dict(row)

                r3 = self.mes.execute(f"""
                    SELECT
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
                    WHERE r.CMO_LOT IN ({lot_ph})
                    ORDER BY r.CCREATE_DATE DESC
                    LIMIT 80
                """, lot_params)
                records.extend((r3.get("data") or []) if r3.get("success") else [])

                r_lot = self.mes.execute(
                    f"SELECT DISTINCT r.CMO_LOT AS lot FROM TBL_QM_INSPECT_RECORD r "
                    f"WHERE r.CMO_LOT IN ({lot_ph})",
                    lot_params,
                )
                if r_lot.get("success"):
                    matched_lots.update(
                        x.get("lot") for x in (r_lot.get("data") or []) if x.get("lot")
                    )

            if wo_list:
                for i in range(0, len(wo_list), 400):
                    _ingest(wo_list[i:i + 400])
                result["wo_without_mes"] = max(0, len(wo_list) - len(matched_lots))
            else:
                # 无过滤：全厂 MES 汇总（单次全表聚合，避免分块无意义）
                r = self.mes.execute("""
                    SELECT
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                        COUNT(*) AS InspectCount,
                        SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) AS PassCount,
                        SUM(CASE WHEN r.CRESULT = 2 THEN 1 ELSE 0 END) AS NgCount
                    FROM TBL_QM_INSPECT_RECORD r
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT IS NOT NULL
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME
                """)
                for row in (r.get("data") or []) if r.get("success") else []:
                    proc_map[(row.get("ProcessID"), row.get("ProcessName") or "")] = dict(row)
                r2 = self.mes.execute("""
                    SELECT
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '未知工序') AS ProcessName,
                        i.CTEMPLATE_ITEM_NAME AS DefectItem,
                        i.CINPUT_VALUE AS DefectValue,
                        COUNT(*) AS DefectCount
                    FROM TBL_QM_INSPECT_RECORD r
                    INNER JOIN TBL_QM_INSPECTION_RECORD_ITEM i ON r.CID = i.CINSPECTION_RECORD_ID
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT = 2 AND i.CTEMPLATE_ITEM_NAME IS NOT NULL
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME, i.CTEMPLATE_ITEM_NAME, i.CINPUT_VALUE
                """)
                for row in (r2.get("data") or []) if r2.get("success") else []:
                    defect_map[(row.get("ProcessID"), row.get("DefectItem"), row.get("DefectValue"))] = dict(row)
                r3 = self.mes.execute("""
                    SELECT
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
                    WHERE r.CRESULT IS NOT NULL
                    ORDER BY r.CCREATE_DATE DESC
                    LIMIT 80
                """)
                records = (r3.get("data") or []) if r3.get("success") else []
                logger.info("无料号/周期过滤，返回全厂工序数据")

            result["mes_process_total"] = len(proc_map)
            # 归一并合并旧 SMT/细分工序名 → 标准 PCB 目录
            raw_proc_rows = []
            for slot in proc_map.values():
                raw_proc_rows.append({
                    "ProcessID": slot.get("ProcessID"),
                    "ProcessName": normalize_process_name(slot.get("ProcessName")) or "待确认工序",
                    "InspectCount": int(slot.get("InspectCount") or 0),
                    "PassCount": int(slot.get("PassCount") or 0),
                    "NgCount": int(slot.get("NgCount") or 0),
                })
            proc_rows = merge_counts_by_process(
                raw_proc_rows,
                name_key="ProcessName",
                sum_keys=["InspectCount", "PassCount", "NgCount"],
            )
            # 禁止用 quality_metrics 伪填无 MES 检验实据的工序（如造数「蚀刻」）

            for slot in proc_rows:
                n = int(slot.get("InspectCount") or 0)
                pass_n = int(slot.get("PassCount") or 0)
                slot["PassRate"] = round(pass_n * 100.0 / n, 2) if n > 0 else None
                slot["yield_rate"] = slot["PassRate"]
            proc_rows.sort(key=lambda x: (
                process_sort_key(x.get("ProcessName")),
                -int(x.get("InspectCount") or 0),
            ))
            result["process_yield"] = proc_rows[:20]
            result["process_catalog"] = list(STANDARD_PROCESSES)

            # 良率均值/最差值仅统计样本 >=15 的工序，避免 1 笔 0% 拉低评分
            min_sample = getattr(self.engine, "ALERT_MIN_INSPECT", 15)
            reliable = [
                d for d in proc_rows
                if d.get("PassRate") is not None
                and int(d.get("InspectCount") or 0) >= min_sample
            ]
            rates = [float(d.get("PassRate")) for d in reliable]
            result["yield_sample_ok"] = bool(reliable)
            result["low_sample_processes"] = [
                d.get("ProcessName") for d in proc_rows
                if d.get("PassRate") is not None
                and int(d.get("InspectCount") or 0) < min_sample
            ]
            if rates:
                result["min_yield"] = round(min(rates), 2)
                result["avg_yield"] = round(sum(rates) / len(rates), 2)
                worst = min(reliable, key=lambda x: float(x.get("PassRate") or 0))
                result["min_yield_process"] = worst.get("ProcessName") or ""
                result["min_yield_sample"] = int(worst.get("InspectCount") or 0)
                result["has_data"] = True
            if proc_rows:
                result["has_data"] = True
                result["inspect_count"] = sum(
                    int(d.get("InspectCount") or 0) for d in proc_rows
                )
                if not rates:
                    result["data_gap"] = (
                        f"MES 工序样本均 <{min_sample} 笔，良率均值不可靠（仅展示明细）"
                    )
            elif not proc_rows:
                result["data_gap"] = result.get("data_gap") or "MES 无工序检验记录"

            defect_rows = []
            for row in defect_map.values():
                item = dict(row)
                item["ProcessName"] = (
                    normalize_process_name(item.get("ProcessName")) or "待确认工序"
                )
                defect_rows.append(item)
            defect_rows = merge_counts_by_process(
                defect_rows,
                name_key="ProcessName",
                sum_keys=["DefectCount"],
            )
            defect_rows.sort(key=lambda x: -int(x.get("DefectCount") or 0))
            result["process_defects"] = defect_rows[:50]

            for rec in records:
                rec["ProcessName"] = (
                    normalize_process_name(rec.get("ProcessName")) or "待确认工序"
                )
            records.sort(key=lambda x: str(x.get("InspectTime") or ""), reverse=True)
            result["inspect_records"] = records[:80]
            if records:
                result["inspect_count"] = max(result["inspect_count"], len(result["inspect_records"]))

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
            "reject_pcs_erp_field": 0, "reject_rate": 0,
            "reject_source": "", "unlinked_scrap_pcs": 0,
            "wo_without_inspection": 0, "data_gap": None,
            "work_orders": [], "daily_trend": []
        }

        if not self.erp or not self.erp.connected:
            return result

        try:
            where_parts = []
            safe_part = None
            if part_number:
                safe_part = str(part_number).replace("'", "''")
                where_parts.append(f"mp.mfgPartCode LIKE '%{safe_part}%'")
            where_parts.append(self._scrap_part_excluded_sql("mp.mfgPartCode"))
            end_excl = None
            # 优先使用FiscalPeriod
            if fiscal_period:
                where_parts.append(f"wo.FiscalPeriod = '{fiscal_period}'")
            else:
                # 兼容旧接口：使用日期范围（右开区间，避免 end 当天漏计）
                if start_date:
                    where_parts.append(f"wo.createDate >= '{start_date}'")
                if end_date:
                    end_excl = _end_exclusive(end_date)
                    where_parts.append(f"wo.createDate < '{end_excl}'")

            where_sql = " AND ".join(where_parts) if where_parts else "1=1"

            # 全量汇总（不受列表 TOP 限制；旧版只统计 TOP100 会低估总量）
            r = self.erp.execute(f"""
                SELECT COUNT(*) AS TotalWo,
                       IFNULL(SUM(wo.qty_PCS_PRODUCED), 0) AS TotalInput,
                       IFNULL(SUM(wo.reject_PCS), 0) AS TotalRejectField
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql}
            """)
            if r.get("success") and r.get("data"):
                row = r["data"][0]
                result["total_wo"] = int(row.get("TotalWo") or 0)
                result["total_input_pcs"] = int(row.get("TotalInput") or 0)
                result["reject_pcs_erp_field"] = int(row.get("TotalRejectField") or 0)

            # 按工单重算报废：View_P_Inspection.WONumber → P_WO.woNumber（以质量实绩为准）
            scrap_map = {}
            r = self.erp.execute(f"""
                SELECT v.WONumber AS WONumber, SUM(v.QtyOfUnit) AS ScrapPcs
                FROM View_P_Inspection v
                INNER JOIN P_WO wo ON v.WONumber = wo.woNumber
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql} AND v.InspectionResult = 'Scrapped'
                  AND {self._scrap_part_excluded_sql('v.MfgPartCode')}
                GROUP BY v.WONumber
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    wn = str(row.get("WONumber") or "")
                    if wn:
                        scrap_map[wn] = int(row.get("ScrapPcs") or 0)
            result["total_reject_pcs"] = int(sum(scrap_map.values()))
            result["reject_source"] = "ERP:View_P_Inspection.WONumber"
            if result["total_reject_pcs"] <= 0 and result.get("reject_pcs_erp_field", 0) > 0:
                # 没有任何检验实绩可关联时降级用 P_WO 字段，并明确标注
                result["total_reject_pcs"] = int(result["reject_pcs_erp_field"])
                result["reject_source"] = "ERP:P_WO.reject_PCS（无检验关联，降级口径）"

            # 时间窗/周期内未关联 WONumber 的报废（单独披露，不摊入工单口径）
            v_window = []
            if fiscal_period:
                v_window.append(f"v.FiscalPeriod = '{fiscal_period}'")
            else:
                if start_date:
                    v_window.append(f"v.CreateDate >= '{start_date}'")
                if end_excl:
                    v_window.append(f"v.CreateDate < '{end_excl}'")
            v_window_sql = " AND ".join(["1=1"] + v_window)
            v_part_sql = f" AND v.MfgPartCode LIKE '%{safe_part}%'" if safe_part else ""
            result["unlinked_scrap_pcs"] = 0
            r = self.erp.execute(f"""
                SELECT IFNULL(SUM(v.QtyOfUnit), 0) AS UnlinkedScrap
                FROM View_P_Inspection v
                WHERE {v_window_sql} AND v.InspectionResult = 'Scrapped'
                  AND {self._scrap_non_production_sql()}{v_part_sql}
                  AND (IFNULL(v.WONumber, '') = '' OR v.WONumber NOT IN (SELECT woNumber FROM P_WO))
            """)
            if r.get("success") and r.get("data"):
                result["unlinked_scrap_pcs"] = int(r["data"][0].get("UnlinkedScrap") or 0)

            # 无任何检验记录的工单数（关联缺口披露）
            result["wo_without_inspection"] = 0
            r = self.erp.execute(f"""
                SELECT COUNT(*) AS Missing
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql}
                  AND NOT EXISTS (
                      SELECT 1 FROM View_P_Inspection v WHERE v.WONumber = wo.woNumber
                  )
            """)
            if r.get("success") and r.get("data"):
                result["wo_without_inspection"] = int(r["data"][0].get("Missing") or 0)

            # 列表（最多 200；逐单附 View_P_Inspection 重算报废）
            r = self.erp.execute(f"""
                SELECT TOP 200
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
                for wo in wos:
                    wn = str(wo.get("woNumber") or "")
                    if wn in scrap_map:
                        wo["RejectPcs"] = scrap_map[wn]
                        wo["RejectSource"] = "View_P_Inspection"
                    else:
                        wo["RejectPcs"] = int(wo.get("RejectPcs") or 0)
                        wo["RejectSource"] = "P_WO.reject_PCS"
                result["work_orders"] = wos[:200]
                if len(wos) < result["total_wo"]:
                    result["list_truncated"] = True

            # 日趋势：投入按 P_WO.createDate 聚合，报废按检验实绩关联聚合
            daily = {}
            r = self.erp.execute(f"""
                SELECT substr(wo.createDate, 1, 10) AS Day,
                       IFNULL(SUM(wo.qty_PCS_PRODUCED), 0) AS Input
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql}
                GROUP BY substr(wo.createDate, 1, 10)
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    d = str(row.get("Day") or "")
                    if d:
                        daily.setdefault(d, {"input": 0, "reject": 0})["input"] += int(row.get("Input") or 0)
            r = self.erp.execute(f"""
                SELECT substr(v.CreateDate, 1, 10) AS Day,
                       IFNULL(SUM(v.QtyOfUnit), 0) AS Scrap
                FROM View_P_Inspection v
                INNER JOIN P_WO wo ON v.WONumber = wo.woNumber
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql} AND v.InspectionResult = 'Scrapped'
                  AND {self._scrap_part_excluded_sql('v.MfgPartCode')}
                GROUP BY substr(v.CreateDate, 1, 10)
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    d = str(row.get("Day") or "")
                    if d:
                        daily.setdefault(d, {"input": 0, "reject": 0})["reject"] += int(row.get("Scrap") or 0)

            if result["total_input_pcs"] > 0:
                result["reject_rate"] = round(
                    result["total_reject_pcs"] * 100.0 / result["total_input_pcs"], 2
                )
            result["daily_trend"] = [
                {"date": k, "input_pcs": v["input"], "reject_pcs": v["reject"]}
                for k, v in sorted(daily.items())
            ]

            gaps = []
            if result["total_wo"] and result["wo_without_inspection"]:
                gaps.append(
                    f"{result['wo_without_inspection']} 张工单无 View_P_Inspection 检验记录，"
                    "工单报废率仅含可关联部分"
                )
            if result["unlinked_scrap_pcs"]:
                gaps.append(
                    f"另有 {result['unlinked_scrap_pcs']} PCS 报废未关联 WONumber，"
                    "未计入工单口径（见全厂报废分析）"
                )
            result["data_gap"] = "；".join(gaps) if gaps else None

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
            parts = _period_parts(period)
            if not parts:
                return {"success": False, "error": f"周期格式无效：{period}"}
            fiscal_period = parts[0]
        elif not start_date and not end_date:
            fiscal_period = datetime.now().strftime("%Y%m")
        else:
            start_date, end_date = self._get_default_dates(start_date, end_date)

        # 1. 各维度数据（无料号=全厂当月；MES 按同周期工单关联）
        scrap = self.get_scrap_analysis(part_number, start_date, end_date, fiscal_period=fiscal_period)
        process = self.get_process_yield(
            part_number, fiscal_period=fiscal_period,
            start_date=start_date if not fiscal_period else None,
            end_date=end_date if not fiscal_period else None,
        )
        wo = self.get_work_orders(part_number, start_date, end_date, fiscal_period=fiscal_period)

        scrap_rate = scrap.get("scrap_rate", 0)
        avg_yield = process.get("avg_yield")  # None=无MES，禁止默认95
        min_yield = process.get("min_yield")
        has_scrap = bool(scrap.get("has_data")) or int(scrap.get("total_scrap_pcs") or 0) > 0 or int(scrap.get("total_in_stock_pcs") or 0) > 0
        has_mes = bool(process.get("has_data")) and bool(process.get("process_yield"))

        # 客诉 / COPQ（与体检周期对齐：FiscalPeriod→当月日历；否则用日期范围）
        if fiscal_period and len(str(fiscal_period)) >= 6:
            fp = str(fiscal_period)
            c_start = f"{fp[:4]}-{fp[4:6]}-01"
            c_end = _month_end_inclusive(fp)  # 含当月最后一天；COPQ 内部统一转右开
        else:
            c_start, c_end = start_date, end_date
        copq = self.get_copq_analysis(c_start, c_end, part_number=part_number)
        has_complaint = bool(copq.get("has_data"))

        # IQC（有 ERP 实表则计分；不做设备 OEE）——与体检周期对齐，避免历史周期仍用近30天口径
        if fiscal_period and str(fiscal_period).isdigit() and len(str(fiscal_period)) >= 6:
            _fp = str(fiscal_period)
            gate_start = f"{_fp[:4]}-{_fp[4:6]}-01"
            _gy, _gm = int(_fp[:4]), int(_fp[4:6])
            gate_end = f"{_gy + 1}-01-01" if _gm == 12 else f"{_gy}-{_gm + 1:02d}-01"
        elif start_date or end_date:
            gate_start = start_date
            gate_end = _end_exclusive(end_date) if end_date else None
        else:
            gate_start = gate_end = None
        gates = self.get_inspection_gate_kpis(30, start_date=gate_start, end_date=gate_end)
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
            fiscal_period=fiscal_period or "",
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
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        # 同料号+同周期当日只保留一份快照：重复体检=原地刷新，避免只读请求无限新增历史
        scope_key = hashlib.md5(
            f"{key}|{fiscal_period or date_range}".encode()
        ).hexdigest()[:8]
        report_id = f"HR-{key[:12]}-{day}-{scope_key}"
        report_no = f"QHC-{day}-{scope_key.upper()}"

        top_defects = (scrap.get("defect_top10") or [])[:8]
        evidence = {
            "fiscal_period": fiscal_period or "",
            "date_range": date_range,
            "part_number": part_label,
            "scope": "plant" if not part_number else "part",
            "has_scrap": has_scrap,
            "has_mes": has_mes,
            "has_complaint": has_complaint,
            "scrap_rate": scrap.get("scrap_rate") if has_scrap else None,
            "rate_reliable": bool(scrap.get("rate_reliable")),
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
                if (pr := p.get("PassRate", p.get("yield_rate"))) is not None and float(pr) < 98
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
        一次分组聚合取满整个窗口，避免逐月重复扫描 View_P_Inspection。
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
            if not fps:
                return []
            fp_lo, fp_hi = fps[0], fps[-1]

            # 1) 报废 PCS（按检验周期）
            scrap_by_fp = {}
            r = self.erp.execute(f"""
                SELECT v.FiscalPeriod AS fp, SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {self._scrap_non_production_sql()}
                  AND v.FiscalPeriod >= '{fp_lo}' AND v.FiscalPeriod <= '{fp_hi}'
                GROUP BY v.FiscalPeriod
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    fp = str(row.get("fp") or "")
                    if fp:
                        scrap_by_fp[fp] = int(row.get("scrap") or 0)

            # 2) FGI 入库 PCS（按工单周期，与 get_scrap_analysis 同口径）
            stock_by_fp = {}
            r = self.erp.execute(f"""
                SELECT wo.FiscalPeriod AS fp, SUM(sfiwo.qtyOfPCS) AS stock
                FROM FGI_StockFormItemWO sfiwo
                INNER JOIN FGI_StockFormItem sfi ON sfiwo.stockFormItemId = sfi.recId
                INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {self._scrap_part_excluded_sql('mp.mfgPartCode')}
                  AND wo.FiscalPeriod >= '{fp_lo}' AND wo.FiscalPeriod <= '{fp_hi}'
                GROUP BY wo.FiscalPeriod
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    fp = str(row.get("fp") or "")
                    if fp:
                        stock_by_fp[fp] = int(row.get("stock") or 0)

            # 3) 工单产出回退（无 FGI 的月份用产出近似入库）
            produced_by_fp = {}
            r = self.erp.execute(f"""
                SELECT wo.FiscalPeriod AS fp, SUM(wo.qty_PCS_PRODUCED) AS produced
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE (wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)
                  AND {self._scrap_part_excluded_sql('mp.mfgPartCode')}
                  AND wo.FiscalPeriod >= '{fp_lo}' AND wo.FiscalPeriod <= '{fp_hi}'
                GROUP BY wo.FiscalPeriod
            """)
            if r.get("success") and r.get("data"):
                for row in r["data"]:
                    fp = str(row.get("fp") or "")
                    if fp:
                        produced_by_fp[fp] = int(row.get("produced") or 0)

            series = []
            for fp in fps:
                scrap_pcs = int(scrap_by_fp.get(fp) or 0)
                fgi_pcs = int(stock_by_fp.get(fp) or 0)
                produced_pcs = int(produced_by_fp.get(fp) or 0)
                if fgi_pcs > 0:
                    stock_pcs, stock_source = fgi_pcs, "FGI_StockFormItemWO"
                elif produced_pcs > 0:
                    stock_pcs, stock_source = produced_pcs, "P_WO.qty_PCS_PRODUCED"
                else:
                    stock_pcs, stock_source = 0, "none"
                input_pcs = scrap_pcs + stock_pcs
                # 无入库仅有报废时，报废率=100% 会误导领导，标记为口径不全
                rate_reliable = stock_pcs > 0 and input_pcs > 0
                rate = round(scrap_pcs * 100.0 / input_pcs, 2) if rate_reliable else None
                has_data = bool(scrap_pcs or stock_pcs)
                gap = None
                if stock_source == "P_WO.qty_PCS_PRODUCED":
                    gap = "本期无 FGI 入库记录，入库口径回退为工单产出 PCS（近似）"
                if scrap_pcs > 0 and stock_pcs <= 0:
                    gap = "仅有报废PCS，无 FGI/工单入库口径，不计算报废率"
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
                    "stock_source": stock_source,
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
                       q.process_name, q.defect_type AS defect_reason, q.inspected_at,
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

    def get_inspection_gate_kpis(self, day_count: int = 30, start_date=None, end_date=None) -> dict:
        """
        来料(IQC)/终检(FQC)/一次交验/复检返工 —— 来自 quality_inspection_records 实表。
        inspection_type: incoming / final / process；is_reinspection=1 视为复检。
        传入 start_date/end_date（右开区间）时按指定周期统计（供体检周期对齐）；
        不传时按最近 day_count 天滚动统计（总览等实时口径）。
        """
        n = max(7, min(int(day_count or 30), 90))
        use_range = bool(start_date or end_date)
        safe_start = str(start_date).replace("'", "''").strip() if start_date else None
        safe_end = str(end_date).replace("'", "''").strip() if end_date else None
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
                if use_range:
                    conds = ["inspected_at IS NOT NULL"]
                    if safe_start:
                        conds.append(f"inspected_at >= '{safe_start}'")
                    if safe_end:
                        conds.append(f"inspected_at < '{safe_end}'")
                    where_time = " AND ".join(conds)
                else:
                    where_time = (
                        "inspected_at IS NOT NULL "
                        f"AND inspected_at >= datetime('now', '-{int(days)} day')"
                    )
                rr = db.execute(f"""
                    SELECT inspection_type,
                           IFNULL(is_reinspection,0) AS is_re,
                           COUNT(*) AS c,
                           SUM(CASE WHEN {done_expr} THEN 1 ELSE 0 END) AS done_c,
                           SUM(CASE WHEN {pass_expr} THEN 1 ELSE 0 END) AS pass_c
                    FROM quality_inspection_records
                    WHERE {where_time}
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
            # （指定周期范围时不回退，保持与所选周期一致）
            extended = False
            if not use_range and (iqc_c == 0 or fqc_c == 0):
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
                result["data_gaps"].append(
                    "所选周期无 incoming（IQC）检验记录" if use_range
                    else "近180日无 incoming（IQC）检验记录"
                )
            elif iqc_d < 30:
                result["data_gaps"].append(
                    f"IQC 已出结论仅 {iqc_d} 单（共 {iqc_c}），样本不足仅供参考"
                )
            if fqc_c == 0:
                result["data_gaps"].append(
                    "所选周期无 final（FQC/出货）检验记录" if use_range
                    else "近180日无 final（FQC/出货）检验记录"
                )
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

    def get_part_options(self, fiscal_period=None, limit: int = 300, q: str = None):
        """ERP 料号下拉（只读实据）。

        来源：P_WO×E_JobMfgParts + View_P_Inspection 报废料号；
        排除 TEST- 非量产前缀；禁止编造料号。
        """
        lim = max(20, min(int(limit or 300), 800))
        fp = (fiscal_period or "").strip() or None
        keyword = (q or "").strip()
        result = {
            "has_data": False,
            "fiscal_period": fp or "",
            "parts": [],
            "data_source": "ERP:P_WO + View_P_Inspection",
            "formula": "DISTINCT 料号来自工单与报废视图，排除 TEST- 前缀",
        }
        if not (self.erp and self.erp.connected):
            result["data_gap"] = "ERP 未连接"
            return result

        try:
            merged: dict[str, dict] = {}

            def _add(code, name="", scrap=None, stock=None):
                code = str(code or "").strip()
                if not code or code.upper().startswith("TEST-"):
                    return
                if keyword and keyword.upper() not in code.upper() and keyword not in str(name or ""):
                    return
                slot = merged.get(code)
                if slot is None:
                    slot = {"part_number": code, "part_name": "", "scrap_pcs": 0}
                    merged[code] = slot
                if name and not slot.get("part_name"):
                    slot["part_name"] = str(name).strip()
                if scrap is not None:
                    try:
                        slot["scrap_pcs"] = int(slot.get("scrap_pcs") or 0) + int(float(scrap or 0))
                    except (TypeError, ValueError):
                        pass

            # 先取报废 TOP（View_P_Inspection 无料名列，名称后续用工单表补）
            scrap_where = [self._scrap_non_production_sql(), "IFNULL(v.MfgPartCode,'') <> ''"]
            scrap_params: list = []
            if fp:
                scrap_where.append("v.FiscalPeriod = ?")
                scrap_params.append(fp)
            if keyword:
                scrap_where.append("v.MfgPartCode LIKE ?")
                scrap_params.append(f"%{keyword}%")
            r_sc = self.erp.execute(
                f"""
                SELECT TOP {lim}
                       v.MfgPartCode AS part_number,
                       SUM(v.QtyOfUnit) AS scrap_pcs
                FROM View_P_Inspection v
                WHERE {' AND '.join(scrap_where)}
                GROUP BY v.MfgPartCode
                ORDER BY SUM(v.QtyOfUnit) DESC
                """,
                scrap_params,
            )
            for row in (r_sc.get("data") or []):
                _add(row.get("part_number"), scrap=row.get("scrap_pcs"))

            # 再取工单料号（补全无报废料号 + 料名）
            wo_where = ["IFNULL(mp.mfgPartCode,'') <> ''", "IFNULL(mp.mfgPartCode,'') NOT LIKE 'TEST-%'"]
            wo_params: list = []
            if fp:
                wo_where.append("wo.FiscalPeriod = ?")
                wo_params.append(fp)
            if keyword:
                wo_where.append(
                    "(mp.mfgPartCode LIKE ? OR IFNULL(mp.mfgPartName,'') LIKE ?)"
                )
                wo_params.extend([f"%{keyword}%", f"%{keyword}%"])
            r_wo = self.erp.execute(
                f"""
                SELECT DISTINCT TOP {lim}
                       mp.mfgPartCode AS part_number,
                       IFNULL(mp.mfgPartName, '') AS part_name
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {' AND '.join(wo_where)}
                ORDER BY mp.mfgPartCode
                """,
                wo_params,
            )
            for row in (r_wo.get("data") or []):
                _add(row.get("part_number"), row.get("part_name"))

            # 报废料号若缺料名，再按料号反查 E_JobMfgParts（参数化 IN）
            missing = [c for c, s in merged.items() if not s.get("part_name")]
            if missing:
                for i in range(0, min(len(missing), lim), 80):
                    chunk = missing[i:i + 80]
                    placeholders = ",".join("?" * len(chunk))
                    r_nm = self.erp.execute(
                        f"""
                        SELECT TOP {len(chunk)}
                               mp.mfgPartCode AS part_number,
                               IFNULL(MAX(mp.mfgPartName), '') AS part_name
                        FROM E_JobMfgParts mp
                        WHERE mp.mfgPartCode IN ({placeholders})
                        GROUP BY mp.mfgPartCode
                        """,
                        chunk,
                    )
                    for row in (r_nm.get("data") or []):
                        _add(row.get("part_number"), row.get("part_name"))

            parts = list(merged.values())
            parts.sort(key=lambda x: (-int(x.get("scrap_pcs") or 0), str(x.get("part_number") or "")))
            result["parts"] = parts[:lim]
            result["has_data"] = bool(result["parts"])
            result["count"] = len(result["parts"])
            if not result["parts"]:
                result["data_gap"] = "ERP 无可用料号（工单/报废视图）"
            return result
        except Exception as e:
            result["data_gap"] = str(e)
            return result

    def get_closed_loop_stats(self, fiscal_period=None) -> dict:
        """当月异常料号 ↔ 8D 闭环（只统计有生产报废的料号，禁止用全库 8D 冒充）。"""
        fp = (fiscal_period or "").strip() or datetime.now().strftime("%Y%m")
        out = {
            "fiscal_period": fp,
            "abnormal_parts": 0,
            "eight_d_open": 0,
            "eight_d_closed": 0,
            "parts_with_open_8d": 0,
            "parts_with_closed_8d": 0,
            "parts_without_8d": 0,
            "formula": (
                "异常料号=当月生产报废 distinct 料号；"
                "已开/已关=这些料号在 8D 库中 status=open/closed 的份数"
            ),
            "data_source": "ERP:View_P_Inspection + KB:eightd_reports",
            "has_data": False,
        }
        if not re.fullmatch(r"(19|20)\d{4}", fp):
            out["data_gap"] = "fiscal_period 格式无效"
            return out
        scrap_parts: set[str] = set()
        if self.erp and self.erp.connected:
            try:
                r = self.erp.execute(
                    f"""
                    SELECT COUNT(DISTINCT v.MfgPartCode) AS cnt
                    FROM View_P_Inspection v
                    WHERE {self._scrap_non_production_sql()}
                      AND v.FiscalPeriod = ?
                      AND IFNULL(v.MfgPartCode,'') <> ''
                      AND IFNULL(v.MfgPartCode,'') NOT LIKE 'TEST-%'
                    """,
                    (fp,),
                )
                if r.get("success") and r.get("data"):
                    out["abnormal_parts"] = int(r["data"][0].get("cnt") or 0)
                r2 = self.erp.execute(
                    f"""
                    SELECT DISTINCT TOP 2000 v.MfgPartCode AS part_number
                    FROM View_P_Inspection v
                    WHERE {self._scrap_non_production_sql()}
                      AND v.FiscalPeriod = ?
                      AND IFNULL(v.MfgPartCode,'') <> ''
                      AND IFNULL(v.MfgPartCode,'') NOT LIKE 'TEST-%'
                    """,
                    (fp,),
                )
                for row in (r2.get("data") or []):
                    code = str(row.get("part_number") or "").strip()
                    if code:
                        scrap_parts.add(code)
            except Exception as e:
                out["data_gap"] = str(e)

        open_n = closed_n = 0
        parts_open: set[str] = set()
        parts_closed: set[str] = set()
        try:
            for rp in (self.db.get_8d_list(status="open", limit=500) or []):
                pn = str(rp.get("part_number") or "").strip()
                if not pn or pn not in scrap_parts:
                    continue
                open_n += 1
                parts_open.add(pn)
            for rp in (self.db.get_8d_list(status="closed", limit=500) or []):
                pn = str(rp.get("part_number") or "").strip()
                if not pn or pn not in scrap_parts:
                    continue
                closed_n += 1
                parts_closed.add(pn)
        except Exception as e:
            out["data_gap"] = ((out.get("data_gap") or "") + "; " + str(e)).strip("; ")

        out["eight_d_open"] = open_n
        out["eight_d_closed"] = closed_n
        out["parts_with_open_8d"] = len(parts_open)
        out["parts_with_closed_8d"] = len(parts_closed)
        if scrap_parts:
            out["parts_without_8d"] = max(
                0, len(scrap_parts) - len(parts_open | parts_closed)
            )
        elif out["abnormal_parts"]:
            # 有计数但未拉到明细时，不做 without 估算
            out["parts_without_8d"] = None
        out["has_data"] = bool(out["abnormal_parts"] or open_n or closed_n)
        return out

    def get_part_spotlight(self, part_number: str, fiscal_period=None) -> dict:
        """异常料号决策卡：报废/率/主责工序/TOP缺陷/8D建议/损失（有单价才估）。"""
        part = (part_number or "").strip()
        fp = (fiscal_period or "").strip() or datetime.now().strftime("%Y%m")
        empty = {
            "success": False,
            "has_data": False,
            "part_number": part,
            "fiscal_period": fp,
            "policy": "仅 ERP/MES 实据；无入库不算报废率；无单价不估损失；禁止编造",
        }
        if not part:
            empty["error"] = "请选择料号"
            return empty
        if not re.fullmatch(r"(19|20)\d{4}", fp):
            empty["error"] = "fiscal_period 格式无效"
            return empty
        if not (self.erp and self.erp.connected):
            empty["error"] = "ERP 未连接"
            return empty

        scrap = self.get_scrap_analysis(part, None, None, fiscal_period=fp)
        scrap_pcs = int(scrap.get("total_scrap_pcs") or 0)
        stock_pcs = int(scrap.get("total_in_stock_pcs") or 0)
        rate = scrap.get("scrap_rate")
        reliable = bool(scrap.get("rate_reliable"))
        defects = list(scrap.get("defect_top10") or [])[:5]
        processes = list(scrap.get("process_scrap") or [])[:5]
        primary = processes[0] if processes else None
        top_defect = defects[0] if defects else None

        # 料名（参数化，禁止拼接料号）
        part_name = ""
        try:
            r_nm = self.erp.execute(
                """
                SELECT TOP 1 IFNULL(mp.mfgPartName, '') AS part_name
                FROM E_JobMfgParts mp
                WHERE mp.mfgPartCode = ?
                """,
                (part,),
            )
            if r_nm.get("success") and r_nm.get("data"):
                part_name = str(r_nm["data"][0].get("part_name") or "").strip()
        except Exception:
            pass

        # 损失：仅 materials 有精确单价时估算（参数化）
        unit_price = None
        loss_est = None
        loss_label = "待定价"
        try:
            r_p = self.erp.execute(
                """
                SELECT TOP 1 purchase_price FROM materials
                WHERE material_code = ?
                  AND purchase_price IS NOT NULL AND purchase_price > 0
                """,
                (part,),
            )
            if r_p.get("success") and r_p.get("data"):
                unit_price = float(r_p["data"][0].get("purchase_price") or 0)
                if unit_price > 0 and scrap_pcs > 0:
                    loss_est = round(unit_price * scrap_pcs, 2)
                    loss_label = "已核价估算"
        except Exception:
            pass

        # 既有 8D
        open_8d = []
        closed_8d = []
        try:
            for rp in (self.db.get_8d_list(part_number=part, status="open", limit=20) or []):
                open_8d.append({
                    "id": rp.get("id"),
                    "report_no": rp.get("report_no"),
                    "status": rp.get("status"),
                    "created_at": rp.get("created_at"),
                })
            for rp in (self.db.get_8d_list(part_number=part, status="closed", limit=10) or []):
                closed_8d.append({
                    "id": rp.get("id"),
                    "report_no": rp.get("report_no"),
                    "status": rp.get("status"),
                    "closed_at": rp.get("closed_at") or rp.get("updated_at"),
                })
        except Exception:
            pass

        recommend = False
        recommend_reason = ""
        if open_8d:
            recommend = False
            recommend_reason = f"已有 {len(open_8d)} 份进行中 8D，优先关闭闭环"
        elif scrap_pcs <= 0:
            recommend_reason = "本期无报废实据，不建议发起 8D"
        elif reliable and rate is not None and float(rate) >= float(TARGET_SCRAP_RATE) * 2:
            recommend = True
            recommend_reason = (
                f"报废率 {rate}% ≥ 目标 {TARGET_SCRAP_RATE:g}% 的 2 倍，建议开 8D"
            )
        elif scrap_pcs >= 500:
            recommend = True
            recommend_reason = f"报废量 {scrap_pcs:,} PCS 较高，建议开 8D 锁定根因"
        elif reliable and rate is not None and float(rate) > float(TARGET_SCRAP_RATE):
            recommend = True
            recommend_reason = (
                f"报废率 {rate}% 高于目标 {TARGET_SCRAP_RATE:g}%，建议评估是否开 8D"
            )
        else:
            recommend_reason = "未达自动建议阈值，可按现场判断是否开 8D"

        plant_share = None
        try:
            plant = self.get_scrap_analysis(None, None, None, fiscal_period=fp)
            plant_scrap = int(plant.get("total_scrap_pcs") or 0)
            if plant_scrap > 0 and scrap_pcs > 0:
                plant_share = round(scrap_pcs * 100.0 / plant_scrap, 2)
        except Exception:
            pass

        return {
            "success": True,
            "has_data": bool(scrap_pcs > 0 or stock_pcs > 0),
            "part_number": part,
            "part_name": part_name,
            "fiscal_period": fp,
            "scrap_pcs": scrap_pcs,
            "stock_pcs": stock_pcs,
            "input_pcs": scrap_pcs + stock_pcs,
            "scrap_rate": rate if reliable else None,
            "rate_reliable": reliable,
            "plant_scrap_share_pct": plant_share,
            "primary_process": (primary or {}).get("process") if primary else None,
            "primary_process_scrap_pcs": (primary or {}).get("scrap_pcs") if primary else None,
            "top_defect": (top_defect or {}).get("defect_type") if top_defect else None,
            "top_defect_scrap_pcs": (top_defect or {}).get("scrap_pcs") if top_defect else None,
            "defects": defects,
            "processes": processes,
            "unit_price": unit_price,
            "loss_est": loss_est,
            "loss_label": loss_label,
            "recommend_8d": recommend,
            "recommend_reason": recommend_reason,
            "open_8d": open_8d,
            "closed_8d": closed_8d,
            "stock_source": scrap.get("stock_source"),
            "data_gap": scrap.get("data_gap"),
            "formula": "报废率=报废/(报废+入库)；无入库不算率；损失=单价×报废PCS（仅有单价）",
            "data_source": scrap.get("data_source") or "ERP:View_P_Inspection+FGI",
            "policy": "仅 ERP/MES 实据；无入库不算报废率；无单价不估损失；禁止编造",
        }

    @staticmethod
    def kpi_formula_catalog() -> dict:
        """仪表板口径说明（前端「口径」展开用）。"""
        return {
            "input_pcs": {
                "name": "投入PCS",
                "formula": "合格数量（入库） + 报废PCS",
                "source": "FGI 入库 + View_P_Inspection 报废",
            },
            "scrap_pcs": {
                "name": "报废PCS",
                "formula": "SUM(QtyOfUnit) WHERE InspectionResult=Scrapped（排除非生产/测试/TEST-）",
                "source": "ERP:View_P_Inspection",
            },
            "scrap_rate": {
                "name": "报废率",
                "formula": "报废PCS / (报废PCS + 入库PCS)；无入库基数时显示「—」不造率",
                "source": "ERP 报废 + FGI/工单入库",
            },
            "pass_qty": {
                "name": "合格数量",
                "formula": "本期生产入库 PCS（无 FGI 时回退工单产出并标注）",
                "source": "ERP:FGI_StockFormItemWO",
            },
            "pass_rate": {
                "name": "合格率",
                "formula": "入库PCS / (报废PCS + 入库PCS)；与报废率同源互补",
                "source": "同报废率分母",
            },
        }

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
            "kpi_formulas": self.kpi_formula_catalog(),
            "closed_loop": {
                "fiscal_period": fiscal_period,
                "abnormal_parts": 0,
                "eight_d_open": 0,
                "eight_d_closed": 0,
                "has_data": False,
            },
        }

        if self.erp and self.erp.connected:
            try:
                scrap_data = self.get_scrap_analysis(None, None, None, fiscal_period=fiscal_period)
                tr = scrap_data.get("total_scrap_pcs", 0)
                ti = scrap_data.get("total_in_stock_pcs", 0)

                # 工单数优先用 P_WO 当月，避免仅统计报废条码导致偏少
                wo_count = 0
                r_wo = self.erp.execute(
                    "SELECT COUNT(*) AS TotalWo FROM P_WO WHERE FiscalPeriod = ?",
                    (fiscal_period,),
                )
                if r_wo.get("success") and r_wo.get("data"):
                    wo_count = int(r_wo["data"][0].get("TotalWo") or 0)
                if wo_count <= 0:
                    r_wo2 = self.erp.execute(
                        """
                        SELECT COUNT(DISTINCT MOBarCode) AS TotalWo
                        FROM View_P_Inspection
                        WHERE InspectionResult = 'Scrapped' AND FiscalPeriod = ?
                        """,
                        (fiscal_period,),
                    )
                    if r_wo2.get("success") and r_wo2.get("data"):
                        wo_count = int(r_wo2["data"][0].get("TotalWo") or 0)

                # 统一口径：
                #   合格数量 = 入库 PCS（良品）
                #   报废 PCS = MRB 报废
                #   投入 PCS = 合格 + 报废
                #   报废率 = 报废 / 投入；仅当入库 > 0 才算可靠
                pass_qty = int(ti or 0)
                scrap_qty = int(tr or 0)
                input_qty = pass_qty + scrap_qty
                kpi_reliable = bool(pass_qty > 0 and input_qty > 0)
                if kpi_reliable:
                    scrap_rate = round(scrap_qty * 100.0 / input_qty, 2)
                    pass_rate = round(pass_qty * 100.0 / input_qty, 2)
                elif scrap_qty > 0:
                    # 有报废但无入库基数：不给 100% 伪高
                    scrap_rate = None
                    pass_rate = None
                else:
                    scrap_rate = 0
                    pass_rate = 0
                result["kpi"] = {
                    "total_wo": wo_count,
                    "total_input": input_qty,
                    "total_reject": scrap_qty,
                    "reject_rate": scrap_rate,
                    "pass_qty": pass_qty,
                    "pass_rate": pass_rate,
                    "rate_reliable": kpi_reliable,
                    "stock_source": scrap_data.get("stock_source") or "none",
                }

                # TOP报废料号（当月，统一生产口径：排除非量产工序/项目/测试/TEST-）
                r_parts = self.erp.execute(
                    f"""
                    SELECT TOP 10
                        v.MfgPartCode AS PartNumber,
                        IFNULL(MAX(mp.mfgPartName), v.MfgPartCode) AS PartName,
                        SUM(v.QtyOfUnit) AS TotalReject
                    FROM View_P_Inspection v
                    LEFT JOIN E_JobMfgParts mp ON mp.mfgPartCode = v.MfgPartCode
                    WHERE {self._scrap_non_production_sql()}
                      AND v.FiscalPeriod = ?
                      AND v.MfgPartCode IS NOT NULL AND v.MfgPartCode <> ''
                    GROUP BY v.MfgPartCode
                    ORDER BY SUM(v.QtyOfUnit) DESC
                    """,
                    (fiscal_period,),
                )
                stock_map = {}
                r_stock = self.erp.execute(
                    f"""
                    SELECT mp.mfgPartCode AS PartNumber, SUM(sfiwo.qtyOfPCS) AS TotalInput
                    FROM FGI_StockFormItemWO sfiwo
                    INNER JOIN P_WO wo ON sfiwo.woId = wo.recId
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE wo.FiscalPeriod = ?
                      AND {self._scrap_part_excluded_sql('mp.mfgPartCode')}
                    GROUP BY mp.mfgPartCode
                    """,
                    (fiscal_period,),
                )
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
                        r_one = self.erp.execute(
                            f"""
                            SELECT IFNULL(SUM(QtyOfUnit),0) AS TotalReject,
                                   IFNULL(MAX(mp.mfgPartName), ?) AS PartName
                            FROM View_P_Inspection v
                            LEFT JOIN E_JobMfgParts mp ON mp.mfgPartCode = v.MfgPartCode
                            WHERE {self._scrap_non_production_sql()}
                              AND v.FiscalPeriod=?
                              AND v.MfgPartCode=?
                            """,
                            (pn, fiscal_period, pn),
                        )
                        reject = 0
                        pname = pn
                        if r_one.get("success") and r_one.get("data"):
                            reject = int(float(r_one["data"][0].get("TotalReject") or 0))
                            pname = r_one["data"][0].get("PartName") or pn
                        if reject <= 0:
                            continue
                        if inp <= 0:  # 无入库基数不产出伪 100% 报废率
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
                min_n = int(getattr(self.engine, "ALERT_MIN_INSPECT", 15))
                # 两种口径分开算，禁止用「检验次数通过率」冒充「单板一次通过率」：
                #   InspectionPassRate = 合格检验笔数 / 检验笔数（同一块板多次检验各算一次）
                #   UnitFirstPassRate  = 该板在该工序首次检验即合格的条码数 / 受检条码数（去重）
                r = self.mes.execute(f"""
                    SELECT
                        ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                        COUNT(*) AS InspectCount,
                        SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END) AS PassCount,
                        CAST(SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END)*100.0/COUNT(*) AS DECIMAL(5,2)) AS InspectionPassRate
                    FROM TBL_QM_INSPECT_RECORD r
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT IS NOT NULL
                    GROUP BY p.CPROCESS_NAME
                    HAVING COUNT(*) >= {min_n}
                    ORDER BY InspectCount DESC
                """)
                if r.get("success"):
                    rows = r.get("data", []) or []
                    unit_map = {}
                    r_unit = self.mes.execute("""
                        WITH ranked AS (
                            SELECT r.CPROCESS_ID, r.CSCAN_BARCODE, r.CRESULT,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY r.CPROCESS_ID, r.CSCAN_BARCODE
                                       ORDER BY r.CCREATE_DATE, r.CID
                                   ) AS rn
                            FROM TBL_QM_INSPECT_RECORD r
                            WHERE r.CRESULT IS NOT NULL
                              AND IFNULL(r.CSCAN_BARCODE, '') <> ''
                        )
                        SELECT ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                               COUNT(*) AS UnitCount,
                               SUM(CASE WHEN x.rn=1 AND x.CRESULT=1 THEN 1 ELSE 0 END) AS UnitFirstPassCount
                        FROM ranked x
                        LEFT JOIN TBL_BD_PROCESS p ON x.CPROCESS_ID = p.CID
                        GROUP BY p.CPROCESS_NAME
                    """)
                    if r_unit.get("success"):
                        for u in (r_unit.get("data") or []):
                            unit_map[u.get("ProcessName") or ""] = u
                    else:
                        result["process_overview_unit_gap"] = (
                            "单板一次通过率未计算：" + str(r_unit.get("error") or "条码去重查询失败")
                        )
                    for row in rows:
                        insp_rate = row.get("InspectionPassRate")
                        if insp_rate is None:
                            insp_count = int(row.get("InspectCount") or 0)
                            insp_rate = (
                                round(int(row.get("PassCount") or 0) * 100.0 / insp_count, 2)
                                if insp_count else None
                            )
                        row["InspectionPassRate"] = (
                            round(float(insp_rate), 2) if insp_rate is not None else None
                        )
                        row["PassRate"] = row["InspectionPassRate"]  # 兼容旧前端字段（检验通过率）
                        u = unit_map.get(row.get("ProcessName") or "") or {}
                        units = int(u.get("UnitCount") or 0)
                        first_pass = int(u.get("UnitFirstPassCount") or 0)
                        row["UnitCount"] = units
                        row["UnitFirstPassCount"] = first_pass
                        row["UnitFirstPassRate"] = (
                            round(first_pass * 100.0 / units, 2) if units else None
                        )
                    # 只取样本量足够的工序按检验通过率升序，避免 1 次检验即垫底
                    rows = [x for x in rows if (x.get("ProcessName") or "").strip()]
                    rows.sort(key=lambda x: float(x.get("PassRate") or 100))
                    result["process_overview"] = rows[:8]
                    result["process_overview_min_sample"] = min_n
                    result["process_overview_basis"] = (
                        "检验通过率=合格检验笔数/检验笔数；"
                        "单板一次通过率=该工序首次检验即合格的条码数/受检条码数（按条码去重）；"
                        "统计范围为 MES 全周期（不限当月）"
                    )
            except Exception as e:
                logger.warning(f"MES仪表板数据: {e}")

        # 左侧排行榜：料号 → 缺陷项 → 低良率工序，避免空界面
        if result["top_defect_parts"]:
            result["ranking_mode"] = "scrap_parts"
            result["ranking_title"] = f"TOP报废料号"
            result["ranking_basis"] = "ERP 检验报废实绩（View_P_Inspection）"
        elif result["top_defect_items"]:
            result["ranking_mode"] = "defect_items"
            result["ranking_title"] = f"TOP报废缺陷"
            result["ranking_basis"] = "ERP 检验报废缺陷项"
        elif result["process_overview"]:
            # 没有任何 ERP 报废实绩时，降级展示 MES 工序合格率（非报废口径）
            low = result["process_overview"][:8]
            result["ranking_mode"] = "low_yield"
            result["ranking_title"] = "关注工序（MES 检验通过率偏低）"
            result["ranking_basis"] = (
                f"{result.get('process_overview_basis') or 'MES 工序检验通过率'}；"
                f"样本≥{result.get('process_overview_min_sample', 15)} 笔；"
                "非报废 PCS 口径"
            )
            result["top_defect_parts"] = [
                {
                    "PartNumber": p.get("ProcessName"),
                    "PartName": f"检验 {int(p.get('InspectCount') or 0)} 次",
                    "TotalReject": int(
                        float(p.get("InspectCount") or 0) - float(p.get("PassCount") or 0)
                    ),
                    "TotalInput": int(p.get("InspectCount") or 0),
                    "RejectRate": round(100 - float(p.get("InspectionPassRate") or 0), 2),
                    "InspectionPassRate": p.get("InspectionPassRate"),
                    "UnitFirstPassRate": p.get("UnitFirstPassRate"),
                    "is_process": True,
                    "record_type": "process_yield",
                }
                for p in low
            ]
        else:
            result["ranking_mode"] = "empty"
            result["ranking_title"] = "品质关注项"
            result["ranking_basis"] = "本期无 ERP 报废或 MES 工序数据"

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

        kpi_rate = (result.get("kpi") or {}).get("reject_rate")
        kpi_pass = (result.get("kpi") or {}).get("pass_rate")
        kpi_reliable = bool((result.get("kpi") or {}).get("rate_reliable"))
        cur_rate = float(kpi_rate) if kpi_rate is not None else 0.0
        cur_pass = float(kpi_pass) if kpi_pass is not None else 0.0
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
                if p_rate is not None and kpi_reliable:
                    mom["scrap_rate_delta"] = round(cur_rate - p_rate, 2)
                if p_pass is not None and kpi_reliable:
                    mom["pass_rate_delta"] = round(cur_pass - p_pass, 2)
                if p_input > 0 and p_stock > 0 and kpi_reliable:
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

        # 目标达成状态（无入库基数→na，不判定达标/超标）
        if not kpi_reliable:
            scrap_status = "na"
            scrap_vs_target = None
        else:
            scrap_status = (
                "ok" if cur_rate <= TARGET_SCRAP_RATE
                else ("warn" if cur_rate <= WARN_SCRAP_RATE else "crit")
            )
            scrap_vs_target = round(cur_rate - TARGET_SCRAP_RATE, 2)
        pass_status = "na" if not kpi_reliable else (
            "ok" if cur_pass >= TARGET_PASS_RATE else "warn"
        )
        vs_target = {
            "scrap_vs_target": scrap_vs_target,
            "scrap_status": scrap_status,
            "scrap_reliable": kpi_reliable,
            "pass_vs_target": round(cur_pass - TARGET_PASS_RATE, 2) if kpi_reliable else None,
            "pass_status": pass_status,
        }
        result["vs_target"] = vs_target

        # ── 领导一眼可读：指标达成看板（达标/关注/严重）──
        achieve_items = []
        # 1) 报废率
        scrap_st = vs_target["scrap_status"]
        achieve_items.append({
            "key": "scrap_rate",
            "name": "报废率",
            "value": cur_rate if kpi_reliable else None,
            "unit": "%",
            "target": TARGET_SCRAP_RATE,
            "target_text": f"≤{TARGET_SCRAP_RATE:g}%",
            "delta": vs_target["scrap_vs_target"],
            "status": scrap_st,
            "status_label": {
                "ok": "达标", "warn": "关注", "crit": "严重", "na": "缺入库基数"
            }.get(scrap_st, "—"),
            "hint": (
                f"较目标 {vs_target['scrap_vs_target']:+.2f}pp"
                if vs_target.get("scrap_vs_target") is not None
                else "无入库基数，不计算报废率"
            ),
        })
        # 2) 合格率
        pass_st = vs_target["pass_status"]
        achieve_items.append({
            "key": "pass_rate",
            "name": "合格率",
            "value": cur_pass if kpi_reliable else None,
            "unit": "%",
            "target": TARGET_PASS_RATE,
            "target_text": f"≥{TARGET_PASS_RATE:g}%",
            "delta": vs_target["pass_vs_target"],
            "status": pass_st,
            "status_label": {
                "ok": "达标", "warn": "未达标", "crit": "严重", "na": "缺入库基数"
            }.get(pass_st, "—"),
            "hint": (
                f"较目标 {vs_target['pass_vs_target']:+.2f}pp"
                if vs_target.get("pass_vs_target") is not None
                else "无入库基数，不计算合格率"
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
            # 与 FiscalPeriod 对齐：当月 1 日 → 当月最后一日（COPQ 内部转右开）
            start_m = f"{str(fiscal_period)[:4]}-{str(fiscal_period)[4:6]}-01"
            end_m = _month_end_inclusive(str(fiscal_period))
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
        if kpi_reliable:
            briefing_lines.append(
                f"【{period_label}】投入 {cur_input:,} PCS，报废 {cur_scrap:,} PCS，"
                f"报废率 {cur_rate:.2f}%（目标 ≤{TARGET_SCRAP_RATE:g}%），合格率 {cur_pass:.2f}%。"
            )
        else:
            briefing_lines.append(
                f"【{period_label}】入库 0 PCS、报废 {cur_scrap:,} PCS：缺生产入库基数，"
                "报废率/合格率不计算（请在 ERP 补充 FGI 生产入库）。"
            )
        if not kpi_reliable:
            pass
        elif vs_target["scrap_status"] == "ok":
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
            if result.get("ranking_mode") == "low_yield":
                briefing_lines.append(
                    f"关注工序 TOP1：{p0.get('PartNumber')}（MES 检验 "
                    f"{int(p0.get('TotalInput') or 0):,} 次，合格率 "
                    f"{round(100 - float(p0.get('RejectRate') or 0), 2)}%）。"
                )
            else:
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
        result["kpi_formulas"] = self.kpi_formula_catalog()
        try:
            result["closed_loop"] = self.get_closed_loop_stats(fiscal_period)
        except Exception as e:
            result["closed_loop"] = {
                "fiscal_period": fiscal_period,
                "abnormal_parts": 0,
                "eight_d_open": 0,
                "eight_d_closed": 0,
                "has_data": False,
                "data_gap": str(e),
            }
        if result.get("ranking_mode") == "scrap_parts" and (result.get("top_defect_parts") or []):
            result["spotlight_part"] = (result["top_defect_parts"][0] or {}).get("PartNumber")
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
        """良率趋势：整体 / 工序 / 客户 / 关键指标。

        仅用 MES 检验实据 + ERP 报废/入库实据；禁止 quality_metrics 造数续轴。
        """
        empty = {
            "days": [],
            "overall": [],
            "processes": {},
            "customers": {},
            "metrics": {
                "inspect_count": [], "scrap_rate": [], "ng_rate": [],
                "formula": (
                    "不良率=MES检验不合格笔数/检验笔数；"
                    "报废率=ERP报废PCS/(报废+入库PCS)；无入库日不计算报废率"
                ),
                "data_source": "MES:quality_inspection_records + ERP:View_P_Inspection/FGI",
            },
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

            # 趋势轴与整体良率：仅 quality_inspection_records（MES 检验桥接实据）
            # 禁止用 quality_metrics 造数续轴（常见于无检验的「蚀刻」伪日汇总）
            day_list = sorted(insp_map.keys())
            overall = [insp_map[d]["yield"] for d in day_list]

            # 关键指标：
            #   不良率/检验量 ← MES 检验笔数
            #   报废率 ← ERP 报废PCS/(报废+入库PCS)；无入库日记 None（不伪造）
            inspect_count = [insp_map[d]["total"] for d in day_list]
            ng_rate = [insp_map[d]["ng_rate"] for d in day_list]
            scrap_rate = [None] * len(day_list)
            if self.erp and self.erp.connected and day_list:
                scrap_filter = self._scrap_non_production_sql()
                rs = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', v.CreateDate) AS day,
                           SUM(v.QtyOfUnit) AS scrap
                    FROM View_P_Inspection v
                    WHERE {scrap_filter}
                      AND v.CreateDate >= date('now', '-{n} days')
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
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE sfi.stockDate >= date('now', '-{n} days')
                      AND {self._scrap_part_excluded_sql('mp.mfgPartCode')}
                    GROUP BY day ORDER BY day
                """)
                stock_map = {
                    row.get("day"): float(row.get("stock") or 0)
                    for row in (rst.get("data") or []) if row.get("day")
                }
                scrap_rate = []
                for d in day_list:
                    sc = scrap_map.get(d, 0.0)
                    st = stock_map.get(d, 0.0)
                    scrap_rate.append(
                        round(sc * 100.0 / (sc + st), 2) if st > 0 else None
                    )

            r_proc = db.execute(f"""
                SELECT {proc_expr} AS pname, COUNT(*) AS c
                FROM quality_inspection_records
                WHERE inspected_at >= date('now', '-{n} days')
                GROUP BY pname ORDER BY c DESC LIMIT 5
            """)
            process_names = []
            for x in (r_proc.get("data") or []):
                pname = normalize_process_name(x.get("pname"))
                if pname and pname not in process_names:
                    process_names.append(pname)
            # 只保留检验实据中出现的工序，不把无数据的标准名硬塞进趋势图
            process_names = process_names[:5]

            processes = {}
            for pname in process_names:
                aliases = aliases_including_self(pname)
                in_list = "', '".join(a.replace("'", "''") for a in aliases)
                rp = db.execute(f"""
                    SELECT strftime('%Y-%m-%d', inspected_at) AS day,
                           COUNT(*) AS total,
                           SUM(CASE WHEN IFNULL(source_result,0)=1
                                     OR LOWER(IFNULL(inspection_result,'')) IN ('pass','ok')
                                THEN 1 ELSE 0 END) AS pass_cnt
                    FROM quality_inspection_records
                    WHERE inspected_at >= date('now', '-{n} days')
                      AND {proc_expr} IN ('{in_list}')
                    GROUP BY day ORDER BY day
                """)
                series = {row.get("day"): round(
                    int(row.get("pass_cnt") or 0) * 100.0 / (int(row.get("total") or 1)), 2
                ) for row in (rp.get("data") or [])}
                # 仅检验实据；不用 quality_metrics 续无检验日
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

            return {
                "days": day_list,
                "overall": overall,
                "processes": processes,
                "customers": customers,
                "metrics": {
                    "inspect_count": inspect_count,
                    "scrap_rate": scrap_rate,
                    "ng_rate": ng_rate,
                    "formula": (
                        "不良率=MES检验不合格笔数/检验笔数；"
                        "报废率=ERP报废PCS/(报废+入库PCS)；"
                        "无入库日不计算报废率"
                    ),
                    "data_source": "MES:quality_inspection_records + ERP:View_P_Inspection/FGI",
                },
                "process_names": process_names,
                "customer_names": customer_names,
            }
        except Exception as e:
            logger.warning(f"良率趋势分析失败: {e}")
            return empty

    # 非量产料号前缀（如 TEST-E200）：不得进入生产报废率/预警/8D 口径
    SCRAP_EXCLUDED_PART_PREFIXES = ("TEST-",)

    def _scrap_part_excluded_sql(self, column: str = "v.MfgPartCode") -> str:
        """生产料号排除条件（TEST- 等非量产料号）。"""
        return " AND ".join(
            f"ISNULL({column}, '') NOT LIKE '{p}%'" for p in self.SCRAP_EXCLUDED_PART_PREFIXES
        )

    def _scrap_non_production_sql(self):
        """生产报废统一口径（View_P_Inspection 别名 v）：
        仅 Scrapped；排除非生产工序/项目、测试工序及项目、TEST- 非量产料号。
        """
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
            "AND ISNULL(v.InspectionItem, '') NOT LIKE '%测试%' "
            f"AND {self._scrap_part_excluded_sql('v.MfgPartCode')}"
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
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE sfi.stockDate >= '{start_d}'
                  AND {self._scrap_part_excluded_sql('mp.mfgPartCode')}
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

            # 工序报废：归一到标准 PCB 目录后取 TOP（含旧名别名）
            r_proc = self.erp.execute(f"""
                SELECT
                    ISNULL(NULLIF(v.InspectionProcess, ''), '未知') AS pname,
                    SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {scrap_filter}
                  AND v.CreateDate >= '{start_d}'
                GROUP BY ISNULL(NULLIF(v.InspectionProcess, ''), '未知')
                ORDER BY SUM(v.QtyOfUnit) DESC
            """)
            scrap_by_std: dict[str, float] = {}
            for x in (r_proc.get("data") or []):
                std = normalize_process_name(x.get("pname")) or "待确认工序"
                scrap_by_std[std] = scrap_by_std.get(std, 0) + float(x.get("scrap") or 0)
            process_names = [
                p for p, _ in sorted(scrap_by_std.items(), key=lambda kv: -kv[1])
                if p
            ][:5]
            if not process_names:
                process_names = [p for p in STANDARD_PROCESSES if p != "待确认工序"][:4]
            processes = {}
            for pname in process_names:
                aliases = aliases_including_self(pname)
                in_list = "', '".join(a.replace("'", "''") for a in aliases)
                rp = self.erp.execute(f"""
                    SELECT strftime('%Y-%m-%d', v.CreateDate) AS day,
                           SUM(v.QtyOfUnit) AS scrap
                    FROM View_P_Inspection v
                    WHERE {scrap_filter}
                      AND v.CreateDate >= '{start_d}'
                      AND ISNULL(NULLIF(v.InspectionProcess, ''), '未知') IN ('{in_list}')
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
            # 默认近 90 天（含今天），覆盖当月客诉与环比
            end = datetime.now().date()
            start = end - timedelta(days=90)
            start_date = start.isoformat()
            end_date = end.isoformat()

        end_excl = _end_exclusive(end_date)
        result["date_filter"] = {
            "start_date": start_date,
            "end_date": end_date,
            "end_exclusive": end_excl,
        }
        conds = ["1=1"]
        if start_date:
            conds.append(f"complaint_date >= '{start_date}'")
        if end_excl:
            conds.append(f"complaint_date < '{end_excl}'")
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
            end_cond = f" AND v.CreateDate < '{end_excl}'" if end_excl else ""
            # 全量按料号聚合：截图/榜单只展示前 30，但 KPI 与成本必须覆盖全部料号，
            # 不能用 TOP30 之和冒充本期报废总量（会系统性少计）。
            scrap_date_conds = []
            if start_date:
                scrap_date_conds.append(f"AND v.CreateDate >= '{start_date}'")
            if end_cond:
                scrap_date_conds.append(end_cond)
            r_parts = self.erp.execute(f"""
                SELECT v.MfgPartCode AS pn, SUM(v.QtyOfUnit) AS scrap
                FROM View_P_Inspection v
                WHERE {self._scrap_non_production_sql()}
                  {' '.join(scrap_date_conds)}
                  {part_filter}
                GROUP BY v.MfgPartCode
                ORDER BY SUM(v.QtyOfUnit) DESC
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
            unpriced_parts = 0
            detail_cap = 30
            total_parts = 0
            for row in (r_parts.get("data") or []):
                pn = str(row.get("pn") or "").strip()
                pcs = int(float(row.get("scrap") or 0))
                total_parts += 1
                scrap_pcs += pcs
                price = price_map.get(pn)
                src = price_src.get(pn)
                # 仅允许精确料号匹配；禁止用无关物料均价冒充成品成本
                if price is None:
                    unpriced_pcs += pcs
                    unpriced_parts += 1
                    if len(result["scrap_cost_by_part"]) < detail_cap:
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
                if len(result["scrap_cost_by_part"]) < detail_cap:
                    result["scrap_cost_by_part"].append({
                        "part_number": pn,
                        "scrap_pcs": pcs,
                        "unit_price": price,
                        "cost_est": amt,
                        "priced": True,
                        "price_source": src,
                    })
            result["scrap_cost_by_part_total_parts"] = total_parts
            result["scrap_cost_by_part_truncated"] = total_parts > len(result["scrap_cost_by_part"])

            total_scrap = scrap_pcs or int(scrap.get("total_scrap_pcs") or 0)
            result["kpi"]["scrap_pcs"] = total_scrap
            result["kpi"]["scrap_cost_est"] = round(scrap_cost, 2)
            result["kpi"]["scrap_priced_pcs"] = priced_pcs
            result["kpi"]["scrap_unpriced_pcs"] = unpriced_pcs
            result["kpi"]["scrap_unpriced_parts"] = unpriced_parts
            result["kpi"]["scrap_price_coverage"] = (
                round(priced_pcs * 100.0 / scrap_pcs, 1) if scrap_pcs > 0 else None
            )
            result["price_policy"] = (
                "单价仅取 materials.material_code / customer_complaints.product_code "
                "精确匹配；匹配不到的料号不计价、不估算，避免用无关物料均价冒充"
            )
            result["data_source"] = "ERP:customer_complaints + materials + View_P_Inspection"
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
                ORDER BY id
            """)
            rows = r.get("data") or []
            chart_ids = [int(x["id"]) for x in rows if x.get("id") is not None]
            meas_map: Dict[int, list] = {}
            if chart_ids:
                in_ids = ",".join(str(i) for i in chart_ids)
                rm_all = self.erp.execute(f"""
                    SELECT chart_id, measured_at, value, subgroup_index, sample_label
                    FROM spc_measurements
                    WHERE chart_id IN ({in_ids})
                    ORDER BY chart_id, subgroup_index, measured_at
                """)
                for mrow in (rm_all.get("data") or []):
                    try:
                        cid = int(mrow.get("chart_id"))
                    except (TypeError, ValueError):
                        continue
                    meas_map.setdefault(cid, []).append(mrow)

            charts = []
            cpk_vals = []
            alert_n = 0
            insufficient_n = 0
            unverified_n = 0
            stats_by_id: Dict[int, tuple] = {}
            for row in rows:
                cid = int(row["id"]) if row.get("id") is not None else None
                recs = meas_map.get(cid, []) if cid is not None else []
                subgroup_size = row.get("subgroup_size") or 1
                calc = None
                if recs:
                    calc = _spc_compute(
                        [x.get("value") for x in recs],
                        subgroup_size=subgroup_size,
                        subgroup_ids=[x.get("subgroup_index") for x in recs],
                        usl=row.get("usl"), lsl=row.get("lsl"),
                        ucl=row.get("ucl"), cl=row.get("cl"), lcl=row.get("lcl"),
                    )
                    stats_by_id[cid] = (calc, recs, subgroup_size)
                stored_status = str(row.get("status") or "").strip().lower() or None
                if calc and calc.get("status"):
                    eff_status = calc["status"]
                    status_source = "recomputed_from_measurements"
                else:
                    eff_status = stored_status or "normal"
                    status_source = "stored_no_measurements"
                    unverified_n += 1
                if eff_status == "alert":
                    alert_n += 1
                elif eff_status == "insufficient":
                    insufficient_n += 1
                eff_cp = calc.get("cp") if calc and calc.get("cp") is not None else row.get("cp")
                eff_cpk = calc.get("cpk") if calc and calc.get("cpk") is not None else row.get("cpk")
                if eff_cpk is not None:
                    try:
                        cpk_vals.append(float(eff_cpk))
                    except (TypeError, ValueError):
                        pass
                eff_ucl = calc.get("ucl") if calc else row.get("ucl")
                eff_cl = calc.get("cl") if calc else row.get("cl")
                eff_lcl = calc.get("lcl") if calc else row.get("lcl")
                charts.append({
                    "id": row.get("id"),
                    "chart_code": row.get("chart_code"),
                    "chart_name": row.get("chart_name"),
                    "process_name": row.get("process_name"),
                    "characteristic": row.get("characteristic"),
                    "chart_type": row.get("chart_type"),
                    "subgroup_size": subgroup_size,
                    "unit": row.get("unit") or "",
                    "usl": row.get("usl"),
                    "lsl": row.get("lsl"),
                    "target": row.get("target"),
                    "ucl": round(eff_ucl, 4) if eff_ucl is not None else None,
                    "cl": round(eff_cl, 4) if eff_cl is not None else None,
                    "lcl": round(eff_lcl, 4) if eff_lcl is not None else None,
                    "cp": round(float(eff_cp), 3) if eff_cp is not None else None,
                    "cpk": round(float(eff_cpk), 3) if eff_cpk is not None else None,
                    "status": eff_status,
                    "status_stored": stored_status,
                    "status_source": status_source,
                    "status_consistent": (
                        None if (stored_status is None or not calc)
                        else (stored_status == eff_status)
                    ),
                    "cp_stored": row.get("cp"),
                    "cpk_stored": row.get("cpk"),
                    "sample_count": (calc or {}).get("sample_count", 0),
                    "measurement_count": (calc or {}).get("measurement_count", 0),
                    "out_of_control_count": (calc or {}).get("out_of_control", 0),
                    "out_of_spec_count": (calc or {}).get("out_of_spec", 0),
                    "limits_source": (calc or {}).get("limits_source") or "stored",
                    "updated_at": row.get("updated_at"),
                })
            # 告警优先，其次样本不足，最后按 id
            charts.sort(key=lambda c: (
                0 if c.get("status") == "alert" else 1,
                0 if c.get("status") == "insufficient" else 1,
                c.get("id") or 0,
            ))
            result["charts"] = charts
            result["summary"] = {
                "total": len(charts),
                "alert": alert_n,
                "normal": sum(1 for c in charts if c.get("status") == "normal"),
                "insufficient": insufficient_n,
                "unverified": unverified_n,
                "avg_cpk": round(sum(cpk_vals) / len(cpk_vals), 3) if cpk_vals else None,
                "status_source": "recomputed_from_measurements",
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
                try:
                    did = int(detail_id)
                except (TypeError, ValueError):
                    did = None
                if did is not None:
                    rd = self.erp.execute(f"SELECT * FROM spc_charts WHERE id = {did}")
                    if rd.get("success") and rd.get("data"):
                        d = rd["data"][0]
                        recs = meas_map.get(did)
                        if recs is None:
                            rm = self.erp.execute(f"""
                                SELECT chart_id, measured_at, value, subgroup_index, sample_label
                                FROM spc_measurements
                                WHERE chart_id = {did}
                                ORDER BY subgroup_index, measured_at
                            """)
                            recs = rm.get("data") or []
                        sub_size = int(d.get("subgroup_size") or 1)
                        calc = None
                        if recs:
                            calc = _spc_compute(
                                [x.get("value") for x in recs],
                                subgroup_size=sub_size,
                                subgroup_ids=[x.get("subgroup_index") for x in recs],
                                usl=d.get("usl"), lsl=d.get("lsl"),
                                ucl=d.get("ucl"), cl=d.get("cl"), lcl=d.get("lcl"),
                            )
                        measurements = [{
                            "measured_at": x.get("measured_at"),
                            "value": x.get("value"),
                            "subgroup_index": x.get("subgroup_index"),
                            "sample_label": x.get("sample_label"),
                        } for x in recs]

                        raw_dp = d.get("data_points") or "[]"
                        try:
                            stored_points = json.loads(raw_dp) if isinstance(raw_dp, str) else (raw_dp or [])
                        except Exception:
                            stored_points = []

                        if calc:
                            # 用 spc_measurements 重建绘图点：Xbar 图为子组均值，I-MR 为单值
                            points = []
                            if sub_size > 1:
                                groups: Dict[Any, list] = {}
                                order: list[Any] = []
                                missing_ids = all(x.get("subgroup_index") is None for x in recs)
                                if missing_ids:
                                    for i in range(0, len(recs), sub_size):
                                        key = ("s", i // sub_size)
                                        groups[key] = recs[i:i + sub_size]
                                        order.append(key)
                                else:
                                    for x in recs:
                                        key = x.get("subgroup_index")
                                        if key not in groups:
                                            groups[key] = []
                                            order.append(key)
                                        groups[key].append(x)
                                for k, key in enumerate(order, 1):
                                    grp = groups[key]
                                    gvals = [float(x["value"]) for x in grp if x.get("value") is not None]
                                    if not gvals:
                                        continue
                                    gmean = sum(gvals) / len(gvals)
                                    grng = (max(gvals) - min(gvals)) if len(gvals) > 1 else None
                                    points.append({
                                        "index": k,
                                        "value": round(gmean, 4),
                                        "range": round(grng, 4) if grng is not None else None,
                                        "subgroup_index": None if isinstance(key, tuple) else key,
                                        "sample_label": grp[0].get("sample_label"),
                                        "measured_at": grp[-1].get("measured_at"),
                                    })
                            else:
                                for k, x in enumerate(recs, 1):
                                    points.append({
                                        "index": k,
                                        "value": x.get("value"),
                                        "subgroup_index": x.get("subgroup_index"),
                                        "sample_label": x.get("sample_label"),
                                        "measured_at": x.get("measured_at"),
                                    })
                            for p in points:
                                try:
                                    pv = float(p["value"])
                                except (TypeError, ValueError):
                                    p["out_of_control"] = False
                                    p["out_of_spec"] = False
                                    continue
                                p["out_of_control"] = bool(
                                    (calc["ucl"] is not None and pv > calc["ucl"])
                                    or (calc["lcl"] is not None and pv < calc["lcl"])
                                )
                                p["out_of_spec"] = bool(
                                    (d.get("usl") is not None and pv > float(d["usl"]))
                                    or (d.get("lsl") is not None and pv < float(d["lsl"]))
                                )
                            ooc = [p for p in points if p.get("out_of_control")]
                            data_points_source = "spc_measurements"
                        else:
                            points = stored_points
                            ooc = [p for p in points if p.get("out_of_control")]
                            data_points_source = "stored_json_no_measurements"

                        eff_status = (
                            calc["status"] if calc and calc.get("status")
                            else (d.get("status") or "normal")
                        )
                        eff_cp = calc["cp"] if calc and calc.get("cp") is not None else d.get("cp")
                        eff_cpk = calc["cpk"] if calc and calc.get("cpk") is not None else d.get("cpk")
                        eff_ucl = calc["ucl"] if calc else d.get("ucl")
                        eff_cl = calc["cl"] if calc else d.get("cl")
                        eff_lcl = calc["lcl"] if calc else d.get("lcl")
                        result["detail"] = {
                            "id": d.get("id"),
                            "chart_code": d.get("chart_code"),
                            "chart_name": d.get("chart_name"),
                            "process_name": d.get("process_name"),
                            "characteristic": d.get("characteristic"),
                            "chart_type": d.get("chart_type"),
                            "subgroup_size": sub_size,
                            "unit": d.get("unit") or "",
                            "usl": d.get("usl"), "lsl": d.get("lsl"), "target": d.get("target"),
                            "ucl": round(eff_ucl, 4) if eff_ucl is not None else None,
                            "cl": round(eff_cl, 4) if eff_cl is not None else None,
                            "lcl": round(eff_lcl, 4) if eff_lcl is not None else None,
                            "cp": round(float(eff_cp), 3) if eff_cp is not None else None,
                            "cpk": round(float(eff_cpk), 3) if eff_cpk is not None else None,
                            "cp_stored": d.get("cp"), "cpk_stored": d.get("cpk"),
                            "status": eff_status,
                            "status_stored": d.get("status"),
                            "status_source": (
                                "recomputed_from_measurements" if calc
                                else "stored_no_measurements"
                            ),
                            "limits_source": (calc or {}).get("limits_source") or "stored",
                            "updated_at": d.get("updated_at"),
                            "data_points": points,
                            "data_points_source": data_points_source,
                            "stored_point_count": len(stored_points),
                            "measurements": measurements,
                            "sample_count": (calc or {}).get("sample_count", len(points)),
                            "measurement_count": len(recs),
                            "out_of_control_count": len(ooc),
                            "out_of_control_points": ooc,
                            "out_of_spec_count": (calc or {}).get("out_of_spec", 0),
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
                "pending_count_scope": "当前全量待审（按工序筛选，不受近N日窗口限制）",
            },
            "process_rates": [],
            "by_inspect_type": [],
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
            q_proc_filter = ""
            q_proc_filter_alias = ""
            if process_name:
                aliases = aliases_including_self(str(process_name).strip())
                in_list = "', '".join(a.replace("'", "''") for a in aliases)
                proc_filter = (
                    f" AND IFNULL(p.CPROCESS_NAME,'待确认工序') IN ('{in_list}')"
                )
                # quality_inspection_records 同口径工序过滤（未取别名的查询/取别名 q 的查询各一份）
                q_proc_filter = f" AND IFNULL(process_name,'') IN ('{in_list}')"
                q_proc_filter_alias = f" AND IFNULL(q.process_name,'') IN ('{in_list}')"
                result["process_filter"] = {"applied": list(aliases)}

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

            # 检验类型拆分（MES CINSPECT_TYPE）：桥接库无类型名称字典，按原始编码展示，避免臆造名称
            r_type = db.execute(f"""
                SELECT r.CINSPECT_TYPE AS inspect_type,
                       COUNT(*) AS total,
                       SUM(CASE WHEN r.CRESULT=1 THEN 1 ELSE 0 END) AS pass_c,
                       SUM(CASE WHEN r.CRESULT=2 THEN 1 ELSE 0 END) AS ng_c
                FROM TBL_QM_INSPECT_RECORD r
                LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                WHERE r.CCREATE_DATE >= date('now', '-{n} days')
                  {proc_filter}
                GROUP BY r.CINSPECT_TYPE
                ORDER BY COUNT(*) DESC
            """)
            for row in (r_type.get("data") or []):
                t = int(row.get("total") or 0)
                if t <= 0:
                    continue
                code = row.get("inspect_type")
                pc = int(row.get("pass_c") or 0)
                nc = int(row.get("ng_c") or 0)
                result["by_inspect_type"].append({
                    "inspect_type": code,
                    "label": f"类型{code}" if code is not None else "未分类",
                    "total": t,
                    "pass_count": pc,
                    "ng_count": nc,
                    "pass_rate": round(pc * 100.0 / t, 2),
                    "ng_rate": round(nc * 100.0 / t, 2),
                })
            result["inspect_type_note"] = (
                "检验类型名称在桥接库中未定义，按 MES 原始 CINSPECT_TYPE 编码展示（类型N）"
            )

            # 待审积压（当前全量未审，领导更关心当前积压；按工序筛选但不受近N日窗口限制）
            r_pend = db.execute(f"""
                SELECT COUNT(*) AS pending FROM quality_inspection_records
                WHERE status='pending' {q_proc_filter}
            """)
            pending = 0
            if r_pend.get("success") and r_pend.get("data"):
                pending = int(r_pend["data"][0].get("pending") or 0)
            result["kpi"]["pending_count"] = pending
            # 同口径（近N日窗口 + 工序）待审计数，便于与 KPI 检验量对照
            r_pend_win = db.execute(f"""
                SELECT COUNT(*) AS pending FROM quality_inspection_records
                WHERE status='pending'
                  AND IFNULL(inspected_at, created_at) >= date('now', '-{n} days')
                  {q_proc_filter}
            """)
            pending_in_window = 0
            if r_pend_win.get("success") and r_pend_win.get("data"):
                pending_in_window = int(r_pend_win["data"][0].get("pending") or 0)

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
            raw_rates = []
            for row in (r.get("data") or []):
                t = int(row.get("total") or 0) or 1
                pc = int(row.get("pass_c") or 0)
                nc = int(row.get("ng_c") or 0)
                raw_rates.append({
                    "process_name": normalize_process_name(row.get("process_name")) or "待确认工序",
                    "total": t,
                    "pass_count": pc,
                    "ng_count": nc,
                })
            for row in merge_counts_by_process(
                raw_rates, name_key="process_name",
                sum_keys=["total", "pass_count", "ng_count"],
            ):
                t = int(row.get("total") or 0) or 1
                pc = int(row.get("pass_count") or 0)
                nc = int(row.get("ng_count") or 0)
                result["process_rates"].append({
                    "process_name": row.get("process_name") or "待确认工序",
                    "total": t,
                    "pass_count": pc,
                    "ng_count": nc,
                    "pass_rate": round(pc * 100.0 / t, 2),
                    "ng_rate": round(nc * 100.0 / t, 2),
                    "data_source": "MES:TBL_QM_INSPECT_RECORD",
                })
            # 仅展示 MES 检验实据工序；禁止 quality_metrics 造数补洞

            result["process_rates"].sort(key=lambda x: process_sort_key(x.get("process_name")))
            result["process_catalog"] = list(STANDARD_PROCESSES)

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
                  {q_proc_filter_alias}
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
                      {q_proc_filter_alias}
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
            r_cnt = db.execute(f"""
                SELECT
                  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status IN ('passed','failed') THEN 1 ELSE 0 END) AS audited
                FROM quality_inspection_records
                WHERE 1=1 {q_proc_filter}
            """)
            audited = 0
            if r_cnt.get("success") and r_cnt.get("data"):
                audited = int(r_cnt["data"][0].get("audited") or 0)
                pending = int(r_cnt["data"][0].get("pending") or pending or 0)
                result["kpi"]["pending_count"] = pending

            r_age = db.execute(f"""
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
                    {q_proc_filter}
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

            r_audit = db.execute(f"""
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN (julianday(source_modified_at)-julianday(source_created_at))*24*60 >= 1
                       THEN 1 ELSE 0 END) AS meaningful,
                  AVG(CASE WHEN (julianday(source_modified_at)-julianday(source_created_at))*24*60 >= 1
                       THEN (julianday(source_modified_at)-julianday(source_created_at))*24 END) AS avg_h
                FROM quality_inspection_records
                WHERE status IN ('passed','failed')
                  AND source_created_at IS NOT NULL AND source_modified_at IS NOT NULL
                  AND source_created_at >= date('now', '-{n} days')
                  {q_proc_filter}
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

            # 口径说明：待审/龄期为当前全量（按工序筛选），已审时效按近 N 日创建口径
            notes.append(
                "待审积压、龄期为当前全量（按工序筛选），不受近N日窗口限制；"
                f"已审时效按近{n}日创建口径"
            )
            result["backlog"] = {
                "pending": pending,
                "pending_in_window": pending_in_window,
                "pending_scope": "当前全量待审（按工序筛选，不受近N日窗口限制）",
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
        """从 ERP/MES 扫描当月品质数据并生成/刷新预警（禁止编造，仅实数）

        预警使用稳定指纹：同周期同问题只保留一条，刷新指标与 last_seen_at；
        本期未再触发的自动预警归档（不删除），已确认记录全程保留。
        """
        if not fiscal_period:
            fiscal_period = datetime.now().strftime("%Y%m")

        # 归档旧版按天 id 的预警（保留审计历史）；清理已证伪的低样本/跳变记录
        archived_legacy = self.db.purge_stale_alerts(fiscal_period)
        purged_bad = self.db.purge_unreliable_alerts()
        merged_patterns = self.db.merge_duplicate_patterns()

        created = []
        scopes = {"全厂"}
        sources = {
            "plant_scrap": False, "plant_mes": False, "top_parts": 0,
            "purged": len(purged_bad), "archived_legacy": len(archived_legacy),
            "merged_patterns": merged_patterns,
        }

        # 1) 全厂报废 + 日趋势（ERP）
        scrap = self.get_scrap_analysis(None, fiscal_period=fiscal_period)
        sources["plant_scrap"] = True
        # rate=None 表示无入库基数，generate_alerts 内部会跳过报废率预警
        created.extend(self.engine.generate_alerts(
            scrap.get("scrap_rate"),
            "全厂",
            [],
            scrap.get("monthly_trend") or [],
            scrap_pcs=float(scrap.get("total_scrap_pcs") or 0),
            fiscal_period=fiscal_period,
        ))

        # 2) 全厂 MES 工序良率（限定当月工单，避免用全历史稀释/放大）
        proc = self.get_process_yield(None, fiscal_period=fiscal_period)
        sources["plant_mes"] = bool(proc.get("process_yield"))
        created.extend(self.engine.generate_alerts(
            0,
            "全厂",
            proc.get("process_yield") or [],
            [],
            scrap_pcs=0,
            fiscal_period=fiscal_period,
        ))

        # 3) TOP 报废料号逐一扫描（ERP 报废率 + 该料号 MES）
        #    仅当排行榜是真实料号口径时才按料号扫描；low_yield 模式下列表是工序名，
        #    若当料号用会造出「以工序名为料号」的伪预警。
        dash = self.get_dashboard(fiscal_period)
        top_parts = (
            (dash.get("top_defect_parts") or [])
            if dash.get("ranking_mode") == "scrap_parts"
            else []
        )
        for row in top_parts[:8]:
            part = (row.get("PartNumber") or row.get("part") or "").strip()
            if not part:
                continue
            scopes.add(part)
            # RejectRate=None 表示该料号无入库基数；不生成 100% 伪预警
            rate = row.get("RejectRate")
            reject = float(row.get("TotalReject") or row.get("scrap_pcs") or 0)
            # 料号报废预警
            created.extend(self.engine.generate_alerts(
                rate, part, [], [], scrap_pcs=reject, fiscal_period=fiscal_period,
            ))
            # 料号 MES 工序（同周期）
            p_mes = self.get_process_yield(part, fiscal_period=fiscal_period)
            if p_mes.get("process_yield"):
                created.extend(self.engine.generate_alerts(
                    0, part, p_mes.get("process_yield") or [], [], scrap_pcs=0,
                    fiscal_period=fiscal_period,
                ))
            sources["top_parts"] += 1

        # 去重（同 id 可能被多次 generate，后写覆盖先写）
        uniq = {}
        for a in created:
            uniq[a["id"]] = a
        # 本期已扫描范围内、本次未再触发的自动预警 → 归档（问题不再成立）
        resolved = self.db.resolve_missing_alerts(fiscal_period, list(uniq), scopes)
        active = self.db.get_alerts(acknowledged=False, limit=200)
        critical = [a for a in active if a.get("level") == "critical"]
        return {
            "scanned_at": datetime.now().isoformat(),
            "fiscal_period": fiscal_period,
            "sources": sources,
            "generated": len(uniq),
            "purged": len(purged_bad),
            "archived_legacy": len(archived_legacy),
            "resolved": len(resolved),
            "active_alerts": len(active),
            "critical_alerts": len(critical),
            "alerts": list(uniq.values())[:30],
            "erp_connected": bool(self.erp and self.erp.connected),
            "mes_connected": bool(self.mes and self.mes.connected),
            "policy": "指标仅来自 ERP/MES 实查，禁止编造；建议文案可为知识库模板",
        }

    def generate_8d_report(self, part_number, start_date=None, end_date=None, period=None, fiscal_period=None, alert_id=None):
        """生成真实有效的 8D：ERP/MES 实据 + 已关闭历史方案 / 已验证知识"""
        part_number = (part_number or "").strip()
        if not part_number:
            return {"success": False, "error": "料号不能为空"}
        if part_number.startswith("QHC-8D-") or part_number.startswith("8D-"):
            return {"success": False, "error": "料号无效（请勿填报告编号）"}

        if fiscal_period:
            pass
        elif period:
            parts = _period_parts(period)
            if not parts:
                return {"success": False, "error": f"周期格式无效：{period}"}
            fiscal_period = parts[0]
        elif not start_date and not end_date:
            fiscal_period = datetime.now().strftime("%Y%m")

        # 清理历史无效稿
        purged = self.db.purge_invalid_8d()

        scrap = self.get_scrap_analysis(part_number, start_date, end_date, fiscal_period=fiscal_period)
        process = self.get_process_yield(
            part_number, fiscal_period=fiscal_period,
            start_date=start_date if not fiscal_period else None,
            end_date=end_date if not fiscal_period else None,
        )
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
            "source_alert_id": str(alert_id or ""),
            "date_range": f"{start_date or ''} ~ {end_date or ''}".strip(" ~"),
            "scrap_rate": scrap.get("scrap_rate"),
            "rate_reliable": bool(scrap.get("rate_reliable")),
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
        db_row["alert_id"] = str(alert_id or "")
        db_row["fiscal_period"] = str(evidence.get("fiscal_period") or "")
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
# 对外 8D 服务（POST /api/v1/8d/generate）代理与字段映射
# ═══════════════════════════════════════════════════

class External8DError(Exception):
    """调用对外 8D 接口失败。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = int(status or 502)
        self.message = message or "8D 服务错误"


def _ext8d_join_lines(items) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        return items.strip()
    lines = []
    for i, item in enumerate(items, 1):
        text = str(item or "").strip()
        if text:
            lines.append(f"{i}. {text}")
    return "\n".join(lines)


def flatten_external_8d_report(
    report: dict,
    part_no: str,
    problem_desc: str,
    extra: Optional[dict] = None,
) -> dict:
    """
    将对外 8D 的 ReportContent（meta + d1_team~d8_closure）压成品质分析本地扁平行。
    同步写入实据快照（D2/MES 摘要）与知识库摘要（D4 根因 / D5 对策），供编辑页直接展示。
    """
    report = report if isinstance(report, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    d1 = report.get("d1_team") if isinstance(report.get("d1_team"), dict) else {}
    d2 = report.get("d2_problem") if isinstance(report.get("d2_problem"), dict) else {}
    d3 = report.get("d3_containment") if isinstance(report.get("d3_containment"), dict) else {}
    d4 = report.get("d4_root_cause") if isinstance(report.get("d4_root_cause"), dict) else {}
    d5 = report.get("d5_corrective") if isinstance(report.get("d5_corrective"), dict) else {}
    d6 = report.get("d6_verification") if isinstance(report.get("d6_verification"), dict) else {}
    d7 = report.get("d7_prevention") if isinstance(report.get("d7_prevention"), dict) else {}
    d8 = report.get("d8_closure") if isinstance(report.get("d8_closure"), dict) else {}

    # 兼容已是扁平 d1~d8 的返回
    if report.get("d1") and not d1.get("content"):
        return {
            "id": report.get("id") or f"8D-{part_no[:8]}-{int(time.time())}",
            "report_no": report.get("report_no") or report.get("id") or "",
            "part_number": report.get("part_number") or part_no,
            "customer": report.get("customer") or "",
            "problem": report.get("problem") or problem_desc,
            "d1": report.get("d1") or "",
            "d2": report.get("d2") or "",
            "d3": report.get("d3") or "",
            "d4": report.get("d4") or "",
            "d5": report.get("d5") or "",
            "d6": report.get("d6") or "",
            "d7": report.get("d7") or "",
            "d8": report.get("d8") or "",
            "defects_json": report.get("defects_json") or "[]",
            "evidence_json": report.get("evidence_json") or "{}",
            "history_refs_json": report.get("history_refs_json") or "[]",
            "knowledge_refs_json": report.get("knowledge_refs_json") or "{}",
            "data_policy": report.get("data_policy") or "external=langgraph;/api/v1/8d/generate",
            "status": report.get("status") or "open",
            "severity": int(report.get("severity") or 3),
            "created_by": report.get("created_by") or "LangGraph-8D",
            "created_at": report.get("created_at") or datetime.now().isoformat(),
        }

    why_chain = []
    why_lines = []
    for step in (d4.get("why_chain") or []):
        if not isinstance(step, dict):
            continue
        why = str(step.get("why") or "").strip()
        ans = str(step.get("answer") or "").strip()
        if why or ans:
            why_chain.append({"why": why, "answer": ans})
        if why:
            why_lines.append(f"↓ {why}")
        if ans:
            why_lines.append(f"→ {ans}")

    d2_parts = []
    if d2.get("product"):
        d2_parts.append(f"【产品】{d2.get('product')}")
    if d2.get("phenomenon"):
        d2_parts.append(f"【现象】{d2.get('phenomenon')}")
    if d2.get("discovery_process"):
        d2_parts.append(f"【发现工序】{d2.get('discovery_process')}")
    if d2.get("affected_qty"):
        d2_parts.append(f"【影响数量】{d2.get('affected_qty')}")
    d2_text = "\n".join(d2_parts)

    d3_title = (d3.get("title") or "").strip()
    d3_items = _ext8d_join_lines(d3.get("items") or [])
    d3_text = (f"【{d3_title}】\n{d3_items}" if d3_title else d3_items).strip()

    d4_ph = str(d4.get("defect_phenomenon") or "").strip()
    d4_text = "\n".join(
        x for x in [
            f"不良现象：{d4_ph}" if d4_ph else "",
            *why_lines,
        ] if x
    )

    d5_items = [
        str(x).strip() for x in (d5.get("items") or []) if str(x or "").strip()
    ]
    d5_text = _ext8d_join_lines(d5_items)
    d6_crit = _ext8d_join_lines(d6.get("criteria") or [])
    d6_text = ("【验收标准】\n" + d6_crit).strip() if d6_crit else ""
    d7_text = _ext8d_join_lines(d7.get("items") or [])
    d8_conf = _ext8d_join_lines(d8.get("confirmations") or [])
    d8_text = ("【团队成果确认】\n" + d8_conf).strip() if d8_conf else ""

    ts = int(time.time())
    safe_part = re.sub(r"[^\w\-]+", "", part_no)[:12] or "PART"
    rid = f"8D-{safe_part}-{ts}"
    c8d = (meta.get("c8d_number") or "").strip()
    report_no = c8d or f"QHC-8D-{datetime.now().strftime('%Y%m%d')}-{hashlib.md5(rid.encode()).hexdigest()[:4].upper()}"
    problem = (
        (meta.get("summary") or "").strip()
        or (meta.get("abnormal_reason") or "").strip()
        or problem_desc
    )
    status_raw = (meta.get("status") or "进行中").strip()
    # 外部/生成 8D 一律以 open 入库：关闭必须经人工编辑 + validate_8d_closure 校验，
    # 否则自动生成稿会混入「已关闭历史方案」污染 D4/D5 知识溯源。
    status = "open"

    phenomenon = (
        str(d2.get("phenomenon") or "").strip()
        or d4_ph
        or problem_desc
    )
    process_name = str(d2.get("discovery_process") or "").strip()
    defects = []
    if phenomenon:
        defects.append({
            "defect_type": phenomenon,
            "process_name": process_name,
            "scrap_pcs": None,
            "source": "external_8d_d2",
        })

    round_logs = extra.get("round_logs") if isinstance(extra.get("round_logs"), list) else []
    evidence = {
        "kind": "external_langgraph",
        "source": "external_8d_api",
        "endpoint": "/api/v1/8d/generate",
        "engine": extra.get("engine") or "langgraph",
        "part_no": (meta.get("part_no") or part_no).strip() or part_no,
        "problem_desc": problem_desc,
        "customer": (meta.get("customer") or "").strip(),
        "product": str(d2.get("product") or "").strip(),
        "phenomenon": phenomenon,
        "discovery_process": process_name,
        "affected_qty": str(d2.get("affected_qty") or "").strip(),
        "d3_title": d3_title,
        "d3_items": list(d3.get("items") or [])[:8],
        "defects": defects,
        "qualified": extra.get("qualified") or "",
        "total_score": extra.get("total_score"),
        "ok": bool(extra.get("ok")),
        "thread_id": extra.get("thread_id") or "",
        "elapsed_s": extra.get("elapsed_s"),
        "round_logs": round_logs[-12:],
        "data_sources": "MES + FMEA GraphRAG + LangGraph",
        "meta": {
            "c8d_number": c8d,
            "summary": (meta.get("summary") or "").strip(),
            "abnormal_reason": (meta.get("abnormal_reason") or "").strip(),
            "status": status_raw,
        },
    }

    # 知识库区：直接展示本次智能生成写入的根因 / 对策（已进入 D4/D5）
    knowledge_pack = {
        "source": "LangGraph-8D / FMEA GraphRAG",
        "source_url": GRAPHRAG_BASE_URL,
        "applied": True,
        "hit_count": 1 if (why_chain or d5_items) else 0,
        "cause_count": len(why_chain),
        "measure_count": len(d5_items),
        "refs": [],
    }
    if why_chain or d5_items:
        knowledge_pack["refs"] = [{
            "matched": phenomenon or problem_desc or part_no,
            "query": problem_desc,
            "match_type": "langgraph_applied",
            "processes": [process_name] if process_name else [],
            "root_causes": [
                {
                    "cause": (x.get("answer") or "").strip(),
                    "category": (x.get("why") or "").strip(),
                    "why": (x.get("why") or "").strip(),
                }
                for x in why_chain
                if (x.get("answer") or "").strip()
            ],
            "countermeasures": [
                {"measure": m, "standard": ""} for m in d5_items
            ],
            "source": "智能生成已写入 D4/D5（须现场复核）",
            "evidence": f"料号 {part_no} · {phenomenon or problem_desc}",
        }]

    return {
        "id": rid,
        "report_no": report_no,
        "part_number": (meta.get("part_no") or part_no).strip() or part_no,
        "customer": (meta.get("customer") or "").strip(),
        "problem": problem,
        "d1": str(d1.get("content") or "").strip(),
        "d2": d2_text,
        "d3": d3_text,
        "d4": d4_text,
        "d5": d5_text,
        "d6": d6_text,
        "d7": d7_text,
        "d8": d8_text,
        "defects_json": json.dumps(defects, ensure_ascii=False),
        "evidence_json": json.dumps(evidence, ensure_ascii=False, default=str),
        "history_refs_json": "[]",
        "knowledge_refs_json": json.dumps(knowledge_pack, ensure_ascii=False),
        "data_policy": "external=langgraph;/api/v1/8d/generate;review_required=1",
        "alert_id": str(extra.get("alert_id") or ""),
        "fiscal_period": str(extra.get("fiscal_period") or ""),
        "status": status,
        "severity": 3,
        "created_by": "LangGraph-8D",
        "created_at": datetime.now().isoformat(),
    }


def call_external_8d_generate(payload: dict) -> dict:
    """同步调用对外 8D 生成接口，返回 JSON 字典。

    入口按当前 QA_ENV 解析：测试→本机 8001；生产→公网 :18880。二者禁止混用。
    """
    import urllib.error
    import urllib.request

    eight = resolve_eight_d_api()
    base = (eight.get("base") or EIGHT_D_API_BASE or "").rstrip("/")
    api_key = (eight.get("api_key") if eight.get("api_key") is not None else EIGHT_D_API_KEY) or ""
    env_name = eight.get("env") or EIGHT_D_API_ENV or "test"
    if not base:
        raise External8DError(500, f"未配置 {env_name} 环境 8D 入口（QA_*_8D_API_BASE）")

    url = f"{base}/api/v1/8d/generate"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    logger.info("调用 8D 生成 env=%s base=%s", env_name, base)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    timeout = max(30.0, float(EIGHT_D_API_TIMEOUT or 180))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body) if err_body else {}
            detail = parsed.get("detail") or parsed.get("error") or err_body
            if isinstance(detail, list):
                detail = "; ".join(
                    str(x.get("msg") or x) if isinstance(x, dict) else str(x)
                    for x in detail
                )
        except Exception:
            detail = str(e)
        raise External8DError(e.code or 502, detail or f"8D 服务 HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise External8DError(
            502,
            f"无法连接 8D 服务 [{env_name}] {base}（请确认已启动）：{e.reason}",
        ) from e

    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise External8DError(502, f"8D 服务返回非 JSON（HTTP {code}）") from e
    if not isinstance(result, dict):
        raise External8DError(502, "8D 服务返回格式异常")
    return result


# ═══════════════════════════════════════════════════
# 8D D4/D5 可追溯闸门
# 只允许 GraphRAG / 已关闭·已验证 8D 中的条目进入报告；
# 匹配不到的根因/对策一律改写为“待现场确认/待制定”，不落库为事实。
# ═══════════════════════════════════════════════════

_D4_PENDING = "待现场确认：GraphRAG 与历史已验证 8D 未检索到可引用根因（禁止以推测代替）"
_D5_PENDING = "待制定：GraphRAG 与历史已验证 8D 未命中可用对策，须现场确认根因后制定并通过 D6 验证"

_GROUND_PUNCT = re.compile(
    r"[\s\u3000·,，。;；:：、/\\|()（）\[\]【】<>《》\"'“”‘’\-—_+%=~`!?！？*#>]+"
)
_GROUND_TAG = re.compile(r"^[\[【][^\]】]{1,16}[\]】]\s*")
_GROUND_SKIP_LINE = re.compile(
    r"^(\d+[.、)]\s*)?(Why\d*|料号|周期|报废率|缺陷帕累托|工序集中度|MES|优先核查|"
    r"数据源|说明|建议|不良现象|产品|现象|发现工序|影响数量|关联证据|完成时限|责任人)[：:]",
    re.IGNORECASE,
)


def _ground_norm(text: str) -> str:
    text = _GROUND_TAG.sub("", str(text or ""))
    return _GROUND_PUNCT.sub("", text).lower()


def _ground_bigrams(norm: str) -> set:
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def _ground_split_lines(text: str) -> List[str]:
    """把历史 8D 的 D4/D5 拆成可逐条比对的候选句，跳过标题、纯实据行与待办占位。"""
    out = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"^[-•·*\d.、)\s→]+", "", raw.strip()).strip()
        if not line or line.startswith("【"):
            continue
        if any(mark in line for mark in ("待现场确认", "待确认", "待填写", "未核实", "禁止以推测")):
            continue
        if _GROUND_SKIP_LINE.match(line):
            continue
        if len(_ground_norm(line)) < 6:
            continue
        out.append(line)
    return out


def _ground_lookup(text: str, candidates: List[dict]) -> Optional[dict]:
    """在允许来源中找最接近的一条；先直接包含，再二元组重合，最后最长公共子串。"""
    tn = _ground_norm(text)
    if len(tn) < 4 or not candidates:
        return None
    tn_bi = _ground_bigrams(tn)
    for c in candidates:
        cn = _ground_norm(c.get("text"))
        if not cn:
            continue
        if tn == cn:
            return c
        if len(tn) >= 6 and tn in cn:
            return c
        if len(cn) >= 6 and cn in tn:
            return c
    best, best_size, best_ratio, best_overlap = None, 0, 0.0, 0.0
    for c in candidates:
        cn = _ground_norm(c.get("text"))
        if not cn:
            continue
        cn_bi = _ground_bigrams(cn)
        overlap = len(tn_bi & cn_bi) / max(1, min(len(tn_bi), len(cn_bi)))
        if overlap >= 0.6:
            return c
        sm = difflib.SequenceMatcher(None, tn, cn)
        size = sm.find_longest_match(0, len(tn), 0, len(cn)).size
        if size < 5:
            continue
        ratio = size / max(1, min(len(tn), len(cn)))
        if size > best_size or (size == best_size and ratio > best_ratio):
            best, best_size, best_ratio, best_overlap = c, size, ratio, overlap
    if best is not None and (best_size >= 6 or best_ratio >= 0.5 or best_overlap >= 0.5):
        return best
    return None


def _ground_filter_text(text: str, candidates: List[dict]) -> tuple:
    """整句命中则保留；否则按句拆分，只保留有出处的分句。"""
    if not candidates:
        return "", []
    hit = _ground_lookup(text, candidates)
    if hit:
        return text, [hit]
    kept, hits = [], []
    for part in re.split(r"(?<=[。；;！!？?\n])", str(text or "")):
        part = part.strip(" \t\r\n")
        if len(_ground_norm(part)) < 5:
            continue
        h = _ground_lookup(part, candidates)
        if h:
            kept.append(part)
            hits.append(h)
    return "".join(kept).strip(), hits


def _collect_8d_grounding(part: str, phenomenon: str, process_name: str, db) -> dict:
    """汇总 D4/D5 允许引用的来源：GraphRAG / 已关闭 8D / 已验证知识。"""
    causes: List[dict] = []
    measures: List[dict] = []
    graphrag_refs: List[dict] = []
    history_refs: List[dict] = []
    verified_refs: List[dict] = []
    seen = set()

    def _add(bucket, text, source, kind):
        text = str(text or "").strip()
        if not text:
            return
        key = (kind, _ground_norm(text)[:120])
        if key in seen:
            return
        seen.add(key)
        bucket.append({"text": text, "source": source, "kind": kind})

    if phenomenon or process_name:
        try:
            pack = get_graphrag_client().lookup_for_8d(
                defect_names=[phenomenon] if phenomenon else [],
                process_names=[process_name] if process_name else [],
            )
            graphrag_refs = [r for r in (pack.get("refs") or []) if r.get("matched")]
            for ref in graphrag_refs:
                gname = str(ref.get("matched") or ref.get("query") or "").strip()
                src = f"GraphRAG:{gname}" if gname else "GraphRAG"
                for rc in ref.get("root_causes") or []:
                    _add(causes, rc.get("cause"), src, "graphrag")
                for cm in ref.get("countermeasures") or []:
                    _add(measures, cm.get("measure"), src, "graphrag")
        except Exception as e:
            logger.warning("8D 可追溯闸门检索 GraphRAG 失败: %s", e)

    try:
        history = db.find_closed_8d_history(
            part_number=part,
            defect_types=[phenomenon] if phenomenon else [],
            process_names=[process_name] if process_name else [],
            limit=6,
        )
    except Exception as e:
        logger.warning("8D 可追溯闸门读取历史关闭 8D 失败: %s", e)
        history = []
    for h in history:
        ref_no = str(h.get("report_no") or h.get("id") or "").strip()
        src = f"历史8D:{ref_no}" if ref_no else "历史8D"
        history_refs.append({
            "report_no": h.get("report_no"),
            "part_number": h.get("part_number"),
            "closed_at": h.get("closed_at"),
            "match_score": h.get("match_score"),
        })
        for line in _ground_split_lines(h.get("d4")):
            _add(causes, line, src, "closed_8d")
        for line in _ground_split_lines(h.get("d5")):
            _add(measures, line, src, "closed_8d")

    if phenomenon:
        try:
            patterns = db.get_verified_patterns(defect_type=phenomenon, limit=10)
        except Exception as e:
            logger.warning("8D 可追溯闸门读取已验证知识失败: %s", e)
            patterns = []
        for vp in patterns:
            src_no = str(vp.get("source_8d") or "").strip()
            src = f"已验证:{src_no}" if src_no else "已验证知识"
            verified_refs.append({
                "defect_type": vp.get("defect_type"),
                "process_name": vp.get("process_name"),
                "source_8d": src_no,
            })
            for c in vp.get("root_causes") or []:
                _add(causes, c, src, "verified")
            for s in vp.get("solutions") or []:
                _add(measures, s, src, "verified")

    return {
        "causes": causes,
        "measures": measures,
        "graphrag_refs": graphrag_refs,
        "history_refs": history_refs,
        "verified_refs": verified_refs,
    }


def _apply_8d_grounding(report: dict, part: str, problem: str, db) -> tuple:
    """在落库前过滤外部 8D 的 D4/D5：无出处的内容不保留为根因/对策。"""
    info = {
        "policy": "d4_d5=GraphRAG+closed/verified_8D_only",
        "phenomenon": "",
        "process_name": "",
        "d4_kept": 0,
        "d4_dropped": 0,
        "d5_kept": 0,
        "d5_dropped": 0,
        "d4_sources": [],
        "d5_sources": [],
        "graphrag_refs": [],
        "history_refs": [],
        "verified_refs": [],
    }
    if not isinstance(report, dict):
        return report, info
    d4 = report.get("d4_root_cause") if isinstance(report.get("d4_root_cause"), dict) else None
    d5 = report.get("d5_corrective") if isinstance(report.get("d5_corrective"), dict) else None
    if d4 is None and d5 is None:
        return report, info

    d2 = report.get("d2_problem") if isinstance(report.get("d2_problem"), dict) else {}
    phenomenon = str(d2.get("phenomenon") or "").strip()
    if not phenomenon and d4:
        phenomenon = str(d4.get("defect_phenomenon") or "").strip()
    if not phenomenon:
        phenomenon = (problem or "").strip()[:60]
    process_name = str(d2.get("discovery_process") or "").strip()
    info["phenomenon"] = phenomenon
    info["process_name"] = process_name

    sources = _collect_8d_grounding(part, phenomenon, process_name, db)
    info["graphrag_refs"] = sources["graphrag_refs"]
    info["history_refs"] = sources["history_refs"]
    info["verified_refs"] = sources["verified_refs"]

    if d4 is not None:
        chain = d4.get("why_chain")
        new_chain = []
        if isinstance(chain, list):
            for step in chain:
                if not isinstance(step, dict):
                    continue
                answer = str(step.get("answer") or "").strip()
                if not answer:
                    new_chain.append(step)
                    continue
                kept_text, hits = _ground_filter_text(answer, sources["causes"])
                if hits:
                    info["d4_kept"] += 1
                    for h in hits:
                        info["d4_sources"].append({
                            "text": kept_text[:300],
                            "source": h["source"],
                            "kind": h["kind"],
                        })
                    new_chain.append({**step, "answer": kept_text})
                else:
                    info["d4_dropped"] += 1
                    new_chain.append({**step, "answer": _D4_PENDING})
        d4["why_chain"] = new_chain or [{"why": "根因（D4）", "answer": _D4_PENDING}]

    if d5 is not None:
        items = d5.get("items") if isinstance(d5.get("items"), list) else []
        kept_items = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            kept_text, hits = _ground_filter_text(text, sources["measures"])
            if hits:
                kept_items.append(kept_text)
                info["d5_kept"] += 1
                for h in hits:
                    info["d5_sources"].append({
                        "text": kept_text[:300],
                        "source": h["source"],
                        "kind": h["kind"],
                    })
            else:
                info["d5_dropped"] += 1
        d5["items"] = kept_items or [_D5_PENDING]

    return report, info


def _finalize_external_8d(
    ext: dict, part: str, problem: str, db,
    alert_id: str = "", fiscal_period: str = "",
) -> dict:
    """把对外 8D 返回整理为本地报告并落库（同步请求与后台任务共用）。"""
    report_raw = ext.get("report") if isinstance(ext.get("report"), dict) else {}
    # D4/D5 可追溯闸门：只保留 GraphRAG / 已关闭·已验证 8D 中的条目
    report_raw, grounding = _apply_8d_grounding(report_raw, part, problem, db)
    flat = flatten_external_8d_report(
        report_raw,
        part,
        problem,
        extra={
            "ok": bool(ext.get("ok")),
            "qualified": ext.get("qualified") or "",
            "total_score": ext.get("total_score"),
            "round_logs": ext.get("round_logs") or [],
            "thread_id": ext.get("thread_id") or "",
            "elapsed_s": ext.get("elapsed_s"),
            "engine": ext.get("engine") or "langgraph",
            "alert_id": alert_id,
            "fiscal_period": fiscal_period,
        },
    )
    # 知识来源改为可追溯结果，避免把未溯源条目标成“知识库已应用”
    used_refs = []
    if grounding["d4_kept"] or grounding["d5_kept"]:
        used_refs.append({
            "matched": grounding.get("phenomenon") or flat.get("part_number") or part,
            "query": problem,
            "match_type": "grounded_selection",
            "processes": [grounding["process_name"]] if grounding.get("process_name") else [],
            "root_causes": [
                {"cause": s["text"], "category": s["source"]}
                for s in grounding["d4_sources"]
            ],
            "countermeasures": [
                {"measure": s["text"], "standard": s["source"]}
                for s in grounding["d5_sources"]
            ],
            "source": "GraphRAG / 历史已验证 8D（仅保留可追溯条目）",
            "evidence": f"料号 {part} · 未命中条目已转待现场确认",
        })
    knowledge_pack = {
        "source": "GraphRAG + 已关闭/已验证 8D（仅可追溯条目）",
        "source_url": GRAPHRAG_BASE_URL,
        "applied": bool(used_refs),
        "hit_count": grounding["d4_kept"] + grounding["d5_kept"],
        "cause_count": grounding["d4_kept"],
        "measure_count": grounding["d5_kept"],
        "refs": used_refs,
        "grounding": grounding,
    }
    flat["knowledge_refs_json"] = json.dumps(knowledge_pack, ensure_ascii=False)
    flat["history_refs_json"] = json.dumps(grounding["history_refs"], ensure_ascii=False)
    flat["data_policy"] = (
        "external=langgraph; "
        "d4_d5=GraphRAG+closed/verified_8D_only; "
        f"d4_kept={grounding['d4_kept']};d4_dropped={grounding['d4_dropped']};"
        f"d5_kept={grounding['d5_kept']};d5_dropped={grounding['d5_dropped']}; "
        "external_status_recorded=evidence.meta.status; "
        "auto_close=disabled; review_required=1"
    )
    try:
        evidence = json.loads(flat.get("evidence_json") or "{}")
    except Exception:
        evidence = {}
    evidence["grounding"] = grounding
    evidence["data_sources"] = "MES + GraphRAG + 已关闭/已验证 8D（D4/D5 仅可追溯条目）"
    if alert_id:
        evidence["source_alert_id"] = alert_id
    if fiscal_period:
        evidence["fiscal_period"] = fiscal_period
    flat["evidence_json"] = json.dumps(evidence, ensure_ascii=False, default=str)

    # 落本地库，便于列表/编辑/PDF（对外接口本身默认不落库）
    # 外部 c8d_number 可能与历史报告重号：自动追加短后缀，避免 UNIQUE 冲突丢失本次报告。
    rno = str(flat.get("report_no") or "").strip()
    if rno:
        dup = db.find_8d_by_report_no(rno, flat.get("id") or "")
        if dup:
            flat["report_no"] = (
                f"{rno}-{hashlib.md5(str(flat.get('id') or rno).encode()).hexdigest()[:4].upper()}"
            )
    db.save_8d(flat)
    # 供前端编辑页直接使用结构化对象
    for key, empty in (("evidence", "{}"), ("knowledge_refs", "{}"), ("defects", "[]")):
        try:
            flat[key] = json.loads(flat.get(f"{key}_json") or empty)
        except Exception:
            flat[key] = json.loads(empty)

    msg = (ext.get("message") or "").strip() or "8D 已生成"
    qualified = ext.get("qualified") or ""
    total_score = ext.get("total_score")
    if qualified or total_score is not None:
        extra = " · ".join(
            x for x in [
                qualified or None,
                f"{total_score}分" if total_score not in (None, "") else None,
            ] if x
        )
        if extra and extra not in msg:
            msg = f"{msg}（{extra}）"
    dropped = grounding["d4_dropped"] + grounding["d5_dropped"]
    if dropped:
        msg = (
            f"{msg}（已剔除 {dropped} 条无 GraphRAG/历史已验证 8D 依据的 "
            "D4/D5 内容，改为待现场确认/待制定）"
        )

    return {
        "report": flat,
        "message": msg,
        "ok": bool(ext.get("ok")),
        "qualified": qualified,
        "total_score": total_score,
        "round_logs": ext.get("round_logs") or [],
        "thread_id": ext.get("thread_id") or "",
        "elapsed_s": ext.get("elapsed_s"),
        "engine": ext.get("engine") or "langgraph",
        "external_part_no": ext.get("part_no") or part,
        "external_problem_desc": ext.get("problem_desc") or problem,
        "grounding": grounding,
    }


# ═══════════════════════════════════════════════════
# 8D 后台生成任务（前端轮询进度，避免单次请求干等 180s）
# 任务只保存在本进程内存：重启后消失，查询返回 404 由前端提示重新生成
# ═══════════════════════════════════════════════════

_8D_JOBS: Dict[str, dict] = {}
_8D_JOBS_LOCK = threading.Lock()
_8D_JOB_TTL = 3600   # 结束后保留 1 小时，便于页面回来补取结果
_8D_JOB_MAX = 100    # 内存任务上限，超出时丢弃最早的已结束任务
_8D_MAX_WORKERS = 4  # 后台生成并发上限，防止请求洪峰耗尽线程
_8D_WORKERS = threading.BoundedSemaphore(_8D_MAX_WORKERS)


def _8d_job_prune_locked(now: float) -> None:
    for jid, job in list(_8D_JOBS.items()):
        if job.get("finished_at") and now - job["finished_at"] > _8D_JOB_TTL:
            _8D_JOBS.pop(jid, None)
    if len(_8D_JOBS) > _8D_JOB_MAX:
        ended = sorted(
            (job.get("finished_at"), jid)
            for jid, job in _8D_JOBS.items()
            if job.get("finished_at")
        )
        for _, jid in ended[: len(_8D_JOBS) - _8D_JOB_MAX]:
            _8D_JOBS.pop(jid, None)


def _8d_job_create(part: str, problem: str, alert_id: str = "", fiscal_period: str = "") -> dict:
    now = time.time()
    job = {
        "id": uuid.uuid4().hex,
        "status": "queued",
        "phase": "已提交，排队中…",
        "detail": "后台任务已创建，正在连接 8D 服务",
        "part_no": part,
        "problem_desc": problem,
        "alert_id": alert_id or "",
        "fiscal_period": fiscal_period or "",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "updated_at": now,
        "result": None,
        "error": "",
    }
    with _8D_JOBS_LOCK:
        _8d_job_prune_locked(now)
        _8D_JOBS[job["id"]] = job
    return job


def _8d_job_update(job_id: str, **fields) -> None:
    with _8D_JOBS_LOCK:
        job = _8D_JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _8d_job_finish(job_id: str, status: str, *, result=None, error="") -> None:
    now = time.time()
    with _8D_JOBS_LOCK:
        job = _8D_JOBS.get(job_id)
        if not job:
            return
        job["status"] = status
        job["result"] = result
        job["error"] = error
        job["finished_at"] = now
        job["updated_at"] = now
        if status == "success":
            job["phase"] = "生成完成"
            job["detail"] = (result or {}).get("message") or "已写入本系统，可继续编辑"
        else:
            job["phase"] = "生成失败"
            job["detail"] = error or "请检查料号/问题描述或 8D 服务状态后重试"


def _8d_job_snapshot(job: dict) -> dict:
    now = time.time()
    end = job.get("finished_at") or now
    return {
        "job_id": job["id"],
        "status": job["status"],
        "phase": job.get("phase") or "",
        "detail": job.get("detail") or "",
        "part_no": job.get("part_no") or "",
        "alert_id": job.get("alert_id") or "",
        "fiscal_period": job.get("fiscal_period") or "",
        "elapsed_s": round(max(0.0, end - job["created_at"]), 1),
        "created_at": datetime.fromtimestamp(job["created_at"]).isoformat(timespec="seconds"),
        "updated_at": datetime.fromtimestamp(job["updated_at"]).isoformat(timespec="seconds"),
        "result": job.get("result"),
        "error": job.get("error") or "",
    }


def _8d_job_get(job_id: str) -> Optional[dict]:
    with _8D_JOBS_LOCK:
        _8d_job_prune_locked(time.time())
        job = _8D_JOBS.get(job_id)
        return _8d_job_snapshot(job) if job else None


def _run_8d_job(job_id: str, payload: dict, part: str, problem: str,
                db, actor: str, role: str, ip: str,
                alert_id: str = "", fiscal_period: str = "") -> None:
    """后台线程：调用外部 8D 服务并落库；状态全程可查。"""
    _8d_job_update(
        job_id,
        status="running",
        started_at=time.time(),
        phase="调用 8D 服务…",
        detail="LangGraph 闭环通常 1–3 分钟；任务在后台运行，可离开本页稍后回来",
    )
    try:
        ext = call_external_8d_generate(payload)
        _8d_job_update(
            job_id,
            phase="整理 D1–D8 并写入本地库…",
            detail="解析实据与知识引用",
        )
        result = _finalize_external_8d(
            ext, part, problem, db,
            alert_id=alert_id, fiscal_period=fiscal_period,
        )
        score = result.get("total_score")
        try:
            db.log_audit(
                actor, role, ip, "8d_generate",
                result["report"].get("report_no") or part,
                f"part={part} ok={result['ok']} qualified={result.get('qualified') or ''} "
                f"score={score if score is not None else ''} async=1",
            )
        except Exception as e:
            logger.warning("审计写入失败 8d_generate: %s", e)
        _8d_job_finish(job_id, "success", result=result)
    except External8DError as e:
        _8d_job_finish(job_id, "error", error=e.message)
    except Exception as e:
        logger.exception("后台 8D 生成失败: %s", e)
        _8d_job_finish(job_id, "error", error=f"生成失败: {e}")


def _run_8d_job_guarded(*args) -> None:
    """包装后台任务，结束时归还并发槽位。"""
    try:
        _run_8d_job(*args)
    finally:
        _8D_WORKERS.release()


# ═══════════════════════════════════════════════════
# 请求参数校验（阻断 SQL 注入：只接受合法格式，拒绝引号/注释/控制字符）
# ═══════════════════════════════════════════════════

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_BAD_SQL_CHARS = re.compile(r"['\"`;\x00-\x1f]|--|/\*|\*/")
_FP_RE = re.compile(r"^(19|20)\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^(\d{4}-\d{2}|\d{6})$")
_DIGITS_RE = re.compile(r"^\d{1,6}$")

# 安全响应头：脚本/样式需允许内联（现有前端为单文件内联实现）；
# ECharts 已本地化到 web/vendor/，不再放行外部 CDN。
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


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
                # URL 令牌已在 do_GET 入口统一拒绝；此处不参与 SQL 字符校验
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
    # 单连接读写超时：慢客户端不得长期占用工作线程
    timeout = 30

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # URL 令牌已禁用：会进入访问日志 / Referer / 浏览器历史
        if params.get("token"):
            self._err(
                403,
                "已禁用 URL 令牌：浏览器请打开 /login.html 登录，自动化请使用 X-API-Token 请求头",
            )
            return

        try:
            invalid = validate_params(params)
            if invalid:
                self._err(400, invalid)
                return

            # 公开接口（登录页 / 健康检查）
            public_api = path in (
                "/api/auth/login",
                "/api/auth/config",
                "/api/health",
            )
            if path.startswith("/api/") or path.startswith("/download/"):
                if path == "/api/health" and not self._authorized():
                    self._health(public=True)
                    return
                if not public_api and not self._authorized():
                    self._err(401, "未登录：请先打开 /login.html 使用 ERP/MES 账号登录")
                    return

            if path == "/api/auth/config":
                self._auth_config()
                return
            if path == "/api/auth/me":
                self._auth_me()
                return
            if path == "/api/health":
                self._health()
            elif path == "/api/project/env":
                self._project_env_get()
            elif path == "/api/defect_mapping":
                self._defect_mapping_get(params)
            elif path == "/api/processes":
                self._processes_catalog()
            elif path == "/api/dashboard":
                self._dashboard(params)
            elif path == "/api/part_spotlight":
                self._part_spotlight(params)
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
            elif path == "/api/8d/job":
                self._8d_job(params)
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
            elif path == "/api/parts":
                self._parts(params)
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
                # 已登录进系统，否则进登录页
                loc = (
                    "/pcb_quality_system.html"
                    if self._page_authorized()
                    else "/login.html"
                )
                self.send_response(302)
                self.send_header("Location", loc)
                self.end_headers()
            elif path.startswith("/download/"):
                # 浏览器下载走 GET
                filename = path.split("/download/", 1)[1]
                self._download_file(filename)
            elif path.endswith(".html") or path.endswith(".css") or path.endswith(".js"):
                # 主系统页需用户会话；登录页仅在已有会话时跳转系统
                if path.endswith("pcb_quality_system.html") and not self._page_authorized():
                    self._redirect("/login.html")
                    return
                if path.endswith("login.html") and self._page_authorized():
                    self._redirect("/pcb_quality_system.html")
                    return
                self._serve_static(path)
            else:
                self._err(404, "接口不存在")
        except Exception as e:
            self._err_internal(e, f"GET {path}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._err(400, "Content-Length 非法")
            return
        if length < 0:
            self._err(400, "Content-Length 非法")
            return
        if length > MAX_BODY_BYTES:
            # 未读取请求体：本连接不可复用
            self.close_connection = True
            self._err(413, f"请求体过大（上限 {MAX_BODY_BYTES} 字节）")
            return
        body = {}
        if length > 0:
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._err(400, "请求体不完整")
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._err(400, "请求体须为合法 JSON")
                return
            if not isinstance(body, dict):
                self._err(400, "请求体须为 JSON 对象")
                return

        try:
            if path == "/api/auth/login":
                self._auth_login(body)
                return
            if path == "/api/auth/logout":
                self._auth_logout()
                return
            if not self._authorized():
                self._err(401, "未登录：请先打开 /login.html 使用 ERP/MES 账号登录")
                return
            params = {k: [v] for k, v in (body or {}).items()}
            # 管理操作：QA_ADMIN_ROLES 为空时不限制；配置后仅管理员角色/服务令牌可执行
            admin_only = {
                "/api/8d/delete",
                "/api/8d/purge_invalid",
                "/api/history/purge_empty",
                "/api/sync/bridge",
            }
            if path in admin_only and not self._is_admin():
                self._err(403, "无权限：该操作仅限管理员角色（QA_ADMIN_ROLES）")
                return
            # 仅对会进入 SQL 拼接的参数做校验；8D/学习正文走参数化存储，不限制标点
            if path in (
                "/api/8d/delete", "/api/8d/purge_invalid",
                "/api/alerts/scan", "/api/alerts/ack", "/api/alerts/ack_batch",
                "/api/history/purge_empty", "/api/export",
            ):
                invalid = validate_params(params)
                if invalid:
                    self._err(400, invalid)
                    return

            if path == "/api/8d/generate":
                # 入参：part_no + problem_desc（兼容旧字段 part / abnormal_reason）
                # 转发至对外 8D 服务；问题描述含标点，不做 SQL 字符校验
                self._8d_generate(body)
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

    def _cookie_value(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, value = part.strip().partition("=")
            if k == name:
                return value.strip()
        return ""

    def _session_id(self) -> str:
        return self._cookie_value("qa_sid")

    def _current_session(self):
        return get_session_store(SESSION_TTL_SECONDS).get(self._session_id())

    def _client_ip(self) -> str:
        peer = (self.client_address[0] if self.client_address else "") or "unknown"
        mode = (TRUST_PROXY or "auto").strip().lower()
        if mode in ("0", "false", "no", "off"):
            return peer
        # auto：仅信任同机反向代理（本机回环对端）转发的头
        trust = mode in ("1", "true", "yes", "on") or _is_loopback(peer)
        if trust:
            real = (self.headers.get("X-Real-IP") or "").strip()
            if real:
                return real[:64]
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return xff[:64]
        return peer

    def _cookie_secure(self) -> bool:
        mode = (COOKIE_SECURE_MODE or "auto").strip().lower()
        if mode in ("1", "true", "yes", "on"):
            return True
        if mode in ("0", "false", "no", "off"):
            return False
        # auto：反向代理 HTTPS 或本连接已是 https 语义
        proto = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
        if proto == "https":
            return True
        return False

    def _has_api_token(self) -> bool:
        """服务令牌只接受请求头；不接受 URL 参数与 Cookie（防日志/Referer 泄露）。"""
        if not API_TOKEN:
            return False
        supplied = (self.headers.get("X-API-Token") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        return bool(supplied) and hmac.compare_digest(supplied, API_TOKEN)

    def _authorized(self) -> bool:
        # 1) ERP/MES 登录会话
        if self._current_session() is not None:
            return True
        # 2) 服务令牌（仅请求头）
        if self._has_api_token():
            return True
        # 3) 未强制登录且未配置令牌 → 开发免登
        if not REQUIRE_LOGIN and not API_TOKEN:
            return True
        return False

    def _page_authorized(self) -> bool:
        """主系统 HTML：强制登录时必须有用户会话（不用服务令牌冒充已登录）。"""
        if self._current_session() is not None:
            return True
        if not REQUIRE_LOGIN:
            return self._authorized()
        return False

    def _is_admin(self) -> bool:
        """管理操作鉴权：未配置 QA_ADMIN_ROLES 时放行；服务令牌视为管理员。"""
        if not ADMIN_ROLES:
            return True
        sess = self._current_session()
        if sess is not None:
            return (sess.role or "").strip().lower() in ADMIN_ROLES
        return self._has_api_token()

    def _actor(self) -> tuple[str, str]:
        """当前操作人 (username, role)：会话优先，其次服务令牌，最后匿名。"""
        sess = self._current_session()
        if sess is not None:
            return sess.username or "", (sess.role or "user")
        if self._has_api_token():
            return "service", "token"
        return "anonymous", ""

    def _audit(self, action: str, target: str = "", detail: str = "") -> None:
        actor, role = self._actor()
        try:
            self.system.db.log_audit(
                actor, role, self._client_ip(), action, target, detail
            )
        except Exception as e:
            logger.warning("审计写入失败 %s: %s", action, e)

    def end_headers(self):
        # 统一补充安全响应头（JSON/静态页/下载/重定向/错误均生效）
        for key, value in (
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "same-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
            ("Content-Security-Policy", _CSP),
        ):
            self.send_header(key, value)
        super().end_headers()

    def _auth_config(self):
        """登录页配置：企业列表与环境标签（公开）。"""
        enterprises = list(LOGIN_ENTERPRISES or DEFAULT_ENTERPRISES)
        erp = resolve_erp_auth()
        self._ok({
            "enterprises": enterprises,
            "default_enterprise": enterprises[0] if enterprises else "江西中软电子有限公司",
            "env": erp.get("env") or ACTIVE_ENV,
            "require_login": bool(REQUIRE_LOGIN),
            "system_name": "PCB智能品质分析系统",
            "version": VERSION,
        })

    def _auth_login(self, body: dict):
        """
        用户登录（代理 ERP/MES）。

        入参：username / password / enterprise_code（可选）
        成功：写入 HttpOnly 会话 Cookie qa_sid，返回用户信息。
        """
        body = body or {}
        client_ip = self._client_ip()
        limited = check_login_rate_limit(client_ip)
        if limited:
            self._err(429, limited)
            return
        username = str(body.get("username") or body.get("user") or body.get("account") or "").strip()
        password = str(body.get("password") or "")
        enterprise = str(
            body.get("enterprise_code")
            or body.get("enterpriseCode")
            or ""
        ).strip()
        if not username or not password:
            self._err(400, "请输入账号和密码")
            return
        erp = resolve_erp_auth()
        base = (erp.get("base") or ERP_AUTH_BASE).rstrip("/")
        try:
            result = erp_login(
                base,
                username,
                password,
                enterprise,
                timeout=ERP_AUTH_TIMEOUT,
            )
        except PermissionError as e:
            record_login_failure(client_ip)
            try:
                self.system.db.log_audit(
                    username, "", client_ip, "login_failed",
                    target=enterprise, detail=str(e)[:200],
                )
            except Exception:
                pass
            # 对外不区分「账号不存在/密码错误/上游 4xx 细节」，避免账号枚举与信息泄露
            self._err(401, "账号或密码错误")
            return
        except ConnectionError as e:
            logger.warning("ERP/MES 登录服务连接失败: %s", e)
            self._err(502, "无法连接 ERP/MES 登录服务，请稍后重试")
            return
        except Exception as e:
            logger.exception("ERP 登录失败: %s", e)
            self._err(502, "登录服务暂不可用，请稍后重试")
            return

        clear_login_failures(client_ip)
        store = get_session_store(SESSION_TTL_SECONDS)
        sess = store.create(
            result["username"],
            enterprise_code=result.get("enterprise_code") or enterprise,
            role=result.get("role") or "",
            erp_token=result.get("access_token") or "",
        )
        try:
            self.system.db.log_audit(
                sess.username, sess.role or "user", client_ip, "login",
                target=sess.enterprise_code, detail="success",
            )
        except Exception as e:
            logger.warning("登录审计写入失败: %s", e)
        cookie = session_cookie_header(
            sess.sid,
            max_age=SESSION_TTL_SECONDS,
            secure=self._cookie_secure(),
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", cookie)
        self._cors_headers()
        payload = json.dumps({
            "success": True,
            "user": sess.to_public(),
            "message": "登录成功",
            "env": erp.get("env") or ACTIVE_ENV,
            "next": safe_internal_next(str(body.get("next") or "")),
        }, ensure_ascii=False, default=str).encode("utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth_logout(self):
        """退出登录：清除会话 Cookie 与遗留的令牌 Cookie。"""
        sid = self._session_id()
        sess = self._current_session()
        if sid:
            get_session_store(SESSION_TTL_SECONDS).delete(sid)
        if sess is not None:
            try:
                self.system.db.log_audit(
                    sess.username, sess.role or "user", self._client_ip(), "logout"
                )
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Set-Cookie",
            session_cookie_header(
                "", max_age=0, clear=True, secure=self._cookie_secure()
            ),
        )
        # 清理历史版本写入的 qa_token（现已不再接受该 Cookie 鉴权）
        self.send_header(
            "Set-Cookie",
            "qa_token=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
            + ("; Secure" if self._cookie_secure() else ""),
        )
        self._cors_headers()
        payload = json.dumps({"success": True, "message": "已退出登录"}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth_me(self):
        """当前登录用户。"""
        sess = self._current_session()
        if sess:
            self._ok({"user": sess.to_public(), "auth": "session"})
            return
        # 强制登录时 /me 不把服务令牌当成「已登录用户」（避免登录页误跳转）
        if not REQUIRE_LOGIN and API_TOKEN and self._has_api_token():
            self._ok({
                "user": {"username": "service", "role": "token", "enterprise_code": ""},
                "auth": "api_token",
            })
            return
        self._err(401, "未登录")

    def _redirect(self, path: str) -> None:
        self.send_response(302)
        self.send_header("Location", safe_internal_next(path))
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
            self._audit("sync_bridge", db_path, json.dumps(stats, ensure_ascii=False, default=str)[:800])
            self._ok({
                "message": "生产桥接同步完成",
                "db": db_path,
                "stats": stats,
            })
        except Exception as e:
            logger.exception("桥接同步失败: %s", e)
            self._audit("sync_bridge_failed", db_path, str(e)[:300])
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

    def _processes_catalog(self):
        """标准工序列表（与 MES/ERP process_catalog 对齐，供筛选下拉）。"""
        self._ok(process_catalog_payload())

    def _dashboard(self, params=None):
        """仪表板总览。

        适用页面：品质总览；含 KPI、闭环状态条、口径说明、TOP 料号。
        查询参数：fiscal_period — ERP 周期码 YYYYMM，默认当月。
        """
        params = params or {}
        fiscal_period = params.get("fiscal_period", [None])[0]
        self._ok(self.system.get_dashboard(fiscal_period))

    def _part_spotlight(self, params=None):
        """异常料号决策卡。

        适用页面：品质总览「异常料号一张卡」；点击 TOP 报废料号柱图打开。
        查询参数：
          - part 或 part_number：料号（必填，二者任一）
          - fiscal_period：ERP 周期码 YYYYMM，默认当月
        """
        params = params or {}
        # 兼容 part / part_number（前端与脚本两种写法）
        part = (
            params.get("part", [""])[0]
            or params.get("part_number", [""])[0]
            or ""
        ).strip()
        fiscal_period = (params.get("fiscal_period", [None])[0] or "").strip() or None
        if not part:
            self._err(400, "请选择料号（参数 part 或 part_number）")
            return
        self._ok(self.system.get_part_spotlight(part, fiscal_period=fiscal_period))

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

    def _parts(self, params):
        """料号下拉列表。

        适用页面：体检/报废/追溯/客诉/8D/历史等所有料号筛选与必填项。
        查询参数：
          - fiscal_period：可选，ERP 周期码 YYYYMM；默认当月
          - limit：最多返回条数，默认 300
          - q：料号/名称模糊过滤
        """
        params = params or {}
        fp = (params.get("fiscal_period", [None])[0] or "").strip() or None
        if not fp:
            fp = datetime.now().strftime("%Y%m")
        q = (params.get("q", [""])[0] or "").strip() or None
        try:
            limit = int((params.get("limit", ["300"])[0] or 300))
        except Exception:
            limit = 300
        self._ok(self.system.get_part_options(fiscal_period=fp, limit=limit, q=q))

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
            # 周期列表按生产报废口径（排除非量产工序/项目/测试/TEST-）
            where = self.system._scrap_non_production_sql()
            where_params = []
            if part:
                where += " AND v.MfgPartCode LIKE ?"
                where_params.append(f"%{part}%")
            
            # 返回FiscalPeriod（ERP系统标准周期码，格式YYYYMM）
            r = erp.execute(
                "SELECT DISTINCT TOP 24 v.FiscalPeriod FROM View_P_Inspection v "
                f"WHERE {where} ORDER BY v.FiscalPeriod DESC",
                where_params,
            )
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
                fiscal_period=fiscal_period or (str(period).replace('-', '') if period else None),
                output_dir=str(OUTPUT_DIR),
            )

            filename = os.path.basename(pdf_path)
            _cleanup_output_dir()
            self._audit("export_health_pdf", filename, f"part={part} period={fiscal_period or period or ''}")

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
                data = self.system.get_process_yield(
                    part or None, fiscal_period=fiscal_period or None,
                    start_date=start, end_date=end,
                )

            xlsx_path = export_module_excel(module, data or {}, str(OUTPUT_DIR), extra=extra)
            filename = os.path.basename(xlsx_path)
            _cleanup_output_dir()
            self._audit(
                "export_excel", filename,
                f"module={module} part={part or ''} period={fiscal_period or period or ''}",
            )
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
            logger.exception("Excel 导出失败: %s", e)
            self._err(500, "Excel 导出失败，请查看服务端日志")

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
                "SELECT * FROM eightd_reports WHERE report_no=? "
                "AND IFNULL(deleted_at,'')='' LIMIT 1",
                (rno,),
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
            _cleanup_output_dir()
            self._audit("export_8d_pdf", filename, f"report_no={report.get('report_no') or ''}")
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
            logger.exception("8D PDF生成失败: %s", e)
            self._err(500, "8D PDF生成失败，请查看服务端日志")

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
        """工序良率；可选 part、fiscal_period 或 start/end（按关联工单限定 MES）。"""
        part = params.get("part", [""])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        self._ok(self.system.get_process_yield(
            part or None, fiscal_period=fiscal_period or None,
            start_date=start, end_date=end,
        ))

    def _work_orders(self, params):
        part = params.get("part", [""])[0]
        start = params.get("start", [None])[0]
        end = params.get("end", [None])[0]
        period = params.get("period", [None])[0]
        fiscal_period = params.get("fiscal_period", [None])[0]
        if not fiscal_period and period:
            fp = str(period).replace("-", "")
            if len(fp) == 6 and fp.isdigit():
                fiscal_period = fp
                start = end = None
        self._ok(self.system.get_work_orders(
            part or None, start, end, fiscal_period=fiscal_period or None,
        ))

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
        proc = self.system.get_process_yield(
            part, fiscal_period=fiscal_period,
            start_date=start if not fiscal_period else None,
            end_date=end if not fiscal_period else None,
        )
        wo = self.system.get_work_orders(part, start, end, fiscal_period=fiscal_period)

        scrap_pcs = int(scrap.get("total_scrap_pcs") or 0)
        stock_pcs = int(scrap.get("total_in_stock_pcs") or 0)
        # 与总览统一：投入=入库+报废，报废率=报废/(入库+报废)；无入库基数不给率
        input_base = scrap_pcs + stock_pcs
        rate_reliable = bool(scrap.get("rate_reliable"))
        scrap_rate = float(scrap.get("scrap_rate")) if rate_reliable and scrap.get("scrap_rate") is not None else None
        pass_qty = stock_pcs
        pass_rate = round(100.0 - scrap_rate, 2) if scrap_rate is not None else None

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
                "rate_reliable": rate_reliable,
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
        fiscal_period = params.get("fiscal_period", [""])[0]
        self._ok({
            "reports": self.system.db.get_8d_list(
                part or None, status or None, fiscal_period=fiscal_period or None,
            ),
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
            if path.startswith("/vendor/"):
                # 第三方库（如本地化 ECharts）：可长缓存，避免每次整包重下
                self.send_header("Cache-Control", "public, max-age=86400")
            else:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._err(404, "文件不存在")
        except Exception as e:
            logger.exception("静态文件错误 %s: %s", path, e)
            self._err(500, "静态文件读取失败")

    def _8d_generate(self, body):
        """
        智能生成 8D（对接对外接口 POST /api/v1/8d/generate）。

        入参（JSON）：
          - part_no / part：料号（必填）
          - problem_desc / abnormal_reason / abnormal_desc：问题描述（必填）
        行为：转发至 8D 服务 LangGraph 闭环；将 D1–D8 映射落本地库供编辑/导出。
        """
        body = body or {}
        part = (
            body.get("part_no")
            or body.get("part")
            or body.get("part_number")
            or ""
        )
        part = str(part).strip()
        problem = (
            body.get("problem_desc")
            or body.get("abnormal_reason")
            or body.get("abnormal_desc")
            or body.get("problem")
            or ""
        )
        problem = str(problem).strip()

        # 预警来源关联：alert_id → 自动带出料号/问题/周期，并把关联落到 8D 报告
        alert_id = str(body.get("alert_id") or "").strip()
        fiscal_period = str(body.get("fiscal_period") or "").strip()
        period = str(body.get("period") or "").strip()
        if fiscal_period and not _FP_RE.match(fiscal_period):
            self._err(400, "fiscal_period 格式应为 YYYYMM")
            return
        if not fiscal_period and period:
            parts = _period_parts(period)
            if not parts:
                self._err(400, f"周期格式无效：{period}")
                return
            fiscal_period = parts[0]
        if alert_id:
            alert = self.system.db.get_alert(alert_id)
            if not alert:
                self._err(404, "来源预警不存在")
                return
            alert_part = str(alert.get("part_number") or "").strip()
            if not part and alert_part and alert_part != "全厂":
                part = alert_part
            if not problem:
                problem = str(alert.get("message") or "").strip() or f"品质预警：{alert_id}"
            if not fiscal_period:
                fp0 = str(alert.get("fiscal_period") or "").strip()
                if fp0:
                    fiscal_period = fp0
                else:
                    d0 = re.sub(r"[^0-9]", "", str(alert.get("created_at") or ""))[:6]
                    fiscal_period = d0 if _FP_RE.match(d0 or "") else ""

        if not part:
            self._err(400, "缺少料号 part_no")
            return
        if not problem:
            self._err(400, "缺少问题描述 problem_desc")
            return

        payload = {"part_no": part, "problem_desc": problem}
        if body.get("max_agent_rounds") is not None:
            try:
                payload["max_agent_rounds"] = int(body["max_agent_rounds"])
            except (TypeError, ValueError):
                self._err(400, "max_agent_rounds 须为 1–10 的整数")
                return

        # async=true：立即返回 job_id，生成在后台进行，前端轮询 /api/8d/job
        if str(body.get("async")).strip().lower() in ("1", "true", "yes", "on"):
            self._8d_generate_async(part, problem, payload, alert_id=alert_id,
                                    fiscal_period=fiscal_period)
            return

        try:
            ext = call_external_8d_generate(payload)
        except External8DError as e:
            self._err(e.status, e.message)
            return
        except Exception as e:
            logger.exception("调用对外 8D 服务失败: %s", e)
            self._err(502, f"调用 8D 服务失败: {e}")
            return

        res = _finalize_external_8d(
            ext, part, problem, self.system.db,
            alert_id=alert_id, fiscal_period=fiscal_period,
        )
        score = res.get("total_score")
        self._audit(
            "8d_generate",
            res["report"].get("report_no") or part,
            f"part={part} ok={res['ok']} qualified={res.get('qualified') or ''} "
            f"score={score if score is not None else ''}",
        )
        self._ok(res)

    def _8d_generate_async(self, part: str, problem: str, payload: dict,
                           alert_id: str = "", fiscal_period: str = ""):
        """提交后台生成任务；操作人/角色/IP 在响应前取好，供后台线程写审计。"""
        if not _8D_WORKERS.acquire(blocking=False):
            self._err(429, f"8D 生成任务已达并发上限（{_8D_MAX_WORKERS}），请稍后重试")
            return
        try:
            actor, role = self._actor()
            ip = self._client_ip()
            job = _8d_job_create(part, problem, alert_id=alert_id, fiscal_period=fiscal_period)
            threading.Thread(
                target=_run_8d_job_guarded,
                args=(job["id"], dict(payload), part, problem,
                      self.system.db, actor, role, ip, alert_id, fiscal_period),
                name=f"8d-job-{job['id'][:8]}",
                daemon=True,
            ).start()
        except Exception:
            _8D_WORKERS.release()
            raise
        self._ok({
            "job_id": job["id"],
            "status": job["status"],
            "async": True,
            "alert_id": alert_id,
            "fiscal_period": fiscal_period,
            "message": "已提交后台生成，页面会自动刷新进度",
        })

    def _8d_job(self, params):
        """查询后台 8D 任务状态；任务在内存中，重启后返回 404。"""
        job_id = (params.get("id", [""])[0] or "").strip()
        if not job_id:
            self._err(400, "缺少任务 id")
            return
        snap = _8d_job_get(job_id)
        if not snap:
            self._err(404, "任务不存在或已过期，请重新生成")
            return
        self._ok({"job": snap})

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
        prev = self.system.db.get_8d(body["id"], include_deleted=True)
        if prev and (prev.get("deleted_at") or "").strip():
            self._err(409, "该 8D 报告已删除，不能继续编辑；请新建报告")
            return
        # 报告编号唯一（软删除报告不占用编号）
        report_no = str(body.get("report_no") or (prev or {}).get("report_no") or "").strip()
        if report_no:
            dup = self.system.db.find_8d_by_report_no(report_no, body["id"])
            if dup:
                self._err(409, f"报告编号 {report_no} 已被 {dup.get('id')} 使用")
                return
            body["report_no"] = report_no
        # 状态机：仅 open/closed；关闭须通过确定性校验，且只在 open→closed 迁移时回写知识
        status = body.get("status") or (prev or {}).get("status") or "open"
        if status not in ("open", "closed"):
            self._err(400, f"不支持的报告状态：{status}")
            return
        promoted = 0
        is_closing = status == "closed" and (prev or {}).get("status") != "closed"
        if is_closing:
            merged = {**(prev or {}), **body}
            blockers = validate_8d_closure(merged)
            if blockers:
                self._err(400, "关闭校验未通过：" + "；".join(blockers))
                return
            body["closed_at"] = body.get("closed_at") or datetime.now().isoformat()
            promoted = self.system.db.promote_8d_knowledge(merged)
        elif status == "closed":
            # 已关闭报告再次编辑：内容有变化仍须通过关闭校验，不能借「已关闭」绕过
            if _8d_content_changed(body, prev):
                merged = {**(prev or {}), **body}
                blockers = validate_8d_closure(merged)
                if blockers:
                    self._err(400, "已关闭报告的修改未通过关闭校验：" + "；".join(blockers))
                    return
            body["closed_at"] = body.get("closed_at") or (prev or {}).get("closed_at") or ""
        else:
            body["closed_at"] = ""
        # 只写表字段
        allow = {
            "id", "report_no", "part_number", "customer", "problem",
            "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
            "defects_json", "evidence_json", "history_refs_json",
            "knowledge_refs_json", "data_policy",
            "alert_id", "fiscal_period",
            "status", "severity", "created_by", "created_at", "updated_at", "closed_at",
        }
        row = {k: v for k, v in body.items() if k in allow}
        if prev is None and not row.get("created_by"):
            row["created_by"] = self._actor()[0]
        self.system.db.save_8d(row)
        self._audit(
            "8d_save",
            row.get("report_no") or body["id"],
            f"status={status or ''} promoted={promoted}",
        )
        self._ok({
            "message": "保存成功" + (f"；已回写验证知识 {promoted} 条" if promoted else ""),
            "promoted_knowledge": promoted,
        })

    def _8d_delete(self, params):
        rid = params.get("id", [""])[0]
        if not rid:
            self._err(400, "缺少id")
            return
        prev = self.system.db.get_8d(rid, include_deleted=True)
        if not prev or (prev.get("deleted_at") or "").strip():
            self._err(404, "未找到该 8D 报告")
            return
        self.system.db.delete_8d(rid)
        self._audit(
            "8d_delete", rid,
            f"soft=1 report_no={prev.get('report_no') or ''} part={prev.get('part_number') or ''}",
        )
        self._ok({"message": "已删除（可在知识库审计日志追溯）", "id": rid})

    def _8d_purge(self, params=None):
        purged = self.system.db.purge_invalid_8d()
        self._audit("8d_purge_invalid", detail=f"purged={len(purged)}")
        self._ok({"message": f"已清理无效报告 {len(purged)} 条", "purged": purged})

    def _alerts(self, params):
        ack = params.get("acknowledged", [None])[0]
        acknowledged = None if ack is None or ack == "" else (ack == "true" or ack == "1")
        alert_type = params.get("type", [None])[0] or None
        level = params.get("level", [None])[0] or None
        fiscal_period = (params.get("fiscal_period", [""])[0] or "").strip() or None
        alerts = self.system.db.get_alerts(
            acknowledged=acknowledged, alert_type=alert_type, level=level, limit=200,
            fiscal_period=fiscal_period,
        )
        # 卡片汇总：含全部确认状态，不受 ack/level 影响（类型/周期筛选仍生效）
        # 未确认+已确认=全部；严重+关注=全部；已归档（问题已不再触发）不计入
        base = self.system.db.get_alerts(
            acknowledged=None, alert_type=alert_type, level=None, limit=200,
            fiscal_period=fiscal_period,
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
            "fiscal_period": fiscal_period or "",
        })

    def _alerts_scan(self, params):
        """从 ERP/MES 扫描并刷新预警"""
        fiscal_period = params.get("fiscal_period", [None])[0]
        result = self.system.scan_quality_alerts(fiscal_period)
        self._audit("alerts_scan", fiscal_period or "", f"added={result.get('added') or result.get('created') or ''}")
        self._ok(result)

    def _ack_alert(self, params):
        aid = params.get("id", [""])[0]
        if not aid:
            self._err(400, "缺少id参数")
            return
        actor, _role = self._actor()
        by = actor if actor not in ("", "anonymous") else params.get("by", ["User"])[0]
        self.system.db.ack_alert(aid, by)
        self._audit("alert_ack", aid, f"by={by}")
        self._ok({"message": "预警已确认", "acked": aid})

    def _ack_alerts_batch(self, params):
        ids_raw = params.get("ids", [""])[0] or ""
        ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
        if not ids:
            self._err(400, "缺少ids参数")
            return
        actor, _role = self._actor()
        by = actor if actor not in ("", "anonymous") else params.get("by", ["User"])[0]
        n = self.system.db.ack_alerts_batch(ids, by)
        self._audit("alert_ack_batch", detail=f"count={n} by={by}")
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
        self._audit("history_purge_empty", detail=f"purged={len(purged)}")
        self._ok({"message": f"已清理空壳历史 {len(purged)} 条", "purged": purged})

    def _manual_learn(self, body):
        learn_type = body.get("type", "manual")
        content = body.get("content", "")
        insight = body.get("insight", "")
        self.system.db.add_learning(learn_type, "manual", content, insight, 0.9)
        self._audit("manual_learn", learn_type, f"content_len={len(str(content))}")
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
        # 诚实标注：prod 仍指向 zr_aicoding 桥接副本 ≠ 生产库
        erp_norm = str(resolved.get("erp_db_path") or "").replace("\\", "/").lower()
        if erp_norm and (
            "pythonproject_zr_aicoding" in erp_norm
            or "erp_quality_bridge" in erp_norm
            or "erp_bridge" in erp_norm
        ):
            msg = f"QA_ENV=prod 但 ERP/MES 指向桥接副本：{resolved.get('erp_db_path')}；对外称生产库前须改为独立生产连接器"
            logger.warning("[ENV] %s", msg)
            print(f"[WARN] {msg}", flush=True)
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


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """线程型 HTTP 服务：限制并发工作线程与监听队列，超限直接 503。"""

    daemon_threads = True
    request_queue_size = 128
    max_workers = 64

    def __init__(self, server_address, handler_cls, max_workers: int = 64):
        super().__init__(server_address, handler_cls)
        self.max_workers = max(1, int(max_workers))
        self._slots = threading.BoundedSemaphore(self.max_workers)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


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
    # 生产并发：有界线程池（连接队列 128 / 工作线程 64）
    server = _BoundedThreadingHTTPServer((API_HOST, API_PORT), PCBQualityHandler)
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
        # 生产：后台周期性从 MES/ERP 生产库刷新桥接表（需显式 QA_BRIDGE_SYNC=1）
        if (_ctx.get("env") or "").lower() == "prod":
            try:
                from app.bridge_sync_worker import start_bridge_sync_worker
                erp_live = (_ctx.get("resolved") or {}).get("erp_db_path")
                if start_bridge_sync_worker(erp_live):
                    print(f"  桥接同步: 已启用（跟生产库动态更新） db={erp_live}", flush=True)
                else:
                    print("  桥接同步: 未启用（如需后台刷新桥接表，请设 QA_BRIDGE_SYNC=1）", flush=True)
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
    try:
        _8d = resolve_eight_d_api()
        print(
            f"  8D 生成入口[{_8d.get('env')}]: {_8d.get('base')}",
            flush=True,
        )
    except Exception:
        print(f"  8D 生成入口:         {EIGHT_D_API_BASE}", flush=True)
    if REQUIRE_LOGIN:
        print("  访问方式: 打开 /login.html 使用 ERP/MES 账号登录", flush=True)
        if API_TOKEN:
            print("  自动化:   请求头 X-API-Token / Authorization: Bearer <QA_API_TOKEN>", flush=True)
    elif API_TOKEN:
        print("  访问方式: 浏览器打开 /login.html 登录；自动化脚本携带请求头 X-API-Token", flush=True)
    print("完整部署说明见 deploy/DEPLOY.md", flush=True)
    print("按 Ctrl+C 停止服务", flush=True)
    print("=" * 60, flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止", flush=True)


if __name__ == "__main__":
    main()
