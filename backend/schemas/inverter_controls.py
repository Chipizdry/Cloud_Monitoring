from __future__ import annotations
from typing import Optional
from pydantic import Field
from backend.schemas.base import BaseSchema



class VebusSOCControl(BaseSchema):
    soc_threshold: int


class EssAdvancedControl(BaseSchema):
    ac_power_setpoint_fine: int = Field(..., ge=-100000, le=100000)


class GridLimitUpdate(BaseSchema):
    enabled: bool  # True → 1, False → 0


class EssModeControl(BaseSchema):
    switch_position: int = Field(..., ge=1, le=4)


class EssPowerControl(BaseSchema):
    ess_power_setpoint_l1: Optional[int] = Field(None, ge=-32768, le=32767)
    ess_power_setpoint_l2: Optional[int] = Field(None, ge=-32768, le=32767)
    ess_power_setpoint_l3: Optional[int] = Field(None, ge=-32768, le=32767)


class EssFeedInControl(BaseSchema):
    max_feed_in_l1: Optional[int] = None
    max_feed_in_l2: Optional[int] = None
    max_feed_in_l3: Optional[int] = None



class RegisterWriteRequest(BaseSchema):
    slave_id: int
    register_number: int
    value: int


class InverterPowerPayload(BaseSchema):
    inverter_power: float

class DVCCMaxChargeCurrentRequest(BaseSchema):
    current_limit: int = Field(
        ..., ge=-1, le=32767, description="DVCCMaxChargeCurrent (-1 или положительное значение до 32767)"
    )


