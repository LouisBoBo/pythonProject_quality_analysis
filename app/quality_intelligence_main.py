# -*- coding: utf-8 -*-
"""
智能品质分析系统 - 主程序入口
===================================
集成追溯分析、8D报告、品质预警、自我学习功能
支持命令行和Web界面模式

作者: WorkBuddy AI
日期: 2026-04-13
版本: 1.0.0
"""

import os
import sys
import json
import time
import logging
import argparse
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import asdict

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────
# 确保项目根目录在 sys.path 中
_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

try:
    from config.settings import LOG_FILE
    _log_path = str(LOG_FILE)
except Exception:
    _log_path = str(_PROJECT_ROOT / "logs" / "quality_system.log")
    Path(_log_path).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QualitySystem] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_log_path, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("QualitySystem")


# ═══════════════════════════════════════════════════════════════
# 数据连接器导入
# ═══════════════════════════════════════════════════════════════

def import_connectors():
    """导入ERP和MES连接器（统一走 app.connectors.loader）"""
    from app.connectors.loader import import_connectors as _load
    return _load()


# ═══════════════════════════════════════════════════════════════
# 智能品质分析系统主类
# ═══════════════════════════════════════════════════════════════

class QualityIntelligenceSystem:
    """
    智能品质分析系统主类
    整合追溯分析、8D报告、品质预警、自我学习功能
    """

    def __init__(self):
        # 导入核心模块
        try:
            from app.quality_intelligence_core import (
                QualityIntelligenceCore, KnowledgeBase
            )
        except ImportError:
            from quality_intelligence_core import (
                QualityIntelligenceCore, KnowledgeBase
            )

        self.core_class = QualityIntelligenceCore
        self.kb_class = KnowledgeBase
        self.core: Optional[QualityIntelligenceCore] = None
        self.erp = None
        self.mes = None

        # 状态
        self.connected = False
        self.connection_status = {"erp": False, "mes": False}

        # 加载核心
        self.kb = KnowledgeBase()

    def connect(
        self,
        erp_db_path: Optional[str] = None,
        mes_db_path: Optional[str] = None,
    ) -> bool:
        """连接ERP和MES数据库。可按项目环境传入各自 SQLite 路径。"""
        get_erp, get_mes = import_connectors()

        if get_erp is None:
            logger.error("无法导入数据连接器")
            return False

        try:
            # 连接ERP（传入路径时重建单例，避免串环境）
            self.erp = get_erp(erp_db_path) if erp_db_path else get_erp()
            if not self.erp.connected:
                result = self.erp.connect()
                if not result["success"]:
                    logger.warning(f"ERP连接失败: {result.get('error')}")
                else:
                    self.connection_status["erp"] = True
                    logger.info(f"ERP连接成功: {getattr(self.erp, 'db_path', '')}")
            else:
                self.connection_status["erp"] = True

            # 连接MES
            self.mes = get_mes(mes_db_path) if mes_db_path else get_mes()
            if not self.mes.connected:
                result = self.mes.connect()
                if not result["success"]:
                    logger.warning(f"MES连接失败: {result.get('error')}")
                else:
                    self.connection_status["mes"] = True
                    logger.info(f"MES连接成功: {getattr(self.mes, 'db_path', '')}")
            else:
                self.connection_status["mes"] = True

            # 创建核心
            self.core = self.core_class(
                erp_connector=self.erp,
                mes_connector=self.mes,
                knowledge_base=self.kb
            )

            self.connected = True
            return True

        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self.erp:
            self.erp.disconnect()
        if self.mes:
            self.mes.disconnect()
        self.connected = False
        self.connection_status = {"erp": False, "mes": False}

    # ──────────────────────────────────────────
    # 追溯分析功能
    # ──────────────────────────────────────────

    def trace_part(self, part_number: str, start_date: str = None,
                  end_date: str = None) -> dict:
        """追溯料号品质"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            record = self.core.trace_part_quality(part_number, start_date, end_date)
            return {
                "success": True,
                "data": asdict(record)
            }
        except Exception as e:
            logger.error(f"追溯分析失败: {e}")
            return {"success": False, "error": str(e)}

    def analyze_defects(self, defect_details: list) -> dict:
        """分析不良类型"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            result = self.core.get_defect_analysis(defect_details)
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def analyze_root_cause(self, part_number: str, defect_details: list) -> dict:
        """根因分析"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            result = self.core.analyze_root_cause(part_number, defect_details)
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ──────────────────────────────────────────
    # 预警功能
    # ──────────────────────────────────────────

    def check_alerts(self, part_numbers: list = None) -> dict:
        """执行预警检查"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            alerts = self.core.check_quality_alerts(part_numbers)
            return {
                "success": True,
                "data": [asdict(a) for a in alerts],
                "count": len(alerts)
            }
        except Exception as e:
            logger.error(f"预警检查失败: {e}")
            return {"success": False, "error": str(e)}

    def get_active_alerts(self) -> dict:
        """获取活跃预警"""
        try:
            alerts = self.kb.get_active_alerts()
            return {
                "success": True,
                "data": [asdict(a) for a in alerts],
                "count": len(alerts)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> dict:
        """确认预警"""
        try:
            self.kb.acknowledge_alert(alert_id, acknowledged_by)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ──────────────────────────────────────────
    # 8D报告功能
    # ──────────────────────────────────────────

    def generate_8d_report(self, part_number: str, created_by: str = "系统用户") -> dict:
        """生成8D报告"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            report = self.core.generate_eight_d_report(part_number, created_by=created_by)
            return {
                "success": True,
                "data": asdict(report),
                "markdown": self.core._export_report_markdown(report)
            }
        except Exception as e:
            logger.error(f"8D报告生成失败: {e}")
            return {"success": False, "error": str(e)}

    def get_8d_reports(self, status: str = None) -> dict:
        """获取8D报告列表"""
        try:
            reports = self.kb.get_eight_d_reports(status)
            return {
                "success": True,
                "data": [asdict(r) for r in reports],
                "count": len(reports)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ──────────────────────────────────────────
    # 自我学习功能
    # ──────────────────────────────────────────

    def run_self_learning(self, period_days: int = 30) -> dict:
        """执行自我学习"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            result = self.core.self_learning(period_days)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"自我学习失败: {e}")
            return {"success": False, "error": str(e)}

    def get_knowledge_stats(self) -> dict:
        """获取知识库统计"""
        try:
            stats = self.kb.get_knowledge_stats()
            return {"success": True, "data": stats}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ──────────────────────────────────────────
    # 四大追溯模块API
    # ──────────────────────────────────────────

    def trace_work_orders(self, part_number: str = None, start_date: str = None,
                         end_date: str = None) -> dict:
        """精确查询 - 工单列表和汇总"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}
        try:
            result = self.core.trace_work_orders(part_number, start_date, end_date)
            return result
        except Exception as e:
            logger.error(f"工单追溯查询失败: {e}")
            return {"success": False, "error": str(e)}

    def trace_mrb_analysis(self, part_number: str = None, start_date: str = None,
                          end_date: str = None) -> dict:
        """MRB分析 - 报废缺陷/责任工序/报废率"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}
        try:
            result = self.core.trace_mrb_analysis(part_number, start_date, end_date)
            return result
        except Exception as e:
            logger.error(f"MRB分析失败: {e}")
            return {"success": False, "error": str(e)}

    def trace_mes_data(self, part_number: str = None, start_date: str = None,
                      end_date: str = None) -> dict:
        """MES数据 - 工序良率/设备状态/药水异常"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}
        try:
            result = self.core.trace_mes_data(part_number, start_date, end_date)
            return result
        except Exception as e:
            logger.error(f"MES数据查询失败: {e}")
            return {"success": False, "error": str(e)}

    def trace_material(self, part_number: str = None, start_date: str = None,
                      end_date: str = None) -> dict:
        """物料追溯 - 原料批次/供应商质量"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}
        try:
            result = self.core.trace_material(part_number, start_date, end_date)
            return result
        except Exception as e:
            logger.error(f"物料追溯失败: {e}")
            return {"success": False, "error": str(e)}

    def generate_html_report(self, part_number: str, date_range: str = None,
                            wo_data: dict = None, mrb_data: dict = None,
                            mes_data: dict = None, material_data: dict = None) -> dict:
        """生成HTML追溯报告（包含真实数据）"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}
        try:
            html_content = self.core.generate_html_report(
                part_number, date_range, wo_data, mrb_data, mes_data, material_data
            )
            return {"success": True, "data": {"html": html_content}}
        except Exception as e:
            logger.error(f"生成HTML报告失败: {e}")
            return {"success": False, "error": str(e)}

    # ──────────────────────────────────────────
    # 仪表板数据
    # ──────────────────────────────────────────

    def get_dashboard_data(self) -> dict:
        """获取仪表板概览数据"""
        if not self.core:
            return {"success": False, "error": "系统未连接"}

        try:
            # 获取预警统计
            alerts = self.kb.get_active_alerts()
            critical_count = sum(1 for a in alerts if a.alert_type == "critical")
            warning_count = sum(1 for a in alerts if a.alert_type == "warning")

            # 获取知识库统计
            kb_stats = self.kb.get_knowledge_stats()

            return {
                "success": True,
                "data": {
                    "connection_status": self.connection_status,
                    "alerts": {
                        "total": len(alerts),
                        "critical": critical_count,
                        "warning": warning_count,
                        "recent": [asdict(a) for a in alerts[:5]]
                    },
                    "knowledge_base": kb_stats,
                    "system_status": "运行中" if self.connected else "未连接"
                }
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 命令行界面
# ═══════════════════════════════════════════════════════════════

class CLIInterface:
    """命令行界面"""

    def __init__(self):
        self.system = QualityIntelligenceSystem()

    def run(self):
        """运行命令行界面"""
        parser = argparse.ArgumentParser(
            description="智能品质分析系统",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
示例:
  python quality_intelligence_main.py --connect          # 连接数据库
  python quality_intelligence_main.py --trace B60P04     # 追溯料号
  python quality_intelligence_main.py --alert             # 预警检查
  python quality_intelligence_main.py --8d B60P04         # 生成8D报告
  python quality_intelligence_main.py --learn              # 自我学习
            """
        )

        parser.add_argument("--connect", action="store_true", help="连接数据库")
        parser.add_argument("--disconnect", action="store_true", help="断开连接")
        parser.add_argument("--trace", metavar="料号", help="追溯料号品质")
        parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
        parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
        parser.add_argument("--alert", action="store_true", help="执行预警检查")
        parser.add_argument("--list-alerts", action="store_true", help="查看预警列表")
        parser.add_argument("--8d", dest="eight_d", metavar="料号", help="生成8D报告")
        parser.add_argument("--list-8d", action="store_true", help="查看8D报告列表")
        parser.add_argument("--learn", action="store_true", help="执行自我学习")
        parser.add_argument("--stats", action="store_true", help="查看知识库统计")
        parser.add_argument("--dashboard", action="store_true", help="查看仪表板")
        parser.add_argument("--gui", action="store_true", help="启动GUI界面")
        parser.add_argument("--output", "-o", help="输出文件路径")

        args = parser.parse_args()

        # GUI模式
        if args.gui:
            self.start_gui()
            return

        # 连接数据库
        if args.connect:
            self.cmd_connect()

        # 断开连接
        if args.disconnect:
            self.cmd_disconnect()

        # 追溯分析
        if args.trace:
            self.cmd_trace(args.trace, args.start, args.end, args.output)

        # 预警检查
        if args.alert:
            self.cmd_alert()

        # 预警列表
        if args.list_alerts:
            self.cmd_list_alerts()

        # 8D报告
        if args.eight_d:
            self.cmd_8d(args.eight_d, args.output)

        # 8D列表
        if args.list_8d:
            self.cmd_list_8d()

        # 自我学习
        if args.learn:
            self.cmd_learn()

        # 知识库统计
        if args.stats:
            self.cmd_stats()

        # 仪表板
        if args.dashboard:
            self.cmd_dashboard()

        # 默认显示帮助
        if not any([args.connect, args.disconnect, args.trace, args.alert,
                   args.list_alerts, args.eight_d, args.list_8d, args.learn,
                   args.stats, args.dashboard, args.gui]):
            parser.print_help()

    def cmd_connect(self):
        """连接数据库"""
        print("\n" + "="*60)
        print("  连接数据库")
        print("="*60)

        if self.system.connect():
            status = self.system.connection_status
            print(f"\n连接成功")
            print(f"  ERP: {'已连接' if status['erp'] else '未连接'}")
            print(f"  MES: {'已连接' if status['mes'] else '未连接'}")
        else:
            print("\n连接失败，请检查配置")

    def cmd_disconnect(self):
        """断开连接"""
        self.system.disconnect()
        print("已断开连接")

    def cmd_trace(self, part_number: str, start_date: str, end_date: str, output: str):
        """追溯料号"""
        print(f"\n{'='*60}")
        print(f"  追溯分析: {part_number}")
        print("="*60)

        if not self.system.connected:
            if not self.system.connect():
                print("连接失败")
                return

        result = self.system.trace_part(part_number, start_date, end_date)

        if result["success"]:
            data = result["data"]
            print(f"\n品质统计:")
            print(f"  总检验: {data['total_inspect']} PCS")
            print(f"  合格数: {data['pass_count']} PCS")
            print(f"  不良数: {data['ng_count']} PCS")
            print(f"  不良率: {data['ng_rate']}%")
            print(f"  报废数: {data['scrap_pcs']} PCS")
            print(f"  返修数: {data['rework_pcs']} PCS")

            if data.get("process_stats"):
                print(f"\n工序统计:")
                for ps in data["process_stats"][:5]:
                    ng_rate = ps.get("NgRate") or 0
                    print(f"  工序{ps.get('ProcessID')}: 检验{ps.get('TotalInspect')} 不良{ps.get('NgCount')} ({ng_rate}%)")

            if data.get("defect_details"):
                defects = self.system.analyze_defects(data["defect_details"])
                if defects["success"]:
                    top_defects = defects["data"].get("top_defects", [])
                    if top_defects:
                        print(f"\nTOP5不良类型:")
                        for i, d in enumerate(top_defects[:5], 1):
                            print(f"  {i}. {d['defect_type']}: {d['count']} PCS ({d['percentage']}%)")

            # 根因分析
            root_cause = self.system.analyze_root_cause(part_number, data.get("defect_details", []))
            if root_cause["success"]:
                rc_data = root_cause["data"]
                if rc_data.get("root_causes"):
                    print(f"\n可能根因:")
                    for cause in rc_data["root_causes"][:3]:
                        print(f"  - {cause}")
                if rc_data.get("suggested_solutions"):
                    print(f"\n建议方案:")
                    for sol in rc_data["suggested_solutions"][:3]:
                        print(f"  - {sol}")

            # 保存结果
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"\n报告已保存: {output}")
        else:
            print(f"追溯失败: {result.get('error')}")

    def cmd_alert(self):
        """预警检查"""
        print(f"\n{'='*60}")
        print(f"  品质预警检查")
        print("="*60)

        if not self.system.connected:
            if not self.system.connect():
                print("连接失败")
                return

        result = self.system.check_alerts()

        if result["success"]:
            alerts = result["data"]
            print(f"\n发现 {len(alerts)} 个预警:")
            for alert in alerts:
                icon = "[CRITICAL]" if alert["alert_type"] == "critical" else "[WARNING]"
                print(f"\n{icon} {alert['part_number']}")
                print(f"  {alert['alert_message']}")
                print(f"  建议: {alert['recommendation']}")
        else:
            print(f"预警检查失败: {result.get('error')}")

    def cmd_list_alerts(self):
        """预警列表"""
        print(f"\n{'='*60}")
        print(f"  当前预警列表")
        print("="*60)

        result = self.system.get_active_alerts()

        if result["success"]:
            alerts = result["data"]
            if alerts:
                print(f"\n共 {len(alerts)} 个未解决预警:")
                for alert in alerts:
                    ack = "[已确认]" if alert["acknowledged"] else "[未确认]"
                    icon = "[CRITICAL]" if alert["alert_type"] == "critical" else "[WARNING]"
                    print(f"\n{icon}{ack} {alert['part_number']}")
                    print(f"  {alert['alert_message']}")
            else:
                print("暂无未解决预警")
        else:
            print(f"获取预警失败: {result.get('error')}")

    def cmd_8d(self, part_number: str, output: str):
        """生成8D报告"""
        print(f"\n{'='*60}")
        print(f"  8D改善报告生成")
        print("="*60)

        if not self.system.connected:
            if not self.system.connect():
                print("连接失败")
                return

        result = self.system.generate_8d_report(part_number, "系统用户")

        if result["success"]:
            report = result["data"]
            print(f"\n报告编号: {report['report_id']}")
            print(f"状态: {report['status']}")
            print(f"\n{report['d0_symptoms']}")

            if output:
                output_file = output
            else:
                output_file = f"{report['report_id']}.md"

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result["markdown"])
            print(f"\n8D报告已保存: {output_file}")
        else:
            print(f"8D报告生成失败: {result.get('error')}")

    def cmd_list_8d(self):
        """8D报告列表"""
        print(f"\n{'='*60}")
        print(f"  8D报告列表")
        print("="*60)

        result = self.system.get_8d_reports()

        if result["success"]:
            reports = result["data"]
            if reports:
                print(f"\n共 {len(reports)} 份8D报告:")
                for r in reports:
                    status_icon = "[草稿]" if r["status"] == "draft" else "[完成]"
                    print(f"  {status_icon} {r['report_id']} - {r['created_time'][:10]}")
            else:
                print("暂无8D报告")
        else:
            print(f"获取报告失败: {result.get('error')}")

    def cmd_learn(self):
        """自我学习"""
        print(f"\n{'='*60}")
        print(f"  自我学习进化")
        print("="*60)

        if not self.system.connected:
            if not self.system.connect():
                print("连接失败")
                return

        result = self.system.run_self_learning()

        if result["success"]:
            data = result["data"]
            print(f"\n学习完成!")
            print(f"  学到的模式: {data.get('patterns_learned', 0)}")
            print(f"\n知识库统计:")
            for k, v in data.get('knowledge_stats', {}).items():
                print(f"  {k}: {v}")
            if data.get('recommendations'):
                print(f"\n建议:")
                for rec in data['recommendations']:
                    print(f"  - {rec}")
        else:
            print(f"自我学习失败: {result.get('error')}")

    def cmd_stats(self):
        """知识库统计"""
        print(f"\n{'='*60}")
        print(f"  知识库统计")
        print("="*60)

        result = self.system.get_knowledge_stats()

        if result["success"]:
            stats = result["data"]
            print("\n知识库统计:")
            table_names = {
                "defect_patterns": "不良模式",
                "alert_rules": "预警规则",
                "quality_alerts": "品质预警",
                "eight_d_reports": "8D报告",
                "improvement_cases": "改善案例",
                "learning_log": "学习记录"
            }
            for k, v in stats.items():
                name = table_names.get(k, k)
                print(f"  {name}: {v}")
        else:
            print(f"获取统计失败: {result.get('error')}")

    def cmd_dashboard(self):
        """仪表板"""
        print(f"\n{'='*60}")
        print(f"  系统仪表板")
        print("="*60)

        if not self.system.connected:
            if not self.system.connect():
                print("连接失败")
                return

        result = self.system.get_dashboard_data()

        if result["success"]:
            data = result["data"]
            status = data["connection_status"]
            alerts = data["alerts"]

            print(f"\n连接状态:")
            print(f"  ERP: {'OK' if status['erp'] else 'FAIL'}")
            print(f"  MES: {'OK' if status['mes'] else 'FAIL'}")

            print(f"\n预警概览:")
            print(f"  总计: {alerts['total']}")
            print(f"  紧急: {alerts['critical']}")
            print(f"  警告: {alerts['warning']}")

            print(f"\n知识库:")
            kb = data.get("knowledge_base", {})
            print(f"  不良模式: {kb.get('defect_patterns', 0)}")
            print(f"  8D报告: {kb.get('eight_d_reports', 0)}")
            print(f"  改善案例: {kb.get('improvement_cases', 0)}")

            print(f"\n系统状态: {data.get('system_status', 'N/A')}")
        else:
            print(f"获取仪表板数据失败: {result.get('error')}")

    def start_gui(self):
        """启动GUI界面"""
        try:
            try:
                from app.quality_intelligence_gui import start_gui
            except ImportError:
                from quality_intelligence_gui import start_gui
            start_gui(self.system)
        except ImportError:
            print("无法导入GUI模块")
            print("请确保 quality_intelligence_gui.py 文件存在")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    """主入口"""
    cli = CLIInterface()
    cli.run()


if __name__ == "__main__":
    main()
