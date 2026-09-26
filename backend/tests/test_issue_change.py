"""报告变更：审批与报告状态共用判定的端到端测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.services import issue_rules  # noqa: E402
from app.services.issue import IssueService  # noqa: E402
from app.services.report import ReportService  # noqa: E402
from app.store import store  # noqa: E402


@pytest.fixture(autouse=True)
def reset_store() -> None:
    store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _issue_report(report_no: str = "REPO-T1") -> int:
    """登记并出具一份报告，返回报告 id。"""
    reports = ReportService()
    entry, missing = reports.create_entry(
        {"报告编号": report_no, "关联样品": "样品A", "报告类型": "正式报告"}
    )
    assert not missing
    report_id = int(entry["id"])
    reports.run_action(report_id, "提交编制")
    reports.run_action(report_id, "确认签发")
    assert reports.get_entry(report_id)["status"] == "已出具"
    return report_id


def _create_change(
    client: TestClient,
    *,
    report_no: str = "REPO-T1",
    change_no: str = "ISSU-T1",
    change_type: str = "报告更正",
    reason: str = "信息更正：修正委托单位名称",
) -> dict:
    payload = {
        "values": {
            "变更编号": change_no,
            "关联报告": report_no,
            "变更类型": change_type,
            "变更原因": reason,
        }
    }
    return client.post("/api/issue", json=payload).json()


# ---------------------------------------------------------------- 登记校验


def test_create_missing_change_number(client: TestClient) -> None:
    """变更编号为空：不允许登记，且不产生新记录。"""
    before = client.get("/api/issue").json()["total"]
    resp = client.post(
        "/api/issue",
        json={"values": {"关联报告": "REPO-T1", "变更类型": "报告更正", "变更原因": "信息更正"}},
    )
    data = resp.json()
    assert data["ok"] is False
    assert "变更编号" in data["message"]
    assert client.get("/api/issue").json()["total"] == before


def test_create_missing_reason(client: TestClient) -> None:
    """原因缺失：不允许登记。"""
    _issue_report()
    resp = client.post(
        "/api/issue",
        json={"values": {"变更编号": "ISSU-T1", "关联报告": "REPO-T1", "变更类型": "报告更正"}},
    )
    data = resp.json()
    assert data["ok"] is False
    assert "变更原因" in data["message"]


def test_create_requires_issued_report(client: TestClient) -> None:
    """报告状态合法性：未出具的报告不能登记变更。"""
    reports = ReportService()
    entry, _ = reports.create_entry(
        {"报告编号": "REPO-DRAFT", "关联样品": "样品B", "报告类型": "正式报告"}
    )
    assert entry["status"] == "待编制"
    data = _create_change(client, report_no="REPO-DRAFT", change_no="ISSU-T1")
    assert data["ok"] is False
    assert "待编制" in data["message"]
    assert "已出具" in data["message"]


def test_create_unknown_report_rejected(client: TestClient) -> None:
    data = _create_change(client, report_no="REPO-NOPE", change_no="ISSU-T1")
    assert data["ok"] is False
    assert "关联报告 REPO-NOPE 不存在" in data["message"]


def test_create_void_report_rejected(client: TestClient) -> None:
    """已作废报告不能被变更。"""
    report_id = _issue_report("REPO-VOID")
    reports = ReportService()
    reports.run_action(report_id, "作废报告")
    data = _create_change(client, report_no="REPO-VOID", change_no="ISSU-T1")
    assert data["ok"] is False
    assert "已作废" in data["message"]


def test_create_valid_change_keeps_fields(client: TestClient) -> None:
    """正常登记：取值与接口返回保持既有形态（字段原文、待申请、pending 标志）。"""
    _issue_report()
    data = _create_change(client)
    assert data["ok"] is True
    assert data["message"] == "变更记录已登记"
    entry = data["entry"]
    assert entry["变更编号"] == "ISSU-T1"
    assert entry["关联报告"] == "REPO-T1"
    assert entry["变更类型"] == "报告更正"
    assert entry["变更原因"] == "信息更正：修正委托单位名称"
    assert entry["status"] == "待申请"
    assert entry["pending"] is True
    assert entry["abnormal"] is False


# ------------------------------------------------- 重复变更：在途 / 已驳回


def test_duplicate_submit_while_pending_blocked(client: TestClient) -> None:
    """重复提交：同报告在途变更存在时，再次登记被拦下。"""
    _issue_report()
    assert _create_change(client, change_no="ISSU-T1")["ok"] is True
    # 第一条提交申请后进入在途（已受理）
    first_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    action = client.post(f"/api/issue/{first_id}/actions", json={"values": {"action": "提交申请"}})
    assert action.json()["ok"] is True
    dup = _create_change(client, change_no="ISSU-T2")
    assert dup["ok"] is False
    assert "在途变更" in dup["message"]


def test_duplicate_submit_after_reject_blocked(client: TestClient) -> None:
    """驳回后报告不能被重复变更。"""
    _issue_report()
    assert _create_change(client, change_no="ISSU-T1")["ok"] is True
    first_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{first_id}/actions", json={"values": {"action": "提交申请"}})
    rejected = client.post(
        f"/api/issue/{first_id}/actions", json={"values": {"action": "驳回申请"}}
    )
    assert rejected.json()["ok"] is True
    dup = _create_change(client, change_no="ISSU-T2")
    assert dup["ok"] is False
    assert "已驳回" in dup["message"]


def test_approved_change_allows_new_change(client: TestClient) -> None:
    """已批准的变更结案后，同报告可再次发起变更。"""
    _issue_report()
    assert _create_change(client, change_no="ISSU-T1")["ok"] is True
    first_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{first_id}/actions", json={"values": {"action": "提交申请"}})
    approved = client.post(
        f"/api/issue/{first_id}/actions", json={"values": {"action": "确认批准"}}
    )
    assert approved.json()["ok"] is True
    again = _create_change(
        client, change_no="ISSU-T2", change_type="报告补充", reason="补充检测：追加微生物项目"
    )
    assert again["ok"] is True


# ------------------------------------------------------- 三个动作共用判定


def test_submit_rejects_missing_reason(client: TestClient) -> None:
    """提交申请：原因缺失拦下，状态不变。"""
    _issue_report()
    assert _create_change(client)["ok"] is True
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    svc = IssueService()
    svc.get_entry(change_id)["变更原因"] = "   "
    entry, message = svc.run_action(change_id, "提交申请")
    assert entry is None
    assert "变更原因缺失" in message
    assert svc.get_entry(change_id)["status"] == "待申请"


def test_approve_rejects_bad_type_reason_combo(client: TestClient) -> None:
    """确认批准：变更类型与原因组合不合法拦下。"""
    _issue_report()
    assert _create_change(client)["ok"] is True
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    # 受理后原因被改成与类型不匹配的内容（绕过登记接口模拟数据维护）
    IssueService().get_entry(change_id)["变更原因"] = "随便写的理由"
    resp = client.post(
        f"/api/issue/{change_id}/actions", json={"values": {"action": "确认批准"}}
    )
    data = resp.json()
    assert data["ok"] is False
    assert "组合不合法" in data["message"]
    # 被拦下后仍是已受理，没有进入已批准
    assert IssueService().get_entry(change_id)["status"] == "已受理"


def test_reject_uses_same_combo_validation(client: TestClient) -> None:
    """驳回申请与批准走同一套组合判定：组合不合法时驳回同样被拦。"""
    _issue_report()
    assert _create_change(client)["ok"] is True
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    IssueService().get_entry(change_id)["变更原因"] = "不相关的说明"
    resp = client.post(
        f"/api/issue/{change_id}/actions", json={"values": {"action": "驳回申请"}}
    )
    assert resp.json()["ok"] is False
    assert "组合不合法" in resp.json()["message"]
    assert IssueService().get_entry(change_id)["status"] == "已受理"


def test_action_state_transition_order_enforced(client: TestClient) -> None:
    """状态合法性：待申请记录不能直接批准，已受理记录不能重复提交。"""
    _issue_report()
    assert _create_change(client)["ok"] is True
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    early_approve = client.post(
        f"/api/issue/{change_id}/actions", json={"values": {"action": "确认批准"}}
    )
    assert early_approve.json()["ok"] is False
    assert "待申请" in early_approve.json()["message"]

    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    repeat_submit = client.post(
        f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}}
    )
    assert repeat_submit.json()["ok"] is False
    assert "已受理" in repeat_submit.json()["message"]


def test_unknown_action_and_entry(client: TestClient) -> None:
    resp = client.post("/api/issue/9999/actions", json={"values": {"action": "提交申请"}})
    assert resp.json()["ok"] is False
    assert "不存在" in resp.json()["message"]
    _issue_report()
    _create_change(client)
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    resp = client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "删除变更"}})
    assert resp.json()["ok"] is False
    assert "不属于报告变更可执行范围" in resp.json()["message"]


def test_approve_does_not_change_report_status(client: TestClient) -> None:
    """审批变更不改变已有报告的出具状态。"""
    report_id = _issue_report()
    _create_change(client)
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "确认批准"}})
    assert ReportService().get_entry(report_id)["status"] == "已出具"
    approved = IssueService().get_entry(change_id)
    assert approved["status"] == "已批准"
    # 接口返回取值沿用旧公式（只有已驳回才清 pending），不因收拢而改变
    assert approved["pending"] is True
    assert approved["abnormal"] is False


def test_reject_marks_abnormal_and_terminal(client: TestClient) -> None:
    _issue_report()
    _create_change(client)
    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "驳回申请"}})
    entry = IssueService().get_entry(change_id)
    assert entry["status"] == "已驳回"
    assert entry["pending"] is False
    assert entry["abnormal"] is True


# ------------------------------------------------------------- 概览一致性


def test_overview_in_flight_change_count(client: TestClient) -> None:
    """概览在途变更数与模块待处理一致，并随流转更新。"""
    overview = client.get("/api/overview").json()
    card = {card["label"]: card["value"] for card in overview["cards"]}
    issue_row = next(row for row in overview["modules"] if row["name"] == "issue")
    assert card["在途变更"] == issue_row["pending"] == 2  # 种子：待申请 + 已受理

    _issue_report()
    _create_change(client)  # 新增一条待申请
    overview = client.get("/api/overview").json()
    card = {item["label"]: item["value"] for item in overview["cards"]}
    issue_row = next(row for row in overview["modules"] if row["name"] == "issue")
    assert card["在途变更"] == issue_row["pending"] == 3

    change_id = next(
        row["id"] for row in client.get("/api/issue").json()["items"] if row["变更编号"] == "ISSU-T1"
    )
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "提交申请"}})
    client.post(f"/api/issue/{change_id}/actions", json={"values": {"action": "确认批准"}})
    overview = client.get("/api/overview").json()
    card = {item["label"]: item["value"] for item in overview["cards"]}
    issue_row = next(row for row in overview["modules"] if row["name"] == "issue")
    assert card["在途变更"] == issue_row["pending"] == 2


# ----------------------------------------------- 共用判定单元测试（三个动作）


def test_all_three_actions_share_one_validator() -> None:
    """直接验证共用实现：三个动作对缺原因/坏组合给出一致结论。"""
    valid_entry = {"status": "已受理", "变更类型": "报告撤回", "变更原因": "撤回：客户申请", "关联报告": "X"}
    for action in ("确认批准", "驳回申请"):
        assert issue_rules.validate_change_action(dict(valid_entry), action, report={"status": "已出具"}) == ""
    submit_entry = dict(valid_entry, status="待申请")
    assert issue_rules.validate_change_action(submit_entry, "提交申请", report={"status": "已出具"}) == ""

    bad_entry = {"status": "已受理", "变更类型": "报告撤回", "变更原因": "信息更正", "关联报告": "X"}
    for action in ("确认批准", "驳回申请"):
        message = issue_rules.validate_change_action(bad_entry, action, report={"status": "已出具"})
        assert "组合不合法" in message

    missing_reason = {"status": "已受理", "变更类型": "报告撤回", "变更原因": "", "关联报告": "X"}
    for action in ("确认批准", "驳回申请"):
        assert "变更原因缺失" in issue_rules.validate_change_action(
            missing_reason, action, report={"status": "已出具"}
        )


def test_seed_records_keep_values_and_flow() -> None:
    """既有变更记录取值不变；历史样例仍可按原方式流转。"""
    svc = IssueService()
    first = svc.get_entry(1)
    assert first["变更编号"] == "ISSU-0001"
    assert first["变更原因"] == "报告变更样例1"
    assert first["status"] == "待申请"
    # 历史样例的关联报告不是正式报告编号：跳过报告校验，动作仍生效（行为不变）
    entry, message = svc.run_action(1, "提交申请")
    assert entry is not None and message == "变更记录已提交申请"
    assert entry["status"] == "已受理"
    # 未知类型的历史数据只要求原因非空，不做组合限制
    entry, _ = svc.run_action(1, "确认批准")
    assert entry["status"] == "已批准"
    # 既有取值不变：批准后 pending 仍为 True（旧公式以「已驳回」为终态）
    assert entry["pending"] is True
