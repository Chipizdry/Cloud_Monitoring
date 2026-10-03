import os

os.environ["DEBUG"] = "false"

from backend.repository.energy.demo_solar_battery_v2 import (  # noqa: E402
    SimpleAlgorithmConfig,
    _simulate_simple_correction,
)


def test_battery_waits_for_first_generation_but_supports_daytime_zero():
    config = SimpleAlgorithmConfig(
        battery_capacity_kwh=100,
        support_power_kw=100,
        support_peak_minutes=10,
        correction_threshold_percent=20,
        min_soc_percent=20,
        recalculation_period_minutes=4,
    )
    raw_values = [0, 0, 80, 20, 0, 20, 0, 0]

    object_result = _simulate_simple_correction(raw_values, config=config)
    global_result = _simulate_simple_correction(
        object_result.corrected_values, config=config
    )

    for result in (object_result, global_result):
        for index in (0, 1):
            assert result.target_values[index] == 0
            assert result.corrected_values[index] == 0
            assert result.battery_discharge_values[index] == 0
            assert result.battery_power_values[index] == 0
            assert result.battery_soc_values[index] == 100

    # После первой генерации АКБ поддерживает цель даже при полном провале солнца.
    assert raw_values[4] == 0
    assert object_result.target_values[4] > 0
    assert object_result.battery_discharge_values[4] > 0
    assert object_result.corrected_values[4] > 0
    assert global_result.corrected_values[4] > 0

    # Поддержка при частичном падении солнечной отдачи также сохраняется.
    assert object_result.battery_discharge_values[3] > 0
    assert object_result.corrected_values[3] > raw_values[3]
