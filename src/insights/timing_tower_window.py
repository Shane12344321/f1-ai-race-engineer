import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from src.gui.pit_wall_window import PitWallWindow

class TimingTowerWindow(PitWallWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("F1 Live Timing Tower")
        self.setGeometry(100, 100, 350, 800)
        
        self.setStyleSheet("""
            QMainWindow {
                background-color: #111111;
            }
            QStatusBar {
                background-color: #222222;
                color: #aaaaaa;
            }
        """)

        # Store references to row widgets to update them
        self.driver_rows = {}

    def setup_ui(self):
        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # Header Title
        title_label = QLabel("LIVE TIMING")
        title_label.setFont(QFont("Arial", 18, QFont.Bold))
        title_label.setStyleSheet("color: white;")
        main_layout.addWidget(title_label)

        # Session Data (Lap, Track Status)
        self.session_info_label = QLabel("Waiting for session data...")
        self.session_info_label.setFont(QFont("Arial", 12))
        self.session_info_label.setStyleSheet("color: #aaaaaa;")
        main_layout.addWidget(self.session_info_label)

        # Scroll area for driver list
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet("background-color: transparent;")

        self.drivers_container = QWidget()
        self.drivers_layout = QVBoxLayout(self.drivers_container)
        self.drivers_layout.setContentsMargins(0, 0, 0, 0)
        self.drivers_layout.setSpacing(4)
        self.drivers_layout.setAlignment(Qt.AlignTop)

        self.scroll_area.setWidget(self.drivers_container)
        main_layout.addWidget(self.scroll_area)

    def _create_driver_row(self, code, color_hex):
        row = QFrame()
        row.setFixedHeight(35)
        # Add a left border for the team color
        row.setStyleSheet(f"""
            QFrame {{
                background-color: #222222;
                border-left: 5px solid {color_hex};
                border-radius: 3px;
            }}
        """)
        
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 10, 0)
        
        pos_label = QLabel("00")
        pos_label.setFixedWidth(30)
        pos_label.setFont(QFont("Arial", 12, QFont.Bold))
        pos_label.setStyleSheet("color: white; border: none;")
        
        code_label = QLabel(code)
        code_label.setFixedWidth(50)
        code_label.setFont(QFont("Arial", 12, QFont.Bold))
        code_label.setStyleSheet("color: white; border: none;")

        speed_label = QLabel("0 km/h")
        speed_label.setFixedWidth(70)
        speed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        speed_label.setFont(QFont("Arial", 11))
        speed_label.setStyleSheet("color: #cccccc; border: none;")

        tyre_label = QLabel("⚪") # Default to Hard tyre
        tyre_label.setFixedWidth(30)
        tyre_label.setAlignment(Qt.AlignCenter)
        tyre_label.setStyleSheet("border: none; font-size: 14px;")

        layout.addWidget(pos_label)
        layout.addWidget(code_label)
        layout.addStretch()
        layout.addWidget(speed_label)
        layout.addWidget(tyre_label)

        self.drivers_layout.addWidget(row)

        self.driver_rows[code] = {
            'widget': row,
            'pos': pos_label,
            'speed': speed_label,
            'tyre': tyre_label
        }

    def on_telemetry_data(self, data):
        if 'frame' not in data or not data['frame']:
            return

        frame = data['frame']
        drivers = frame.get('drivers', {})
        driver_colors = data.get('driver_colors', {})
        track_status = data.get('track_status', 'GREEN')

        # Update Session Info
        session_data = data.get('session_data', {})
        lap = session_data.get('lap', '?')
        total_laps = session_data.get('total_laps', '?')
        time_str = session_data.get('time', '00:00:00')

        status_color = "white"
        if track_status == "2":
            status_color = "#ffcc00"
            track_status = "YELLOW FLAG"
        elif track_status == "4":
            status_color = "#ff8800"
            track_status = "SAFETY CAR"
        elif track_status in ("6", "7"):
            status_color = "#ff8800"
            track_status = "VSC"

        self.session_info_label.setText(f"Lap {lap}/{total_laps}  •  {time_str}  •  <span style='color: {status_color}'>{track_status}</span>")

        # Sort drivers by their lap and distance (progress)
        sorted_drivers = sorted(
            drivers.items(),
            key=lambda x: (x[1].get('lap', 1) * 100000 + x[1].get('dist', 0)),
            reverse=True
        )

        for position, (code, pos_data) in enumerate(sorted_drivers, start=1):
            if code not in self.driver_rows:
                # Create the row if it doesn't exist
                color_hex = driver_colors.get(code, "#ffffff")
                self._create_driver_row(code, color_hex)
            
            # Update data
            row_data = self.driver_rows[code]
            row_data['pos'].setText(str(position))
            
            speed = int(pos_data.get('speed', 0))
            row_data['speed'].setText(f"{speed} km/h")
            
            # Update Tyre compound icon
            tyre_val = pos_data.get('tyre')
            if tyre_val == 1.0: # Soft
                row_data['tyre'].setText("🔴")
            elif tyre_val == 2.0: # Medium
                row_data['tyre'].setText("🟡")
            elif tyre_val == 3.0: # Hard
                row_data['tyre'].setText("⚪")
            elif tyre_val == 4.0: # Inter
                row_data['tyre'].setText("🟢")
            elif tyre_val == 5.0: # Wet
                row_data['tyre'].setText("🔵")

        # Reorder widgets in the layout based on current standings
        for i, (code, _) in enumerate(sorted_drivers):
            widget = self.driver_rows[code]['widget']
            self.drivers_layout.insertWidget(i, widget)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TimingTowerWindow()
    window.show()
    sys.exit(app.exec())
