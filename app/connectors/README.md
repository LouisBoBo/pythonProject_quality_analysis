# 数据连接器目录

请将以下文件放到本目录：

- `erp_connector.py` — ERP（atomLive）连接器，需提供 `get_erp()` / `ERPConnector`
- `mes_connector.py` — MES（CIMOM）连接器，需提供 `get_mes()`

也可通过环境变量指定额外搜索路径：

```bash
export QA_CONNECTOR_PATHS="/path/to/connectors"
```
