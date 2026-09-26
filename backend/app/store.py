"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS
from app.services import issue_rules


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def reset(self) -> None:
        """恢复到示例数据初始状态，供测试之间相互隔离。"""
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def _pending_count(self, name: str, rows: list[dict[str, Any]]) -> int:
        # 报告变更按审批状态口径统计在途量，避免与动作流转结果不一致。
        if name == issue_rules.MODULE_NAME:
            return sum(1 for row in rows if issue_rules.is_in_flight(row.get("status")))
        return sum(1 for row in rows if row.get("pending"))

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": self._pending_count(name, rows),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        pending_changes = next(
            (int(item["pending"]) for item in modules if item["name"] == issue_rules.MODULE_NAME),
            0,
        )
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "在途变更", "value": pending_changes},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
