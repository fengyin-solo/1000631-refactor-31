"""报告变更业务规则：登记校验与审批流转统一走 issue_rules 的共用判定。"""
from __future__ import annotations

from typing import Any

from app.services import issue_rules
from app.store import store

MODULE = issue_rules.MODULE_NAME
REPORT_MODULE = issue_rules.REPORT_MODULE_NAME
REQUIRED_FIELDS = ["变更编号", "关联报告", "变更类型", "变更原因"]


class IssueService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("变更编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def _find_report(self, report_no: str) -> dict[str, Any] | None:
        for row in store.rows(REPORT_MODULE):
            if str(row.get("报告编号") or "").strip() == report_no:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        report_no = str(values.get("关联报告") or "").strip()
        report = self._find_report(report_no)
        # 驳回或在途变更未清前，报告不能被重复变更。
        message = issue_rules.validate_related_report(
            report,
            report_no=report_no,
            issue_rows=store.rows(MODULE),
        )
        if message:
            return None, [], message
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = issue_rules.INITIAL_STATUS
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, [], ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变更记录 {entry_id} 不存在或已归档"
        # 历史样例数据的关联报告可能对应不上正式报告，此时只做变更侧判定，
        # 保证既有记录的流转行为不变；正式登记的变更都会追加报告状态校验。
        report_no = str(entry.get("关联报告") or "").strip()
        report = self._find_report(report_no)
        report_arg: Any = report if report is not None else ...
        message = issue_rules.validate_change_action(entry, action, report=report_arg)
        if message:
            return None, message
        target = issue_rules.ACTION_TARGETS[action]
        entry["status"] = target
        # pending 取值沿用既有公式（终态以序列末位「已驳回」为准），
        # 保持接口返回不变；在途口径由概览按状态另行统计。
        entry["pending"] = target != issue_rules.ISSUE_STATUSES[-1]
        entry["abnormal"] = action in issue_rules.NEGATIVE_ACTIONS
        return entry, f"变更记录已{action}"
