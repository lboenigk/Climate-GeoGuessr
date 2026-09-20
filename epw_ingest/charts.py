"""Plotly renderers for each chart in the catalogue.

Build-time only - importing this pulls in ladybug, ladybug-charts and plotly.
The served app imports `catalog` instead, which is why the axis ranges and the
chart list live there rather than here.
"""
from __future__ import annotations

from typing import Callable, Dict

from ladybug.epw import EPW
from ladybug.psychchart import PsychrometricChart
from ladybug.sunpath import Sunpath
from ladybug.windrose import WindRose
from ladybug_charts.to_figure import (
    diurnal_average_chart,
    heat_map,
    psych_chart,
    sunpath,
    wind_rose,
)

from epw_ingest.catalog import (
    HUMIDITY_RANGE,
    MAX_HUMIDITY_RATIO,
    RADIATION_RANGE,
    TEMP_RANGE,
)


def _psychrometric(epw: EPW):
    chart = PsychrometricChart.from_epw(
        epw.file_path,
        min_temperature=TEMP_RANGE[0],
        max_temperature=TEMP_RANGE[1],
        max_humidity_ratio=MAX_HUMIDITY_RATIO,
    )
    return psych_chart(chart, show_title=False)


def _temperature_heatmap(epw: EPW):
    return heat_map(epw.dry_bulb_temperature, min_range=TEMP_RANGE[0],
                    max_range=TEMP_RANGE[1], show_title=False)


def _humidity_heatmap(epw: EPW):
    return heat_map(epw.relative_humidity, min_range=HUMIDITY_RANGE[0],
                    max_range=HUMIDITY_RANGE[1], show_title=False)


def _radiation_heatmap(epw: EPW):
    return heat_map(epw.global_horizontal_radiation, min_range=RADIATION_RANGE[0],
                    max_range=RADIATION_RANGE[1], show_title=False)


def _diurnal(epw: EPW):
    return diurnal_average_chart(epw, show_title=False)


def _wind_rose(epw: EPW):
    return wind_rose(WindRose(epw.wind_direction, epw.wind_speed, direction_count=16),
                     show_title=False)


def _sunpath(epw: EPW):
    return sunpath(
        Sunpath.from_location(epw.location),
        data=epw.global_horizontal_radiation,
        min_range=RADIATION_RANGE[0],
        max_range=RADIATION_RANGE[1],
        show_title=False,
    )


BUILDERS: Dict[str, Callable[[EPW], object]] = {
    "psychrometric": _psychrometric,
    "temperature": _temperature_heatmap,
    "humidity": _humidity_heatmap,
    "radiation": _radiation_heatmap,
    "diurnal": _diurnal,
    "windrose": _wind_rose,
    "sunpath": _sunpath,
}


def build(chart_id: str, epw: EPW):
    return BUILDERS[chart_id](epw)
