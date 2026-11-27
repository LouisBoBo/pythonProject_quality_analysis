# -*- coding: utf-8 -*-
"""
品质分析系统 - HTTP API 服务
为HTML版本提供数据查询接口
"""

import sys
import os
import json
import hmac
import logging
import decimal
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

# 自定义JSON编码器，处理Decimal类型
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        return super().default(obj)

# 添加项目路径
_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_APP_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [API] %(levelname)s %(message)s"
)
logger = logging.getLogger("QualityAPI")


def _required_token() -> str:
    """复用主服务的 QA_API_TOKEN；未配置时为空（仅本机监听）。"""
    try:
        from config.settings import API_TOKEN
        return (API_TOKEN or "").strip()
    except Exception:
        return ""


class QualityAPIHandler(BaseHTTPRequestHandler):
    """HTTP API 处理器"""
    
    system = None

    def _authorized(self) -> bool:
        """配置了 QA_API_TOKEN 时，须在请求头提供 X-API-Token / Bearer（禁用 URL 令牌）。"""
        token = _required_token()
        if not token:
            return True
        supplied = (self.headers.get('X-API-Token') or '').strip()
        if not supplied:
            auth = (self.headers.get('Authorization') or '').strip()
            if auth.lower().startswith('bearer '):
                supplied = auth[7:].strip()
        return bool(supplied) and hmac.compare_digest(supplied, token)
    
    def do_GET(self):
        """处理GET请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        try:
            if path != '/api/health' and not self._authorized():
                self._send_error(401, "未授权：需提供 X-API-Token 或 Authorization: Bearer")
                return
            if path == '/api/health':
                self._handle_health()
            elif path == '/api/trace':
                self._handle_trace(params)
            elif path == '/api/wo':
                self._handle_work_orders(params)
            elif path == '/api/mrb':
                self._handle_mrb(params)
            elif path == '/api/mes':
                self._handle_mes(params)
            elif path == '/api/material':
                self._handle_material(params)
            else:
                self._send_error(404, "API路径不存在")
        except Exception as e:
            logger.error(f"API处理错误: {e}")
            self._send_error(500, str(e))
    
    def do_OPTIONS(self):
        """处理CORS预检请求"""
        self.send_response(200)
        self.end_headers()
    
    def _handle_health(self):
        """健康检查"""
        connected = False
        if self.system and self.system.erp and self.system.erp.connected:
            connected = True
        self._send_json({"success": True, "connected": connected, "message": "服务正常" if connected else "ERP未连接"})
    
    def _handle_trace(self, params):
        """一键追溯 - 获取所有数据"""
        part_number = params.get('part', [''])[0]
        start_date = params.get('start', [None])[0]
        end_date = params.get('end', [None])[0]
        
        if not part_number:
            self._send_error(400, "缺少part参数")
            return
        
        logger.info(f"一键追溯: {part_number}")
        
        # 调用系统各模块
        wo_result = self.system.trace_work_orders(part_number, start_date, end_date)
        mrb_result = self.system.trace_mrb_analysis(part_number, start_date, end_date)
        mes_result = self.system.trace_mes_data(part_number, start_date, end_date)
        mat_result = self.system.trace_material(part_number, start_date, end_date)
        
        # 组装数据
        wo_data = wo_result.get('data', {})
        mrb_data = mrb_result.get('data', {})
        mes_data = mes_result.get('data', {})
        mat_data = mat_result.get('data', {})
        
        # 概览数据
        overview = {
            "total_wo": wo_data.get('total_wo', 0),
            "total_input": wo_data.get('total_input_pcs', 0),
            "total_reject": wo_data.get('total_reject_pcs', 0),
            "reject_rate": wo_data.get('reject_rate', 0),
            "mrb_reject": mrb_data.get('total_reject_pcs', 0),
            "inspect_pcs": mrb_data.get('total_inspect_pcs', 0)
        }
        
        self._send_json({
            "success": True,
            "part_number": part_number,
            "overview": overview,
            "work_orders": wo_data.get('work_orders', [])[:50],
            "mrb": {
                "defect_top10": mrb_data.get('defect_top10', []),
                "scrap_rate": mrb_data.get('scrap_rate', 0),
                "total_input_pcs": mrb_data.get('total_input_pcs', 0),
                "total_reject_pcs": mrb_data.get('total_reject_pcs', 0),
                "monthly_trend": mrb_data.get('monthly_trend', [])
            },
            "mes": {
                "process_yield": mes_data.get('process_yield', []),
                "process_defects": mes_data.get('process_defects', []),
                "total_process": len(mes_data.get('process_yield', []))
            },
            "material": {
                "supplier_quality": mat_data.get('supplier_quality', []),
                "total_supplier": len(mat_data.get('supplier_quality', []))
            }
        })
    
    def _handle_work_orders(self, params):
        """工单追溯"""
        part_number = params.get('part', [''])[0]
        start_date = params.get('start', [None])[0]
        end_date = params.get('end', [None])[0]
        
        result = self.system.trace_work_orders(part_number, start_date, end_date)
        self._send_json(result)
    
    def _handle_mrb(self, params):
        """MRB分析"""
        part_number = params.get('part', [''])[0]
        start_date = params.get('start', [None])[0]
        end_date = params.get('end', [None])[0]
        
        result = self.system.trace_mrb_analysis(part_number, start_date, end_date)
        self._send_json(result)
    
    def _handle_mes(self, params):
        """MES数据"""
        part_number = params.get('part', [''])[0]
        start_date = params.get('start', [None])[0]
        end_date = params.get('end', [None])[0]
        
        result = self.system.trace_mes_data(part_number, start_date, end_date)
        self._send_json(result)
    
    def _handle_material(self, params):
        """物料追溯"""
        part_number = params.get('part', [''])[0]
        start_date = params.get('start', [None])[0]
        end_date = params.get('end', [None])[0]
        
        result = self.system.trace_material(part_number, start_date, end_date)
        self._send_json(result)
    
    def _send_json(self, data):
        """发送JSON响应"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('X-Data-Version', '2026-04-15-0915')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, cls=DecimalEncoder).encode('utf-8'))
    
    def _send_error(self, code, message):
        """发送错误响应"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"success": False, "error": message}, ensure_ascii=False, cls=DecimalEncoder).encode('utf-8'))
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        logger.info(f"{args[0]}")


class QualityAPIServer:
    """HTTP API 服务器"""
    
    def __init__(self, system, port=8765):
        self.system = system
        self.port = port
        self.server = None
        self.thread = None
    
    def start(self):
        """启动服务器"""
        if self.server:
            logger.warning("服务器已在运行")
            return
        
        # 设置处理器中的system引用
        QualityAPIHandler.system = self.system
        
        # 仅本机监听（旧版接口无鉴权，不对外暴露）
        self.server = HTTPServer(('127.0.0.1', self.port), QualityAPIHandler)
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
        if _required_token():
            logger.info("鉴权已启用：请求需带 X-API-Token / Authorization: Bearer")
        else:
            logger.warning("未设置 QA_API_TOKEN：旧版接口无鉴权（仅本机监听）")
        logger.info(f"API服务已启动: http://localhost:{self.port}")
        return f"http://localhost:{self.port}"
    
    def _run_server(self):
        """运行服务器"""
        try:
            self.server.serve_forever()
        except Exception as e:
            logger.error(f"服务器异常: {e}")
    
    def stop(self):
        """停止服务器"""
        if self.server:
            self.server.shutdown()
            self.server = None
            logger.info("API服务已停止")


def main():
    """启动API服务"""
    from app.quality_intelligence_main import QualityIntelligenceSystem
    
    print("=" * 50)
    print("品质分析系统 - HTTP API 服务")
    print("=" * 50)
    
    # 初始化系统
    system = QualityIntelligenceSystem()
    print("正在连接数据库...")
    system.connect()
    
    if not system.erp or not system.erp.connected:
        print("错误: 无法连接ERP数据库!")
        return
    
    print("ERP连接成功!")
    
    # 启动API服务
    server = QualityAPIServer(system, port=8765)
    url = server.start()
    
    print()
    print("API接口说明:")
    print(f"  健康检查: GET {url}/api/health")
    print(f"  一键追溯: GET {url}/api/trace?part=料号&start=开始日期&end=结束日期")
    print(f"  工单追溯: GET {url}/api/wo?part=料号")
    print(f"  MRB分析: GET {url}/api/mrb?part=料号")
    print(f"  MES数据: GET {url}/api/mes?part=料号")
    print(f"  物料追溯: GET {url}/api/material?part=料号")
    print()
    print("按 Ctrl+C 停止服务")
    print("=" * 50)
    
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止服务...")
        server.stop()


if __name__ == "__main__":
    main()
