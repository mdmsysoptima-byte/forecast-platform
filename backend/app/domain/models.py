"""Application data model (section 4 of the specification).

Every number is addressed by (series_key, bucket_id, layer_id, version_id).
Large fact tables (actual, run_result, snapshot_value, rule_effect) live in the
analytic store, not here. See app/adapters/.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Stamped:
    """Audit stamps required on every table by the specification."""

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------
# Dimensions and hierarchies
# --------------------------------------------------------------------------


class Dimension(Base, Stamped):
    __tablename__ = "dimension"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(32))  # product, geo, channel, customer, custom
    position: Mapped[int] = mapped_column(Integer, default=0)
    hierarchies: Mapped[list["Hierarchy"]] = relationship(back_populates="dimension")


class Hierarchy(Base, Stamped):
    __tablename__ = "hierarchy"
    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    levels: Mapped[list[str]] = mapped_column(JSON, default=list)
    dimension: Mapped[Dimension] = relationship(back_populates="hierarchies")
    __table_args__ = (UniqueConstraint("dimension_id", "code"),)


class Member(Base, Stamped):
    __tablename__ = "member"
    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("dimension_id", "code"),)


class HierarchyNode(Base, Stamped):
    """Tree node. `path` stands in for Postgres ltree (dotted node ids)."""

    __tablename__ = "hierarchy_node"
    id: Mapped[int] = mapped_column(primary_key=True)
    hierarchy_id: Mapped[int] = mapped_column(ForeignKey("hierarchy.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"))
    parent_node_id: Mapped[int | None] = mapped_column(ForeignKey("hierarchy_node.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(64))
    level_index: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(512), index=True)
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_node_hier_level", "hierarchy_id", "level_index"),)


class UserGroup(Base, Stamped):
    __tablename__ = "user_group"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)


class UserGroupMember(Base, Stamped):
    __tablename__ = "user_group_member"
    group_id: Mapped[int] = mapped_column(ForeignKey("user_group.id"), primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), primary_key=True)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


class Calendar(Base, Stamped):
    __tablename__ = "calendar"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(32), default="gregorian")  # gregorian, fiscal, 445
    fiscal_start_month: Mapped[int] = mapped_column(Integer, default=1)
    week_start_day: Mapped[int] = mapped_column(Integer, default=1)  # 1 = monday


class TimeBucket(Base, Stamped):
    __tablename__ = "time_bucket"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendar.id"))
    grain: Mapped[str] = mapped_column(String(16))  # hour, day, week, month, quarter, year
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    label: Mapped[str] = mapped_column(String(32))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    parent_bucket_id: Mapped[int | None] = mapped_column(ForeignKey("time_bucket.id"), nullable=True)
    working_days: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        UniqueConstraint("calendar_id", "grain", "start_date"),
        Index("ix_bucket_cal_grain_seq", "calendar_id", "grain", "seq"),
    )


class CalendarEvent(Base, Stamped):
    __tablename__ = "calendar_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendar.id"))
    type: Mapped[str] = mapped_column(String(32))  # holiday, bridge, ramadan, kurban, custom
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(128))
    is_shifting: Mapped[bool] = mapped_column(Boolean, default=False)


class Cycle(Base, Stamped):
    __tablename__ = "cycle"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendar.id"))
    grain: Mapped[str] = mapped_column(String(16), default="month")
    horizon_periods: Mapped[int] = mapped_column(Integer, default=18)
    fence_periods: Mapped[int] = mapped_column(Integer, default=1)
    freeze_periods: Mapped[int] = mapped_column(Integer, default=2)
    current_period_bucket_id: Mapped[int] = mapped_column(ForeignKey("time_bucket.id"))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open, review, approved, locked
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# Series and reference data
# --------------------------------------------------------------------------


class Measure(Base, Stamped):
    __tablename__ = "measure"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    unit_type: Mapped[str] = mapped_column(String(16))  # quantity, value, weight
    base_unit: Mapped[str] = mapped_column(String(32))
    formula: Mapped[str | None] = mapped_column(String(256), nullable=True)


class UnitConversion(Base, Stamped):
    __tablename__ = "unit_conversion"
    id: Mapped[int] = mapped_column(primary_key=True)
    from_unit: Mapped[str] = mapped_column(String(32))
    to_unit: Mapped[str] = mapped_column(String(32))
    factor: Mapped[float] = mapped_column(Float)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("member.id"), nullable=True)


class Series(Base, Stamped):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    dim_members: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    dim_codes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    measure_id: Mapped[int] = mapped_column(ForeignKey("measure.id"))
    grain: Mapped[str] = mapped_column(String(16), default="month")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SeriesNode(Base):
    """Materialised series to tree-node map; makes scope filtering a single join."""

    __tablename__ = "series_node"
    series_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hierarchy_id: Mapped[int] = mapped_column(ForeignKey("hierarchy.id"), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("hierarchy_node.id"), primary_key=True)
    level_index: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_series_node_node", "node_id"),)


class FxRate(Base, Stamped):
    __tablename__ = "fx_rate"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    from_ccy: Mapped[str] = mapped_column(String(8))
    to_ccy: Mapped[str] = mapped_column(String(8))
    rate: Mapped[float] = mapped_column(Float)


class PriceIndex(Base, Stamped):
    __tablename__ = "price_index"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    index_code: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)


# --------------------------------------------------------------------------
# Drivers and scenarios
# --------------------------------------------------------------------------


class Driver(Base, Stamped):
    __tablename__ = "driver"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="manual")  # manual, table, api
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    grain: Mapped[str] = mapped_column(String(16), default="month")
    scope_dimension_id: Mapped[int | None] = mapped_column(ForeignKey("dimension.id"), nullable=True)


class DriverValue(Base, Stamped):
    __tablename__ = "driver_value"
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"))
    bucket_id: Mapped[int] = mapped_column(ForeignKey("time_bucket.id"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("member.id"), nullable=True)
    value: Mapped[float] = mapped_column(Float)
    is_actual: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_driver_value_lookup", "driver_id", "bucket_id", "member_id"),)


class DriverSet(Base, Stamped):
    __tablename__ = "driver_set"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=True)


class DriverSetValue(Base, Stamped):
    __tablename__ = "driver_set_value"
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_set_id: Mapped[int] = mapped_column(ForeignKey("driver_set.id"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"))
    bucket_id: Mapped[int] = mapped_column(ForeignKey("time_bucket.id"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("member.id"), nullable=True)
    value: Mapped[float] = mapped_column(Float)
    entry_method: Mapped[str] = mapped_column(String(16), default="constant")
    __table_args__ = (Index("ix_dsv_lookup", "driver_set_id", "driver_id", "bucket_id"),)


class Scenario(Base, Stamped):
    __tablename__ = "scenario"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    base_version_id: Mapped[int | None] = mapped_column(ForeignKey("version.id"), nullable=True)
    driver_set_id: Mapped[int | None] = mapped_column(ForeignKey("driver_set.id"), nullable=True)
    model_registry_id: Mapped[int] = mapped_column(ForeignKey("model_registry.id"))
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycle.id"), nullable=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    grain: Mapped[str] = mapped_column(String(16), default="month")
    level: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    horizon_periods: Mapped[int] = mapped_column(Integer, default=18)
    model_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)


class ScenarioRule(Base, Stamped):
    __tablename__ = "scenario_rule"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id"))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128), default="")
    target_scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bucket_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bucket_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# --------------------------------------------------------------------------
# Runs and versions
# --------------------------------------------------------------------------


class ModelRegistry(Base, Stamped):
    __tablename__ = "model_registry"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    package_path: Mapped[str] = mapped_column(String(512))
    supported_grains: Mapped[list[str]] = mapped_column(JSON, default=list)
    supported_levels: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    required_drivers: Mapped[list[str]] = mapped_column(JSON, default=list)
    optional_drivers: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_has_interval: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")


class Run(Base, Stamped):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id"))
    model_registry_id: Mapped[int] = mapped_column(ForeignKey("model_registry.id"))
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycle.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    scope_hash: Mapped[str] = mapped_column(String(64), default="")
    started_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    series_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")


class Version(Base, Stamped):
    __tablename__ = "version"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), nullable=True)
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenario.id"), nullable=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycle.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="draft")
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("version.id"), nullable=True)
    # Frozen provenance chain: scenario definition, driver set, rules, model version, data date.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Layer(Base, Stamped):
    __tablename__ = "layer"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    is_editable: Mapped[bool] = mapped_column(Boolean, default=False)
    edit_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    provenance: Mapped[str] = mapped_column(String(16), default="model")  # actual, model, human, budget


class LayerValue(Base):
    """Base level values only. Upper levels are aggregated on read."""

    __tablename__ = "layer_value"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id"))
    layer_id: Mapped[int] = mapped_column(ForeignKey("layer.id"))
    series_key: Mapped[str] = mapped_column(String(64))
    bucket_id: Mapped[int] = mapped_column(ForeignKey("time_bucket.id"))
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="model")  # model, user, allocation, rule
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("version_id", "layer_id", "series_key", "bucket_id", name="uq_layer_value"),
        Index("ix_layer_value_read", "version_id", "layer_id", "bucket_id"),
    )


# --------------------------------------------------------------------------
# Override, approval, traceability
# --------------------------------------------------------------------------


class ReasonCode(Base, Stamped):
    __tablename__ = "reason_code"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    sector_tag: Mapped[str] = mapped_column(String(32), default="")
    requires_note: Mapped[bool] = mapped_column(Boolean, default=False)


class Override(Base, Stamped):
    __tablename__ = "override"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id"))
    layer_id: Mapped[int] = mapped_column(ForeignKey("layer.id"))
    series_key: Mapped[str] = mapped_column(String(64))
    bucket_id: Mapped[int] = mapped_column(ForeignKey("time_bucket.id"))
    old_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_value: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16), default="absolute")  # absolute, pct, delta
    reason_code_id: Mapped[int] = mapped_column(ForeignKey("reason_code.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    attachment_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    valid_until_bucket_id: Mapped[int | None] = mapped_column(ForeignKey("time_bucket.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    entry_level: Mapped[str] = mapped_column(String(64), default="base")
    entry_node_label: Mapped[str] = mapped_column(String(256), default="")
    allocated_from_override_id: Mapped[int | None] = mapped_column(ForeignKey("override.id"), nullable=True)
    reverted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_override_cell", "version_id", "layer_id", "series_key", "bucket_id"),)


class Insight(Base, Stamped):
    __tablename__ = "insight"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    series_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bucket_from_id: Mapped[int | None] = mapped_column(ForeignKey("time_bucket.id"), nullable=True)
    bucket_to_id: Mapped[int | None] = mapped_column(ForeignKey("time_bucket.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    reason_code_id: Mapped[int | None] = mapped_column(ForeignKey("reason_code.id"), nullable=True)
    attachment_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # correct, wrong, unknown


class Approval(Base, Stamped):
    __tablename__ = "approval"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("version.id"))
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycle.id"), nullable=True)
    step: Mapped[str] = mapped_column(String(16))  # draft, review, approved, locked
    actor_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    decision: Mapped[str] = mapped_column(String(16), default="ok")
    comment: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user")  # user, assistant, system
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Alert(Base, Stamped):
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    series_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bucket_id: Mapped[int | None] = mapped_column(ForeignKey("time_bucket.id"), nullable=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("version.id"), nullable=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycle.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open, ack, closed


# --------------------------------------------------------------------------
# Users and permissions
# --------------------------------------------------------------------------


class User(Base, Stamped):
    """Table named app_user because `user` is reserved in PostgreSQL."""

    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    locale: Mapped[str] = mapped_column(String(8), default="tr-TR")
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base, Stamped):
    __tablename__ = "role"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class UserRole(Base):
    __tablename__ = "user_role"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), primary_key=True)


class RowPermission(Base, Stamped):
    __tablename__ = "row_permission"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    hierarchy_node_id: Mapped[int] = mapped_column(ForeignKey("hierarchy_node.id"))
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False)


class SavedView(Base, Stamped):
    __tablename__ = "saved_view"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    name: Mapped[str] = mapped_column(String(128))
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SectorConfig(Base, Stamped):
    """Loaded sector YAML, kept for the assistant system prompt and admin screen."""

    __tablename__ = "sector_config"
    id: Mapped[int] = mapped_column(primary_key=True)
    sector: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScheduledReport(Base, Stamped):
    __tablename__ = "scheduled_report"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_code: Mapped[str] = mapped_column(String(64))
    cron: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recipients: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
