"""
Race Event Detector — watches live telemetry for interesting moments.

Analyzes the telemetry stream to detect events worth narrating:
  - Close battles (gap < 1.0s between adjacent drivers)
  - Pit stops (driver enters pit lane)
  - Track status changes (Safety Car, VSC, flags)
  - DRS activations in battle
  - Tyre compound switches

Each event includes a cooldown to prevent spamming the LLM.
"""

import time as _time
from typing import Optional


# Tyre compound names (matching the numeric encoding in the telemetry)
_TYRE_NAMES = {
    1.0: "Soft", 1: "Soft",
    2.0: "Medium", 2: "Medium",
    3.0: "Hard", 3: "Hard",
    4.0: "Intermediate", 4: "Intermediate",
    5.0: "Wet", 5: "Wet",
}


def _tyre_name(val) -> str:
    if val is None:
        return "Unknown"
    try:
        return _TYRE_NAMES.get(float(val), "Unknown")
    except (ValueError, TypeError):
        return "Unknown"


def _format_time(seconds) -> str:
    if seconds is None or seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02}"


class RaceEventDetector:
    """
    Stateful detector that processes telemetry frames and emits event dicts.

    Each event dict contains:
        type:     str   — event category
        prompt:   str   — pre-formatted prompt for the LLM
        label:    str   — short human-readable summary
        lap:      int   — current lap number
        time_str: str   — formatted race time
    """

    # Cooldown durations in real (wall-clock) seconds
    COOLDOWNS = {
        "close_battle": 45,
        "pit_stop": 45,
        "track_status": 60,
        "drs_battle": 60,
        "overtake": 30,
        "tyre_change": 30,
        "dnf": 10,
        "weather": 300,  # 5 minutes before we mention weather again
    }

    # Don't fire battles/overtakes until the race has settled (after Lap 1 chaos)
    WARMUP_LAPS = 3

    # Minimum gap (seconds) for a "close battle" — filters out grid bunching noise
    MIN_BATTLE_GAP_S = 0.15

    # Global cooldown across *all* event types to prevent API flooding
    GLOBAL_COOLDOWN_S = 15

    # Only narrate overtakes involving the top N positions (ignore midfield noise)
    OVERTAKE_TOP_N = 10

    def __init__(self):
        # Cooldown tracking: key → last-fire wall-clock time
        self._cooldowns: dict[str, float] = {}

        # Global timestamp of last event of any type
        self._last_event_time: float = 0.0

        # Whether we've emitted the initial "race start" event
        self._race_started: bool = False

        # Previous frame state for delta detection
        self._prev_track_status: Optional[str] = None
        self._prev_pit_state: dict[str, bool] = {}  # code → was_in_pit
        self._prev_tyres: dict[str, float] = {}      # code → tyre compound
        self._prev_positions: dict[str, int] = {}    # code → last known race position
        self._prev_rain_state: Optional[str] = None  # "DRY" or "RAINING"

    def _on_cooldown(self, key: str, category: str) -> bool:
        """Return True if this key is still on cooldown."""
        last = self._cooldowns.get(key, 0)
        cooldown = self.COOLDOWNS.get(category, 30)
        return (_time.time() - last) < cooldown

    def _on_global_cooldown(self) -> bool:
        """Return True if any event was fired too recently."""
        return (_time.time() - self._last_event_time) < self.GLOBAL_COOLDOWN_S

    def _fire(self, key: str, category: str):
        """Mark a cooldown key as fired and update global cooldown."""
        now = _time.time()
        self._cooldowns[key] = now
        self._last_event_time = now

    def _parse_lap(self, lap_val) -> int:
        """Safely parse lap number to int."""
        try:
            return int(lap_val)
        except (ValueError, TypeError):
            return 0

    def process_frame(self, data: dict) -> list[dict]:
        """
        Process one telemetry frame and return a list of detected events.
        Most frames return an empty list.  At most ONE event is returned
        per frame to prevent API flooding.
        """
        frame = data.get("frame")
        if not frame:
            return []

        drivers = frame.get("drivers", {})
        if not drivers:
            return []

        session_data = data.get("session_data", {})
        lap = session_data.get("lap", "?")
        total_laps = session_data.get("total_laps", "?")
        time_str = session_data.get("time", "00:00:00")
        track_status = data.get("track_status", "1")
        weather = data.get("weather", {})
        lap_int = self._parse_lap(lap)

        # Build a sorted leaderboard by progress
        standings = []
        for code, pos in drivers.items():
            driver_lap = pos.get("lap", 1)
            dist = pos.get("dist", 0)
            try:
                progress = int(driver_lap) * 100000 + float(dist)
            except (ValueError, TypeError):
                progress = 0
            standings.append((code, pos, progress))
        standings.sort(key=lambda x: x[2], reverse=True)

        # ── Snapshot PREVIOUS state before overwriting ──
        prev_pit_state = dict(self._prev_pit_state)
        prev_tyres = dict(self._prev_tyres)
        prev_positions = dict(self._prev_positions)
        prev_track_status = self._prev_track_status

        # ── Now update state to current frame values ──
        for code, pos, _ in standings:
            self._prev_pit_state[code] = bool(pos.get("in_pit", False))

        for code, pos, _ in standings:
            current_tyre = pos.get("tyre")
            if current_tyre is not None:
                try:
                    self._prev_tyres[code] = float(current_tyre)
                except (ValueError, TypeError):
                    pass

        current_positions: dict[str, int] = {}
        for i, (code, _, _) in enumerate(standings):
            current_positions[code] = i + 1
        self._prev_positions = current_positions

        self._prev_track_status = track_status
        
        # Only update rain state if it's explicitly present in the telemetry frame
        if "rain_state" in weather:
            self._prev_rain_state = weather.get("rain_state")

        # ── 0. Race Start (fires once on the very first frame) ──
        if not self._race_started:
            self._race_started = True
            self._fire("race_start", "track_status")

            # Summarize tyre choices on the grid, filtering out "Unknown"
            tyre_counts: dict[str, int] = {}
            for _, pos, _ in standings:
                t = _tyre_name(pos.get("tyre"))
                if t != "Unknown":
                    tyre_counts[t] = tyre_counts.get(t, 0) + 1

            # Build prompt — do NOT claim specific positions (GPS is
            # unreliable at lights out, leaderboard order is wrong)
            if tyre_counts:
                tyre_summary = ", ".join(f"{count} on {name}" for name, count in tyre_counts.items())
                prompt = (
                    f"LIGHTS OUT — RACE START (Lap 1/{total_laps}):\n"
                    f"- Total cars: {len(standings)}\n"
                    f"- Starting tyre strategies: {tyre_summary}\n"
                    f"Set the scene for the start of this race in an exciting "
                    f"broadcast style. Mention the tyre strategies in play. "
                    f"Do NOT mention specific driver positions or who is leading — "
                    f"the grid order is not yet confirmed."
                )
            else:
                # Tyre data not yet available on this frame
                prompt = (
                    f"LIGHTS OUT — RACE START (Lap 1/{total_laps}):\n"
                    f"- Total cars: {len(standings)}\n"
                    f"Set the scene for the start of this race in an exciting "
                    f"broadcast style. Tyre compound data is not yet available, "
                    f"so do not speculate about tyre strategies. "
                    f"Do NOT mention specific driver positions or who is leading — "
                    f"the grid order is not yet confirmed."
                )

            return [{
                "type": "track_status",
                "prompt": prompt,
                "label": "LIGHTS OUT — Race Start",
                "lap": lap,
                "time_str": time_str,
            }]

        # ── Detect events (at most one per frame) ──

        # ── 1. Driver Retirements (DNFs - always checked immediately) ──
        if prev_positions:
            for code in list(prev_positions.keys()):
                if code not in drivers:
                    key = f"dnf_{code}_{lap}"
                    if not self._on_cooldown(key, "dnf"):
                        self._fire(key, "dnf")

                        last_pos = prev_positions[code]
                        last_tyre = _tyre_name(prev_tyres.get(code))

                        prompt = (
                            f"DRIVERS IN THIS EVENT: {code}\n"
                            f"RETIREMENT (DNF) on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                            f"- {code} has retired from the race (was running in P{last_pos})\n"
                            f"- Last tyre compound run: {last_tyre}\n"
                            f"Describe this retirement and its impact on their race. "
                            f"Only reference the driver listed above."
                        )
                        return [{
                            "type": "track_status",
                            "prompt": prompt,
                            "label": f"RETIREMENT: {code} DNF",
                            "lap": lap,
                            "time_str": time_str,
                        }]

        # Check global cooldown — if we just fired something recently, skip remaining checks
        if self._on_global_cooldown():
            return []

        # ── 2. Weather / Rain Alerts ──
        current_rain = weather.get("rain_state")
        if current_rain == "RAINING" and self._prev_rain_state == "DRY":
            key = f"rain_start_{lap}"
            if not self._on_cooldown(key, "weather"):
                self._fire(key, "weather")
                leader = standings[0][0] if standings else "?"
                prompt = (
                    f"DRIVERS IN THIS EVENT: {leader}\n"
                    f"WEATHER UPDATE on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                    f"- Rain has started falling on the circuit\n"
                    f"- Current leader: {leader}\n"
                    f"Describe the arrival of the rain in an exciting broadcast style "
                    f"and note the current tyre strategies in play. "
                    f"Only reference the driver listed above."
                )
                return [{
                    "type": "track_status",
                    "prompt": prompt,
                    "label": "WEATHER: Rain starting",
                    "lap": lap,
                    "time_str": time_str,
                }]

        # ── 3. Track Status Changes (always allowed, even on Lap 1) ──
        if track_status != prev_track_status and prev_track_status is not None:
            key = f"track_status_{track_status}"
            if not self._on_cooldown(key, "track_status"):
                self._fire(key, "track_status")

                status_names = {
                    "1": "Green Flag (racing resumes)",
                    "2": "Yellow Flag",
                    "4": "Safety Car deployed",
                    "5": "Red Flag (race stopped)",
                    "6": "Virtual Safety Car (VSC)",
                    "7": "VSC Ending",
                }
                status_name = status_names.get(track_status, f"Status {track_status}")

                leader = standings[0][0] if standings else "?"
                prompt = (
                    f"DRIVERS IN THIS EVENT: {leader}\n"
                    f"TRACK STATUS CHANGE on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                    f"- New status: {status_name}\n"
                    f"- Race leader: {leader}\n"
                    f"Describe this track status change and its immediate effect on the pace "
                    f"of the leader {leader}. "
                    f"Only reference the driver listed above."
                )
                return [{
                    "type": "track_status",
                    "prompt": prompt,
                    "label": status_name,
                    "lap": lap,
                    "time_str": time_str,
                }]

        # ── 2. Pit Stops (allowed from Lap 1) ──
        for code, pos, _ in standings:
            in_pit = bool(pos.get("in_pit", False))
            was_in_pit = prev_pit_state.get(code, False)

            # Detect entering pit (transition from not-in-pit to in-pit)
            if in_pit and not was_in_pit:
                key = f"pit_stop_{code}"
                if not self._on_cooldown(key, "pit_stop"):
                    self._fire(key, "pit_stop")
                    tyre = _tyre_name(pos.get("tyre"))
                    tyre_life = pos.get("tyre_life", "?")

                    position = next(
                        (i + 1 for i, (c, _, _) in enumerate(standings) if c == code),
                        "?"
                    )

                    prompt = (
                        f"DRIVERS IN THIS EVENT: {code}\n"
                        f"PIT STOP on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                        f"- {code} (P{position}) has entered the pits\n"
                        f"- Current tyres: {tyre} ({tyre_life} laps old)\n"
                        f"Describe this pit stop based on their tyre age. "
                        f"Only reference the driver listed above."
                    )
                    return [{
                        "type": "pit_stop",
                        "prompt": prompt,
                        "label": f"{code} pits from P{position}",
                        "lap": lap,
                        "time_str": time_str,
                    }]

        # ── Skip noisy events during warmup laps ──
        if lap_int < self.WARMUP_LAPS:
            return []

        # ── 3. Overtakes (only top positions, after warmup) ──
        if prev_positions:
            for code, new_pos in current_positions.items():
                # Only narrate overtakes in the top N
                if new_pos > self.OVERTAKE_TOP_N:
                    continue

                old_pos = prev_positions.get(code)
                if old_pos is None:
                    continue

                # Must gain at least 1 position
                if new_pos < old_pos:
                    key = f"overtake_{code}_{new_pos}"
                    if not self._on_cooldown(key, "overtake"):
                        self._fire(key, "overtake")

                        overtaken_code = None
                        for c2, p2 in current_positions.items():
                            prev_p2 = prev_positions.get(c2)
                            if prev_p2 is not None and prev_p2 == new_pos and p2 > prev_p2:
                                overtaken_code = c2
                                break

                        driver_pos = next((p for c, p, _ in standings if c == code), {})
                        tyre = _tyre_name(driver_pos.get("tyre") if isinstance(driver_pos, dict) else None)
                        speed = driver_pos.get("speed", 0) if isinstance(driver_pos, dict) else 0

                        overtaken_label = overtaken_code or "a rival"
                        involved = f"{code}, {overtaken_label}" if overtaken_code else code
                        prompt = (
                            f"DRIVERS IN THIS EVENT: {involved}\n"
                            f"OVERTAKE on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                            f"- {code} has moved from P{old_pos} to P{new_pos}\n"
                            f"- Overtook: {overtaken_label}\n"
                            f"- {code}: {speed:.0f} km/h on {tyre} tyres\n"
                            f"Describe this overtake dynamically based on the speeds and tyres. "
                            f"Only reference the drivers listed above."
                        )
                        return [{
                            "type": "close_battle",
                            "prompt": prompt,
                            "label": f"{code} overtakes {overtaken_label} → P{new_pos}",
                            "lap": lap,
                            "time_str": time_str,
                        }]

        # ── 4. Close Battles (after warmup, minimum gap filter) ──
        for i in range(len(standings) - 1):
            # Only narrate battles in the top positions
            if i + 1 > self.OVERTAKE_TOP_N:
                break

            code_a, pos_a, prog_a = standings[i]
            code_b, pos_b, prog_b = standings[i + 1]

            gap_m = abs(prog_a - prog_b) / 10.0
            gap_s = gap_m / 55.56  # approx seconds at 200 km/h

            # Filter: must be a real battle (not just grid bunching)
            if self.MIN_BATTLE_GAP_S < gap_s < 1.0:
                key = f"close_battle_{code_a}_{code_b}"
                if not self._on_cooldown(key, "close_battle"):
                    self._fire(key, "close_battle")

                    speed_a = pos_a.get("speed", 0)
                    speed_b = pos_b.get("speed", 0)
                    tyre_a = _tyre_name(pos_a.get("tyre"))
                    tyre_b = _tyre_name(pos_b.get("tyre"))
                    tyre_life_a = pos_a.get("tyre_life", "?")
                    tyre_life_b = pos_b.get("tyre_life", "?")
                    drs_b = int(pos_b.get("drs", 0) or 0)
                    drs_text_b = "DRS OPEN" if drs_b >= 10 else "DRS closed"

                    prompt = (
                        f"DRIVERS IN THIS EVENT: {code_a}, {code_b}\n"
                        f"CLOSE BATTLE on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                        f"- P{i+1} {code_a}: {speed_a:.0f} km/h, {tyre_a} tyres ({tyre_life_a} laps old)\n"
                        f"- P{i+2} {code_b}: {speed_b:.0f} km/h, {tyre_b} tyres ({tyre_life_b} laps old), {drs_text_b}\n"
                        f"- Gap: {gap_s:.2f} seconds\n"
                        f"Describe this close battle dynamically based on the gap, speeds, and tyres. "
                        f"Only reference the drivers listed above."
                    )
                    return [{
                        "type": "close_battle",
                        "prompt": prompt,
                        "label": f"{code_b} is {gap_s:.1f}s behind {code_a}",
                        "lap": lap,
                        "time_str": time_str,
                    }]

        # ── 5. Tyre Compound Changes ──
        for code, pos, _ in standings:
            current_tyre = pos.get("tyre")
            if current_tyre is None:
                continue
            try:
                current_val = float(current_tyre)
            except (ValueError, TypeError):
                continue

            prev_val = prev_tyres.get(code)
            if prev_val is not None and current_val != prev_val:
                key = f"tyre_change_{code}_{lap}"
                if not self._on_cooldown(key, "tyre_change"):
                    self._fire(key, "tyre_change")

                    old_name = _tyre_name(prev_val)
                    new_name = _tyre_name(current_val)
                    position = next(
                        (i + 1 for i, (c, _, _) in enumerate(standings) if c == code),
                        "?"
                    )

                    prompt = (
                        f"DRIVERS IN THIS EVENT: {code}\n"
                        f"TYRE CHANGE on Lap {lap}/{total_laps} (Race time: {time_str}):\n"
                        f"- {code} (P{position}) switched from {old_name} to {new_name} tyres\n"
                        f"Explain this tyre strategy decision and whether it is "
                        f"conventional or an undercut/overcut attempt. "
                        f"Only reference the driver listed above."
                    )
                    return [{
                        "type": "pit_stop",
                        "prompt": prompt,
                        "label": f"{code} switches to {new_name} tyres",
                        "lap": lap,
                        "time_str": time_str,
                    }]

        return []
