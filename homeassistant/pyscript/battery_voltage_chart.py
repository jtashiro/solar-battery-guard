# Renders a PNG line chart of an entity's recent history to a local file,
# for embedding in the AC charger ON/OFF email alerts (see
# ../solar_battery_guard.yaml). Runs entirely locally on this HA instance -
# no external charting service, no battery data leaves the network.
#
# ONE-TIME SETUP (can't be done via a YAML package - pyscript scripts are
# plain .py files HA loads from a fixed folder):
#   1. Install the "pyscript" custom integration (HACS, or manually into
#      /config/custom_components/pyscript).
#   2. Add to configuration.yaml:
#        pyscript:
#          allow_all_imports: true
#          hass_is_global: true
#   3. Copy this file to /config/pyscript/battery_voltage_chart.py (create
#      the pyscript/ folder under /config if it doesn't exist yet).
#   4. Restart Home Assistant - pyscript auto-installs the "matplotlib"
#      requirement declared below on first load. On a Docker install this
#      only survives until the container is recreated (image update,
#      `docker-compose up --force-recreate`, etc.) unless matplotlib is
#      baked into a custom image - see the chat history / README for the
#      Dockerfile approach if you want it to survive updates.
# requirements: matplotlib

import os
from datetime import datetime as dt, timedelta, timezone


@pyscript_compile
def _render_chart_sync(points, output_path, tz_name, title):
    # All matplotlib work (import, plotting, file I/O) is deliberately
    # kept inside this plain function, only ever invoked via
    # task.executor() below - matplotlib's import, dateutil's tzdata file
    # read, and the PNG file write are all blocking calls that HA's event
    # loop watchdog flags (and slows other things down for) if run
    # directly in the async service function instead.
    #
    # @pyscript_compile is required for this to work with task.executor()
    # at all: pyscript interprets every top-level def in this file as its
    # own "pyscript function" by default, which task.executor() rejects
    # ("must be a regular python function") - the decorator makes this one
    # function compile as native Python instead.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from zoneinfo import ZoneInfo

    # savefig() does not create missing parent directories - on a fresh
    # HA install /config/www may not exist yet until something else
    # (e.g. the Local Media frontend panel) creates it.
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 3), dpi=150)
    if points:
        times = [p[0] for p in points]
        values = [p[1] for p in points]
        ax.plot(times, values, color="#3b82f6", linewidth=1.5)
        # DateFormatter renders in UTC unless told otherwise - it does not
        # inherit the tzinfo of the plotted datetimes, so this tz= is what
        # actually controls the displayed labels, regardless of what
        # timezone `times` are already in.
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=ZoneInfo(tz_name)))
        ax.set_title(title)
        ax.set_ylabel("Battery Voltage (V)")
        ax.set_xlabel("Time of Day")
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


@service
def render_battery_voltage_chart(entity_id=None, hours=6, output_path=None, voltage_source_entity_id=None):
    """yaml
name: Render Battery Voltage Chart
description: >
  Renders a PNG line chart of an entity's recent numeric history to a
  local file under /config/www, for attaching to an email via
  notify.homeassistant's "images" option.
fields:
  entity_id:
    description: Entity to chart.
    example: sensor.solar_battery_guard_battery_voltage
  hours:
    description: How many hours of history to include.
    example: 6
  output_path:
    description: Where to save the PNG. Parent directory is created if missing.
    example: /config/www/battery_voltage_chart.png
  voltage_source_entity_id:
    description: >
      Optional select entity whose current option names which sensor
      (Renogy/BM2) is actually driving the AC charger ON/OFF decision.
      When given, its value is prepended to the chart title as "<source>:
      <title>".
    example: select.si_mining_shed_solar_battery_guard_charger_voltage_source
    """
    from homeassistant.components.recorder import history

    end_time = dt.now(timezone.utc)
    start_time = end_time - timedelta(hours=float(hours))

    # Blocking DB call - must run in the executor, not the event loop.
    result = task.executor(
        history.get_significant_states,
        hass,
        start_time,
        end_time,
        [entity_id],
    )
    states = result.get(entity_id, [])

    points = []
    for s in states:
        try:
            points.append((s.last_changed, float(s.state)))
        except (ValueError, TypeError):
            continue  # skip "unknown"/"unavailable" states

    current_state = hass.states.get(entity_id)
    title = current_state.attributes.get("friendly_name", entity_id) if current_state else entity_id

    if voltage_source_entity_id:
        source_state = hass.states.get(voltage_source_entity_id)
        if source_state:
            title = f"{source_state.state}: {title}"

    task.executor(_render_chart_sync, points, output_path, hass.config.time_zone, title)
