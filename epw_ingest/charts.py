"""Plotly renderers for each chart in the catalogue.

Build-time only - importing this pulls in ladybug, ladybug-charts and plotly.
The served app imports `catalog` instead, which is why the axis ranges and the
chart list live there rather than here.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import plotly.graph_objects as go
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
    PRECIP_RANGE,
    RADIATION_RANGE,
    TEMP_RANGE,
)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _psychrometric(epw: EPW, **_):
    chart = PsychrometricChart.from_epw(
        epw.file_path,
        min_temperature=TEMP_RANGE[0],
        max_temperature=TEMP_RANGE[1],
        max_humidity_ratio=MAX_HUMIDITY_RATIO,
    )
    return psych_chart(chart, show_title=False)


def _temperature_heatmap(epw: EPW, **_):
    return heat_map(epw.dry_bulb_temperature, min_range=TEMP_RANGE[0],
                    max_range=TEMP_RANGE[1], show_title=False)


def _humidity_heatmap(epw: EPW, **_):
    return heat_map(epw.relative_humidity, min_range=HUMIDITY_RANGE[0],
                    max_range=HUMIDITY_RANGE[1], show_title=False)


def _radiation_heatmap(epw: EPW, **_):
    return heat_map(epw.global_horizontal_radiation, min_range=RADIATION_RANGE[0],
                    max_range=RADIATION_RANGE[1], show_title=False)


def _diurnal(epw: EPW, **_):
    return diurnal_average_chart(epw, show_title=False)


def _wind_rose(epw: EPW, **_):
    return wind_rose(WindRose(epw.wind_direction, epw.wind_speed, direction_count=16),
                     show_title=False)


def _precipitation(epw: EPW, precip: Optional[List[float]] = None, **_):
    """Monthly rainfall totals as a bar chart.

    Hand-built rather than routed through ladybug's monthly_bar_chart for two
    reasons. The 999 missing sentinel has to be dropped *before* the monthly
    sum or a station with a few blank hours reads as a rainforest, and the
    caller has already done that filtering — see build_assets._monthly_precip.
    And a ladybug collection carries the EPW header along with it, so the
    figure would arrive needing the sanitizer's help; built from twelve floats,
    there is nothing in it to leak.

    The y-axis is pinned to PRECIP_RANGE for the same reason every other chart
    is pinned: an auto-scaled axis makes a 40 mm month and a 400 mm month draw
    identically, which both leaks the answer and makes rounds incomparable.
    """
    if not precip:
        raise ValueError("precipitation chart requires monthly totals")

    fig = go.Figure(
        go.Bar(
            x=list(MONTHS),
            y=list(precip),
            marker_color="#3d7ea6",
            hovertemplate="%{x}: %{y:.1f} mm<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_white",
        showlegend=False,
        bargap=0.25,
        margin=dict(l=60, r=30, t=20, b=50),
    )
    fig.update_xaxes(title_text="Month", fixedrange=True)
    fig.update_yaxes(
        title_text="Precipitation (mm)",
        range=list(PRECIP_RANGE),
        dtick=100,            # gridlines a player can count against
        fixedrange=True,      # zooming out would defeat the fixed range
    )
    return fig


def _sunpath(epw: EPW, **_):
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
    "precipitation": _precipitation,
}


def build(chart_id: str, epw: EPW, **ctx):
    """Render one chart.

    `ctx` carries anything the EPW alone cannot supply — currently just
    `precip`, which the caller has already sentinel-filtered. Every builder
    takes `**_` so it can be passed unconditionally; catching TypeError here
    instead would silently swallow a genuine type error inside a renderer and
    retry it with different arguments.
    """
    return BUILDERS[chart_id](epw, **ctx)
