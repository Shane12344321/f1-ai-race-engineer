# F1 Race Replay — Codebase Documentation

## Overview

The **F1 Race Replay** project is a Python application designed to visualize Formula 1 race telemetry, track positions, and replay entire race events with interactive controls and graphical interfaces. It relies on the `FastF1` library for fetching session data and the `Arcade` game library for rendering high-performance 2D visualisations.

This project enables data-loving F1 fans to explore data generated during a race weekend, functioning as a personal "pit wall" tool for deep telemetry analysis and race replay.

## Core Features
- **Race Replay Visualization**: Real-time driver positions on a rendered track with smooth interpolation and playback speed controls.
- **Safety Car Simulation**: Animates a simulated safety car during track status periods when the safety car is deployed.
- **AI Race Engineer (IBM Granite)**: Detects significant race events (battles, overtakes, pit stops, tyre changes, track status) and generates plain-English narrations using IBM Granite on watsonx.ai.
- **Insights Menu (Pit Wall Windows)**: A floating PyQt/PySide6 menu for quick access to various telemetry analysis tools and live data feeds.
- **Telemetry Streaming**: A TCP socket server broadcasting live telemetry frame data for consumption by external applications or custom insight windows.
- **Bayesian Tyre Degradation Model**: An advanced state-space model that integrates fuel load effects, track abrasion, and tyre compound specific data to estimate tyre degradation during a race.

## Architecture & Directory Structure

```text
f1-race-replay/
├── main.py                    # Main entry point (CLI and GUI launch).
├── requirements.txt           # Project dependencies.
├── README.md                  # General project documentation.
├── src/
│   ├── f1_data.py             # Telemetry extraction, interpolation, and SC simulation.
│   ├── bayesian_tyre_model.py # Statistical model predicting tyre degradation.
│   ├── tyre_degradation_integration.py # Links tyre model to UI/App logic.
│   ├── run_session.py         # Subprocess launcher for the Arcade replays and Insights UI.
│   ├── ui_components.py       # Reusable Arcade UI elements (Leaderboard, Legends).
│   ├── arcade_replay.py       # Core replay rendering logic (legacy/shared UI).
│   ├── cli/                   # Command Line Interface (questionary menus).
│   ├── gui/                   # PySide6 GUI elements (Race Selection, Insights Menu, settings).
│   ├── interfaces/            # Specific Arcade Windows for Race and Qualifying Replays.
│   ├── insights/              # Extracted insight windows (track pos, tyre strategy, feeds).
│   ├── services/              # Background services, notably the TelemetryStreamServer.
│   │   ├── stream.py          # TCP telemetry broadcast server & client.
│   │   ├── granite_client.py  # IBM watsonx.ai Granite LLM client.
│   │   └── race_event_detector.py  # Detects race events from telemetry for narration.
│   └── lib/                   # Utility scripts (season fetching, tyre definitions, time format).
├── computed_data/             # Pickled data cache generated via fastf1 and the custom interpolator.
└── .fastf1-cache/             # FastF1 raw API response cache.
```

## Key Components

### 1. Data Pipeline (`src/f1_data.py`)
- Responsible for querying `FastF1` to get raw laps, telemetry, weather, and race control messages.
- Resamples driver telemetry (which arrives at different frequencies) onto a standardized timeline using linear interpolation to allow synchronous playback.
- **Safety Car Simulation**: Simulates safety car coordinates and phases (`deploying`, `on_track`, `returning`) using KD-Tree based projections onto the track polyline.

### 2. Rendering Engine (`src/interfaces/race_replay.py`)
- Built on the Python `Arcade` library.
- Defines `F1RaceReplayWindow`, which manages the rendering loop (`on_update`, `on_draw`).
- Draws track inner/outer bounds, DRS zones, driver dots, UI components (Leaderboard, Race Progress Bar), and the Safety Car animations.
- Uses `cKDTree` for spatial queries and accurate rendering.

### 3. Telemetry Streamer (`src/services/stream.py`)
- An active replay broadcasts its state (current positions, track status, weather, tyre health) over a TCP socket on `localhost:9999`.
- Allows detached UI components (the Insights Menu) to read live metrics while the main Arcade window renders.

### 4. Tyre Degradation Model (`src/bayesian_tyre_model.py`)
- Implements `BayesianTyreDegradationModel`.
- Estimates latent tyre pace taking into account track abrasion, fuel weight, lap history, and compound choice using state-space updates.

### 5. GUI & Insights (`src/gui` & `src/insights`)
- Uses `PySide6` for desktop application components that are difficult to build in an Arcade game loop.
- **`RaceSelectionWindow`**: A desktop entry point for picking Year, Round, and Session.
- **`PitWallWindow`**: A base class for custom insight views that connects to the TCP stream automatically.
- **`GraniteNarratorWindow`**: AI Race Engineer window that displays Granite-generated narrations.

### 6. AI Race Engineer Pipeline (`src/services/granite_client.py` & `src/services/race_event_detector.py`)
- **`RaceEventDetector`**: A stateful detector that processes each telemetry frame and identifies key events:
  - Close battles (gap < 1.0s)
  - Pit stops (driver enters pit lane)
  - Track status changes (SC, VSC, Red Flag)
  - Overtakes (position changes between frames)
  - Tyre compound switches
  - Each event type has its own cooldown to avoid LLM spam.
- **`GraniteClient`**: Thread-safe client for IBM Granite (`ibm/granite-3-3-8b-instruct`) on watsonx.ai.
  - Credentials loaded from `.env` via `python-dotenv`.
  - Provides both `generate()` (synchronous) and `generate_async()` (background thread + callback) methods.
  - Fails gracefully when credentials are missing — the window shows a warning banner but doesn't crash.

## How to Run

1. **Setup Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Launch GUI (Default)**:
   ```bash
   python main.py
   ```
3. **Launch CLI Menu**:
   ```bash
   python main.py --cli
   ```
4. **Quick Launch a Specific Race**:
   ```bash
   python main.py --viewer --year 2024 --round 1 --refresh-data
   ```

## Creating Custom Insights
Developers can hook into the telemetry stream to build custom visualizations by inheriting from `PitWallWindow` (found in `src.gui.pit_wall_window`). This base class handles socket connections and triggers an `on_telemetry_data(self, data)` callback.
