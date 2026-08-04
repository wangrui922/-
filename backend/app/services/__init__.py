"""业务服务层。

约定：
    * API 层只做「参数校验 + 依赖注入 + 调用 service + 包 ``ok()``」；
    * 事务边界由 ``app.db.get_db`` 依赖统一管理，service 内只 ``flush``，不 ``commit``；
    * service 内禁止出现 PG 专属 SQL（跨方言硬约定）。
"""

from __future__ import annotations

__all__: list[str] = []
