# -*- coding: utf-8 -*-
"""
GraphRAG 生产环境客户端
======================
对接 pythonProject_GraphRAG 公网/内网 API，拉取 FMEA 知识图谱真实数据。

权威接口（生产）：
  GET /api/graph/overview      工序与缺陷计数
  GET /api/graph/defects       全部缺陷名
  GET /api/graph/by-process    单工序缺陷列表
  GET /api/defect/query        缺陷 → 根因 / 对策
  GET /api/process/query       工序 → 缺陷 / 根因摘要
  GET /api/entity/detail       节点详情与全库统计
  GET /api/analytics/nodes     生产环境查阅频次（有据可依）
  GET /api/analytics/summary   浏览统计汇总

配置：config.settings.GRAPHRAG_BASE_URL（可用环境变量 QA_GRAPHRAG_URL 覆盖）
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PCB_Quality.GraphRAG")

try:
    from config.settings import (
        GRAPHRAG_BASE_URL,
        GRAPHRAG_CACHE_TTL,
        GRAPHRAG_TIMEOUT,
    )
except ImportError:  # pragma: no cover
    GRAPHRAG_BASE_URL = "http://175.178.238.31"
    GRAPHRAG_TIMEOUT = 12.0
    GRAPHRAG_CACHE_TTL = 1800


def _defect_aliases(name: str) -> List[str]:
    try:
        from config.defect_mapping import get_defect_candidates
        return get_defect_candidates(name)
    except Exception:
        return []


def _process_aliases(name: str) -> List[str]:
    try:
        from config.defect_mapping import get_process_candidates
        return get_process_candidates(name)
    except Exception:
        return []


class GraphRAGClient:
    """只读访问 GraphRAG 生产 API；带短时内存缓存。"""

    SOURCE_NAME = "GraphRAG/FMEA"
    EVIDENCE = "Neo4j Entity(Defect)-[:CAUSED_BY]->RootCause-[:HAS_COUNTERMEASURE]->Countermeasure"

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        cache_ttl: Optional[int] = None,
    ):
        self.base_url = (base_url or GRAPHRAG_BASE_URL).rstrip("/")
        self.timeout = float(timeout if timeout is not None else GRAPHRAG_TIMEOUT)
        self.cache_ttl = int(cache_ttl if cache_ttl is not None else GRAPHRAG_CACHE_TTL)
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ── HTTP ─────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None, use_cache: bool = True) -> Any:
        qs = urllib.parse.urlencode(params or {}, doseq=True, safe="")
        url = f"{self.base_url}{path}" + (f"?{qs}" if qs else "")
        cache_key = url
        if use_cache:
            with self._lock:
                hit = self._cache.get(cache_key)
                if hit and (time.time() - hit["ts"]) < self.cache_ttl:
                    return hit["data"]

        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PCB-QA/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            logger.warning("GraphRAG HTTP %s %s: %s", e.code, url, body[:200])
            raise
        except Exception as e:
            logger.warning("GraphRAG 请求失败 %s: %s", url, e)
            raise

        if use_cache:
            with self._lock:
                self._cache[cache_key] = {"ts": time.time(), "data": data}
        return data

    def health(self) -> bool:
        try:
            data = self._get("/health", use_cache=False)
            return (data or {}).get("status") == "ok"
        except Exception:
            return False

    # ── 图谱查询 ─────────────────────────────────────

    def overview(self) -> dict:
        return self._get("/api/graph/overview") or {}

    def list_defects(self) -> List[str]:
        data = self._get("/api/graph/defects") or {}
        return [d for d in (data.get("defects") or []) if d and str(d).strip()]

    def graph_by_process(self, name: str) -> dict:
        return self._get("/api/graph/by-process", {"name": name}) or {}

    def query_defect(self, name: str) -> dict:
        return self._get("/api/defect/query", {"name": name}) or {}

    def query_process(self, name: str) -> dict:
        return self._get("/api/process/query", {"name": name}) or {}

    def entity_detail(self, name: str, entity_type: str) -> dict:
        return self._get("/api/entity/detail", {"name": name, "type": entity_type}) or {}

    def analytics_summary(self) -> dict:
        try:
            return self._get("/api/analytics/summary") or {}
        except Exception:
            return {}

    def analytics_defect_ranking(self, limit: int = 100) -> List[dict]:
        try:
            data = self._get(
                "/api/analytics/nodes",
                {"limit": max(1, min(int(limit), 500)), "entity_type": "Defect"},
            ) or {}
            return list(data.get("nodes") or [])
        except Exception:
            return []

    def root_stats(self) -> dict:
        """全库节点统计（缺陷/根因/对策/工序）。"""
        detail = self.entity_detail("工序", "Root")
        props = (detail or {}).get("properties") or {}
        processes = (detail or {}).get("processes") or []

        def _int(key: str, default: int = 0) -> int:
            try:
                return int(str(props.get(key) or default).strip() or default)
            except Exception:
                return default

        return {
            "defect_total": _int("缺陷总数"),
            "root_cause_total": _int("根因总数"),
            "countermeasure_total": _int("对策总数"),
            "process_total": len(processes) or int((detail or {}).get("degree") or 0),
            "processes": [p.get("name") for p in processes if p.get("name")],
        }

    # ── 业务组装：知识库列表 ─────────────────────────

    def _normalize_pattern(self, defect_payload: dict, drill_count: int = 0) -> Optional[dict]:
        if not defect_payload or defect_payload.get("error"):
            return None
        defect = defect_payload.get("defect")
        if not defect:
            return None

        root_details = []
        for rc in defect_payload.get("root_causes") or []:
            cause = (rc.get("cause") or "").strip()
            if not cause:
                continue
            root_details.append({
                "cause": cause,
                "category": (rc.get("category") or "").strip(),
            })

        cm_details = []
        solutions = []
        for cm in defect_payload.get("countermeasures") or []:
            measure = (cm.get("measure") or "").strip()
            if not measure or measure.isdigit():
                continue
            standard = (cm.get("standard") or "").strip()
            cm_details.append({"measure": measure, "standard": standard})
            solutions.append(measure if not standard else f"{measure}（标准：{standard}）")

        for s in defect_payload.get("direct_solutions") or []:
            s = (s or "").strip()
            if s and s not in solutions and not s.isdigit():
                solutions.append(s)

        processes = [p for p in (defect_payload.get("processes") or []) if p]
        return {
            "id": f"graphrag:{defect}",
            "defect_type": defect,
            "process_name": " / ".join(processes) if processes else "",
            "processes": processes,
            "root_causes": [r["cause"] for r in root_details],
            "root_cause_details": root_details,
            "solutions": solutions,
            "countermeasures": cm_details,
            "occurrence": int(drill_count or 0),
            "verified": 1,
            "source": self.SOURCE_NAME,
            "evidence": self.EVIDENCE,
            "source_url": f"{self.base_url}/",
        }

    def build_knowledge_patterns(
        self,
        search: str = "",
        limit: int = 40,
        max_workers: int = 8,
    ) -> Dict[str, Any]:
        """
        组装自主学习知识库列表。
        - 名单与根因/对策：GraphRAG 生产 Neo4j
        - occurrence：生产环境图谱查阅次数（analytics），有据可依
        """
        search = (search or "").strip()
        defects = self.list_defects()
        ranking = self.analytics_defect_ranking(limit=500)
        drill_map = {
            (n.get("entity_name") or ""): int(n.get("drill_count") or 0)
            for n in ranking
            if n.get("entity_name")
        }

        if search:
            # 精确命中优先，再模糊包含
            exact = [d for d in defects if d == search]
            fuzzy = [d for d in defects if search in d and d != search]
            candidates = exact + fuzzy
        else:
            # 按生产查阅频次排序；无统计的缺陷排后
            candidates = sorted(
                defects,
                key=lambda d: (-drill_map.get(d, 0), d),
            )

        candidates = candidates[: max(1, min(int(limit), 80))]
        patterns: List[dict] = []
        errors: List[str] = []

        def _fetch(name: str):
            try:
                return name, self.query_defect(name), None
            except Exception as e:
                return name, None, str(e)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch, name) for name in candidates]
            for fut in as_completed(futures):
                name, payload, err = fut.result()
                if err:
                    errors.append(f"{name}: {err}")
                    continue
                if not payload or payload.get("error"):
                    continue
                pat = self._normalize_pattern(payload, drill_map.get(name, 0))
                if pat:
                    patterns.append(pat)

        patterns.sort(key=lambda p: (-int(p.get("occurrence") or 0), p.get("defect_type") or ""))
        return {
            "patterns": patterns,
            "matched": len(patterns),
            "candidate_count": len(candidates),
            "defect_universe": len(defects),
            "errors": errors[:5],
        }

    def build_process_knowledge(self, limit: int = 40) -> List[dict]:
        """工序知识卡片：工序名、缺陷数、缺陷名单均来自 GraphRAG。"""
        ov = self.overview()
        nodes = [
            n for n in (ov.get("nodes") or [])
            if n.get("type") == "Process" and n.get("name")
        ]
        nodes.sort(key=lambda n: (-int(n.get("defect_count") or 0), n.get("name") or ""))
        selected = nodes[:limit]

        def _one(n: dict) -> dict:
            name = n["name"]
            count = int(n.get("defect_count") or 0)
            defects: List[str] = []
            try:
                detail = self.graph_by_process(name)
                defects = [
                    d.get("name")
                    for d in (detail.get("nodes") or [])
                    if isinstance(d, dict) and d.get("type") == "Defect" and d.get("name")
                ]
            except Exception:
                defects = []
            if count >= 10:
                risk = "high"
            elif count >= 5:
                risk = "medium"
            else:
                risk = "low"
            return {
                "process_name": name,
                "defect_count": count,
                "defects": defects,
                "risk": risk,
                "source": self.SOURCE_NAME,
                "evidence": f"Neo4j Process={name} ← OCCURS_IN Defect×{count}",
            }

        out: List[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_one, n) for n in selected]
            for fut in as_completed(futures):
                try:
                    out.append(fut.result())
                except Exception:
                    continue
        out.sort(key=lambda p: (-int(p.get("defect_count") or 0), p.get("process_name") or ""))
        return out

    def build_knowledge_bundle(self, search: str = "", limit: int = 40) -> Dict[str, Any]:
        """知识库页一站式数据（生产权威）。"""
        root = self.root_stats()
        analytics = self.analytics_summary()
        pattern_pack = self.build_knowledge_patterns(search=search, limit=limit)
        processes = self.build_process_knowledge(limit=36)
        learning_entries = int(root.get("root_cause_total") or 0) + int(
            root.get("countermeasure_total") or 0
        )
        drills = int(analytics.get("total_node_drills") or 0)
        return {
            "source": self.SOURCE_NAME,
            "source_url": self.base_url,
            "connected": True,
            "evidence_policy": "根因/对策仅来自 GraphRAG 生产 Neo4j；频次来自生产图谱查阅统计；禁止本地模板充数",
            "patterns": pattern_pack["patterns"],
            "processes": processes,
            "stats": {
                "total_learning": drills or learning_entries,
                "defect_patterns": int(root.get("defect_total") or pattern_pack.get("defect_universe") or 0),
                "root_causes": int(root.get("root_cause_total") or 0),
                "countermeasures": int(root.get("countermeasure_total") or 0),
                "process_total": int(root.get("process_total") or len(processes)),
                "graph_drills": drills,
                "page_views": int(analytics.get("total_page_views") or 0),
                "unique_sessions": int(analytics.get("unique_sessions") or 0),
            },
            "meta": {
                "search": search,
                "matched": pattern_pack.get("matched"),
                "defect_universe": pattern_pack.get("defect_universe"),
                "errors": pattern_pack.get("errors") or [],
            },
        }

    def suggest_for_process(self, process_name: str, max_items: int = 3) -> List[str]:
        """给预警/建议用的真实对策摘要（工序维度）。"""
        if not process_name:
            return []
        try:
            data = self.query_process(process_name)
        except Exception:
            # 工序名可能与 ERP 不完全一致，尝试 overview 模糊匹配
            try:
                ov = self.overview()
                names = [
                    n.get("name") for n in (ov.get("nodes") or [])
                    if n.get("type") == "Process" and n.get("name")
                    and (process_name in n["name"] or n["name"] in process_name)
                ]
                if not names:
                    return []
                data = self.query_process(names[0])
            except Exception:
                return []
        if not data or data.get("error"):
            return []
        defects = [d for d in (data.get("defects") or []) if d][:2]
        tips: List[str] = []
        for d in defects:
            try:
                detail = self.query_defect(d)
            except Exception:
                continue
            for cm in (detail.get("countermeasures") or [])[:2]:
                m = (cm.get("measure") or "").strip()
                if m and not m.isdigit() and m not in tips:
                    tips.append(m)
            if len(tips) >= max_items:
                break
        return tips[:max_items]

    def suggest_for_defect(self, defect_name: str, max_items: int = 3) -> List[str]:
        if not defect_name:
            return []
        try:
            detail = self.query_defect(defect_name)
        except Exception:
            return []
        if not detail or detail.get("error"):
            # 模糊：缺陷名单包含匹配
            try:
                hits = [d for d in self.list_defects() if defect_name in d or d in defect_name]
                if not hits:
                    return []
                detail = self.query_defect(hits[0])
            except Exception:
                return []
        if not detail or detail.get("error"):
            return []
        tips = []
        for cm in detail.get("countermeasures") or []:
            m = (cm.get("measure") or "").strip()
            if m and not m.isdigit() and m not in tips:
                tips.append(m)
            if len(tips) >= max_items:
                break
        return tips

    def resolve_process_names(self, name: str) -> List[str]:
        """把 ERP 工序名解析为图谱中存在的工序名列表（按优先级）。"""
        name = (name or "").strip()
        if not name:
            return []
        try:
            ov = self.overview()
            graph_procs = [
                n.get("name") for n in (ov.get("nodes") or [])
                if n.get("type") == "Process" and n.get("name")
            ]
        except Exception:
            graph_procs = []
        graph_set = set(graph_procs)
        out: List[str] = []
        if name in graph_set:
            out.append(name)
        for alias in _process_aliases(name):
            if alias in graph_set and alias not in out:
                out.append(alias)
        # 包含匹配兜底
        for p in graph_procs:
            if (name in p or p in name) and p not in out:
                out.append(p)
        return out

    def resolve_defect_name(self, name: str) -> Optional[str]:
        """精确 / 配置映射 / 模糊解析为图谱中的缺陷名。"""
        name = (name or "").strip()
        if not name:
            return None
        # 配置映射优先（config/defect_mapping.json）
        candidates = [name] + _defect_aliases(name)
        if name.startswith("阻焊") and len(name) > 2:
            candidates.append(name[2:])
        seen_c = set()
        ordered = []
        for c in candidates:
            c = (c or "").strip()
            if c and c not in seen_c:
                seen_c.add(c)
                ordered.append(c)

        for cand in ordered:
            try:
                detail = self.query_defect(cand)
                if detail and not detail.get("error") and detail.get("defect"):
                    return detail["defect"]
            except Exception:
                pass

        try:
            defects = self.list_defects()
        except Exception:
            return None
        if not defects:
            return None
        for cand in ordered:
            if cand in defects:
                return cand
        # 打分：共享汉字越多越好
        best, best_score = None, 0
        for cand in ordered:
            cset = set(cand)
            for d in defects:
                dset = set(d)
                inter = len(cset & dset)
                if inter <= 0:
                    continue
                score = inter * 10 + (5 if cand in d or d in cand else 0) - abs(len(d) - len(cand))
                if score > best_score:
                    best_score = score
                    best = d
        if best_score >= 20:
            return best
        # 图谱暂不可达或未收录时：信任配置映射首项，保证 8D 对齐可展示
        aliases = _defect_aliases(name)
        return aliases[0] if aliases else None

    def align_erp_defects(self, erp_defects: List[str]) -> Dict[str, Any]:
        """批量 ERP→GraphRAG 对齐（供 8D / API 展示）。"""
        from config.defect_mapping import map_erp_defects
        return map_erp_defects(erp_defects, resolver=self.resolve_defect_name)

    def lookup_for_8d(
        self,
        defect_names: Optional[List[str]] = None,
        process_names: Optional[List[str]] = None,
        max_defects: int = 3,
        max_causes: int = 8,
        max_measures: int = 8,
    ) -> Dict[str, Any]:
        """
        为 8D 编写拉取知识库参考（有据可依）。
        缺陷名经别名/模糊对齐图谱；工序按主责工序展开（如 阻焊→显影/开油/丝印）。
        """
        refs: List[dict] = []
        seen = set()
        names = [str(n).strip() for n in (defect_names or []) if str(n).strip()]
        alignment = self.align_erp_defects(names[:max_defects])
        for raw in names[:max_defects]:
            # 优先用对齐结果，保证与 config/defect_mapping.json 一致
            resolved = None
            for a in alignment.get("alignments") or []:
                if a.get("erp_defect") == raw and a.get("graphrag_defect"):
                    resolved = a["graphrag_defect"]
                    break
            if not resolved:
                resolved = self.resolve_defect_name(raw)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            try:
                detail = self.query_defect(resolved)
            except Exception as e:
                refs.append({
                    "query": raw,
                    "matched": resolved,
                    "error": str(e),
                    "source": self.SOURCE_NAME,
                })
                continue
            if not detail or detail.get("error"):
                continue
            causes = []
            for rc in detail.get("root_causes") or []:
                cause = (rc.get("cause") or "").strip()
                if not cause:
                    continue
                causes.append({
                    "cause": cause,
                    "category": (rc.get("category") or "").strip(),
                })
                if len(causes) >= max_causes:
                    break
            measures = []
            for cm in detail.get("countermeasures") or []:
                measure = (cm.get("measure") or "").strip()
                if not measure or measure.isdigit():
                    continue
                measures.append({
                    "measure": measure,
                    "standard": (cm.get("standard") or "").strip(),
                })
                if len(measures) >= max_measures:
                    break
            processes = [p for p in (detail.get("processes") or []) if p]
            refs.append({
                "query": raw,
                "matched": resolved,
                "match_type": "exact" if raw == resolved else "alias_or_fuzzy",
                "processes": processes,
                "root_causes": causes,
                "countermeasures": measures,
                "source": self.SOURCE_NAME,
                "evidence": self.EVIDENCE,
                "source_url": f"{self.base_url}/",
            })

        # 缺陷未命中或根因仍空：按主责工序展开图谱工序，拉取关联缺陷的根因/对策
        need_process_fill = (not refs) or (
            sum(len(r.get("root_causes") or []) for r in refs) == 0
        )
        if need_process_fill:
            primary_procs = [str(p).strip() for p in (process_names or []) if str(p).strip()][:2]
            expanded: List[str] = []
            for pname in primary_procs:
                for rp in self.resolve_process_names(pname):
                    if rp not in expanded:
                        expanded.append(rp)
            for gproc in expanded[:4]:
                if gproc in seen:
                    continue
                seen.add(gproc)
                try:
                    pdata = self.query_process(gproc)
                except Exception:
                    pdata = None
                if not pdata or pdata.get("error"):
                    # 部分图谱工序无 process/query，改走 by-process 缺陷再查
                    try:
                        detail = self.graph_by_process(gproc)
                        defect_list = [
                            n.get("name") for n in (detail.get("nodes") or [])
                            if isinstance(n, dict) and n.get("type") == "Defect" and n.get("name")
                        ][:3]
                    except Exception:
                        defect_list = []
                else:
                    defect_list = [d for d in (pdata.get("defects") or []) if d][:3]
                causes, measures = [], []
                for dname in defect_list:
                    try:
                        ddetail = self.query_defect(dname)
                    except Exception:
                        continue
                    if not ddetail or ddetail.get("error"):
                        continue
                    for rc in ddetail.get("root_causes") or []:
                        cause = (rc.get("cause") or "").strip()
                        if cause:
                            causes.append({
                                "cause": f"[{dname}] {cause}",
                                "category": (rc.get("category") or "").strip(),
                            })
                        if len(causes) >= max_causes:
                            break
                    for cm in ddetail.get("countermeasures") or []:
                        measure = (cm.get("measure") or "").strip()
                        if measure and not measure.isdigit():
                            measures.append({
                                "measure": f"[{dname}] {measure}",
                                "standard": (cm.get("standard") or "").strip(),
                            })
                        if len(measures) >= max_measures:
                            break
                    if len(causes) >= max_causes and len(measures) >= max_measures:
                        break
                if not causes and not measures:
                    continue
                refs.append({
                    "query": primary_procs[0] if primary_procs else gproc,
                    "matched": gproc,
                    "match_type": "process_expand",
                    "processes": [gproc],
                    "root_causes": causes[:max_causes],
                    "countermeasures": measures[:max_measures],
                    "source": self.SOURCE_NAME,
                    "evidence": f"Neo4j Process={gproc} 关联缺陷根因/对策",
                    "source_url": f"{self.base_url}/",
                })

        return {
            "source": self.SOURCE_NAME,
            "source_url": self.base_url,
            "evidence_policy": "GraphRAG 条目仅作 FMEA 参考，须现场复核后写入本单结论",
            "defect_alignment": alignment,
            "refs": refs,
            "hit_count": len(refs),
            "cause_count": sum(len(r.get("root_causes") or []) for r in refs),
            "measure_count": sum(len(r.get("countermeasures") or []) for r in refs),
        }


_client: Optional[GraphRAGClient] = None
_client_lock = threading.Lock()


def get_graphrag_client(base_url: Optional[str] = None) -> GraphRAGClient:
    """获取 GraphRAG 客户端。传入 base_url 时按新地址重建（环境切换）。"""
    global _client
    with _client_lock:
        if base_url is not None:
            url = (base_url or "").strip() or GRAPHRAG_BASE_URL
            if _client is None or _client.base_url.rstrip("/") != url.rstrip("/"):
                _client = GraphRAGClient(base_url=url)
            return _client
        if _client is None:
            _client = GraphRAGClient()
        return _client


def reset_graphrag_client() -> None:
    global _client
    with _client_lock:
        _client = None
