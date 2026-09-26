"""报告变更业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "issue"
REPORT_MODULE = "report"
REQUIRED_FIELDS = ["变更编号", "关联报告", "变更类型", "变更原因"]
STATUS_ORDER = ["待申请", "已受理", "已批准", "已驳回"]
ACTION_RULES = {"提交申请": "已受理", "确认批准": "已批准", "驳回申请": "已驳回"}
NEGATIVE_ACTIONS = ["驳回申请"]
TERMINAL_STATUSES = ["已批准", "已驳回"]
# 变更类型与变更原因的组合判断：两个字段必须同时出现，缺任意一个都算组合不完整
COMBO_FIELDS = ["变更类型", "变更原因"]
# 报告状态合法性：已作废的报告不允许再发起或推进变更
VOID_REPORT_STATUS = "已作废"
# 驳回或在途（待申请、已受理）的变更会锁住关联报告，不允许重复变更
BLOCKING_STATUSES = ["待申请", "已受理", "已驳回"]


def missing_required_fields(values: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]


def combo_problem(values: dict[str, Any]) -> str | None:
    """变更类型与变更原因的组合判断：缺任意一个都视为组合不完整。"""
    missing = [field for field in COMBO_FIELDS if not str(values.get(field) or "").strip()]
    if missing:
        return f"变更类型与变更原因必须同时填写，缺少：{'、'.join(missing)}"
    return None


def report_status_problem(values: dict[str, Any]) -> str | None:
    """报告状态合法性：关联报告已作废时不能再变更；未匹配到报告编号时保持既有口径，不拦截。"""
    report_no = str(values.get("关联报告") or "").strip()
    if not report_no:
        return None
    for report in store.rows(REPORT_MODULE):
        if str(report.get("报告编号") or "").strip() == report_no:
            if report.get("status") == VOID_REPORT_STATUS:
                return f"关联报告 {report_no} 已作废，不能发起变更"
            return None
    return None


def duplicate_problem(values: dict[str, Any], *, exclude_id: int | None = None) -> str | None:
    """重复变更判断：同一关联报告存在驳回或在途变更时，不允许重复提交。"""
    report_no = str(values.get("关联报告") or "").strip()
    if not report_no:
        return None
    for other in store.rows(MODULE):
        if exclude_id is not None and int(other.get("id", 0)) == exclude_id:
            continue
        if str(other.get("关联报告") or "").strip() != report_no:
            continue
        if other.get("status") in BLOCKING_STATUSES:
            return f"关联报告 {report_no} 存在驳回或在途变更，不能重复提交"
    return None


def validate_change(values: dict[str, Any], *, exclude_id: int | None = None) -> str | None:
    """变更审批共用校验：组合判断、报告状态、重复变更只在这一处判定，登记与三个动作共用。"""
    return (
        combo_problem(values)
        or report_status_problem(values)
        or duplicate_problem(values, exclude_id=exclude_id)
    )


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

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = missing_required_fields(values)
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        problem = validate_change(values)
        if problem:
            return None, problem
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"变更记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于报告变更可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        problem = validate_change(entry, exclude_id=entry_id)
        if problem:
            return None, problem
        entry["status"] = target
        entry["pending"] = target not in TERMINAL_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"变更记录已{action}"
