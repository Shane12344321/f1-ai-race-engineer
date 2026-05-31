"""
Granite AI Race Narrator — PitWallWindow insight.

Watches the live telemetry stream for interesting events, sends them
to IBM Granite for plain-English narration, and displays the results
as a scrolling broadcast-style feed.
"""

import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextBrowser, QFrame, QPushButton, QLineEdit
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont

from src.gui.pit_wall_window import PitWallWindow
from src.services.race_event_detector import RaceEventDetector
from src.services.granite_client import GraniteClient


# ── Theme ─────────────────────────────────────────────────────────────────
_BG         = "#15151E"
_BG_DARKER  = "#0D0D14"
_BORDER     = "#2A2A3A"
_TEXT        = "#E0E0E0"
_TEXT_DIM    = "#888899"
_TEXT_TIME   = "#666677"
_ACCENT_RED = "#E10600"

_EVENT_COLOURS = {
    "close_battle": "#FFD700",  # gold
    "pit_stop":     "#00CED1",  # cyan
    "track_status": "#FF8C00",  # orange
    "drs_battle":   "#2ECC71",  # green
}

_EVENT_ICONS = {
    "close_battle": "⚔️",
    "pit_stop":     "🔧",
    "track_status": "🚩",
    "drs_battle":   "💨",
}


class _SignalBridge(QObject):
    """Bridge to emit Qt signals from background threads."""
    narration_ready = Signal(dict)


class GraniteNarratorWindow(PitWallWindow):
    """
    AI Race Engineer insight window.

    Detects race events from the telemetry stream, sends them to
    IBM Granite for narration, and displays the results as a
    scrolling feed.
    """

    def __init__(self):
        self._detector = RaceEventDetector()
        self._granite = GraniteClient()
        self._signal_bridge = _SignalBridge()
        self._signal_bridge.narration_ready.connect(self._on_narration_ready)
        self._pending_count = 0
        self._is_paused = False
        self._latest_telemetry = None  # Store latest frame for user questions
        super().__init__()
        self.setWindowTitle("AI Race Engineer — Powered by IBM Granite")
        self.setGeometry(100, 100, 480, 700)

    def setup_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background: {_BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setStyleSheet(
            f"background: {_BG_DARKER}; border-bottom: 1px solid {_BORDER};"
        )
        header.setFixedHeight(72)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(2)

        title_row = QHBoxLayout()
        title = QLabel("🏎️ AI RACE ENGINEER")
        title.setFont(QFont("Arial", 15, QFont.Bold))
        title.setStyleSheet(f"color: {_TEXT}; border: none;")
        title_row.addWidget(title)

        self._granite_badge = QLabel("IBM GRANITE")
        self._granite_badge.setFont(QFont("Arial", 9, QFont.Bold))
        self._granite_badge.setStyleSheet(
            f"color: white; background: {_ACCENT_RED}; border: none; "
            f"border-radius: 3px; padding: 2px 6px;"
        )
        self._granite_badge.setFixedHeight(20)
        title_row.addWidget(self._granite_badge)
        title_row.addStretch()
        header_layout.addLayout(title_row)

        self._status_line = QLabel("Waiting for telemetry...")
        self._status_line.setFont(QFont("Arial", 10))
        self._status_line.setStyleSheet(f"color: {_TEXT_DIM}; border: none;")
        header_layout.addWidget(self._status_line)

        root.addWidget(header)

        # ── Granite status banner (shown if credentials are missing) ──
        if not self._granite.is_available:
            banner = QLabel(f"⚠️  {self._granite.error_message}")
            banner.setFont(QFont("Arial", 10))
            banner.setWordWrap(True)
            banner.setStyleSheet(
                f"color: #FFD700; background: #2A2200; border: none; "
                f"padding: 10px 16px;"
            )
            root.addWidget(banner)

        # ── Feed area ──
        self._feed = QTextBrowser()
        self._feed.setOpenExternalLinks(False)
        self._feed.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._feed.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._feed.setStyleSheet(f"""
            QTextBrowser {{
                background: {_BG};
                color: {_TEXT};
                border: none;
            }}
            QScrollBar:vertical {{
                border: none;
                background: {_BG};
                width: 10px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_ACCENT_RED};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
        root.addWidget(self._feed, stretch=1)

        # ── Input Area ──
        input_container = QWidget()
        input_container.setStyleSheet(f"background: {_BG_DARKER}; border-top: 1px solid {_BORDER};")
        input_container.setFixedHeight(48)
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(12, 8, 12, 8)
        input_layout.setSpacing(8)

        self._ask_input = QLineEdit()
        self._ask_input.setPlaceholderText("Ask the Race Engineer...")
        self._ask_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_BG};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QLineEdit:focus {{
                border: 1px solid {_ACCENT_RED};
            }}
        """)
        self._ask_input.returnPressed.connect(self._handle_user_question)
        input_layout.addWidget(self._ask_input)

        self._ask_btn = QPushButton("Ask")
        self._ask_btn.setFixedWidth(60)
        self._ask_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_DARKER};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background: {_BORDER};
                color: white;
            }}
        """)
        self._ask_btn.clicked.connect(self._handle_user_question)
        input_layout.addWidget(self._ask_btn)

        root.addWidget(input_container)

        # ── Footer ──
        footer = QWidget()
        footer.setStyleSheet(
            f"background: {_BG_DARKER}; border-top: 1px solid {_BORDER};"
        )
        footer.setFixedHeight(32)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 0, 16, 0)
        self._pending_label = QLabel("")
        self._pending_label.setFont(QFont("Arial", 9))
        self._pending_label.setStyleSheet(f"color: {_TEXT_DIM}; border: none;")
        footer_layout.addWidget(self._pending_label)
        footer_layout.addStretch()

        self._pause_btn = QPushButton("Stop Asking Granite")
        self._pause_btn.setFont(QFont("Arial", 9, QFont.Bold))
        self._pause_btn.setFixedWidth(130)
        self._pause_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_DARKER};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background: {_BORDER};
                color: white;
            }}
        """)
        self._pause_btn.clicked.connect(self._toggle_pause)
        footer_layout.addWidget(self._pause_btn)

        root.addWidget(footer)

    def _toggle_pause(self):
        self._is_paused = not self._is_paused
        if self._is_paused:
            self._pause_btn.setText("Start Asking Granite")
            self._pause_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_ACCENT_RED};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    background: #B30500;
                }}
            """)
        else:
            self._pause_btn.setText("Stop Asking Granite")
            self._pause_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_BG_DARKER};
                    color: {_TEXT};
                    border: 1px solid {_BORDER};
                    border-radius: 4px;
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    background: {_BORDER};
                    color: white;
                }}
            """)

    def _build_telemetry_context(self) -> str:
        """Build a text summary of current race state for user question context."""
        data = self._latest_telemetry
        if not data:
            return "No live telemetry data available yet."

        lines = []

        # Session info
        session_data = data.get("session_data", {})
        lap = session_data.get("lap", "?")
        total = session_data.get("total_laps", "?")
        time_str = session_data.get("time", "")
        lines.append(f"Current Race State — Lap {lap}/{total} ({time_str})")

        # Tyre compound map
        _TYRE_NAMES = {1.0: "Soft", 2.0: "Medium", 3.0: "Hard", 4.0: "Intermediate", 5.0: "Wet"}

        # Driver data
        frame = data.get("frame", {})
        drivers = frame.get("drivers", {})
        if drivers:
            # Sort by track progress (lap * big number + distance)
            sorted_d = sorted(
                drivers.items(),
                key=lambda x: (x[1].get('lap') or 0) * 100000 + (x[1].get('dist') or 0),
                reverse=True
            )
            lines.append("\nDrivers (in approximate race order):")
            for i, (code, d) in enumerate(sorted_d, 1):
                speed = d.get('speed', '?')
                tyre_val = d.get('tyre')
                tyre = _TYRE_NAMES.get(float(tyre_val), 'Unknown') if tyre_val is not None else '?'
                tyre_life = d.get('tyre_life', '?')
                in_pit = d.get('in_pit', False)
                drv_lap = d.get('lap', '?')
                pit_str = " [IN PIT]" if in_pit else ""
                lines.append(
                    f"  P{i}: {code} | Lap {drv_lap} | {speed} km/h | "
                    f"Tyre: {tyre} ({tyre_life} laps old){pit_str}"
                )

        # Weather
        weather = frame.get("weather", {})
        if weather:
            track_temp = weather.get('track_temp', '?')
            air_temp = weather.get('air_temp', '?')
            rain = weather.get('rain_state', 'None')
            lines.append(f"\nWeather: Track {track_temp}°C | Air {air_temp}°C | Rain: {rain}")

        return "\n".join(lines)

    def _handle_user_question(self):
        question = self._ask_input.text().strip()
        if not question:
            return

        self._ask_input.clear()
        
        # Don't flood if paused or already waiting for too many things
        if self._is_paused or self._pending_count >= self.MAX_PENDING:
            return

        # Build prompt with live telemetry context
        context = self._build_telemetry_context()
        prompt = (
            f"USER QUESTION: {question}\n\n"
            f"CURRENT TELEMETRY DATA:\n{context}\n\n"
            f"Answer the user's question as the AI Race Engineer using ONLY the "
            f"telemetry data above. Keep it concise (1-3 sentences) and factual."
        )

        # Get current lap info for the card
        session_data = (self._latest_telemetry or {}).get("session_data", {})
        current_lap = session_data.get("lap", "?")

        # Build a synthetic event to render it in the feed
        event = {
            "type": "track_status",
            "prompt": prompt,
            "label": f"Q: {question}",
            "lap": current_lap,
            "time_str": "Now"
        }

        self._add_pending_card(event)

        event_copy = dict(event)
        def _callback(result, ev=event_copy):
            ev["narration"] = result
            self._signal_bridge.narration_ready.emit(ev)

        self._granite.generate_async(prompt, _callback)

    # ── Telemetry callback ────────────────────────────────────────────────

    # Maximum number of concurrent in-flight Granite API requests
    MAX_PENDING = 3

    def on_telemetry_data(self, data):
        # Store latest telemetry for user questions
        self._latest_telemetry = data

        # Update header status
        session_data = data.get("session_data", {})
        if session_data:
            lap = session_data.get("lap", "?")
            total = session_data.get("total_laps", "?")
            time_str = session_data.get("time", "")
            self._status_line.setText(f"Lap {lap}/{total}  ·  {time_str}")

        if self._is_paused:
            return

        # Run event detection
        events = self._detector.process_frame(data)

        for event in events:
            # Drop events if too many requests are already in-flight
            if self._pending_count >= self.MAX_PENDING:
                break

            self._add_pending_card(event)

            # Fire async Granite call
            prompt = event["prompt"]
            event_copy = dict(event)  # capture for closure

            def _callback(result, ev=event_copy):
                ev["narration"] = result
                self._signal_bridge.narration_ready.emit(ev)

            self._granite.generate_async(prompt, _callback)

    # ── UI updates ────────────────────────────────────────────────────────

    def _add_pending_card(self, event):
        """Add a 'thinking...' placeholder card to the feed."""
        self._pending_count += 1
        self._pending_label.setText(f"⏳ {self._pending_count} pending...")

        icon = _EVENT_ICONS.get(event["type"], "📡")
        accent = _EVENT_COLOURS.get(event["type"], "#666666")
        label = event.get("label", "")
        time_str = event.get("time_str", "")
        lap = event.get("lap", "?")

        html = f"""
        <table width="100%" cellspacing="0" cellpadding="10"
               style="background: {_BG}; margin-bottom: 4px;">
          <tr>
            <td width="4" style="background-color: {accent}; padding: 0;"></td>
            <td style="border-bottom: 1px solid {_BORDER}; padding: 10px 12px;">
              <span style="font-size: 13px; color: {_TEXT_DIM};">
                {icon} Lap {lap} · {time_str}
              </span><br/>
              <span style="font-size: 14px; color: {_TEXT}; font-weight: bold;">
                {label}
              </span><br/>
              <span id="narration_{self._pending_count}"
                    style="font-size: 13px; color: {_TEXT_DIM}; font-style: italic;">
                Asking IBM Granite...
              </span>
            </td>
          </tr>
        </table>
        """
        self._feed.append(html)
        scrollbar = self._feed.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_narration_ready(self, event: dict):
        """Replace the last 'thinking' card with the actual narration."""
        self._pending_count = max(0, self._pending_count - 1)
        if self._pending_count == 0:
            self._pending_label.setText("")
        else:
            self._pending_label.setText(f"⏳ {self._pending_count} pending...")

        icon = _EVENT_ICONS.get(event["type"], "📡")
        accent = _EVENT_COLOURS.get(event["type"], "#666666")
        label = event.get("label", "")
        time_str = event.get("time_str", "")
        lap = event.get("lap", "?")
        narration = event.get("narration", "No response.")

        html = f"""
        <table width="100%" cellspacing="0" cellpadding="10"
               style="background: #1A1A28; margin-bottom: 4px;
                      border-radius: 4px;">
          <tr>
            <td width="4" style="background-color: {accent}; padding: 0;"></td>
            <td style="border-bottom: 1px solid {_BORDER}; padding: 10px 12px;">
              <span style="font-size: 13px; color: {_TEXT_DIM};">
                {icon} Lap {lap} · {time_str}
              </span><br/>
              <span style="font-size: 14px; color: {_TEXT}; font-weight: bold;">
                {label}
              </span><br/><br/>
              <span style="font-size: 13px; color: #CCCCDD; line-height: 1.5;">
                {narration}
              </span>
            </td>
          </tr>
        </table>
        """
        self._feed.append(html)
        scrollbar = self._feed.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_connection_status_changed(self, status):
        if status == "Disconnected":
            self._status_line.setText("Disconnected")
            self._status_line.setStyleSheet(f"color: #E74C3C; border: none;")
        elif status == "Connecting...":
            self._status_line.setText("Connecting...")
            self._status_line.setStyleSheet(f"color: #FF8C00; border: none;")
        elif status == "Connected":
            self._status_line.setStyleSheet(f"color: {_TEXT_DIM}; border: none;")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AI Race Engineer")
    window = GraniteNarratorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
