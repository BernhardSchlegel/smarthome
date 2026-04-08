log.warning("energy optimizer file loaded")
# see the logs here http://homeassistant.local:8123/config/logs

from .solar.SolarForecast import SolarForecast, PanelGroup
from datetime import datetime, timedelta

# 1=use predictions as is, smaller than 1: more conservative, i.e., prediction of 1000W will only be used as 400W
ADJUST_PREDICTION = 0.4 
HOUSE_IDLE_WATTAGE = 400
HOUSE_CONSUMPTION_PER_DAY_WATTHOURS = 25000
ENTITY_ACTOR_SET_IDLE = "switch.shelly_sperre_switch_0"
# If there is less than MIN_PV_KWH_EXCESS_TO_FORCE_IDLE, no idle will be set
MIN_PV_KWH_EXCESS_TO_FORCE_IDLE = 1.5
PANEL_GROUPS = [{
    "name": "Main",
    "latitude": 48.40788898251442,
    "longitude": 9.96413786487143,
    "declination": 15,
    "azimuth": 0,
    "kwp": 5.4
}]

def get_first_and_last_timestamp(data, min_wattage: int, date: datetime):
    relevant_times = sorted([
        timestamp for timestamp, watt in data.items()
        if watt * ADJUST_PREDICTION > min_wattage and timestamp.date() == date.date()
    ])
    # if watt > min_wattage and datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").date() == date.date()


    if not relevant_times:
        return None, None

    first_time = relevant_times[0]
    last_time = relevant_times[-1]

    # Convert strings to datetime objects for clearer formatting or further processing
    first_dt = first_time # datetime.strptime(first_time, "%Y-%m-%d %H:%M:%S")
    last_dt = last_time # datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")

    return first_dt, last_dt

def calculate_watthours_with_baseline(data, start: datetime, end: datetime, baseline_watts: int):
    total_watthours = 0

    # datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    sorted_timestamps = sorted([
        (ts, watt) 
        for ts, watt in data.items()
    ])

    for i in range(len(sorted_timestamps) - 1):
        current_time, current_watthours = sorted_timestamps[i]
        next_time, _ = sorted_timestamps[i + 1]

        if start <= current_time <= end:
            duration_hours = (next_time - current_time).total_seconds() / 3600
            adjusted_watts = max(0, current_watthours*ADJUST_PREDICTION/1 - baseline_watts)
            total_watthours += adjusted_watts * duration_hours

    return total_watthours

@service
async def heatpump_idle_service(threshold=400):
    log.info(f"Heatpump idle logic triggered with threshold DEBUG THRESHOLD {threshold}")

    # Check current state of ENTITY_ACTOR_SET_IDLE
    current_state = await state.get(ENTITY_ACTOR_SET_IDLE)

    if current_state == "on":
        log.info(f"{ENTITY_ACTOR_SET_IDLE} is already ON. No action needed.")
    else:
        # Query forecast for both panel groups
        forecast = SolarForecast()
        await forecast.query(PANEL_GROUPS)

        # Get combined production for today and tomorrow
        today_wh, tomorrow_wh = await forecast.get_daily_watthours()
        log.info(f"Total expected production today: {today_wh/1000:.1f} kWh")
        log.info(f"Total expected production tomorrow: {tomorrow_wh/1000:.1f} kWh")
        
        # Access individual panel group results
        for i, panel_result in enumerate(forecast.panel_results):
            panel_name = PANEL_GROUPS[i]["name"]
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            panel_wh = panel_result["watt_hours_day"][today]
            log.info(f"{panel_name} expected today: {panel_wh/1000:.1f} kWh")
        
        # Access power forecast for specific times
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        current_power = forecast.result["watts"].get(now, 0)
        log.info(f"Current power output: {current_power/1000:.1f} kW")

        
        heatpump_average_wattage = int(HOUSE_CONSUMPTION_PER_DAY_WATTHOURS / 24 - 400)
        log.info(f"assumption is an average of {heatpump_average_wattage}W for the heatpump")

        log.info(f"here are the forecasts: {forecast.result["watt_hours_period"]}.")
        
        first, last = get_first_and_last_timestamp(forecast.result["watt_hours_period"], HOUSE_IDLE_WATTAGE, datetime.today())
        if first is None:
            log.info(f"solar wattage will never exceed {HOUSE_IDLE_WATTAGE}W – skipping idle scheduling")
        else:
            log.info(f"solar wattage will exceed {HOUSE_IDLE_WATTAGE}W from {first} to {last}")
            excess_pv_kWh = calculate_watthours_with_baseline(forecast.result["watt_hours_period"], first, last, HOUSE_IDLE_WATTAGE)
            log.info(f"with a idle wattage of {HOUSE_IDLE_WATTAGE}W, PV Excess is {round(excess_pv_kWh/1000,2)}kWh")
            if excess_pv_kWh/1000 < MIN_PV_KWH_EXCESS_TO_FORCE_IDLE:
                log.info(f"Total PV excess ({excess_pv_kWh/1000}kWh) is less than threshold ({MIN_PV_KWH_EXCESS_TO_FORCE_IDLE}kWh) - skipping.")
            else:
                heatpump_idle_target_hours = excess_pv_kWh / heatpump_average_wattage
                log.info(f"heatpump idle target {heatpump_idle_target_hours}hours before {first}")
                ts_heatpump_idle_start = first - timedelta(hours=heatpump_idle_target_hours)
                log.info(f"heatpump should go idle mode starting {ts_heatpump_idle_start}")
                
                now = datetime.now()
                # Check if the current time is within the specified range
                if ts_heatpump_idle_start <= now <= first:
                    log.info(f"Current time {now} is between {ts_heatpump_idle_start} and {first}, switching {ENTITY_ACTOR_SET_IDLE} ON.")
                    await service.call("switch", "turn_on", entity_id=ENTITY_ACTOR_SET_IDLE)
                else:
                    log.info(f"Current time {now} is outside the idle window ({ts_heatpump_idle_start} to {first}). No action taken.")
