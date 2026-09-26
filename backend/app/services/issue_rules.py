"""报告变更共用判定：变更类型/原因组合、报告状态与重复变更校验只此一份。

提交申请、确认批准、驳回申请三个动作以及登记入口都通过这里的
``validate_change_action`` / ``validate_related_report`` 判定，
避免同一套规则在不同动作里各写一遍后改漏。
"""
from __future__ import annotations

from typing import Any, Iterable

# 内存仓库里报告变更模块与报告出具模块的表名。
MODULE_NAME = "issue"
REPORT_MODULE_NAME = "report"

# 变更记录自身的状态序列与动作对应的目标状态。
INITIAL_STATUS = "待申请"
ISSUE_STATUSES = ["待申请", "已受理", "已批准", "已驳回"]
ACTION_TARGETS = {"提交申请": "已受理", "确认批准": "已批准", "驳回申请": "已驳回"}
NEGATIVE_ACTIONS = ["驳回申请"]

# 仍在审批流程中的状态；处于这些状态的关联报告不允许再发起变更。
IN_FLIGHT_STATUSES = ["待申请", "已受理"]
# 终态状态。
APPROVED_STATUS = "已批准"
REJECTED_STATUS = "已驳回"
TERMINAL_STATUSES = [APPROVED_STATUS, REJECTED_STATUS]

# 各动作只允许在这些前置状态下执行。
ACTION_ALLOWED_FROM = {
    "提交申请": ["待申请"],
    "确认批准": ["已受理"],
    "驳回申请": ["已受理"],
}

# 只有已出具的报告才能发起变更。
REPORT_ISSUED_STATUS = "已出具"

# 变更类型与变更原因的合法组合：原因按关键字匹配，三种动作共用同一份口径。
CHANGE_TYPE_REASONS: dict[str, tuple[str, ...]] = {
    "报告更正": ("信息更正", "更正"),
    "报告补充": ("补充检测", "补充"),
    "报告撤回": ("撤回",),
    "作废重发": ("作废重发", "重发"),
}


def is_in_flight(status: Any) -> bool:
    """变更记录是否仍在途（待申请或已受理，未出终态结论）。"""
    return str(status or "") in IN_FLIGHT_STATUSES


def find_blocking_change(issue_rows: Iterable[dict[str, Any]], report_no: str) -> dict[str, Any] | None:
    """找出阻止同一报告再次发起变更的记录：在途变更或被驳回的变更。

    已批准的变更不在拦截范围内（报告已按批准结论重新出具）。
    """
    for row in issue_rows:
        if str(row.get("关联报告") or "").strip() != report_no:
            continue
        status = str(row.get("status") or "")
        if is_in_flight(status) or status == REJECTED_STATUS:
            return row
    return None


def validate_related_report(
    report: dict[str, Any] | None,
    *,
    report_no: str,
    issue_rows: Iterable[dict[str, Any]] | None = None,
    exclude_id: int | None = None,
) -> str:
    """校验关联报告能否被变更：报告存在、已出具、不存在在途或被驳回的变更。

    返回空串表示通过；否则返回可读的拦截原因。``exclude_id`` 用于在动作
    流转时排除当前变更记录自身，只检查同报告的其他变更。
    """
    if report is None:
        return f"关联报告 {report_no} 不存在或已归档"
    status = str(report.get("status") or "")
    if status != REPORT_ISSUED_STATUS:
        return f"报告 {report_no} 当前状态为「{status}」，仅「{REPORT_ISSUED_STATUS}」的报告允许发起变更"
    if issue_rows is None:
        return ""
    for row in issue_rows:
        if exclude_id is not None and int(row.get("id", 0)) == exclude_id:
            continue
        if str(row.get("关联报告") or "").strip() != report_no:
            continue
        blocker_status = str(row.get("status") or "")
        if not (is_in_flight(blocker_status) or blocker_status == REJECTED_STATUS):
            continue
        if blocker_status == REJECTED_STATUS:
            return f"报告 {report_no} 存在已驳回的变更（变更编号 {row.get('变更编号')}），请先处理后再发起"
        return f"报告 {report_no} 已有在途变更（变更编号 {row.get('变更编号')}），不能重复变更"
    return ""


def validate_change_action(
    entry: dict[str, Any],
    action: str,
    *,
    report: dict[str, Any] | None | Ellipsis = ...,
) -> str:
    """提交申请、确认批准、驳回申请共用的判定。

    ``entry`` 为目标变更记录，``action`` 为动作名；``report`` 传入关联报告
    行（可为 ``None``）以追加报告状态校验，传 ``...``（默认）表示跳过报告
    校验，兼容没有可靠关联报告的历史数据。返回空串表示通过。

    判定顺序：动作是否可执行 → 当前变更状态是否允许该动作 → 变更类型与
    变更原因组合是否合法 →（可选）关联报告是否仍处于已出具状态。
    """
    if action not in ACTION_TARGETS:
        return f"动作「{action}」不属于报告变更可执行范围"
    current = str(entry.get("status") or "")
    if current not in ACTION_ALLOWED_FROM[action]:
        return f"变更记录当前状态为「{current}」，不允许执行「{action}」"
    change_type = str(entry.get("变更类型") or "").strip()
    reason = str(entry.get("变更原因") or "").strip()
    if not reason:
        return "变更原因缺失，无法说明变更事由"
    reasons = CHANGE_TYPE_REASONS.get(change_type)
    if reasons is not None and not any(keyword in reason for keyword in reasons):
        allowed = "、".join(f"「{item}」" for item in reasons)
        return f"变更类型「{change_type}」与变更原因「{reason}」组合不合法，原因应包含 {allowed}"
    if report is not ...:
        report_no = str(entry.get("关联报告") or "").strip()
        if report is None:
            return f"关联报告 {report_no} 不存在或已归档"
        report_status = str(report.get("status") or "")
        if report_status != REPORT_ISSUED_STATUS:
            return f"报告 {report_no} 当前状态为「{report_status}」，无法对其执行变更"
    return ""
