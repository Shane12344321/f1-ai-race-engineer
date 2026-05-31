import sys
from collections import deque

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from src.gui.pit_wall_window import PitWallWindow

_TIME_WINDOW = 30
_BG = "#282828"
_COL1 = "#00FFFF"  # Cyan
_COL2 = "#FF00FF"  # Magenta

class BattleTelemetryWindow(PitWallWindow):
    def __init__(self):
        self._known_drivers = []
        self._time_buffers = {}
        self._auto_battle = True
        super().__init__()
        self.setWindowTitle("F1 TeleTranslator - Head-to-Head Battle")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 10)

        # Controls
        controls = QHBoxLayout()
        d1_label = QLabel("Driver 1:")
        d1_label.setStyleSheet(f"color: {_COL1}; font-weight: bold;")
        self.d1_combo = QComboBox()
        self.d1_combo.currentTextChanged.connect(self._redraw)

        d2_label = QLabel("Driver 2:")
        d2_label.setStyleSheet(f"color: {_COL2}; font-weight: bold;")
        self.d2_combo = QComboBox()
        self.d2_combo.currentTextChanged.connect(self._redraw)

        self.auto_check = QCheckBox("Auto-Track Battles (Gap < 1.0s)")
        self.auto_check.setChecked(True)
        self.auto_check.stateChanged.connect(self._on_auto_check)

        controls.addWidget(d1_label)
        controls.addWidget(self.d1_combo)
        controls.addSpacing(15)
        controls.addWidget(d2_label)
        controls.addWidget(self.d2_combo)
        controls.addSpacing(25)
        controls.addWidget(self.auto_check)
        controls.addStretch()
        root.addLayout(controls)

        # Plot
        self._fig = plt.figure(figsize=(10, 6), facecolor=_BG)
        gs = gridspec.GridSpec(3, 1, figure=self._fig, height_ratios=[2, 1, 1], hspace=0.15)

        # Speed
        self.ax_speed = self._fig.add_subplot(gs[0])
        self.line_s1, = self.ax_speed.plot([], [], color=_COL1, linewidth=2, label="Driver 1")
        self.line_s2, = self.ax_speed.plot([], [], color=_COL2, linewidth=2, linestyle='--', label="Driver 2")
        self._setup_ax(self.ax_speed, "Speed (km/h)", 0, 380)
        self.ax_speed.legend(loc='upper right', facecolor=_BG, edgecolor='#555555', labelcolor='#F0F0F0')

        # Gear
        self.ax_gear = self._fig.add_subplot(gs[1])
        self.line_g1, = self.ax_gear.plot([], [], color=_COL1, linewidth=2, drawstyle='steps-post')
        self.line_g2, = self.ax_gear.plot([], [], color=_COL2, linewidth=2, drawstyle='steps-post', linestyle='--')
        self._setup_ax(self.ax_gear, "Gear", 0, 9)
        self.ax_gear.set_yticks(range(1, 9))

        # Throttle/Brake
        self.ax_ctrl = self._fig.add_subplot(gs[2])
        self.line_t1, = self.ax_ctrl.plot([], [], color=_COL1, linewidth=2)
        self.line_t2, = self.ax_ctrl.plot([], [], color=_COL2, linewidth=2, linestyle='--')
        self.line_b1, = self.ax_ctrl.plot([], [], color=_COL1, linewidth=2, alpha=0.5)
        self.line_b2, = self.ax_ctrl.plot([], [], color=_COL2, linewidth=2, linestyle='--', alpha=0.5)
        self._setup_ax(self.ax_ctrl, "Throt/Brake (%)", -5, 105)
        self.ax_ctrl.set_xlabel("Time (s)", color="#F0F0F0")

        self.canvas = FigureCanvas(self._fig)
        root.addWidget(self.canvas)

    def _setup_ax(self, ax, ylabel, ymin, ymax):
        ax.set_facecolor(_BG)
        ax.set_ylabel(ylabel, color="#F0F0F0", fontsize=10)
        ax.set_ylim(ymin, ymax)
        ax.tick_params(colors="#F0F0F0")
        for spine in ax.spines.values():
            spine.set_edgecolor("#555555")

    def _on_auto_check(self, state):
        self._auto_battle = (state == Qt.Checked)

    def _ensure_buffer(self, code):
        if code not in self._time_buffers:
            self._time_buffers[code] = deque()

    def _append_sample(self, code, driver, session_t):
        self._ensure_buffer(code)
        tb = self._time_buffers[code]
        tb.append({
            "t": session_t,
            "speed": float(driver.get("speed") or 0),
            "gear": int(driver.get("gear") or 0),
            "throttle": float(driver.get("throttle") or 0),
            "brake": float(driver.get("brake") or 0) * 100
        })
        cutoff = session_t - _TIME_WINDOW
        while tb and tb[0]["t"] < cutoff:
            tb.popleft()

    def _check_auto_battle(self, drivers):
        # Sort by distance on track
        sorted_drivers = sorted(drivers.items(), key=lambda x: (x[1].get('lap') or 0) * 100000 + (x[1].get('dist') or 0), reverse=True)
        for i in range(len(sorted_drivers) - 1):
            code_a, driver_a = sorted_drivers[i]
            code_b, driver_b = sorted_drivers[i+1]
            dist_a = (driver_a.get('lap') or 0) * 100000 + (driver_a.get('dist') or 0)
            dist_b = (driver_b.get('lap') or 0) * 100000 + (driver_b.get('dist') or 0)
            
            # Rough distance to time gap mapping: ~80m/s = 1 second gap at high speed
            if (dist_a - dist_b) < 80:
                if self.d1_combo.currentText() != code_a:
                    self.d1_combo.setCurrentText(code_a)
                if self.d2_combo.currentText() != code_b:
                    self.d2_combo.setCurrentText(code_b)
                # Only track the lead battle
                return

    def on_telemetry_data(self, data):
        if "frame" not in data or not data["frame"]:
            return
        drivers = data["frame"].get("drivers", {})
        if not drivers:
            return

        session_t = float(data["frame"].get("t") or 0)
        
        incoming = sorted(drivers.keys())
        if incoming != self._known_drivers:
            self.d1_combo.blockSignals(True)
            self.d2_combo.blockSignals(True)
            self.d1_combo.clear()
            self.d2_combo.clear()
            self.d1_combo.addItems(incoming)
            self.d2_combo.addItems(incoming)
            self.d1_combo.blockSignals(False)
            self.d2_combo.blockSignals(False)
            self._known_drivers = incoming
            if len(incoming) >= 2:
                self.d1_combo.setCurrentIndex(0)
                self.d2_combo.setCurrentIndex(1)

        for code, driver in drivers.items():
            self._append_sample(code, driver, session_t)

        if self._auto_battle:
            self._check_auto_battle(drivers)

        self._redraw()

    def _get_lines(self, code):
        tb = self._time_buffers.get(code)
        if not tb: return [], [], [], [], []
        samples = list(tb)
        t_now = samples[-1]["t"]
        xs = [s["t"] - t_now for s in samples]
        speeds = [s["speed"] for s in samples]
        gears = [s["gear"] for s in samples]
        throttles = [s["throttle"] for s in samples]
        brakes = [s["brake"] for s in samples]
        return xs, speeds, gears, throttles, brakes

    def _redraw(self, *args):
        c1 = self.d1_combo.currentText()
        c2 = self.d2_combo.currentText()
        
        # Update legend labels dynamically
        if self.ax_speed.get_legend():
            self.ax_speed.get_legend().get_texts()[0].set_text(c1 if c1 else "Driver 1")
            self.ax_speed.get_legend().get_texts()[1].set_text(c2 if c2 else "Driver 2")
        
        x1, s1, g1, t1, b1 = self._get_lines(c1)
        x2, s2, g2, t2, b2 = self._get_lines(c2)

        self.line_s1.set_data(x1, s1)
        self.line_s2.set_data(x2, s2)
        
        self.line_g1.set_data(x1, g1)
        self.line_g2.set_data(x2, g2)
        
        self.line_t1.set_data(x1, t1)
        self.line_t2.set_data(x2, t2)
        self.line_b1.set_data(x1, b1)
        self.line_b2.set_data(x2, b2)

        for ax in (self.ax_speed, self.ax_gear, self.ax_ctrl):
            ax.set_xlim(-_TIME_WINDOW, 0)
            
        self.canvas.draw_idle()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Head-to-Head Battle")
    window = BattleTelemetryWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
