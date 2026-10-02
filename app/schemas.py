from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FenceConfigSchema(BaseModel):
    """围栏判定参数。enter_buffer_m 必须 <= exit_buffer_m 才能构成有效迟滞带。"""

    enter_buffer_m: float = Field(0.0, ge=0, description="进入缓冲距离（米）")
    exit_buffer_m: float = Field(0.0, ge=0, description="离开缓冲距离（米）")
    enter_dwell_s: float = Field(0.0, ge=0, description="进入确认停留时长（秒）")
    exit_dwell_s: float = Field(0.0, ge=0, description="离开确认停留时长（秒）")
    stay_min_s: float = Field(300.0, ge=0, description="产生 stay 事件的最小连续停留时长（秒）")

    @model_validator(mode="after")
    def check_buffers(self):
        if self.enter_buffer_m > self.exit_buffer_m:
            raise ValueError("enter_buffer_m 必须小于等于 exit_buffer_m")
        return self


class FenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geometry: dict = Field(description="GeoJSON Polygon，coordinates[0] 为外环，其余为孔洞")
    config: FenceConfigSchema = FenceConfigSchema()


class FenceOut(BaseModel):
    id: int
    name: str
    geometry: dict
    config: FenceConfigSchema
    created_at: datetime


class PointIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)
    ts: datetime = Field(description="ISO 8601 时间戳，缺省时区按 UTC 处理")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PointBatch(BaseModel):
    points: list[PointIn] = Field(min_length=1, max_length=10000)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    devices_updated: int


class EventOut(BaseModel):
    id: int
    device_id: str
    fence_id: int
    type: str
    ts: datetime


class FenceStateOut(BaseModel):
    fence_id: int
    fence_name: str
    state: Literal["inside", "outside"]
    since: datetime | None


class DeviceStatusOut(BaseModel):
    device_id: str
    fences: list[FenceStateOut]


class CapacityRuleCreate(BaseModel):
    effective_from: datetime = Field(description="规则生效时刻（ISO 8601，含该时刻）")
    max_occupancy: int = Field(gt=0, description="容量上限（正整数）")


class CapacityRuleOut(BaseModel):
    id: int
    fence_id: int
    effective_from: datetime
    max_occupancy: int
    created_at: datetime


class OccupancySegmentOut(BaseModel):
    start: datetime
    end: datetime
    count: int = Field(description="分段内在场设备数")
    limit: int | None = Field(description="分段内生效的容量上限；无规则时为 null")
    exceeded: bool = Field(description="是否超限（count > limit）")
    excess: int | None = Field(description="超额人数，仅超限分段有值")
    devices: list[str] | None = Field(description="在场设备（按 ID 排序），仅超限分段有值")


class OccupancyOut(BaseModel):
    fence_id: int
    start: datetime
    end: datetime
    segments: list[OccupancySegmentOut]


class GateLineCreate(BaseModel):
    geometry: dict = Field(
        description="GeoJSON LineString，至少两个不同坐标；整条线必须位于围栏有效区域内"
    )
    forward_name: str = Field(min_length=1, max_length=50, description="正向（右->左）名称")
    reverse_name: str = Field(min_length=1, max_length=50, description="反向（左->右）名称")
    min_displacement_m: float = Field(
        0.0, ge=0, description="相邻轨迹点最小位移（米），不足则忽略该次穿越"
    )


class GateLineOut(BaseModel):
    id: int
    fence_id: int
    geometry: dict
    forward_name: str
    reverse_name: str
    min_displacement_m: float
    created_at: datetime


class CrossingOut(BaseModel):
    id: int
    device_id: str
    fence_id: int
    gate_id: int
    direction: Literal["forward", "reverse"]
    direction_name: str = Field(description="方向名称（取门线的正向/反向名称）")
    ts: datetime = Field(description="按交点比例插值得到的穿越时刻")
    lat: float = Field(description="交点纬度")
    lon: float = Field(description="交点经度")


class ClearanceTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="清场任务名称")
    start_at: datetime = Field(description="清场开始时刻（ISO 8601），起始名单边界含该时刻")
    deadline: datetime = Field(description="清场截止时刻（ISO 8601），必须晚于开始时刻")


class ClearanceTaskOut(BaseModel):
    id: int
    fence_id: int
    name: str
    start_at: datetime
    deadline: datetime
    created_at: datetime


class ClearanceDeviceOut(BaseModel):
    device_id: str
    status: Literal["cleared", "remaining"] = Field(description="按时清场 / 截止时仍在场")
    last_leave_at: datetime | None = Field(
        description="cleared 时为最后有效离开时刻；remaining 为 null"
    )


class ClearanceSummaryOut(BaseModel):
    start_count: int = Field(description="起始名单人数")
    cleared_count: int = Field(description="按时清场人数")
    remaining_count: int = Field(description="截止时未清场人数")


class ClearanceReportOut(ClearanceTaskOut):
    summary: ClearanceSummaryOut
    devices: list[ClearanceDeviceOut] = Field(description="起始名单设备的核验结果，按 ID 排序")


# ---------- 有序门线巡检路线 ----------


class InspectionCheckpointCreate(BaseModel):
    gate_id: int = Field(description="已有门线 ID，必须属于路线所属围栏")
    direction: Literal["forward", "reverse"] = Field(description="要求的穿越方向")
    max_interval_s: float | None = Field(
        default=None,
        ge=0,
        description="与上一检查点的最长允许间隔（秒）；首点必须省略（null）",
    )


class InspectionRouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="巡检路线名称")
    checkpoints: list[InspectionCheckpointCreate] = Field(
        min_length=2, max_length=8, description="按顺序排列的门线检查点（2～8 个）"
    )

    @model_validator(mode="after")
    def check_intervals_and_gates(self):
        if self.checkpoints[0].max_interval_s is not None:
            raise ValueError("首个检查点不设与上一点的间隔，max_interval_s 必须省略")
        for cp in self.checkpoints[1:]:
            if cp.max_interval_s is None:
                raise ValueError("除首个检查点外，每项都必须给出 max_interval_s")
        gate_ids = [cp.gate_id for cp in self.checkpoints]
        if len(set(gate_ids)) != len(gate_ids):
            raise ValueError("路线内门线不得重复")
        return self


class InspectionCheckpointOut(BaseModel):
    position: int = Field(description="检查点序号（从 0 起）")
    gate_id: int
    direction: Literal["forward", "reverse"]
    direction_name: str = Field(description="要求方向的名称（取门线的正向/反向名称）")
    max_interval_s: float | None


class InspectionRouteOut(BaseModel):
    id: int
    fence_id: int
    name: str
    checkpoints: list[InspectionCheckpointOut] = Field(description="按顺序排列的检查点")
    created_at: datetime


class InspectionCheckpointResultOut(BaseModel):
    position: int = Field(description="检查点序号（从 0 起）")
    gate_id: int
    direction: Literal["forward", "reverse"]
    direction_name: str
    status: Literal["passed", "timeout", "sequence_violation", "not_reached"] = Field(
        description="passed=按要求通过；timeout=超过最长间隔；"
                    "sequence_violation=顺序违规（反向穿越或后续门线先被穿越）；"
                    "not_reached=此前已失败或窗口结束仍未到达"
    )
    passed_at: datetime | None = Field(description="通过时刻；未通过为 null")
    failed_at: datetime | None = Field(
        description="timeout 为间隔截止时刻；sequence_violation 为违规穿越时刻；其余为 null"
    )


class InspectionReportOut(InspectionRouteOut):
    device_id: str
    start: datetime
    end: datetime
    passed: bool = Field(description="全部检查点按序、按方向且未超间隔地通过")
    first_violation_at: datetime | None = Field(description="首个违规时刻；全部通过为 null")
    results: list[InspectionCheckpointResultOut] = Field(
        description="每个检查点的通过时刻或失败原因，按顺序排列"
    )


# ---------- 协同到场核验 ----------


class RendezvousTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="协同任务名称")
    members: list[str] = Field(
        min_length=2, max_length=8, description="协同设备 ID 列表（2～8 台，不得重复）"
    )
    start_at: datetime = Field(description="有效窗口开始时刻（ISO 8601），含该时刻")
    end_at: datetime = Field(description="有效窗口结束时刻（ISO 8601，不含，必须晚于开始")
    min_co_presence_s: float = Field(
        gt=0, description="最短共同在场时长（秒，正数）；等于该时长即算达标"
    )

    @model_validator(mode="after")
    def check_members_and_window(self):
        if any(not m.strip() for m in self.members):
            raise ValueError("设备 ID 不得为空")
        if len(set(self.members)) != len(self.members):
            raise ValueError("协同设备 ID 不得重复")
        if not self.start_at < self.end_at:
            raise ValueError("开始时刻必须早于结束时刻")
        return self


class RendezvousTaskOut(BaseModel):
    id: int
    fence_id: int
    name: str
    members: list[str] = Field(description="协同设备 ID（按提交顺序）")
    start_at: datetime
    end_at: datetime
    min_co_presence_s: float
    created_at: datetime


class RendezvousSegmentOut(BaseModel):
    start: datetime = Field(description="共同在场片段开始时刻（半开，含）")
    end: datetime = Field(description="共同在场片段结束时刻（半开，不含）")
    duration_s: float = Field(description="片段持续秒数")
    members: list[str] = Field(description="共同在场成员（即全部任务成员，按 ID 排序）")


class RendezvousReportOut(RendezvousTaskOut):
    passed: bool = Field(description="是否存在窗口内持续达到最短时长的共同在场片段")
    segments: list[RendezvousSegmentOut] = Field(
        description="达标的共同在场连续片段，按起止时刻升序；无达标片段为空数组"
    )


# ---------- 设备接力交班核验 ----------


class RelayTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="接力交班任务名称")
    devices: list[str] = Field(
        min_length=2, max_length=8,
        description="按交班顺序排列的设备 ID 列表（2～8 台，不得重复）",
    )
    start_at: datetime = Field(description="有效窗口开始时刻（ISO 8601），含该时刻")
    end_at: datetime = Field(description="有效窗口结束时刻（ISO 8601），不含，必须晚于开始")
    max_gap_s: float = Field(
        ge=0, description="相邻交班允许的最大空档（秒，非负）；空档等于该值即算允许"
    )
    min_overlap_s: float = Field(
        ge=0, description="相邻交班要求的最短交叠时长（秒，非负）；交叠等于该值即算达标"
    )

    @model_validator(mode="after")
    def check_devices_and_window(self):
        if any(not d.strip() for d in self.devices):
            raise ValueError("设备 ID 不得为空")
        if len(set(self.devices)) != len(self.devices):
            raise ValueError("交班设备 ID 不得重复")
        if not self.start_at < self.end_at:
            raise ValueError("开始时刻必须早于结束时刻")
        return self


class RelayTaskOut(BaseModel):
    id: int
    fence_id: int
    name: str
    devices: list[str] = Field(description="按交班顺序排列的设备 ID")
    start_at: datetime
    end_at: datetime
    max_gap_s: float
    min_overlap_s: float
    created_at: datetime


class RelayIntervalOut(BaseModel):
    start: datetime = Field(description="半开在场区间起端（已截断到有效窗口，含）")
    end: datetime = Field(description="半开在场区间止端（已截断到有效窗口，不含）")


class RelayDeviceOut(BaseModel):
    position: int = Field(description="交班顺序序号（从 0 起）")
    device_id: str
    status: Literal["present", "failed", "absent", "not_reached"] = Field(
        description="present=已选入交班链；failed=在场但该步交班未达标；"
                    "absent=窗口内缺席（或无满足时间推进的区间）；"
                    "not_reached=交班链此前已终止，不参与核验"
    )
    interval: RelayIntervalOut | None = Field(
        description="该设备在交班链中的实际（截断）区间；absent/not_reached 为 null"
    )


class RelayStepOut(BaseModel):
    position: int = Field(description="交班步序号（从 0 起：devices[position] -> devices[position+1]）")
    from_device: str
    to_device: str
    status: Literal[
        "passed", "absent", "gap_exceeded", "overlap_insufficient", "not_reached"
    ] = Field(
        description="passed=交叠达标或空档不超限；absent=接班设备缺席；"
                    "gap_exceeded=分离且空档超限；"
                    "overlap_insufficient=相交但交叠不足；not_reached=此前已失败"
    )
    takeover_at: datetime | None = Field(
        description="接班时刻（接班设备实际区间起端）；缺席/未到达为 null"
    )
    from_interval: RelayIntervalOut | None = Field(description="交班设备实际区间；未到达为 null")
    to_interval: RelayIntervalOut | None = Field(
        description="接班设备实际区间；缺席/未到达为 null"
    )
    overlap_s: float | None = Field(
        description="实际交叠秒数；两区间相交时给出（半开：起端严格早于交班区间止端）"
    )
    gap_s: float | None = Field(
        description="实际空档秒数；两区间分离（含端点相接）时给出"
    )
    first_violation_at: datetime | None = Field(
        description="该步首个违规时刻；达标/未到达为 null"
    )


class RelayReportOut(RelayTaskOut):
    passed_steps: int = Field(description="交班链中通过的相邻交班步数")
    passed: bool = Field(description="全部相邻交班步是否通过")
    first_violation_at: datetime | None = Field(
        description="整条链的首个违规时刻；全部通过为 null"
    )
    device_results: list[RelayDeviceOut] = Field(
        description="按交班顺序的每台设备实际区间与状态"
    )
    steps: list[RelayStepOut] = Field(description="每个相邻交班步的实际区间与状态")
