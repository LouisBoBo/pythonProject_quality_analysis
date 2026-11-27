# -*- coding: utf-8 -*-
"""
智能品质分析系统 - 核心引擎 v1.0.0
===================================
功能：
  1. 产品全流程追溯查询与品质分析
  2. 8D改善报告智能输出
  3. 在线产品品质预警
  4. 自我学习进化功能

作者: WorkBuddy AI
日期: 2026-04-13
"""

import os
import sys
import json
import time
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
import threading
import re

try:
    from app.date_window import end_exclusive
except ImportError:  # 以 app/ 为顶层目录直接运行时
    from date_window import end_exclusive

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QI] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("QualityIntelligence")


# ═══════════════════════════════════════════════════════════════
# 数据结构定义
# ═══════════════════════════════════════════════════════════════

@dataclass
class PartQualityRecord:
    """单一料号品质记录"""
    part_number: str
    part_name: str = ""
    total_inspect: int = 0
    pass_count: int = 0
    ng_count: int = 0
    ng_rate: float = 0.0
    scrap_pcs: int = 0
    rework_pcs: int = 0
    first_inspect_date: str = ""
    last_inspect_date: str = ""
    lot_count: int = 0
    process_stats: List[Dict] = field(default_factory=list)
    defect_details: List[Dict] = field(default_factory=list)
    defect_trend: List[Dict] = field(default_factory=list)


@dataclass
class DefectPattern:
    """不良模式定义"""
    pattern_id: str
    pattern_name: str
    defect_types: List[str]
    root_causes: List[str]
    solutions: List[str]
    priority: int = 1
    occurrence_count: int = 0
    success_rate: float = 0.0
    created_date: str = ""


@dataclass
class QualityAlert:
    """品质预警"""
    alert_id: str
    alert_type: str  # critical/warning/info
    part_number: str
    alert_message: str
    ng_rate: float
    threshold: float
    recommendation: str
    create_time: str
    acknowledged: bool = False
    acknowledged_by: str = ""
    acknowledged_time: str = ""


@dataclass
class EightDReport:
    """8D改善报告"""
    report_id: str
    d0_symptoms: str
    d1_team: str
    d2_problem: str
    d3_containment: str
    d4_root_cause: str
    d5_selection: str
    d6_implementation: str
    d7_prevention: str
    d8_completion: str
    related_defects: List[Dict] = field(default_factory=list)
    status: str = "draft"  # draft/approved/completed
    created_by: str = ""
    created_time: str = ""
    closed_time: str = ""


# ═══════════════════════════════════════════════════════════════
# 知识库管理器
# ═══════════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    品质知识库 - 存储不良模式、根因分析规则、改善对策
    支持自我学习进化：基于历史案例自动更新规则
    """

    def __init__(self, kb_path: str = None):
        if kb_path is None:
            try:
                from config.settings import KB_V1_PATH
                kb_path = KB_V1_PATH
            except Exception:
                base_dir = Path(__file__).resolve().parent.parent
                kb_path = base_dir / "data" / "quality_knowledge.db"
        self.kb_path = Path(kb_path)
        self.kb_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.kb_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_database(self):
        """初始化知识库表结构"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 不良模式表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS defect_patterns (
                pattern_id TEXT PRIMARY KEY,
                pattern_name TEXT NOT NULL,
                defect_types TEXT NOT NULL,  -- JSON数组
                root_causes TEXT NOT NULL,    -- JSON数组
                solutions TEXT NOT NULL,      -- JSON数组
                priority INTEGER DEFAULT 1,
                occurrence_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                created_date TEXT,
                modified_date TEXT
            )
        """)

        # 8D报告表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eight_d_reports (
                report_id TEXT PRIMARY KEY,
                part_number TEXT NOT NULL,
                d0_symptoms TEXT,
                d1_team TEXT,
                d2_problem TEXT,
                d3_containment TEXT,
                d4_root_cause TEXT,
                d5_selection TEXT,
                d6_implementation TEXT,
                d7_prevention TEXT,
                d8_completion TEXT,
                related_defects TEXT,  -- JSON数组
                status TEXT DEFAULT 'draft',
                created_by TEXT,
                created_time TEXT,
                closed_time TEXT
            )
        """)

        # 品质预警表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quality_alerts (
                alert_id TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                part_number TEXT NOT NULL,
                alert_message TEXT,
                ng_rate REAL,
                threshold REAL,
                recommendation TEXT,
                create_time TEXT,
                acknowledged INTEGER DEFAULT 0,
                acknowledged_by TEXT,
                acknowledged_time TEXT
            )
        """)

        # 学习历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learning_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learning_type TEXT NOT NULL,
                content TEXT NOT NULL,
                result TEXT,
                created_time TEXT
            )
        """)

        conn.commit()
        logger.info(f"知识库初始化完成: {self.kb_path}")

    def get_stats(self) -> Dict:
        """获取知识库统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        stats = {
            "defect_patterns": cursor.execute("SELECT COUNT(*) FROM defect_patterns").fetchone()[0],
            "eight_d_reports": cursor.execute("SELECT COUNT(*) FROM eight_d_reports").fetchone()[0],
            "quality_alerts": cursor.execute("SELECT COUNT(*) FROM quality_alerts").fetchone()[0],
            "learning_history": cursor.execute("SELECT COUNT(*) FROM learning_history").fetchone()[0]
        }
        return stats

    def save_defect_pattern(self, pattern: DefectPattern):
        """保存不良模式"""
        import json
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO defect_patterns 
            (pattern_id, pattern_name, defect_types, root_causes, solutions, 
             priority, occurrence_count, success_rate, created_date, modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pattern.pattern_id,
            pattern.pattern_name,
            json.dumps(pattern.defect_types),
            json.dumps(pattern.root_causes),
            json.dumps(pattern.solutions),
            pattern.priority,
            pattern.occurrence_count,
            pattern.success_rate,
            pattern.created_date or datetime.now().isoformat(),
            datetime.now().isoformat()
        ))
        conn.commit()

    def get_all_defect_patterns(self) -> List[DefectPattern]:
        """获取所有不良模式"""
        import json
        conn = self._get_connection()
        cursor = conn.cursor()
        rows = cursor.execute("SELECT * FROM defect_patterns ORDER BY priority").fetchall()
        
        patterns = []
        for row in rows:
            patterns.append(DefectPattern(
                pattern_id=row["pattern_id"],
                pattern_name=row["pattern_name"],
                defect_types=json.loads(row["defect_types"]),
                root_causes=json.loads(row["root_causes"]),
                solutions=json.loads(row["solutions"]),
                priority=row["priority"],
                occurrence_count=row["occurrence_count"],
                success_rate=row["success_rate"],
                created_date=row["created_date"]
            ))
        return patterns

    def save_eight_d_report(self, report: EightDReport):
        """保存8D报告"""
        import json
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO eight_d_reports 
            (report_id, part_number, d0_symptoms, d1_team, d2_problem, d3_containment,
             d4_root_cause, d5_selection, d6_implementation, d7_prevention, d8_completion,
             related_defects, status, created_by, created_time, closed_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.report_id,
            report.part_number,
            report.d0_symptoms,
            report.d1_team,
            report.d2_problem,
            report.d3_containment,
            report.d4_root_cause,
            report.d5_selection,
            report.d6_implementation,
            report.d7_prevention,
            report.d8_completion,
            json.dumps(report.related_defects),
            report.status,
            report.created_by,
            report.created_time or datetime.now().isoformat(),
            report.closed_time
        ))
        conn.commit()

    def get_eight_d_reports(self, part_number: str = None) -> List[EightDReport]:
        """获取8D报告列表"""
        import json
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if part_number:
            rows = cursor.execute(
                "SELECT * FROM eight_d_reports WHERE part_number LIKE ? ORDER BY created_time DESC",
                (f"%{part_number}%",)
            ).fetchall()
        else:
            rows = cursor.execute("SELECT * FROM eight_d_reports ORDER BY created_time DESC").fetchall()
        
        reports = []
        for row in rows:
            reports.append(EightDReport(
                report_id=row["report_id"],
                part_number=row["part_number"],
                d0_symptoms=row["d0_symptoms"] or "",
                d1_team=row["d1_team"] or "",
                d2_problem=row["d2_problem"] or "",
                d3_containment=row["d3_containment"] or "",
                d4_root_cause=row["d4_root_cause"] or "",
                d5_selection=row["d5_selection"] or "",
                d6_implementation=row["d6_implementation"] or "",
                d7_prevention=row["d7_prevention"] or "",
                d8_completion=row["d8_completion"] or "",
                related_defects=json.loads(row["related_defects"] or "[]"),
                status=row["status"],
                created_by=row["created_by"] or "",
                created_time=row["created_time"] or "",
                closed_time=row["closed_time"] or ""
            ))
        return reports

    def save_alert(self, alert: QualityAlert):
        """保存品质预警"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO quality_alerts 
            (alert_id, alert_type, part_number, alert_message, ng_rate, threshold,
             recommendation, create_time, acknowledged, acknowledged_by, acknowledged_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.alert_id,
            alert.alert_type,
            alert.part_number,
            alert.alert_message,
            alert.ng_rate,
            alert.threshold,
            alert.recommendation,
            alert.create_time,
            1 if alert.acknowledged else 0,
            alert.acknowledged_by,
            alert.acknowledged_time
        ))
        conn.commit()

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str = "System"):
        """确认预警"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE quality_alerts 
            SET acknowledged = 1, acknowledged_by = ?, acknowledged_time = ?
            WHERE alert_id = ?
        """, (acknowledged_by, datetime.now().isoformat(), alert_id))
        conn.commit()

    def get_active_alerts(self) -> List[QualityAlert]:
        """获取未确认的预警"""
        conn = self._get_connection()
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT * FROM quality_alerts WHERE acknowledged = 0 ORDER BY create_time DESC"
        ).fetchall()
        
        alerts = []
        for row in rows:
            alerts.append(QualityAlert(
                alert_id=row["alert_id"],
                alert_type=row["alert_type"],
                part_number=row["part_number"],
                alert_message=row["alert_message"] or "",
                ng_rate=row["ng_rate"] or 0,
                threshold=row["threshold"] or 0,
                recommendation=row["recommendation"] or "",
                create_time=row["create_time"] or "",
                acknowledged=False
            ))
        return alerts

    def add_learning_record(self, learning_type: str, content: str, result: str = None):
        """添加学习记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO learning_history (learning_type, content, result, created_time)
            VALUES (?, ?, ?, ?)
        """, (learning_type, content, result, datetime.now().isoformat()))
        conn.commit()


# ═══════════════════════════════════════════════════════════════
# 核心分析引擎
# ═══════════════════════════════════════════════════════════════

class QualityIntelligenceCore:
    """
    智能品质分析核心引擎
    整合ERP/MES数据，提供全面的品质分析能力
    """

    def __init__(self, erp_connector=None, mes_connector=None, knowledge_base=None):
        self.erp = erp_connector
        self.mes = mes_connector
        self.kb = knowledge_base if knowledge_base else KnowledgeBase()
        self._analysis_cache = {}
        self._cache_ttl = 300  # 5分钟缓存

    def set_erp_connector(self, connector):
        """设置ERP连接器"""
        self.erp = connector
        logger.info("ERP连接器已设置")

    def set_mes_connector(self, connector):
        """设置MES连接器"""
        self.mes = connector
        logger.info("MES连接器已设置")

    def _is_cache_valid(self, cache_key: str) -> bool:
        """检查缓存是否有效"""
        if cache_key not in self._analysis_cache:
            return False
        cached_time = self._analysis_cache[cache_key].get("timestamp", 0)
        return (time.time() - cached_time) < self._cache_ttl

    def _get_cache(self, cache_key: str) -> Optional[Any]:
        """获取缓存数据"""
        if self._is_cache_valid(cache_key):
            return self._analysis_cache[cache_key].get("data")
        return None

    def _set_cache(self, cache_key: str, data: Any):
        """设置缓存数据"""
        self._analysis_cache[cache_key] = {
            "data": data,
            "timestamp": time.time()
        }

    def analyze_part_quality(self, part_number: str, start_date: str = None,
                             end_date: str = None) -> PartQualityRecord:
        """
        分析单一料号的品质数据
        综合ERP和MES数据源
        """
        cache_key = f"quality_{part_number}_{start_date}_{end_date}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        record = PartQualityRecord(part_number=part_number)
        
        # 1. 从ERP获取工单数据
        if self.erp and self.erp.connected:
            self._analyze_erp_data(record, part_number, start_date, end_date)
        
        # 2. 从MES获取检验数据
        if self.mes and self.mes.connected:
            self._analyze_mes_data(record, part_number, start_date, end_date)
        
        # 3. 计算汇总指标
        if record.total_inspect > 0:
            record.ng_rate = round(record.ng_count / record.total_inspect * 100, 2)
        
        self._set_cache(cache_key, record)
        return record

    def _analyze_erp_data(self, record: PartQualityRecord, part_number: str,
                          start_date: str = None, end_date: str = None):
        """从ERP分析数据"""
        try:
            # 构建查询条件
            conditions = ["mp.mfgPartCode LIKE ?"]
            params = [f"%{part_number}%"]
            
            if start_date:
                conditions.append("wo.createDate >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("wo.createDate < ?")
                params.append(end_exclusive(end_date))

            where_sql = " AND ".join(conditions)
            
            # 查询工单数据
            sql = f"""
                SELECT 
                    wo.woNumber,
                    mp.mfgPartCode AS PartNumber,
                    mp.mfgPartName AS PartName,
                    wo.qty_PCS_PRODUCED AS PcsProduced,
                    wo.reject_PCS AS RejectPcs,
                    wo.defect_PCS AS DefectPcs,
                    wo.qty_PNL_PRODUCED AS PnlProduced,
                    wo.createDate,
                    wo.endDate,
                    wo.status
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE {where_sql}
                ORDER BY wo.createDate DESC
            """
            
            result = self.erp.execute(sql, params)
            if result.get("success") and result.get("data"):
                work_orders = result["data"]
                
                if work_orders:
                    # 取第一条的料号名称
                    first_wo = work_orders[0]
                    record.part_name = first_wo.get("PartName", "")
                    record.lot_count = len(work_orders)
                    
                    # 汇总数据
                    for wo in work_orders:
                        pcs_produced = int(wo.get("PcsProduced") or 0)
                        reject_pcs = int(wo.get("RejectPcs") or 0)
                        defect_pcs = int(wo.get("DefectPcs") or 0)
                        
                        record.total_inspect += pcs_produced
                        record.ng_count += reject_pcs + defect_pcs
                        record.scrap_pcs += reject_pcs
                        
                        # 更新日期范围
                        create_date = str(wo.get("createDate", "") or "")
                        if create_date:
                            if not record.first_inspect_date or create_date < record.first_inspect_date:
                                record.first_inspect_date = create_date[:10]
                            if not record.last_inspect_date or create_date > record.last_inspect_date:
                                record.last_inspect_date = create_date[:10]
                
                logger.info(f"ERP数据分析完成: {record.lot_count} 个工单")

        except Exception as e:
            logger.error(f"ERP数据分析失败: {e}")

    def _analyze_mes_data(self, record: PartQualityRecord, part_number: str,
                          start_date: str = None, end_date: str = None):
        """从MES分析数据"""
        try:
            # MES的检验记录查询（如果表结构支持）
            conditions = ["1=1"]
            params = []
            
            if start_date:
                conditions.append("r.CDATETIME_CREATED >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("r.CDATETIME_CREATED < ?")
                params.append(end_exclusive(end_date))
            
            where_sql = " AND ".join(conditions)
            
            sql = f"""
                SELECT TOP 100
                    r.CINSPECT_CODE AS InspectCode,
                    r.CINSPECT_TYPE AS InspectType,
                    r.CSTATE AS State,
                    r.CDATETIME_CREATED AS InspectDate
                FROM TBL_QM_INSPECT_RECORD r
                WHERE {where_sql}
                ORDER BY r.CDATETIME_CREATED DESC
            """
            
            result = self.mes.execute(sql, params)
            if result.get("success"):
                logger.info(f"MES数据分析完成: {result.get('row_count', 0)} 条记录")

        except Exception as e:
            logger.warning(f"MES数据分析失败: {e}")

    def analyze_defects(self, part_number: str = None, start_date: str = None,
                       end_date: str = None) -> List[Dict]:
        """
        分析缺陷数据
        返回缺陷统计列表
        """
        defects = []
        
        if self.erp and self.erp.connected:
            try:
                conditions = ["1=1"]
                params = []
                
                if part_number:
                    conditions.append("mp.mfgPartCode LIKE ?")
                    params.append(f"%{part_number}%")
                if start_date:
                    conditions.append("wo.createDate >= ?")
                    params.append(start_date)
                if end_date:
                    conditions.append("wo.createDate < ?")
                    params.append(end_exclusive(end_date))

                where_sql = " AND ".join(conditions)
                
                # 按料号统计缺陷
                sql = f"""
                    SELECT 
                        mp.mfgPartCode AS PartNumber,
                        mp.mfgPartName AS PartName,
                        SUM(wo.qty_PCS_PRODUCED) AS TotalPcs,
                        SUM(wo.reject_PCS) AS TotalReject,
                        SUM(wo.defect_PCS) AS TotalDefect,
                        CAST(SUM(wo.reject_PCS) * 100.0 / NULLIF(SUM(wo.qty_PCS_PRODUCED), 0) AS DECIMAL(5,2)) AS RejectRate
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {where_sql}
                        AND wo.qty_PCS_PRODUCED > 0
                    GROUP BY mp.mfgPartCode, mp.mfgPartName
                    ORDER BY SUM(wo.reject_PCS) DESC
                """
                
                result = self.erp.execute(sql, params)
                if result.get("success") and result.get("data"):
                    for row in result["data"]:
                        defects.append({
                            "part_number": row.get("PartNumber", ""),
                            "part_name": row.get("PartName", ""),
                            "total_pcs": int(row.get("TotalPcs") or 0),
                            "reject_pcs": int(row.get("TotalReject") or 0),
                            "defect_pcs": int(row.get("TotalDefect") or 0),
                            "reject_rate": float(row.get("RejectRate") or 0)
                        })
                
                logger.info(f"缺陷分析完成: {len(defects)} 个料号")
                
            except Exception as e:
                logger.error(f"缺陷分析失败: {e}")
        
        return defects

    def analyze_root_cause(self, part_number: str, defect_type: str = None) -> Dict:
        """
        根因分析
        基于知识库和历史数据提供根因建议
        """
        # 1. 查询知识库中的匹配模式
        patterns = self.kb.get_all_defect_patterns()
        
        # 2. 从ERP获取相关缺陷详情
        related_defects = []
        if self.erp and self.erp.connected:
            try:
                sql = """
                    SELECT TOP 10
                        wo.woNumber,
                        wo.reject_PCS,
                        wo.defect_PCS,
                        wo.createDate
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE mp.mfgPartCode LIKE ?
                        AND wo.reject_PCS > 0
                    ORDER BY wo.reject_PCS DESC
                """
                result = self.erp.execute(sql, [f"%{part_number}%"])
                if result.get("success"):
                    related_defects = result.get("data", [])
            except Exception as e:
                logger.warning(f"根因分析-缺陷查询失败: {e}")
        
        # 3. 匹配知识库模式
        matched_patterns = []
        for pattern in patterns:
            if defect_type and defect_type in pattern.defect_types:
                matched_patterns.append({
                    "pattern_id": pattern.pattern_id,
                    "pattern_name": pattern.pattern_name,
                    "root_causes": pattern.root_causes,
                    "solutions": pattern.solutions,
                    "success_rate": pattern.success_rate
                })
        
        return {
            "part_number": part_number,
            "defect_type": defect_type,
            "related_defects": related_defects[:10],
            "matched_patterns": matched_patterns,
            "knowledge_base_patterns": len(patterns),
            "analysis_time": datetime.now().isoformat()
        }

    def check_alerts(self, threshold_ng_rate: float = 2.0) -> List[QualityAlert]:
        """
        预警检查
        检查当前品质指标是否超过阈值
        """
        alerts = []
        
        if not self.erp or not self.erp.connected:
            return alerts
        
        try:
            # 查询最近7天的不良率
            sql = """
                SELECT 
                    mp.mfgPartCode AS PartNumber,
                    mp.mfgPartName AS PartName,
                    SUM(wo.qty_PCS_PRODUCED) AS TotalPcs,
                    SUM(wo.reject_PCS) AS TotalReject,
                    CAST(SUM(wo.reject_PCS) * 100.0 / NULLIF(SUM(wo.qty_PCS_PRODUCED), 0) AS DECIMAL(5,2)) AS NgRate
                FROM P_WO wo
                INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                WHERE wo.createDate >= DATEADD(day, -7, GETDATE())
                    AND wo.qty_PCS_PRODUCED > 0
                GROUP BY mp.mfgPartCode, mp.mfgPartName
                HAVING SUM(wo.reject_PCS) * 100.0 / NULLIF(SUM(wo.qty_PCS_PRODUCED), 0) > ?
                ORDER BY NgRate DESC
            """
            
            result = self.erp.execute(sql, [threshold_ng_rate])
            if result.get("success") and result.get("data"):
                for row in result["data"]:
                    alert = QualityAlert(
                        alert_id=f"ALERT_{row['PartNumber']}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        alert_type="critical" if float(row["NgRate"] or 0) > threshold_ng_rate * 2 else "warning",
                        part_number=row["PartNumber"],
                        alert_message=f"最近7天不良率 {row['NgRate']}% 超过阈值 {threshold_ng_rate}%",
                        ng_rate=float(row["NgRate"] or 0),
                        threshold=threshold_ng_rate,
                        recommendation=f"建议对 {row['PartNumber']} 进行专项分析",
                        create_time=datetime.now().isoformat()
                    )
                    alerts.append(alert)
                    self.kb.save_alert(alert)
            
            logger.info(f"预警检查完成: {len(alerts)} 个预警")
            
        except Exception as e:
            logger.error(f"预警检查失败: {e}")
        
        return alerts

    def generate_8d_report(self, part_number: str, symptoms: str = None) -> EightDReport:
        """
        生成8D改善报告
        基于历史数据和根因分析
        """
        report_id = f"8D_{part_number}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 获取相关缺陷数据
        related_defects = self.analyze_defects(part_number)
        
        # 获取根因分析
        root_cause = self.analyze_root_cause(part_number)
        
        # 构建报告
        report = EightDReport(
            report_id=report_id,
            part_number=part_number,
            d0_symptoms=symptoms or "详见缺陷分析数据",
            d1_team="品质部 + 生产部 + 工程部",
            d2_problem=f"料号 {part_number} 存在品质异常",
            d3_containment="临时对策：全检/挑选",
            d4_root_cause=json.dumps(root_cause.get("matched_patterns", [])),
            d5_selection="选择最佳改善方案",
            d6_implementation="实施改善措施",
            d7_prevention="建立防呆机制",
            d8_completion="验证效果并标准化",
            related_defects=related_defects[:5],
            status="draft",
            created_by="System",
            created_time=datetime.now().isoformat()
        )
        
        # 保存到知识库
        self.kb.save_eight_d_report(report)
        
        return report

    def run_self_learning(self) -> Dict:
        """
        自我学习功能
        从历史数据中学习，更新知识库
        """
        learning_result = {
            "patterns_discovered": 0,
            "rules_updated": 0,
            "insights": []
        }
        
        try:
            # 1. 分析高不良率料号
            defects = self.analyze_defects()
            high_defect_parts = [d for d in defects if d["reject_rate"] > 1.0]
            
            # 2. 为高频缺陷创建模式
            for defect in high_defect_parts[:10]:
                pattern = DefectPattern(
                    pattern_id=f"PAT_{defect['part_number']}_{datetime.now().strftime('%Y%m%d')}",
                    pattern_name=f"{defect['part_number']} 高不良模式",
                    defect_types=["高不良率"],
                    root_causes=["需进一步分析"],
                    solutions=["增加检验频次", "改善工艺参数"],
                    priority=2,
                    occurrence_count=int(defect["reject_pcs"]),
                    created_date=datetime.now().isoformat()
                )
                self.kb.save_defect_pattern(pattern)
                learning_result["patterns_discovered"] += 1
            
            # 3. 记录学习历史
            self.kb.add_learning_record(
                "self_learning",
                f"分析了 {len(defects)} 个料号，发现 {len(high_defect_parts)} 个高不良率料号",
                json.dumps(learning_result)
            )
            
            learning_result["insights"].append(
                f"系统已完成自我学习，更新了 {learning_result['patterns_discovered']} 个不良模式"
            )
            
            logger.info(f"自我学习完成: {learning_result}")
            
        except Exception as e:
            logger.error(f"自我学习失败: {e}")
            learning_result["error"] = str(e)
        
        return learning_result

    def get_dashboard_data(self) -> Dict:
        """
        获取仪表板数据
        用于GUI概览展示
        """
        dashboard = {
            "timestamp": datetime.now().isoformat(),
            "quality_kpi": {
                "total_wo": 0,
                "total_input_pcs": 0,
                "total_reject_pcs": 0,
                "overall_reject_rate": 0
            },
            "top_defect_parts": [],
            "recent_alerts": [],
            "knowledge_stats": {}
        }
        
        # 1. 获取知识库统计
        dashboard["knowledge_stats"] = self.kb.get_stats()
        
        # 2. 从ERP获取汇总数据
        if self.erp and self.erp.connected:
            try:
                sql = """
                    SELECT 
                        COUNT(*) AS TotalWo,
                        SUM(wo.qty_PCS_PRODUCED) AS TotalInput,
                        SUM(wo.reject_PCS) AS TotalReject
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE wo.createDate >= DATEADD(day, -30, GETDATE())
                """
                
                result = self.erp.execute(sql)
                if result.get("success") and result.get("data"):
                    row = result["data"][0]
                    dashboard["quality_kpi"]["total_wo"] = int(row.get("TotalWo") or 0)
                    dashboard["quality_kpi"]["total_input_pcs"] = int(row.get("TotalInput") or 0)
                    dashboard["quality_kpi"]["total_reject_pcs"] = int(row.get("TotalReject") or 0)
                    
                    total_input = dashboard["quality_kpi"]["total_input_pcs"]
                    if total_input > 0:
                        dashboard["quality_kpi"]["overall_reject_rate"] = round(
                            dashboard["quality_kpi"]["total_reject_pcs"] / total_input * 100, 2
                        )
                
                # 获取TOP缺陷料号
                sql = """
                    SELECT TOP 10
                        mp.mfgPartCode AS PartNumber,
                        mp.mfgPartName AS PartName,
                        SUM(wo.reject_PCS) AS TotalReject,
                        CAST(SUM(wo.reject_PCS) * 100.0 / NULLIF(SUM(wo.qty_PCS_PRODUCED), 0) AS DECIMAL(5,2)) AS RejectRate
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE wo.createDate >= DATEADD(day, -30, GETDATE())
                        AND wo.qty_PCS_PRODUCED > 0
                    GROUP BY mp.mfgPartCode, mp.mfgPartName
                    ORDER BY SUM(wo.reject_PCS) DESC
                """
                
                result = self.erp.execute(sql)
                if result.get("success"):
                    dashboard["top_defect_parts"] = result.get("data", [])
                
            except Exception as e:
                logger.error(f"仪表板数据获取失败: {e}")
        
        # 3. 获取最近预警
        dashboard["recent_alerts"] = self.kb.get_active_alerts()[:5]
        
        return dashboard

    # ═══════════════════════════════════════════════════════════════
    # 产品追溯数据查询方法（GUI调用）
    # ═══════════════════════════════════════════════════════════════

    def trace_work_orders(self, part_number: str = None, start_date: str = None,
                          end_date: str = None) -> Dict:
        """
        精确查询 - 获取工单列表和汇总数据
        用于GUI的精确查询模块
        """
        result = {
            "success": False,
            "data": {
                "total_wo": 0,
                "total_batch": 0,
                "total_input_pcs": 0,
                "total_output_pcs": 0,
                "total_reject_pcs": 0,
                "reject_rate": 0,
                "work_orders": [],
                "daily_trend": [],
                "process_distribution": []
            }
        }

        if not self.erp or not self.erp.connected:
            logger.warning("ERP未连接，返回空数据")
            result["error"] = "ERP未连接"
            return result

        try:
            # 构建查询条件（参数绑定，禁止把用户输入拼进 SQL）
            where_parts = []
            params = []
            if part_number:
                where_parts.append("mp.mfgPartCode LIKE ?")
                params.append(f"%{part_number}%")
            if start_date:
                where_parts.append("wo.createDate >= ?")
                params.append(start_date)
            if end_date:
                where_parts.append("wo.createDate < ?")
                params.append(end_exclusive(end_date))
            
            where_sql = " AND ".join(where_parts) if where_parts else "1=1"

            # 查询工单数据
            sql = f"SELECT TOP 100 wo.woNumber, mp.mfgPartCode AS PartNumber, mp.mfgPartName AS PartName, wo.qty_PCS_PRODUCED AS PcsProduced, wo.reject_PCS AS RejectPcs, wo.defect_PCS AS DefectPcs, wo.qty_PNL_PRODUCED AS PnlProduced, wo.createDate, wo.endDate, wo.status FROM P_WO wo INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId WHERE {where_sql} ORDER BY wo.createDate DESC"

            query_result = self.erp.execute(sql, params)

            if query_result.get("success") and query_result.get("data"):
                work_orders = query_result["data"]
                result["data"]["total_wo"] = len(work_orders)
                result["data"]["work_orders"] = work_orders[:50]  # 最多50条

                # 计算汇总
                for wo in work_orders:
                    result["data"]["total_batch"] += 1
                    result["data"]["total_input_pcs"] += int(wo.get("PcsProduced") or 0)
                    result["data"]["total_reject_pcs"] += int(wo.get("RejectPcs") or 0)

                # 计算报废率
                total_prod = result["data"]["total_input_pcs"]
                if total_prod > 0:
                    result["data"]["reject_rate"] = round(
                        result["data"]["total_reject_pcs"] / total_prod * 100, 2
                    )

                # 计算每日趋势
                daily_stats = defaultdict(lambda: {"input": 0, "reject": 0})
                for wo in work_orders:
                    create_date = str(wo.get("createDate", ""))[:10]
                    if create_date:
                        daily_stats[create_date]["input"] += int(wo.get("PcsProduced") or 0)
                        daily_stats[create_date]["reject"] += int(wo.get("RejectPcs") or 0)

                for date_str in sorted(daily_stats.keys()):
                    stats = daily_stats[date_str]
                    result["data"]["daily_trend"].append({
                        "date": date_str,
                        "input_pcs": stats["input"],
                        "reject_pcs": stats["reject"]
                    })

            result["success"] = True
            logger.info(f"工单追溯查询完成: {result['data']['total_wo']} 个工单")

        except Exception as e:
            logger.error(f"工单追溯查询失败: {e}")
            result["error"] = str(e)

        return result

    def trace_mrb_analysis(self, part_number: str = None, start_date: str = None,
                          end_date: str = None) -> Dict:
        """
        MRB分析 - 报废缺陷、责任工序、报废率分析
        数据来源：View_P_Inspection（TType=1为报废）与P_WO工单表
        报废率 = 报废PCS / (产出PCS + 报废PCS)
        """
        result = {
            "success": False,
            "data": {
                "total_reject_pcs": 0,  # GUI概览卡片使用
                "total_scrap_pcs": 0,
                "total_input_pcs": 0,
                "total_inspect_pcs": 0,
                "scrap_rate": 0,
                "reject_rate": 0,
                "top_defects": [],  # GUI导出使用（按工序/缺陷项分组）
                "defect_top10": [],
                "monthly_trend": []
            }
        }

        try:
            if self.erp and self.erp.connected:
                # ─────────────────────────────────────────────────────
                # 1. 从 View_P_Inspection 查报废明细（TType=1 = 正式报废）
                #    此视图与ERP报废明细报表完全一致
                # ─────────────────────────────────────────────────────
                insp_conditions = []
                insp_params = []
                if part_number:
                    insp_conditions.append("v.MfgPartCode LIKE ?")
                    insp_params.append(f"%{part_number}%")
                else:
                    insp_conditions.append("1=1")
                insp_conditions.append("v.TType = 1")           # 正式报废
                insp_conditions.append("v.InspectionResult = 'Scrapped'")

                if start_date:
                    insp_conditions.append("v.CreateDate >= ?")
                    insp_params.append(start_date)
                if end_date:
                    insp_conditions.append("v.CreateDate < ?")
                    insp_params.append(end_exclusive(end_date))

                insp_where = " AND ".join(insp_conditions)

                # 按缺陷项汇总 TOP10
                defect_sql = f"""
                    SELECT TOP 10
                        v.MfgPartCode AS PartNumber,
                        ISNULL(v.InspectionItem, v.InspectionProcess) AS DefectType,
                        v.InspectionProcess AS Process,
                        SUM(v.QtyOfUnit) AS ScrapPcs
                    FROM View_P_Inspection v
                    WHERE {insp_where}
                    GROUP BY v.MfgPartCode, v.InspectionItem, v.InspectionProcess
                    ORDER BY SUM(v.QtyOfUnit) DESC
                """
                defect_result = self.erp.execute(defect_sql, insp_params)

                # 总报废PCS
                total_sql = f"""
                    SELECT SUM(v.QtyOfUnit) AS TotalScrap
                    FROM View_P_Inspection v
                    WHERE {insp_where}
                """
                total_result = self.erp.execute(total_sql, insp_params)
                if total_result.get("success") and total_result.get("data"):
                    row0 = total_result["data"][0]
                    total_scrap = int(row0.get("TotalScrap") or 0)
                    result["data"]["total_scrap_pcs"] = total_scrap
                    result["data"]["total_reject_pcs"] = total_scrap

                # ─────────────────────────────────────────────────────
                # 2. 从 P_WO 取投入PCS（产出+报废=总投入）
                # ─────────────────────────────────────────────────────
                wo_conditions = []
                wo_params = []
                if part_number:
                    wo_conditions.append("mp.mfgPartCode LIKE ?")
                    wo_params.append(f"%{part_number}%")
                if start_date:
                    wo_conditions.append("wo.createDate >= ?")
                    wo_params.append(start_date)
                if end_date:
                    wo_conditions.append("wo.createDate < ?")
                    wo_params.append(end_exclusive(end_date))
                wo_conditions.append("(wo.qty_PCS_PRODUCED > 0 OR wo.reject_PCS > 0)")
                wo_where = " AND ".join(wo_conditions) if wo_conditions else "1=1"

                input_sql = f"""
                    SELECT SUM(wo.qty_PCS_PRODUCED) AS TotalInput
                    FROM P_WO wo
                    INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                    WHERE {wo_where}
                """
                input_result = self.erp.execute(input_sql, wo_params)
                if input_result.get("success") and input_result.get("data"):
                    row0 = input_result["data"][0]
                    total_input = int(row0.get("TotalInput") or 0)
                    result["data"]["total_input_pcs"] = total_input

                # 计算报废率
                total_input = result["data"]["total_input_pcs"]
                total_scrap = result["data"]["total_scrap_pcs"]
                if total_input > 0:
                    result["data"]["scrap_rate"] = round(total_scrap * 100.0 / total_input, 2)
                    result["data"]["reject_rate"] = result["data"]["scrap_rate"]

                # ─────────────────────────────────────────────────────
                # 3. 缺陷 TOP10 （兼容旧代码格式）
                # ─────────────────────────────────────────────────────
                if defect_result.get("success") and defect_result.get("data"):
                    defect_data = defect_result["data"]
                    top10 = []
                    for row in defect_data:
                        scrap_pcs = int(row.get("ScrapPcs") or 0)
                        ratio = round(scrap_pcs * 100.0 / total_scrap, 1) if total_scrap > 0 else 0
                        top10.append({
                            "part_code": row.get("PartNumber", ""),
                            "defect_type": row.get("DefectType", ""),
                            "process": row.get("Process", ""),
                            "scrap_pcs": scrap_pcs,
                            "ratio": ratio,
                            # 兼容旧字段名
                            "part_number": row.get("PartNumber", ""),
                            "defect_name": row.get("DefectType", ""),
                            "reject_pcs": scrap_pcs,
                            "percentage": ratio
                        })
                    result["data"]["defect_top10"] = top10
                    result["data"]["top_defects"] = top10

                # ─────────────────────────────────────────────────────
                # 4. 月度趋势（从 View_P_Inspection 按月统计报废PCS）
                # ─────────────────────────────────────────────────────
                trend_conditions = []
                trend_params = []
                if part_number:
                    trend_conditions.append("v.MfgPartCode LIKE ?")
                    trend_params.append(f"%{part_number}%")
                trend_conditions.append("v.TType = 1")
                trend_conditions.append("v.InspectionResult = 'Scrapped'")
                trend_where = " AND ".join(trend_conditions) if trend_conditions else "1=1"

                monthly_sql = f"""
                    SELECT 
                        CONVERT(VARCHAR(7), v.CreateDate, 120) AS Month,
                        SUM(v.QtyOfUnit) AS TotalScrap,
                        COUNT(DISTINCT v.WONumber) AS WoCount
                    FROM View_P_Inspection v
                    WHERE {trend_where}
                    GROUP BY CONVERT(VARCHAR(7), v.CreateDate, 120)
                    ORDER BY Month DESC
                """
                monthly_result = self.erp.execute(monthly_sql, trend_params)
                if monthly_result.get("success") and monthly_result.get("data"):
                    # 需要投入数据计算月度报废率，从P_WO按月取
                    monthly_input_sql = f"""
                        SELECT 
                            CONVERT(VARCHAR(7), wo.createDate, 120) AS Month,
                            SUM(wo.qty_PCS_PRODUCED) AS TotalInput
                        FROM P_WO wo
                        INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                        WHERE {wo_where}
                        GROUP BY CONVERT(VARCHAR(7), wo.createDate, 120)
                        ORDER BY Month DESC
                    """
                    monthly_input_result = self.erp.execute(monthly_input_sql, wo_params)
                    monthly_input_map = {}
                    if monthly_input_result.get("success") and monthly_input_result.get("data"):
                        for row in monthly_input_result["data"]:
                            monthly_input_map[row.get("Month", "")] = int(row.get("TotalInput") or 0)

                    trend_list = []
                    for row in monthly_result["data"][:12]:
                        month = row.get("Month", "")
                        scrap = int(row.get("TotalScrap") or 0)
                        inp = monthly_input_map.get(month, 0)
                        rate = round(scrap * 100.0 / inp, 2) if inp > 0 else 0
                        trend_list.append({
                            "Month": month,
                            "TotalScrap": scrap,
                            "TotalInput": inp,
                            "ScrapRate": rate
                        })
                    result["data"]["monthly_trend"] = trend_list

            result["success"] = True
            logger.info(f"MRB分析完成: 报废率={result['data']['scrap_rate']}%")

        except Exception as e:
            logger.error(f"MRB分析失败: {e}")
            result["error"] = str(e)

        return result

    def trace_mes_data(self, part_number: str = None, start_date: str = None,
                       end_date: str = None) -> Dict:
        """
        MES数据 - 工序良率、设备状态、药水异常、不良项目明细
        通过ERP工单号关联MES检验数据
        """
        result = {
            "success": False,
            "data": {
                "process_yield": [],
                "process_defects": [],  # 工序不良项目明细
                "equipment_status": [],
                "chemistry_anomaly": [],
                "spc_data": []
            }
        }

        try:
            if self.mes and self.mes.connected:
                # 关键修复：MES检验表的 CMO_LOT/CSCAN_BARCODE 存储的是工单号，不是料号
                # 必须先从ERP获取料号对应的工单号列表，再用工单号去MES查询
                wo_list = []
                if part_number and self.erp and self.erp.connected:
                    erp_r = self.erp.execute("""
                        SELECT DISTINCT wo.woNumber
                        FROM P_WO wo
                        INNER JOIN E_JobMfgParts mp ON wo.mfgPartId = mp.recId
                        WHERE mp.mfgPartCode LIKE ?
                    """, [f"%{part_number}%"])
                    if erp_r.get("success") and erp_r.get("data"):
                        wo_list = [d.get("woNumber", "") for d in erp_r["data"] if d.get("woNumber")]
                        logger.info(f"MES工序查询: 料号 {part_number} 找到 {len(wo_list)} 个工单")

                # 如果没有工单数据，返回空
                if not wo_list:
                    logger.info(f"MES工序查询: 料号 {part_number} 没有关联工单")
                    result["success"] = True
                    return result

                # 工序良率数据 - 用工单号过滤
                wo_filter = "','".join(wo_list)
                process_sql = f"""
                    SELECT TOP 20
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '') AS ProcessName,
                        COUNT(*) AS InspectCount,
                        SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) AS PassCount,
                        SUM(CASE WHEN r.CRESULT = 2 THEN 1 ELSE 0 END) AS NgCount,
                        CAST(SUM(CASE WHEN r.CRESULT = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0) AS DECIMAL(5,2)) AS PassRate
                    FROM TBL_QM_INSPECT_RECORD r
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT IS NOT NULL AND r.CMO_LOT IN ('{wo_filter}')
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME
                    ORDER BY InspectCount DESC
                """

                query_result = self.mes.execute(process_sql)
                if query_result.get("success"):
                    data = query_result.get("data", [])
                    for item in data:
                        # 添加HTML需要的字段
                        item["total_qty"] = item.get("InspectCount", 0)
                        item["pass_qty"] = item.get("PassCount", 0)
                        item["yield_rate"] = item.get("PassRate", 0)
                    result["data"]["process_yield"] = data

                # 工序不良项目明细 - 用工单号过滤
                defect_sql = f"""
                    SELECT TOP 50
                        r.CPROCESS_ID AS ProcessID,
                        ISNULL(p.CPROCESS_NAME, '未知工序') AS ProcessName,
                        i.CTEMPLATE_ITEM_NAME AS DefectItem,
                        i.CINPUT_VALUE AS DefectValue,
                        COUNT(*) AS DefectCount
                    FROM TBL_QM_INSPECT_RECORD r
                    INNER JOIN TBL_QM_INSPECTION_RECORD_ITEM i ON r.CID = i.CINSPECTION_RECORD_ID
                    LEFT JOIN TBL_BD_PROCESS p ON r.CPROCESS_ID = p.CID
                    WHERE r.CRESULT = 2 AND i.CTEMPLATE_ITEM_NAME IS NOT NULL AND r.CMO_LOT IN ('{wo_filter}')
                    GROUP BY r.CPROCESS_ID, p.CPROCESS_NAME, i.CTEMPLATE_ITEM_NAME, i.CINPUT_VALUE
                    ORDER BY DefectCount DESC
                """

                query_result = self.mes.execute(defect_sql)
                if query_result.get("success"):
                    result["data"]["process_defects"] = query_result.get("data", [])
                    logger.info(f"工序不良项目明细: {len(result['data']['process_defects'])}条")

                # 设备状态（从工序看板表）- tbl_ipqc_kanban实际字段
                equip_sql = """
                    SELECT TOP 20
                        k.CPROCESS_NAME AS ProcessName,
                        k.notVaildCount AS NgCount,
                        k.vaildCount AS PassCount,
                        k.vaildTotal AS TotalCount,
                        k.vaildRate AS YieldRate,
                        k.ctype AS ProcessType
                    FROM tbl_ipqc_kanban k
                    WHERE k.vaildTotal > 0
                    ORDER BY k.vaildTotal DESC
                """

                query_result = self.mes.execute(equip_sql)
                if query_result.get("success"):
                    result["data"]["equipment_status"] = query_result.get("data", [])

            result["success"] = True
            logger.info(f"MES数据查询完成")

        except Exception as e:
            logger.error(f"MES数据查询失败: {e}")
            result["error"] = str(e)

        return result

    def trace_material(self, part_number: str = None, start_date: str = None,
                       end_date: str = None) -> Dict:
        """
        物料追溯 - 原料批次、供应商质量、来料合格率
        """
        result = {
            "success": False,
            "data": {
                "supplier_quality": [],
                "material_lots": [],
                "iqc_pass_rate": 0,
                "iqc_sample_count": 0,
                "trace_coverage": None
            }
        }

        try:
            if self.mes and self.mes.connected:
                # 来料检验数据 - 使用TBL_QM_INSPECT_RECORD模拟
                # 因为TBL_QM_IQC_INSPECT表为空
                iqc_sql = """
                    SELECT TOP 20
                        r.CINSPECT_CODE AS InspectCode,
                        r.CSCAN_BARCODE AS Barcode,
                        r.CINSPECT_QTY AS InspectQty,
                        r.CPROCESS_ID AS ProcessID,
                        r.CINSPECT_TIME AS InspectTime,
                        r.CRESULT AS Result
                    FROM TBL_QM_INSPECT_RECORD r
                    WHERE r.CINSPECT_TYPE = 1
                    ORDER BY r.CINSPECT_TIME DESC
                """

                query_result = self.mes.execute(iqc_sql)
                if query_result.get("success"):
                    data = query_result.get("data", [])
                    total = len(data)
                    if total:
                        # 来料合格率：仅基于实际取样的检验记录
                        pass_count = sum(1 for d in data if d.get("Result") == 1)
                        result["data"]["iqc_pass_rate"] = round(pass_count / total * 100, 2)
                        result["data"]["iqc_sample_count"] = total

            # 桥接库 TBL_QM_INSPECT_RECORD 不含供应商与批次链路字段：
            # 供应商质量、追溯覆盖率一律置空并在 data_available 中标记，禁止编造
            result["data"]["supplier_quality"] = []
            result["data"]["trace_coverage"] = None
            result["data"]["data_available"] = {
                "iqc": bool(result["data"].get("iqc_sample_count")),
                "supplier_quality": False,
                "trace_coverage": False,
            }
            result["data"]["note"] = (
                "桥接库不含供应商与批次链路字段，供应商质量与追溯覆盖率不计算；"
                "来料合格率仅基于已取样的检验记录。"
            )

            result["success"] = True
            logger.info(f"物料追溯查询完成")

        except Exception as e:
            logger.error(f"物料追溯查询失败: {e}")
            result["error"] = str(e)

        return result

    # ═══════════════════════════════════════════════════════════════
    # HTML报告生成方法
    # ═══════════════════════════════════════════════════════════════

    def generate_html_report(self, part_number: str, date_range: str = None,
                            wo_data: Dict = None, mrb_data: Dict = None,
                            mes_data: Dict = None, material_data: Dict = None) -> str:
        """
        生成包含真实数据的HTML追溯报告

        参数:
            part_number: 料号
            date_range: 日期范围描述
            wo_data: 工单数据
            mrb_data: MRB分析数据
            mes_data: MES数据
            material_data: 物料追溯数据

        返回:
            HTML内容字符串
        """
        from datetime import datetime

        gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 提取概览数据
        total_wo = wo_data.get('total_wo', 0) if wo_data else 0
        total_input = wo_data.get('total_input_pcs', 0) if wo_data else 0
        total_reject = wo_data.get('total_reject_pcs', 0) if wo_data else 0
        scrap_rate = wo_data.get('scrap_rate', 0) if wo_data else 0
        mrb_reject = mrb_data.get('total_reject_pcs', 0) if mrb_data else 0
        inspect_pcs = mrb_data.get('total_inspect_pcs', 0) if mrb_data else 0

        # 工单列表
        wo_list = wo_data.get('work_orders', [])[:50] if wo_data else []
        wo_table = self._generate_wo_table_html(wo_list)

        # MRB数据 - 使用 defect_top10 字段
        mrb_list = mrb_data.get('defect_top10', [])[:10] if mrb_data else []
        mrb_table = self._generate_mrb_table_html(mrb_list, mrb_data)
        mrb_pie = self._generate_mrb_pie_data(mrb_list)

        # MES数据
        mes_list = mes_data.get('process_yield', []) if mes_data else []
        mes_table = self._generate_mes_table_html(mes_list)
        mes_chart = self._generate_mes_chart_data(mes_list)

        # 物料数据
        material_list = material_data.get('supplier_quality', []) if material_data else []
        material_table = self._generate_material_table_html(material_list)

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>产品追溯报告 - {part_number}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Microsoft YaHei', Arial, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; }}
        .header {{ background: linear-gradient(135deg, #161b22 0%, #1a2332 100%); padding: 20px 30px; border-bottom: 1px solid #30363d; }}
        .header h1 {{ color: #58a6ff; font-size: 24px; margin-bottom: 5px; }}
        .header p {{ color: #8b949e; font-size: 13px; }}
        .container {{ max-width: 1600px; margin: 0 auto; padding: 20px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; margin-bottom: 20px; overflow: hidden; }}
        .card-header {{ background: #1c2128; padding: 15px 20px; border-bottom: 1px solid #30363d; }}
        .card-header h2 {{ color: #f0f6fc; font-size: 16px; }}
        .card-body {{ padding: 20px; }}
        .stats-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .stat-card {{ background: linear-gradient(135deg, #1c2128, #161b22); border: 1px solid #30363d; border-radius: 10px; padding: 20px; text-align: center; }}
        .stat-card .icon {{ font-size: 28px; margin-bottom: 10px; }}
        .stat-card .value {{ font-size: 28px; font-weight: 700; color: #f0f6fc; margin-bottom: 5px; }}
        .stat-card .label {{ color: #8b949e; font-size: 13px; }}
        .tabs {{ display: flex; gap: 5px; margin-bottom: 15px; flex-wrap: wrap; }}
        .tab {{ background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 10px 20px; color: #8b949e; cursor: pointer; font-size: 14px; }}
        .tab:hover {{ background: #30363d; color: #c9d1d9; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #30363d; }}
        th {{ background: #1c2128; color: #8b949e; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
        td {{ color: #c9d1d9; font-size: 14px; }}
        tr:hover {{ background: #1c2128; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
        .badge-success {{ background: rgba(63, 185, 80, 0.2); color: #3fb950; }}
        .badge-danger {{ background: rgba(248, 81, 73, 0.2); color: #f85149; }}
        .badge-warning {{ background: rgba(210, 153, 34, 0.2); color: #d29922; }}
        .badge-info {{ background: rgba(88, 166, 255, 0.2); color: #58a6ff; }}
        .highlight {{ color: #58a6ff; }}
        .section-title {{ color: #f0f6fc; font-size: 14px; margin: 20px 0 15px; padding-bottom: 10px; border-bottom: 1px solid #30363d; }}
        .chart-container {{ background: #1c2128; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin: 15px 0; }}
        .chart-title {{ color: #f0f6fc; font-size: 14px; margin-bottom: 15px; }}
        .chart-svg {{ width: 100%; height: 250px; }}
        .footer {{ text-align: center; padding: 20px; color: #8b949e; font-size: 12px; border-top: 1px solid #30363d; margin-top: 40px; }}
        .info {{ color: #8b949e; font-size: 12px; margin-top: 5px; }}
        @media print {{ body {{ background: #fff; color: #000; }} .card {{ border: 1px solid #ccc; }} }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📦 产品追溯报告</h1>
        <p>料号: <span class="highlight">{part_number}</span> | 日期范围: {date_range or '全部'} | 生成时间: {gen_time}</p>
    </div>

    <div class="container">
        <!-- 概览卡片 -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="icon">📋</div>
                <div class="value">{total_wo:,}</div>
                <div class="label">工单数量</div>
            </div>
            <div class="stat-card">
                <div class="icon">📥</div>
                <div class="value">{total_input:,}</div>
                <div class="label">投入PCS</div>
            </div>
            <div class="stat-card">
                <div class="icon">❌</div>
                <div class="value">{total_reject:,}</div>
                <div class="label">报废PCS</div>
            </div>
            <div class="stat-card">
                <div class="icon">⚠️</div>
                <div class="value">{scrap_rate:.2f}%</div>
                <div class="label">报废率</div>
            </div>
            <div class="stat-card">
                <div class="icon">📦</div>
                <div class="value">{mrb_reject:,}</div>
                <div class="label">MRB报废</div>
            </div>
            <div class="stat-card">
                <div class="icon">🔍</div>
                <div class="value">{inspect_pcs:,}</div>
                <div class="label">检验PCS</div>
            </div>
        </div>

        <!-- Tab导航 -->
        <div class="card">
            <div class="card-header">
                <h2>📈 详细数据</h2>
            </div>
            <div class="card-body">
                <div class="tabs">
                    <div class="tab" onclick="switchTab('workOrders')">工单明细</div>
                    <div class="tab" onclick="switchTab('mrb')">MRB分析</div>
                    <div class="tab" onclick="switchTab('mes')">MES数据</div>
                    <div class="tab" onclick="switchTab('material')">物料追溯</div>
                </div>

                <!-- 工单明细 -->
                <div class="tab-content" id="tab-workOrders">
                    <div class="section-title">工单列表 (按投入PCS降序)</div>
                    {wo_table}
                </div>

                <!-- MRB分析 -->
                <div class="tab-content" id="tab-mrb">
                    <div class="section-title">缺陷TOP10 (按报废数量降序)</div>
                    {mrb_table}
                    <div class="chart-container">
                        <div class="chart-title">报废分布 - 缺陷类型占比</div>
                        <svg id="mrbPieChart" class="chart-svg"></svg>
                    </div>
                </div>

                <!-- MES数据 -->
                <div class="tab-content" id="tab-mes">
                    <div class="section-title">工序良率</div>
                    {mes_table}
                </div>

                <!-- 物料追溯 -->
                <div class="tab-content" id="tab-material">
                    <div class="section-title">供应商质量排名</div>
                    {material_table}
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        智能品质分析系统 v2.0 | 数据来源: ERP(MRP/工单) + MES(检验/追溯) | {gen_time}
    </div>

    <script>
        // 初始化工单表格
        document.querySelectorAll('.tab').forEach(tab => {{
            if (tab.textContent === '工单明细') tab.classList.add('active');
        }});
        document.getElementById('tab-workOrders').classList.add('active');

        function switchTab(tabName) {{
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => {{
                if (t.textContent === tabName || t.getAttribute('onclick')?.includes(tabName)) t.classList.add('active');
            }});
            document.getElementById('tab-' + tabName).classList.add('active');
        }}

        // 渲染MRB饼图
        const mrbPieData = {mrb_pie};
        if (mrbPieData.length) renderPieChart('mrbPieChart', mrbPieData);

        function renderPieChart(svgId, data) {{
            const svg = document.getElementById(svgId);
            if (!svg) return;
            const width = svg.clientWidth || 400;
            const height = 220;
            const centerX = width / 2;
            const centerY = height / 2;
            const radius = Math.min(width, height) / 2 - 30;
            const total = data.reduce((s, d) => s + d.value, 0);
            const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#d2a8ff'];
            let paths = '', startAngle = 0;
            data.slice(0, 6).forEach((d, i) => {{
                const angle = (d.value / total) * 360;
                const endAngle = startAngle + angle;
                const x1 = centerX + radius * Math.cos((startAngle - 90) * Math.PI / 180);
                const y1 = centerY + radius * Math.sin((startAngle - 90) * Math.PI / 180);
                const x2 = centerX + radius * Math.cos((endAngle - 90) * Math.PI / 180);
                const y2 = centerY + radius * Math.sin((endAngle - 90) * Math.PI / 180);
                const largeArc = angle > 180 ? 1 : 0;
                const path = `M ${{centerX}} ${{centerY}} L ${{x1.toFixed(1)}} ${{y1.toFixed(1)}} A ${{radius}} ${{radius}} 0 ${{largeArc}} 1 ${{x2.toFixed(1)}} ${{y2.toFixed(1)}} Z`;
                paths += `<path d="${{path}}" fill="${{colors[i]}}" stroke="#161b22" stroke-width="2"/>`;
                startAngle = endAngle;
            }});
            let legend = '';
            data.slice(0, 6).forEach((d, i) => {{
                const y = 30 + i * 22;
                legend += `<rect x="${{centerX + radius + 20}}" y="${{y - 10}}" width="12" height="12" fill="${{colors[i]}}"/>`;
                legend += `<text x="${{centerX + radius + 38}}" y="${{y}}" fill="#c9d1d9" font-size="11">${{d.label}} (${{d.value}})</text>`;
            }});
            svg.innerHTML = paths + legend;
        }}

        // 初始化第一个tab
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelector('.tab').click();
        }});
    </script>
</body>
</html>'''
        return html

    def _generate_wo_table_html(self, wo_list: list) -> str:
        """生成工单表格HTML"""
        if not wo_list:
            return '<p style="color:#8b949e;padding:20px;">暂无工单数据</p>'
        headers = ["工单号", "投入PCS", "产出PCS", "报废PCS", "报废率", "状态"]
        html = f'<table><thead><tr>{"".join([f"<th>{h}</th>" for h in headers])}</tr></thead><tbody>'
        for wo in wo_list:
            rate = wo.get('scrap_rate', 0)
            rate_class = 'badge-danger' if rate > 3 else 'badge-warning' if rate > 1 else 'badge-success'
            status = '已检验' if wo.get('reject_pcs', 0) > 0 else '生产中'
            html += f'''<tr>
                <td><span class="highlight">{wo.get('wo_number', '')}</span></td>
                <td>{wo.get('input_pcs', 0):,}</td>
                <td>{wo.get('output_pcs', 0):,}</td>
                <td>{wo.get('reject_pcs', 0):,}</td>
                <td><span class="badge {rate_class}">{rate:.2f}%</span></td>
                <td>{status}</td>
            </tr>'''
        html += '</tbody></table>'
        html += f'<p class="info">共 {len(wo_list)} 个工单</p>'
        return html

    def _generate_mrb_table_html(self, mrb_list: list, mrb_data: dict = None) -> str:
        """生成MRB表格HTML - 使用正确的字段名"""
        if not mrb_list:
            # 无明细数据时显示汇总
            total_input = mrb_data.get('total_input_pcs', 0) if mrb_data else 0
            total_reject = mrb_data.get('total_reject_pcs', 0) if mrb_data else 0
            scrap_rate = mrb_data.get('scrap_rate', 0) if mrb_data else 0
            return f'''<p style="color:#8b949e;padding:20px;">汇总数据</p>
            <table><thead><tr><th>料号</th><th>投入PCS</th><th>报废PCS</th><th>报废率</th></tr></thead><tbody>
            <tr><td><span class="highlight">{mrb_data.get('part_number', '汇总') if mrb_data else '汇总'}</span></td>
            <td>{total_input:,}</td><td>{total_reject:,}</td>
            <td><span class="badge badge-warning">{scrap_rate:.2f}%</span></td></tr>
            </tbody></table>'''
        
        headers = ["料号", "报废数量", "报废率"]
        html = f'<table><thead><tr>{"".join([f"<th>{h}</th>" for h in headers])}</tr></thead><tbody>'
        for mrb in mrb_list:
            rate = float(mrb.get('ScrapRate', mrb.get('scrap_rate', 0)))
            rate_class = 'badge-danger' if rate > 10 else 'badge-warning' if rate > 3 else 'badge-success'
            html += f'''<tr>
                <td><span class="highlight">{mrb.get('PartNumber', mrb.get('part_number', ''))}</span></td>
                <td>{mrb.get('TotalScrap', mrb.get('total_scrap', 0)):,}</td>
                <td><span class="badge {rate_class}">{rate:.2f}%</span></td>
            </tr>'''
        html += '</tbody></table>'
        html += f'<p class="info">共 {len(mrb_list)} 条记录</p>'
        return html

    def _generate_mrb_pie_data(self, mrb_list: list) -> str:
        """生成MRB饼图数据JS数组"""
        if not mrb_list:
            return '[{"label":"无数据","value":1}]'
        data = [{'label': m.get('PartNumber', m.get('part_number', '')), 
                 'value': m.get('TotalScrap', m.get('total_scrap', 0))} for m in mrb_list[:6]]
        return str(data).replace("'", "")

    def _generate_mes_table_html(self, mes_list: list) -> str:
        """生成MES表格HTML - 使用正确的字段名"""
        if not mes_list:
            return '<p style="color:#8b949e;padding:20px;">暂无MES数据</p>'
        headers = ["工序ID", "检验数量", "合格数量", "合格率"]
        html = f'<table><thead><tr>{"".join([f"<th>{h}</th>" for h in headers])}</tr></thead><tbody>'
        for mes in mes_list:
            rate = float(mes.get('PassRate', mes.get('pass_rate', 0)))
            rate_class = 'badge-success' if rate > 95 else 'badge-warning' if rate > 90 else 'badge-danger'
            process_id = str(mes.get('ProcessID', mes.get('process_id', '')))[-6:]
            html += f'''<tr>
                <td><span class="highlight">工序{process_id}</span></td>
                <td>{mes.get('InspectCount', mes.get('inspect_count', 0)):,}</td>
                <td>{mes.get('PassCount', mes.get('pass_count', 0)):,}</td>
                <td><span class="badge {rate_class}">{rate:.2f}%</span></td>
            </tr>'''
        html += '</tbody></table>'
        return html

    def _generate_mes_chart_data(self, mes_list: list) -> str:
        """生成MES图表数据"""
        if not mes_list:
            return '[]'
        return str([{'process': str(m.get('ProcessID', m.get('process_id', '')))[-4:], 
                     'rate': float(m.get('PassRate', m.get('pass_rate', 0)))} for m in mes_list])

    def _generate_material_table_html(self, material_list: list) -> str:
        """生成物料追溯表格HTML - 使用正确的字段名"""
        if not material_list:
            return '<p style="color:#8b949e;padding:20px;">暂无物料数据</p>'
        headers = ["供应商", "编码", "来料总数", "合格数", "合格率", "质量等级"]
        html = f'<table><thead><tr>{"".join([f"<th>{h}</th>" for h in headers])}</tr></thead><tbody>'
        for mat in material_list:
            rate = float(mat.get('rate', mat.get('pass_rate', 0)))
            rate_class = 'badge-success' if rate > 95 else 'badge-warning' if rate > 90 else 'badge-danger'
            level = 'A' if rate >= 95 else 'B' if rate >= 90 else 'C'
            html += f'''<tr>
                <td><span class="highlight">{mat.get('supplier', '')}</span></td>
                <td>{mat.get('code', '')}</td>
                <td>{mat.get('total', 0):,}</td>
                <td>{mat.get('pass', 0):,}</td>
                <td><span class="badge {rate_class}">{rate:.1f}%</span></td>
                <td><span class="badge badge-info">{level}</span></td>
            </tr>'''
        html += '</tbody></table>'
        return html


if __name__ == "__main__":
    # 测试代码
    core = QualityIntelligenceCore()
    
    # 测试仪表板
    dashboard = core.get_dashboard_data()
    print(json.dumps(dashboard, indent=2, ensure_ascii=False, default=str))
