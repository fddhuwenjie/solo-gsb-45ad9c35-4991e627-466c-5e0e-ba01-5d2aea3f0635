from datetime import datetime, timedelta, timezone

import pytest

from app.engine import (
    EVENT_ENTER,
    EVENT_LEAVE,
    compute_relay_report,
)

BASE = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)

PLAIN_FENCE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]],
}

INSIDE = (0.005, 0.005)
OUTSIDE = (0.02, 0.02)

START_SEC = 100
END_SEC = 800
GAP_SEC = 30.0
OV_SEC = 20.0


def pt(device, sec, inside=True):
    lat, lon = INSIDE if inside else OUTSIDE
    return {
        "device_id": device,
        "ts": (BASE + timedelta(seconds=sec)).isoformat(),
        "lat": lat,
        "lon": lon,
    }


def iso(sec):
    return (BASE + timedelta(seconds=sec)).isoformat()


def dts(sec):
    return BASE + timedelta(seconds=sec)


def create_fence(client):
    r = client.post("/fences", json={"name": "接力测试", "geometry": PLAIN_FENCE})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def submit(client, points):
    r = client.post("/points", json={"points": points})
    assert r.status_code == 200, r.text
    return r.json()


def create_task(
    client,
    fence_id,
    devices=("d1", "d2", "d3"),
    name="三班接力",
    start=START_SEC,
    end=END_SEC,
    gap=GAP_SEC,
    ov=OV_SEC,
):
    r = client.post(
        f"/fences/{fence_id}/relay-tasks",
        json={
            "name": name,
            "devices": list(devices),
            "start_at": iso(start),
            "end_at": iso(end),
            "max_gap_s": gap,
            "min_overlap_s": ov,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def report(client, fence_id, task_id):
    r = client.get(f"/fences/{fence_id}/relay-tasks/{task_id}/report")
    assert r.status_code == 200, r.text
    return r.json()


def test_create_task_validation_and_crud(client):
    fence_id = create_fence(client)

    # 围栏不存在 -> 404
    r = client.post(
        f"/fences/999/relay-tasks",
        json={
            "name": "x",
            "devices": ["a", "b"],
            "start_at": iso(0),
            "end_at": iso(10),
            "max_gap_s": 5,
            "min_overlap_s": 5,
        },
    )
    assert r.status_code == 404

    def bad(body_mod):
        body = {
            "name": "bad",
            "devices": ["a", "b"],
            "start_at": iso(0),
            "end_at": iso(10),
            "max_gap_s": 5,
            "min_overlap_s": 5,
        }
        body.update(body_mod)
        return client.post(f"/fences/{fence_id}/relay-tasks", json=body)

    # 设备数量必须 2～8
    assert bad({"devices": ["a"]}).status_code == 422
    assert bad({"devices": [f"d{i}" for i in range(9)]}).status_code == 422
    assert bad({"devices": ["a", "a"]}).status_code == 422               # 不得重复
    assert bad({"devices": ["a", "  "]}).status_code == 422              # 不得为空
    assert bad({"start_at": iso(10), "end_at": iso(10)}).status_code == 422  # 窗口无效
    assert bad({"start_at": iso(20), "end_at": iso(10)}).status_code == 422
    assert bad({"max_gap_s": -0.1}).status_code == 422                   # 空档非负
    assert bad({"min_overlap_s": -1}).status_code == 422                 # 交叠非负
    assert bad({"name": ""}).status_code == 422

    task = create_task(client, fence_id, devices=("d3", "d1", "d2"), name="接力A")
    assert task["fence_id"] == fence_id
    assert task["devices"] == ["d3", "d1", "d2"]  # 按交班顺序保存
    assert task["max_gap_s"] == GAP_SEC
    assert task["min_overlap_s"] == OV_SEC
    assert datetime.fromisoformat(task["start_at"]) == dts(START_SEC)
    assert datetime.fromisoformat(task["end_at"]) == dts(END_SEC)

    # 空档与交叠允许为 0
    task0 = create_task(client, fence_id, name="零门槛", devices=("a", "b"),
                        gap=0, ov=0)
    assert task0["max_gap_s"] == 0
    assert task0["min_overlap_s"] == 0

    # 列表按 id 升序
    create_task(client, fence_id, devices=("d4", "d5"), name="接力B")
    tasks = client.get(f"/fences/{fence_id}/relay-tasks").json()
    assert [t["name"] for t in tasks] == ["接力A", "零门槛", "接力B"]

    # 删除；重复删除与跨围栏访问 -> 404
    tid = tasks[0]["id"]
    assert client.delete(f"/fences/{fence_id}/relay-tasks/{tid}").status_code == 204
    assert client.delete(f"/fences/{fence_id}/relay-tasks/{tid}").status_code == 404
    assert client.get(
        f"/fences/{fence_id}/relay-tasks/{tid}/report"
    ).status_code == 404
    other = create_fence(client)
    assert client.get(
        f"/fences/{other}/relay-tasks/{tasks[1]['id']}/report"
    ).status_code == 404


def test_report_full_overlap_chain_passed(client):
    fence_id = create_fence(client)
    # d1 [50,300)、d2 [120,400)、d3 [200,500)，窗口 [100,800)
    submit(client, [
        pt("d1", 50), pt("d1", 300, inside=False),
        pt("d2", 120), pt("d2", 400, inside=False),
        pt("d3", 200), pt("d3", 500, inside=False),
    ])
    task = create_task(client, fence_id, devices=("d1", "d2", "d3"), ov=20)
    rep = report(client, fence_id, task["id"])

    assert rep["passed"] is True
    assert rep["passed_steps"] == 2
    assert rep["first_violation_at"] is None
    assert [d["device_id"] for d in rep["device_results"]] == ["d1", "d2", "d3"]
    assert all(d["status"] == "present" for d in rep["device_results"])

    s0, s1 = rep["steps"]
    assert s0["status"] == "passed"
    assert datetime.fromisoformat(s0["takeover_at"]) == dts(120)
    assert s0["overlap_s"] == pytest.approx(180.0)
    assert s0["gap_s"] is None
    # d1 在窗口前进入，起端截断到窗口起点
    assert datetime.fromisoformat(s0["from_interval"]["start"]) == dts(START_SEC)
    assert datetime.fromisoformat(s0["from_interval"]["end"]) == dts(300)
    assert datetime.fromisoformat(s0["to_interval"]["start"]) == dts(120)
    assert s1["status"] == "passed"
    assert datetime.fromisoformat(s1["takeover_at"]) == dts(200)
    assert s1["overlap_s"] == pytest.approx(200.0)
    assert s1["first_violation_at"] is None


def test_report_gap_boundary_equal_allowed(client):
    fence_id = create_fence(client)
    # d1 [0,120)、d2 [150,250)：空档恰 30s
    submit(client, [
        pt("d1", 0), pt("d1", 120, inside=False),
        pt("d2", 150), pt("d2", 250, inside=False),
    ])
    task = create_task(client, fence_id, devices=("d1", "d2"), gap=30, ov=0)
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is True
    assert rep["steps"][0]["gap_s"] == pytest.approx(30.0)
    assert rep["steps"][0]["overlap_s"] is None

    # 差 1 秒 -> 空档超限，首个违规时刻 = 120+30 = 150（接班设备实际 151 才到）
    submit(client, [
        pt("d1b", 0), pt("d1b", 120, inside=False),
        pt("d2b", 151), pt("d2b", 250, inside=False),
    ])
    task = create_task(client, fence_id, devices=("d1b", "d2b"), name="超空档",
                       gap=30, ov=0)
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is False
    assert rep["passed_steps"] == 0
    step = rep["steps"][0]
    assert step["status"] == "gap_exceeded"
    assert step["gap_s"] == pytest.approx(31.0)
    assert datetime.fromisoformat(step["first_violation_at"]) == dts(150)
    assert datetime.fromisoformat(step["takeover_at"]) == dts(151)
    assert rep["device_results"][1]["status"] == "failed"
    assert rep["first_violation_at"] == step["first_violation_at"]


def test_report_overlap_insufficient(client):
    fence_id = create_fence(client)
    # d1 [0,140)、d2 [120,300)：交叠 20s
    submit(client, [
        pt("d1", 0), pt("d1", 140, inside=False),
        pt("d2", 120), pt("d2", 300, inside=False),
    ])
    # 门槛 20 -> 恰好达标
    task = create_task(client, fence_id, devices=("d1", "d2"), gap=0, ov=20)
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is True
    assert rep["steps"][0]["overlap_s"] == pytest.approx(20.0)

    # 门槛 21 -> 交叠不足，首个违规时刻 = 交班区间止端 140
    task = create_task(client, fence_id, devices=("d1", "d2"), name="严格交叠",
                       gap=0, ov=21)
    rep = report(client, fence_id, task["id"])
    step = rep["steps"][0]
    assert step["status"] == "overlap_insufficient"
    assert datetime.fromisoformat(step["first_violation_at"]) == dts(140)
    assert datetime.fromisoformat(step["takeover_at"]) == dts(120)
    assert rep["device_results"][0]["status"] == "present"
    assert rep["device_results"][1]["status"] == "failed"


def test_report_absent_stops_remaining_handovers(client):
    fence_id = create_fence(client)
    # d1 [0,400) 在场；d2 全程缺席；d3 在场但不应参与
    submit(client, [
        pt("d1", 0), pt("d1", 400, inside=False),
        pt("d3", 200), pt("d3", 300, inside=False),
    ])
    task = create_task(client, fence_id, devices=("d1", "d2", "d3"), gap=30, ov=10)
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is False
    assert rep["passed_steps"] == 0

    d1, d2, d3r = rep["device_results"]
    assert d1["status"] == "present"
    assert d2["status"] == "absent"
    assert d2["interval"] is None
    assert d3r["status"] == "not_reached"

    s0, s1 = rep["steps"]
    assert s0["status"] == "absent"
    assert s0["takeover_at"] is None
    assert s0["to_interval"] is None
    # 首个违规时刻 = 交班区间止端 + 最大空档 = 430（窗口未结束）
    assert datetime.fromisoformat(s0["first_violation_at"]) == dts(430)
    assert s1["status"] == "not_reached"
    assert s1["first_violation_at"] is None
    assert rep["first_violation_at"] == s0["first_violation_at"]


def test_first_device_absent(client):
    fence_id = create_fence(client)
    submit(client, [pt("d2", 200), pt("d2", 300, inside=False)])
    task = create_task(client, fence_id, devices=("d1", "d2"))
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is False
    assert rep["passed_steps"] == 0
    assert rep["device_results"][0]["status"] == "absent"
    assert rep["device_results"][1]["status"] == "not_reached"
    # 交班窗口耗尽时刻即窗口止端
    assert datetime.fromisoformat(rep["first_violation_at"]) == dts(END_SEC)
    assert rep["steps"][0]["status"] == "absent"


def test_dp_prefers_longer_chain(client):
    fence_id = create_fence(client)
    # d1 两段：[0,120)（无法交班）与 [200,400)；d2 [300,500)；d3 [350,600)
    submit(client, [
        pt("d1", 0), pt("d1", 120, inside=False),
        pt("d1", 200), pt("d1", 400, inside=False),
        pt("d2", 300), pt("d2", 500, inside=False),
        pt("d3", 350), pt("d3", 600, inside=False),
    ])
    task = create_task(client, fence_id, gap=30, ov=20)
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is True
    assert rep["passed_steps"] == 2
    assert datetime.fromisoformat(rep["steps"][0]["takeover_at"]) == dts(300)
    assert datetime.fromisoformat(rep["steps"][1]["takeover_at"]) == dts(350)
    # 链首选择后一段（起端 200），而非最早的 [0,120)
    assert datetime.fromisoformat(rep["device_results"][0]["interval"]["start"]) == dts(200)


def test_dp_tie_break_lexicographic(client):
    fence_id = create_fence(client)
    # 两条完整链：A 接班 (150,180) 与 B 接班 (550,580)，步数相同取字典序最早 A
    submit(client, [
        pt("d1", 50), pt("d1", 300, inside=False),
        pt("d1", 400), pt("d1", 700, inside=False),
        pt("d2", 150), pt("d2", 200, inside=False),
        pt("d2", 550), pt("d2", 600, inside=False),
        pt("d3", 180), pt("d3", 220, inside=False),
        pt("d3", 580), pt("d3", 620, inside=False),
    ])
    task = create_task(client, fence_id, gap=30, ov=10)
    rep = report(client, fence_id, task["id"])
    assert rep["passed_steps"] == 2
    assert datetime.fromisoformat(rep["steps"][0]["takeover_at"]) == dts(150)
    assert datetime.fromisoformat(rep["steps"][1]["takeover_at"]) == dts(180)


def test_time_progression_skips_earlier_candidate(client):
    fence_id = create_fence(client)
    # d1 [0,300)、d2 [200,400) 接班时刻 200；d3 只有 [120,180)（早于接班时刻）
    # 与 [460,500)（空档超限）-> 链在 d3 终止
    submit(client, [
        pt("d1", 0), pt("d1", 300, inside=False),
        pt("d2", 200), pt("d2", 400, inside=False),
        pt("d3", 120), pt("d3", 180, inside=False),
        pt("d3", 460), pt("d3", 500, inside=False),
    ])
    task = create_task(client, fence_id, gap=30, ov=10)
    rep = report(client, fence_id, task["id"])
    assert rep["passed_steps"] == 1
    step = rep["steps"][1]
    assert step["status"] == "gap_exceeded"
    # [120,180) 不满足时间推进不参与；实际区间取 [460,500)
    assert datetime.fromisoformat(step["to_interval"]["start"]) == dts(460)
    assert datetime.fromisoformat(step["first_violation_at"]) == dts(430)
    assert rep["device_results"][2]["status"] == "failed"


def test_backfill_recomputes_without_old_results(client):
    fence_id = create_fence(client)
    task = create_task(client, fence_id, devices=("d1", "d2"), gap=30, ov=20)

    # 初次：d1 [0,120)、d2 缺席 -> 链在第一步终止
    submit(client, [pt("d1", 0), pt("d1", 120, inside=False)])
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is False
    assert rep["steps"][0]["status"] == "absent"

    # 补录乱序点：d2 在 150 到场，空档 30s 恰好允许 -> 通过
    submit(client, [pt("d2", 150), pt("d2", 300, inside=False)])
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is True
    assert rep["passed_steps"] == 1
    assert rep["first_violation_at"] is None

    # 再补录：d1 实际 110 就已离开（110/115 连续两个界外点确认），
    # 空档变为 150-110=40s 超限 -> 重新失败，旧通过结果不残留
    submit(client, [
        pt("d1", 110, inside=False),
        pt("d1", 115, inside=False),
    ])
    rep = report(client, fence_id, task["id"])
    assert rep["passed"] is False
    step = rep["steps"][0]
    assert step["status"] == "gap_exceeded"
    assert step["gap_s"] == pytest.approx(40.0)
    assert datetime.fromisoformat(step["from_interval"]["end"]) == dts(110)
    assert datetime.fromisoformat(step["first_violation_at"]) == dts(140)


def test_events_scoped_to_fence(client):
    fence_id = create_fence(client)
    r = client.post(
        "/fences",
        json={
            "name": "远端围栏",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[1.0, 1.0], [1.01, 1.0], [1.01, 1.01], [1.0, 1.01], [1.0, 1.0]]
                ],
            },
        },
    )
    other = r.json()["id"]
    submit(client, [
        pt("d1", 0), pt("d1", 500, inside=False),
        pt("d2", 100), pt("d2", 500, inside=False),
    ])
    task = create_task(client, fence_id, devices=("d1", "d2"), ov=1)
    assert report(client, fence_id, task["id"])["passed"] is True

    task_other = create_task(client, other, devices=("d1", "d2"), ov=1)
    rep = report(client, other, task_other["id"])
    # 远端围栏两设备从无进入 -> 链首缺席
    assert rep["passed"] is False
    assert rep["device_results"][0]["status"] == "absent"


def test_delete_fence_removes_relay_tasks(client):
    fence_id = create_fence(client)
    create_task(client, fence_id)
    create_task(client, fence_id, devices=("d4", "d5"), name="接力B")
    assert client.delete(f"/fences/{fence_id}").status_code == 204

    assert client.get(f"/fences/{fence_id}/relay-tasks").status_code == 404
    new_id = create_fence(client)
    assert client.get(f"/fences/{new_id}/relay-tasks").json() == []


# ---------- 引擎级边界 ----------


def engine_report(events, members, start=START_SEC, end=END_SEC, gap=30.0, ov=20.0):
    return compute_relay_report(events, members, start, end, gap, ov)


def test_engine_touching_intervals_zero_gap():
    # a leave@300、b enter@300：半开下端点相接不相交，空档 0；max_gap=0 通过
    events = [
        ("a", EVENT_ENTER, 100), ("a", EVENT_LEAVE, 300),
        ("b", EVENT_ENTER, 300), ("b", EVENT_LEAVE, 500),
    ]
    rep = engine_report(events, ["a", "b"], start=0, gap=0, ov=60)
    assert rep.passed is True
    assert rep.steps[0].gap_s == 0.0
    assert rep.steps[0].overlap_s is None


def test_engine_same_instant_leave_enter_keeps_presence():
    # b 在 t=200 同刻 leave+enter：旧区间止端不含 200、新区间起端含 200。
    # a [100,300) 与 b 的两段交叠分别判定，新区间 [200,400) 交叠 100s 达标
    events = [
        ("a", EVENT_ENTER, 100), ("a", EVENT_LEAVE, 300),
        ("b", EVENT_ENTER, 150), ("b", EVENT_LEAVE, 200),
        ("b", EVENT_ENTER, 200), ("b", EVENT_LEAVE, 400),
    ]
    rep = engine_report(events, ["a", "b"], start=0, gap=0, ov=60)
    assert rep.passed is True
    assert rep.steps[0].takeover_ts == 200
    assert rep.steps[0].overlap_s == pytest.approx(100.0)


def test_engine_window_clipping_open_intervals():
    # 窗口前进入、窗口后仍未离开：两端均截断到窗口
    events = [("a", EVENT_ENTER, 0), ("b", EVENT_ENTER, 150)]
    rep = engine_report(events, ["a", "b"], start=100, end=400, gap=0, ov=100)
    assert rep.passed is True
    assert rep.devices[0].interval == (100, 400)
    assert rep.devices[1].interval == (150, 400)
    assert rep.steps[0].overlap_s == pytest.approx(250.0)

    # 链首区间只在窗口前（leave@100 恰在窗口起点，半开下窗口内无区间）-> 缺席
    events = [("a", EVENT_ENTER, 0), ("a", EVENT_LEAVE, 100),
              ("b", EVENT_ENTER, 200)]
    rep = engine_report(events, ["a", "b"], start=100, end=400, gap=0, ov=10)
    assert rep.passed is False
    assert rep.devices[0].status == "absent"


def test_engine_overlap_failure_precedes_gap_candidate():
    # 接班设备先有交叠不足区间 [150,160)，后有空档超限区间 [460,500)：
    # 交叠最早确定不足（160），优先判 overlap_insufficient
    events = [
        ("a", EVENT_ENTER, 100), ("a", EVENT_LEAVE, 300),
        ("b", EVENT_ENTER, 150), ("b", EVENT_LEAVE, 160),
        ("b", EVENT_ENTER, 460), ("b", EVENT_LEAVE, 500),
    ]
    rep = engine_report(events, ["a", "b"], start=0, gap=30, ov=20)
    step = rep.steps[0]
    assert step.status == "overlap_insufficient"
    assert step.first_violation_ts == 160
    assert step.to_interval == (150, 160)


def test_engine_absent_violation_clamped_to_window_end():
    # a 在 390 才交班结束，空档窗口 30s 超出窗口止端 400 -> 违规时刻截到 400
    events = [("a", EVENT_ENTER, 350), ("a", EVENT_LEAVE, 390)]
    rep = engine_report(events, ["a", "b"], start=100, end=400, gap=30, ov=10)
    assert rep.steps[0].status == "absent"
    assert rep.steps[0].first_violation_ts == 400


def test_engine_lex_tiebreak_on_takeover_vector():
    # d1 单区间；d2、d3 各两段，形成两条 2 步链，接班向量
    # (150,180) 与 (550,580) -> 取前者
    events = [
        ("d1", EVENT_ENTER, 50), ("d1", EVENT_LEAVE, 700),
        ("d2", EVENT_ENTER, 150), ("d2", EVENT_LEAVE, 200),
        ("d2", EVENT_ENTER, 550), ("d2", EVENT_LEAVE, 600),
        ("d3", EVENT_ENTER, 180), ("d3", EVENT_LEAVE, 220),
        ("d3", EVENT_ENTER, 580), ("d3", EVENT_LEAVE, 620),
    ]
    rep = engine_report(events, ["d1", "d2", "d3"], start=0, end=800, gap=30, ov=10)
    assert rep.passed_steps == 2
    assert [s.takeover_ts for s in rep.steps] == [150, 180]
