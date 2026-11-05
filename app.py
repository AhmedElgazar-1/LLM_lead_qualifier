import sys
import os
import json
import traceback
import webbrowser
import pandas as pd
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QComboBox, QSpinBox, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QGroupBox,
    QDialog, QTextEdit, QMessageBox, QSplitter, QFrame, QRadioButton,
    QButtonGroup, QAbstractItemView, QCheckBox
)
from PyQt5.QtGui import QFont, QIcon, QColor, QCursor

import b2b_engine

CONFIG_FILE = "config.json"
CRASH_LOG = "crash_log.txt"

# ---------------------------------------------------------------------
# SAFE STRING HELPER (Prevents PyQt TypeError with float/NaN values)
# ---------------------------------------------------------------------
def safe_str(val, default=""):
    if val is None or pd.isna(val):
        return default
    s = str(val).strip()
    if s.lower() in ["nan", "none", "null"]:
        return default
    return s


# ---------------------------------------------------------------------
# GLOBAL UNCAUGHT EXCEPTION HANDLER
# ---------------------------------------------------------------------
def global_excepthook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("CRITICAL ERROR:", err_msg, file=sys.stderr)
    try:
        with open(CRASH_LOG, "w", encoding="utf-8") as f:
            f.write(err_msg)
    except Exception:
        pass
    if QApplication.instance():
        try:
            QMessageBox.critical(
                None, "Application Error",
                f"An error occurred:\n\n{exc_value}\n\nDetails have been saved to crash_log.txt"
            )
        except Exception:
            pass

sys.excepthook = global_excepthook


DEFAULT_CONFIG = {
    "api_key": b2b_engine.get_env_api_key(),
    "model_name": b2b_engine.DEFAULT_MODEL,
    "our_description": "",
    "target_types": ["B2B"],
    "allow_professional_services": False,
    "targets_confirmed": False,
    "concurrency": 5,
    "chunk_size": 25,
    "page_size": 25
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                c = json.load(f)
                c.pop("api_key", None)  # the key only comes from .env, never from config.json
                return {**DEFAULT_CONFIG, **c}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        to_save = {k: v for k, v in cfg.items() if k != "api_key"}  # never write the key to disk
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")


# =====================================================================
# BACKGROUND PROCESSING WORKER (QThread)
# =====================================================================
class EnrichmentWorker(QThread):
    progress_signal = pyqtSignal(int, int, str)        # current, total, status_message
    row_completed_signal = pyqtSignal(dict)           # enriched row dict
    finished_signal = pyqtSignal(list)                 # all processed rows
    error_signal = pyqtSignal(str)

    def __init__(self, engine, rows, website_col, company_col, concurrency=5):
        super().__init__()
        self.engine = engine
        self.rows = rows
        self.website_col = website_col
        self.company_col = company_col
        self.concurrency = max(1, concurrency)
        self._is_running = True
        self._is_paused = False

    def stop(self):
        self._is_running = False

    def pause(self):
        self._is_paused = True

    def resume(self):
        self._is_paused = False

    def run(self):
        results = []
        total = len(self.rows)
        
        for i, row in enumerate(self.rows):
            while self._is_paused and self._is_running:
                self.msleep(200)

            if not self._is_running:
                break

            target_url = safe_str(row.get(self.website_col, ""))
            display_url = target_url[:35] if target_url else "No URL"
            self.progress_signal.emit(i + 1, total, f"Analyzing [{i+1}/{total}]: {display_url}...")

            try:
                enriched = self.engine.process_single(
                    row, 
                    website_col=self.website_col, 
                    company_col=self.company_col
                )
            except Exception as e:
                enriched = dict(row)
                enriched["website"] = target_url
                enriched["company_name"] = safe_str(row.get(self.company_col, ""))
                enriched["ai_verdict"] = "Needs Review"
                enriched["ai_why"] = f"Processing exception: {e}"
                enriched["ai_error"] = str(e)

            results.append(enriched)
            self.row_completed_signal.emit(enriched)

        self.finished_signal.emit(results)


# =====================================================================
# DOSSIER / LEAD DETAILS DIALOG
# =====================================================================
class LeadDetailsDialog(QDialog):
    def __init__(self, record, parent=None):
        super().__init__(parent)
        self.record = record
        comp_name = safe_str(record.get("company_name")) or safe_str(record.get("website"), "Details")
        self.setWindowTitle(f"Lead Dossier - {comp_name}")
        self.resize(750, 620)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header Box with Status Badge
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background-color: #1e2430;
                border-radius: 8px;
                padding: 12px;
                border: 1px solid #2e384d;
            }
        """)
        h_layout = QHBoxLayout(header_frame)
        
        comp_title = safe_str(self.record.get("company_name"), "Unnamed Company")
        title_label = QLabel(f"<h2 style='margin:0; color:#f8fafc;'>{comp_title}</h2>")
        h_layout.addWidget(title_label)
        h_layout.addStretch()

        verdict = safe_str(self.record.get("ai_verdict"), "Unknown")
        badge_color = "#10b981" if verdict == "Qualified" else ("#f59e0b" if verdict == "Needs Review" else "#ef4444")
        badge_label = QLabel(f"<b>{verdict.upper()}</b>")
        badge_label.setStyleSheet(f"""
            background-color: {badge_color};
            color: #ffffff;
            font-size: 13px;
            font-weight: bold;
            padding: 6px 16px;
            border-radius: 6px;
        """)
        h_layout.addWidget(badge_label)
        layout.addWidget(header_frame)

        # Info Grid
        info_frame = QFrame()
        info_frame.setStyleSheet("background-color: #161b22; border-radius: 8px; padding: 12px; border: 1px solid #2a3447;")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(8)

        url = safe_str(self.record.get("website"))
        about_url = safe_str(self.record.get("about_url"))
        emp = safe_str(self.record.get("employees_mentioned"), "Not detected")
        biz_model = safe_str(self.record.get("ai_business_model"), "Unknown")
        eligible = self.record.get("ai_eligible")
        eligible_str = "Eligible" if eligible in [True, "True", "true"] else ("Not Eligible" if eligible in [False, "False", "false"] else "Unclear / Review")

        row1 = QLabel(f"<b>Website:</b> <a href='{url}' style='color:#38bdf8;'>{url}</a> &nbsp;&nbsp;|&nbsp;&nbsp; <b>About URL:</b> {about_url or 'Home only'}")
        row1.setOpenExternalLinks(True)
        row1.setStyleSheet("color: #cbd5e1; font-size: 13px;")
        
        row2 = QLabel(f"<b>Business Model:</b> {biz_model} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Eligibility:</b> {eligible_str} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Employees:</b> {emp}")
        row2.setStyleSheet("color: #cbd5e1; font-size: 13px;")

        info_layout.addWidget(row1)
        info_layout.addWidget(row2)
        layout.addWidget(info_frame)

        # AI Reasoning ("Why")
        why_group = QGroupBox("AI Qualification Verdict & Analysis")
        why_group.setStyleSheet("QGroupBox { font-weight: bold; color: #38bdf8; font-size: 13px; }")
        w_layout = QVBoxLayout(why_group)
        why_text = QTextEdit()
        why_text.setReadOnly(True)
        why_text.setText(safe_str(self.record.get("ai_why"), "No detailed analysis provided."))
        why_text.setStyleSheet("background-color: #0f172a; color: #e2e8f0; font-size: 13px; border: 1px solid #334155; border-radius: 6px; padding: 8px;")
        why_text.setMaximumHeight(100)
        w_layout.addWidget(why_text)
        layout.addWidget(why_group)

        # Pitch Angle & Fit
        pitch_angle = safe_str(self.record.get("ai_pitch_angle"))
        if pitch_angle:
            pitch_group = QGroupBox("Suggested Outreach / Pitch Angle")
            pitch_group.setStyleSheet("QGroupBox { font-weight: bold; color: #34d399; font-size: 13px; }")
            p_layout = QVBoxLayout(pitch_group)
            pitch_text = QTextEdit()
            pitch_text.setReadOnly(True)
            pitch_text.setText(pitch_angle)
            pitch_text.setStyleSheet("background-color: #064e3b; color: #ecfdf5; font-size: 13px; border: 1px solid #059669; border-radius: 6px; padding: 8px;")
            pitch_text.setMaximumHeight(70)
            p_layout.addWidget(pitch_text)
            layout.addWidget(pitch_group)

        # Strengths & Weaknesses
        sw_frame = QFrame()
        sw_layout = QHBoxLayout(sw_frame)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        
        str_box = QGroupBox("Identified Strengths")
        str_box.setStyleSheet("QGroupBox { font-weight: bold; color: #10b981; }")
        sb_layout = QVBoxLayout(str_box)
        st_text = QTextEdit()
        st_text.setReadOnly(True)
        st_text.setText(safe_str(self.record.get("ai_strengths"), "None identified"))
        st_text.setStyleSheet("background-color: #0f172a; color: #cbd5e1; border: 1px solid #1e293b; border-radius: 6px;")
        sb_layout.addWidget(st_text)
        sw_layout.addWidget(str_box)

        wk_box = QGroupBox("Identified Weaknesses / Risks")
        wk_box.setStyleSheet("QGroupBox { font-weight: bold; color: #f87171; }")
        wb_layout = QVBoxLayout(wk_box)
        wk_text = QTextEdit()
        wk_text.setReadOnly(True)
        wk_text.setText(safe_str(self.record.get("ai_weaknesses"), "None identified"))
        wk_text.setStyleSheet("background-color: #0f172a; color: #cbd5e1; border: 1px solid #1e293b; border-radius: 6px;")
        wb_layout.addWidget(wk_text)
        sw_layout.addWidget(wk_box)
        layout.addWidget(sw_frame)

        # Scraped Description
        desc_group = QGroupBox("Scraped Website Text")
        desc_group.setStyleSheet("QGroupBox { font-weight: bold; color: #94a3b8; }")
        d_layout = QVBoxLayout(desc_group)
        desc_text = QTextEdit()
        desc_text.setReadOnly(True)
        desc_text.setText(safe_str(self.record.get("description"), "No description captured"))
        desc_text.setStyleSheet("background-color: #090d16; color: #94a3b8; font-size: 12px; border: 1px solid #1e293b; border-radius: 6px;")
        desc_text.setMaximumHeight(100)
        d_layout.addWidget(desc_text)
        layout.addWidget(desc_group)

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 24px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
        """)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


# =====================================================================
# COMPANY DESCRIPTION PROMPT
# =====================================================================
class TargetTypeBox(QGroupBox):
    """Checkboxes for which kinds of companies the user wants to find."""
    def __init__(self, target_types=None, allow_prof=False, parent=None):
        super().__init__("Which companies do you want to find?", parent)
        self.setStyleSheet("QGroupBox { font-weight: bold; color: #38bdf8; }")
        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        selected = target_types or ["B2B"]
        self.cb_b2b = QCheckBox("B2B: companies that sell to other businesses")
        self.cb_b2c = QCheckBox("B2C: companies that sell to individual consumers")
        self.cb_both = QCheckBox("Both: companies that sell to businesses and consumers")
        self.cb_b2b.setChecked("B2B" in selected)
        self.cb_b2c.setChecked("B2C" in selected)
        self.cb_both.setChecked("Both" in selected)
        for cb in (self.cb_b2b, self.cb_b2c, self.cb_both):
            cb.setStyleSheet("font-weight: normal; color: #e2e8f0;")
            lay.addWidget(cb)

        self.cb_prof = QCheckBox("Also include professional services (consulting, agencies, recruiting, etc.)")
        self.cb_prof.setChecked(bool(allow_prof))
        self.cb_prof.setStyleSheet("font-weight: normal; color: #e2e8f0; margin-top: 6px;")
        lay.addWidget(self.cb_prof)

    def get_types(self):
        types = []
        if self.cb_b2b.isChecked():
            types.append("B2B")
        if self.cb_b2c.isChecked():
            types.append("B2C")
        if self.cb_both.isChecked():
            types.append("Both")
        return types

    def get_allow_prof(self):
        return self.cb_prof.isChecked()


class CompanyDescriptionDialog(QDialog):
    def __init__(self, current="", target_types=None, allow_prof=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Up Your Search")
        self.resize(580, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("<h3>What does your company do?</h3>")
        title.setStyleSheet("color: #f1f5f9;")
        layout.addWidget(title)

        hint = QLabel(
            "Every company is judged against this description, so be specific: "
            "what you sell, who you sell to, and what problem you solve."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94a3b8;")
        layout.addWidget(hint)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(current)
        self.text_edit.setPlaceholderText(
            "Example: We sell inventory software to warehouses and want to find "
            "companies that manage large stock across several locations..."
        )
        self.text_edit.setStyleSheet(
            "background-color: #1e293b; color: white; padding: 8px; border-radius: 6px; "
            "border: 1px solid #475569; font-size: 13px;"
        )
        layout.addWidget(self.text_edit, 1)

        self.target_box = TargetTypeBox(target_types, allow_prof)
        layout.addWidget(self.target_box)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: #475569; color: white; padding: 8px 18px; border-radius: 6px; border: none;")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Save && Continue")
        ok_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold; padding: 8px 22px; border-radius: 6px; border: none;")
        ok_btn.clicked.connect(self.validate_and_accept)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

    def validate_and_accept(self):
        if len(self.text_edit.toPlainText().strip()) < 20:
            QMessageBox.warning(self, "Too Short", "Please describe your company in at least a sentence or two.")
            return
        if not self.target_box.get_types():
            QMessageBox.warning(self, "Pick At Least One", "Tick at least one type of company you want to find.")
            return
        self.accept()

    def get_text(self):
        return self.text_edit.toPlainText().strip()

    def get_types(self):
        return self.target_box.get_types()

    def get_allow_prof(self):
        return self.target_box.get_allow_prof()


# =====================================================================
# SETTINGS DIALOG
# =====================================================================
class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.setWindowTitle("Engine & AI Settings")
        self.resize(620, 700)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("<h3>AI Model & Qualification Settings</h3>")
        title.setStyleSheet("color: #f1f5f9;")
        layout.addWidget(title)

        # API Key
        key_label = QLabel("Google Gemini API Key (loaded from .env; changes here last for this session only):")
        key_label.setStyleSheet("color: #cbd5e1; font-weight: bold;")
        self.key_edit = QLineEdit(safe_str(self.config.get("api_key", "")))
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setStyleSheet("background-color: #1e293b; color: white; padding: 8px; border-radius: 6px; border: 1px solid #475569;")
        
        key_toggle_btn = QPushButton("Show / Hide Key")
        key_toggle_btn.setStyleSheet("background-color: #334155; color: #e2e8f0; padding: 6px 12px; border-radius: 4px; border: none;")
        key_toggle_btn.clicked.connect(self.toggle_key_visibility)
        
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit)
        key_row.addWidget(key_toggle_btn)
        layout.addWidget(key_label)
        layout.addLayout(key_row)

        # Model Selector
        model_label = QLabel("Gemini Model:")
        model_label.setStyleSheet("color: #cbd5e1; font-weight: bold;")
        self.model_combo = QComboBox()
        self.model_combo.addItems([
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
            "gemini-1.5-flash",
            "gemini-2.0-flash"
        ])
        current_model = safe_str(self.config.get("model_name"), "gemini-3.5-flash-lite")
        idx = self.model_combo.findText(current_model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.addItem(current_model)
            self.model_combo.setCurrentText(current_model)
        self.model_combo.setStyleSheet("background-color: #1e293b; color: white; padding: 8px; border-radius: 6px; border: 1px solid #475569;")
        layout.addWidget(model_label)
        layout.addWidget(self.model_combo)

        # Our Company Description
        our_label = QLabel("Our Company Description (Evaluated against target companies):")
        our_label.setStyleSheet("color: #cbd5e1; font-weight: bold;")
        self.our_text = QTextEdit()
        self.our_text.setText(safe_str(self.config.get("our_description"), b2b_engine.DEFAULT_OUR_DESC))
        self.our_text.setStyleSheet("background-color: #1e293b; color: white; padding: 8px; border-radius: 6px; border: 1px solid #475569; font-size: 13px;")
        self.our_text.setMaximumHeight(150)
        layout.addWidget(our_label)
        layout.addWidget(self.our_text)

        # Target company types
        self.target_box = TargetTypeBox(
            self.config.get("target_types"),
            self.config.get("allow_professional_services", False)
        )
        layout.addWidget(self.target_box)

        # Concurrency
        conc_label = QLabel("Scraping Concurrency (Threads):")
        conc_label.setStyleSheet("color: #cbd5e1; font-weight: bold;")
        self.conc_spin = QSpinBox()
        self.conc_spin.setRange(1, 20)
        self.conc_spin.setValue(int(self.config.get("concurrency", 5)))
        self.conc_spin.setStyleSheet("background-color: #1e293b; color: white; padding: 6px; border-radius: 6px; border: 1px solid #475569;")
        layout.addWidget(conc_label)
        layout.addWidget(self.conc_spin)

        layout.addStretch()

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: #475569; color: white; padding: 8px 18px; border-radius: 6px; border: none;")
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold; padding: 8px 22px; border-radius: 6px; border: none;")
        save_btn.clicked.connect(self.save_and_close)
        
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def toggle_key_visibility(self):
        if self.key_edit.echoMode() == QLineEdit.Password:
            self.key_edit.setEchoMode(QLineEdit.Normal)
        else:
            self.key_edit.setEchoMode(QLineEdit.Password)

    def save_and_close(self):
        if not self.target_box.get_types():
            QMessageBox.warning(self, "Pick At Least One", "Tick at least one type of company you want to find.")
            return
        self.config["target_types"] = self.target_box.get_types()
        self.config["allow_professional_services"] = self.target_box.get_allow_prof()
        self.config["targets_confirmed"] = True
        self.config["api_key"] = self.key_edit.text().strip()
        self.config["model_name"] = self.model_combo.currentText().strip()
        self.config["our_description"] = self.our_text.toPlainText().strip()
        self.config["concurrency"] = self.conc_spin.value()
        save_config(self.config)
        self.accept()


# =====================================================================
# MAIN APPLICATION WINDOW
# =====================================================================
class B2BProspectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Lead Filter")
        self.resize(1280, 850)
        self.setMinimumSize(1000, 700)

        self.config = load_config()
        self.engine = b2b_engine.B2BEngine(**self.engine_settings())

        # State variables
        self.raw_df = None
        self.file_path = None
        self.records = []          # All processed records
        self.filtered_records = [] # Currently filtered list
        self.visible_count = 25    # Displays 25 at a time, click Show More to expand!
        self.worker = None

        self.init_theme()
        self.init_ui()

        # Ask for the company description right after the window shows, if we don't have one
        QTimer.singleShot(300, self.ensure_company_description)

    def engine_settings(self):
        return dict(
            api_key=self.config.get("api_key"),
            model_name=self.config.get("model_name"),
            our_description=self.config.get("our_description"),
            target_types=self.config.get("target_types"),
            allow_professional_services=self.config.get("allow_professional_services", False),
        )

    def ensure_company_description(self):
        """Returns True once we have a company description and the user has picked target types."""
        if safe_str(self.config.get("our_description")) and self.config.get("targets_confirmed"):
            return True
        dlg = CompanyDescriptionDialog(
            safe_str(self.config.get("our_description")),
            self.config.get("target_types"),
            self.config.get("allow_professional_services", False),
            self
        )
        if dlg.exec_() != QDialog.Accepted:
            return False
        self.config["our_description"] = dlg.get_text()
        self.config["target_types"] = dlg.get_types()
        self.config["allow_professional_services"] = dlg.get_allow_prof()
        self.config["targets_confirmed"] = True
        save_config(self.config)
        self.engine.update_settings(**self.engine_settings())
        return True

    def init_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0f172a;
                color: #e2e8f0;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 10px;
                padding: 12px;
                font-weight: bold;
                color: #94a3b8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #334155;
                color: #64748b;
            }
            QTableWidget {
                background-color: #1e293b;
                alternate-background-color: #162032;
                border: 1px solid #334155;
                border-radius: 8px;
                gridline-color: #334155;
                color: #f1f5f9;
                selection-background-color: #3b82f6;
                selection-color: white;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-weight: bold;
                padding: 8px;
                border: none;
                border-bottom: 2px solid #334155;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #38bdf8;
            }
            QProgressBar {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                text-align: center;
                color: white;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #10b981;
                border-radius: 5px;
            }
            QScrollBar:vertical {
                background: #0f172a;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # Top Header & Stats
        header_bar = QHBoxLayout()
        app_title = QLabel("<h2>🚀 AI Lead Filter</h2>")
        app_title.setStyleSheet("color: #f8fafc; margin: 0;")
        header_bar.addWidget(app_title)
        header_bar.addStretch()

        settings_btn = QPushButton("⚙️ Settings")
        settings_btn.setStyleSheet("background-color: #334155; padding: 8px 16px;")
        settings_btn.clicked.connect(self.open_settings)
        header_bar.addWidget(settings_btn)
        main_layout.addLayout(header_bar)

        # File Input & Run Control Panel
        control_group = QGroupBox("1. Select Spreadsheet & Configure Scraping")
        ctrl_layout = QVBoxLayout(control_group)

        # File Select Row
        file_row = QHBoxLayout()
        self.select_file_btn = QPushButton("📁 Browse Sheet (.xlsx / .csv)")
        self.select_file_btn.setStyleSheet("background-color: #0284c7; padding: 9px 18px; font-size: 13px;")
        self.select_file_btn.clicked.connect(self.browse_file)
        file_row.addWidget(self.select_file_btn)

        self.file_path_label = QLabel("No spreadsheet loaded. Please select an Excel or CSV file.")
        self.file_path_label.setStyleSheet("color: #94a3b8; font-style: italic;")
        file_row.addWidget(self.file_path_label, 1)
        ctrl_layout.addLayout(file_row)

        # Column selection & options row
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)

        opts_row.addWidget(QLabel("Website Column:"))
        self.col_website_combo = QComboBox()
        self.col_website_combo.setMinimumWidth(160)
        opts_row.addWidget(self.col_website_combo)

        opts_row.addWidget(QLabel("Company Name Column:"))
        self.col_company_combo = QComboBox()
        self.col_company_combo.setMinimumWidth(160)
        opts_row.addWidget(self.col_company_combo)

        opts_row.addWidget(QLabel("Rows to Process:"))
        self.row_limit_combo = QComboBox()
        self.row_limit_combo.addItems(["First 25 rows", "First 50 rows", "First 100 rows", "First 250 rows", "All Rows"])
        opts_row.addWidget(self.row_limit_combo)

        opts_row.addStretch()

        # Action Buttons
        self.start_btn = QPushButton("▶ Start AI Screener")
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet("background-color: #10b981; font-size: 13px; padding: 9px 20px;")
        self.start_btn.clicked.connect(self.start_processing)
        opts_row.addWidget(self.start_btn)

        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setStyleSheet("background-color: #f59e0b; padding: 9px 16px;")
        self.pause_btn.clicked.connect(self.toggle_pause)
        opts_row.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #ef4444; padding: 9px 16px;")
        self.stop_btn.clicked.connect(self.stop_processing)
        opts_row.addWidget(self.stop_btn)

        ctrl_layout.addLayout(opts_row)

        # Progress bar & status message
        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(20)
        prog_row.addWidget(self.progress_bar, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #38bdf8; font-weight: bold; min-width: 280px;")
        prog_row.addWidget(self.status_label)
        ctrl_layout.addLayout(prog_row)

        main_layout.addWidget(control_group)

        # Results, Filter Bar & Pagination
        results_group = QGroupBox("2. Processed Results & Lead Qualification (25 Displayed at a Time)")
        res_layout = QVBoxLayout(results_group)
        res_layout.setSpacing(10)

        # Filter and Search Bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(10)

        filter_bar.addWidget(QLabel("<b>Filter by Status:</b>"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            "All Records (0)",
            "Qualified Only (0)",
            "Needs Review Only (0)",
            "Not Qualified (0)",
            "Errors / Unusable (0)"
        ])
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        filter_bar.addWidget(self.filter_combo)

        filter_bar.addWidget(QLabel("<b>Search:</b>"))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search company, website, reason, pitch...")
        self.search_box.textChanged.connect(self.apply_filter)
        filter_bar.addWidget(self.search_box, 1)

        # Export Buttons
        self.export_filtered_btn = QPushButton("📥 Export Filtered View (Excel)")
        self.export_filtered_btn.setStyleSheet("background-color: #059669; padding: 8px 16px; font-weight: bold;")
        self.export_filtered_btn.clicked.connect(self.export_filtered)
        self.export_filtered_btn.setEnabled(False)
        filter_bar.addWidget(self.export_filtered_btn)

        self.export_all_btn = QPushButton("📑 Export All (Excel)")
        self.export_all_btn.setStyleSheet("background-color: #0f766e; padding: 8px 16px;")
        self.export_all_btn.clicked.connect(self.export_all)
        self.export_all_btn.setEnabled(False)
        filter_bar.addWidget(self.export_all_btn)

        res_layout.addLayout(filter_bar)

        # Results Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "#", "Filter Result", "Company Name", "Website", "Eligibility", "Reasoning / Summary", "Actions"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.on_table_row_double_click)
        res_layout.addWidget(self.table, 1)

        # SHOW MORE / PAGINATION BAR (25 at a time)
        pagination_bar = QHBoxLayout()
        pagination_bar.setContentsMargins(4, 4, 4, 4)

        self.page_info_label = QLabel("Showing 0 of 0 records")
        self.page_info_label.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 13px;")
        pagination_bar.addWidget(self.page_info_label)

        pagination_bar.addStretch()

        self.show_more_btn = QPushButton("➕ Show More (+25 Rows)")
        self.show_more_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 28px;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
            QPushButton:disabled {
                background-color: #334155;
                color: #64748b;
            }
        """)
        self.show_more_btn.clicked.connect(self.show_more_records)
        self.show_more_btn.setEnabled(False)
        pagination_bar.addWidget(self.show_more_btn)

        self.show_all_btn = QPushButton("Show All")
        self.show_all_btn.setStyleSheet("background-color: #475569; padding: 10px 18px;")
        self.show_all_btn.clicked.connect(self.show_all_records)
        self.show_all_btn.setEnabled(False)
        pagination_bar.addWidget(self.show_all_btn)

        res_layout.addLayout(pagination_bar)
        main_layout.addWidget(results_group, 1)

    # =====================================================================
    # FILE HANDLING & COLUMN SELECTION
    # =====================================================================
    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Spreadsheet", "", "Spreadsheets (*.xlsx *.xls *.csv)"
        )
        if not path:
            return

        try:
            if path.lower().endswith(".csv"):
                df = pd.read_csv(path)
            else:
                df = pd.read_excel(path)

            # Strip whitespace from columns to avoid issues with ' website' vs 'website'
            df.columns = df.columns.astype(str).str.strip()
            # Drop completely blank rows
            df = df.dropna(how="all")

            self.file_path = path
            self.raw_df = df
            self.file_path_label.setText(f"Loaded: <b>{os.path.basename(path)}</b> ({len(df):,} total rows)")
            self.file_path_label.setStyleSheet("color: #38bdf8;")

            cols = list(df.columns)
            self.col_website_combo.clear()
            self.col_website_combo.addItems(cols)

            self.col_company_combo.clear()
            self.col_company_combo.addItems(["(None)"] + cols)

            # Auto-detect website column
            web_candidates = ["website", "web", "url", "link", "domain", "company website", "site"]
            matched_web = False
            for cand in web_candidates:
                for c in cols:
                    if c.lower() == cand:
                        self.col_website_combo.setCurrentText(c)
                        matched_web = True
                        break
                if matched_web:
                    break

            # Auto-detect company name column
            comp_candidates = ["company name", "company", "name", "account name", "organization"]
            matched_comp = False
            for cand in comp_candidates:
                for c in cols:
                    if c.lower() == cand:
                        self.col_company_combo.setCurrentText(c)
                        matched_comp = True
                        break
                if matched_comp:
                    break

            self.start_btn.setEnabled(True)
            self.status_label.setText(f"File loaded: {len(df)} rows. Click Start to begin.")

        except Exception as e:
            QMessageBox.critical(self, "Error Loading File", f"Could not open spreadsheet:\n{e}")

    # =====================================================================
    # PROCESSING CONTROLS (START / PAUSE / STOP)
    # =====================================================================
    def start_processing(self):
        if not self.ensure_company_description():
            QMessageBox.information(self, "Company Description Needed",
                                    "Add your company description first so companies can be judged against it.")
            return
        if self.raw_df is None or self.raw_df.empty:
            QMessageBox.warning(self, "No Data", "Please load a spreadsheet first.")
            return

        web_col = self.col_website_combo.currentText()
        comp_col = self.col_company_combo.currentText()
        if comp_col == "(None)":
            comp_col = None

        # Determine row limit
        limit_txt = self.row_limit_combo.currentText()
        if "25" in limit_txt:
            limit = 25
        elif "50" in limit_txt:
            limit = 50
        elif "100" in limit_txt:
            limit = 100
        elif "250" in limit_txt:
            limit = 250
        else:
            limit = len(self.raw_df)

        rows_to_process = self.raw_df.head(limit).to_dict("records")

        # Reset states
        self.records = []
        self.filtered_records = []
        self.visible_count = 25
        self.table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(rows_to_process))

        self.start_btn.setEnabled(False)
        self.select_file_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.export_filtered_btn.setEnabled(False)
        self.export_all_btn.setEnabled(False)

        # Launch background worker
        self.worker = EnrichmentWorker(
            engine=self.engine,
            rows=rows_to_process,
            website_col=web_col,
            company_col=comp_col,
            concurrency=self.config.get("concurrency", 5)
        )
        self.worker.progress_signal.connect(self.on_worker_progress)
        self.worker.row_completed_signal.connect(self.on_row_completed)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    def toggle_pause(self):
        if not self.worker:
            return
        if self.worker._is_paused:
            self.worker.resume()
            self.pause_btn.setText("⏸ Pause")
            self.status_label.setText("Resumed processing...")
        else:
            self.worker.pause()
            self.pause_btn.setText("▶ Resume")
            self.status_label.setText("Processing paused.")

    def stop_processing(self):
        if self.worker:
            self.status_label.setText("Stopping worker...")
            self.worker.stop()
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)

    def on_worker_progress(self, current, total, msg):
        self.progress_bar.setValue(current)
        self.status_label.setText(msg)

    def on_row_completed(self, enriched_row):
        self.records.append(enriched_row)
        self.update_filter_counts()
        self.apply_filter()

    def on_worker_finished(self, all_results):
        self.status_label.setText(f"✓ Complete! Processed {len(all_results)} leads.")
        self.start_btn.setEnabled(True)
        self.select_file_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.export_filtered_btn.setEnabled(len(self.records) > 0)
        self.export_all_btn.setEnabled(len(self.records) > 0)
        self.update_filter_counts()
        self.apply_filter()

    # =====================================================================
    # FILTERING, SEARCH & PAGINATION (25 at a time)
    # =====================================================================
    def update_filter_counts(self):
        total = len(self.records)
        q_cnt = sum(1 for r in self.records if safe_str(r.get("ai_verdict")) == "Qualified")
        rev_cnt = sum(1 for r in self.records if safe_str(r.get("ai_verdict")) == "Needs Review")
        nq_cnt = sum(1 for r in self.records if safe_str(r.get("ai_verdict")) == "Not Qualified")
        err_cnt = sum(1 for r in self.records if r.get("ai_error") or safe_str(r.get("description")).startswith("ERROR"))

        curr_idx = self.filter_combo.currentIndex()
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItems([
            f"All Records ({total})",
            f"Qualified Only ({q_cnt})",
            f"Needs Review Only ({rev_cnt})",
            f"Not Qualified ({nq_cnt})",
            f"Errors / Unusable ({err_cnt})"
        ])
        if 0 <= curr_idx < self.filter_combo.count():
            self.filter_combo.setCurrentIndex(curr_idx)
        self.filter_combo.blockSignals(False)

    def apply_filter(self):
        filter_idx = self.filter_combo.currentIndex()
        query = self.search_box.text().strip().lower()

        filtered = []
        for r in self.records:
            verdict = safe_str(r.get("ai_verdict"))
            has_error = bool(r.get("ai_error") or safe_str(r.get("description")).startswith("ERROR"))

            # Status filter
            if filter_idx == 1 and verdict != "Qualified":
                continue
            elif filter_idx == 2 and verdict != "Needs Review":
                continue
            elif filter_idx == 3 and verdict != "Not Qualified":
                continue
            elif filter_idx == 4 and not has_error:
                continue

            # Query search
            if query:
                combined_text = " ".join([
                    safe_str(r.get("company_name")),
                    safe_str(r.get("website")),
                    safe_str(r.get("ai_why")),
                    safe_str(r.get("ai_pitch_angle")),
                    safe_str(r.get("ai_strengths")),
                    safe_str(r.get("ai_weaknesses")),
                ]).lower()
                if query not in combined_text:
                    continue

            filtered.append(r)

        self.filtered_records = filtered
        self.render_table()

    def render_table(self):
        total_matching = len(self.filtered_records)
        to_display = self.filtered_records[:self.visible_count]
        
        self.table.setRowCount(len(to_display))

        for row_idx, record in enumerate(to_display):
            # 0. Index
            idx_item = QTableWidgetItem(str(row_idx + 1))
            idx_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 0, idx_item)

            # 1. Filter Result (Badge)
            verdict = safe_str(record.get("ai_verdict"), "Pending")
            verdict_item = QTableWidgetItem(verdict)
            verdict_item.setTextAlignment(Qt.AlignCenter)
            if verdict == "Qualified":
                verdict_item.setForeground(QColor("#10b981"))
            elif verdict == "Needs Review":
                verdict_item.setForeground(QColor("#f59e0b"))
            elif verdict == "Not Qualified":
                verdict_item.setForeground(QColor("#ef4444"))
            else:
                verdict_item.setForeground(QColor("#94a3b8"))
            self.table.setItem(row_idx, 1, verdict_item)

            # 2. Company Name
            comp_name = safe_str(record.get("company_name"), "—")
            self.table.setItem(row_idx, 2, QTableWidgetItem(comp_name))

            # 3. Website
            url = safe_str(record.get("website"), "")
            web_item = QTableWidgetItem(url)
            web_item.setForeground(QColor("#38bdf8"))
            self.table.setItem(row_idx, 3, web_item)

            # 4. Eligibility
            eligible = record.get("ai_eligible")
            if eligible in [True, "True", "true"]:
                elig_str = "Eligible"
            elif eligible in [False, "False", "false"]:
                elig_str = "Not Eligible"
            else:
                elig_str = "Review"
            elig_item = QTableWidgetItem(elig_str)
            elig_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 4, elig_item)

            # 5. Reasoning / Summary
            why = safe_str(record.get("ai_why")) or safe_str(record.get("description"))[:120]
            self.table.setItem(row_idx, 5, QTableWidgetItem(why))

            # 6. Action Button ("View Dossier")
            view_btn = QPushButton("View Dossier")
            view_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3b82f6;
                    color: white;
                    padding: 4px 10px;
                    font-size: 11px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #2563eb;
                }
            """)
            view_btn.clicked.connect(lambda _, rec=record: self.open_lead_details(rec))
            self.table.setCellWidget(row_idx, 6, view_btn)

        # Update Pagination Bar
        num_shown = min(self.visible_count, total_matching)
        self.page_info_label.setText(f"Showing <b>{num_shown}</b> of <b>{total_matching}</b> matching companies")

        if num_shown < total_matching:
            remaining = total_matching - num_shown
            next_batch = min(25, remaining)
            self.show_more_btn.setText(f"➕ Show More (+{next_batch} Rows)")
            self.show_more_btn.setEnabled(True)
            self.show_all_btn.setEnabled(True)
        else:
            self.show_more_btn.setText(f"✓ All {total_matching} Displayed")
            self.show_more_btn.setEnabled(False)
            self.show_all_btn.setEnabled(False)

        self.export_filtered_btn.setEnabled(total_matching > 0)
        self.export_all_btn.setEnabled(len(self.records) > 0)

    def show_more_records(self):
        """Specifically adds 25 more rows to the view!"""
        self.visible_count += 25
        self.render_table()

    def show_all_records(self):
        """Shows all matching rows at once."""
        self.visible_count = len(self.filtered_records)
        self.render_table()

    def on_table_row_double_click(self, row, col):
        if row < len(self.filtered_records):
            record = self.filtered_records[row]
            self.open_lead_details(record)

    def open_lead_details(self, record):
        dialog = LeadDetailsDialog(record, self)
        dialog.exec_()

    # =====================================================================
    # EXPORT SPREADSHEETS (EXCEL / CSV)
    # =====================================================================
    def export_filtered(self):
        if not self.filtered_records:
            QMessageBox.information(self, "No Records", "No records in the current filtered view to export.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Export Filtered Sheet", "filtered_companies.xlsx", "Excel Files (*.xlsx);;CSV Files (*.csv)"
        )
        if not save_path:
            return

        try:
            df_out = b2b_engine.export_records_to_dataframe(
                self.filtered_records,
                original_df=self.raw_df,
                website_col=self.col_website_combo.currentText()
            )
            if save_path.lower().endswith(".csv"):
                df_out.to_csv(save_path, index=False, encoding="utf-8-sig")
            else:
                self._save_styled_excel(df_out, save_path)

            QMessageBox.information(
                self, "Export Successful", 
                f"Successfully saved {len(df_out)} filtered leads to:\n{save_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export file:\n{e}")

    def export_all(self):
        if not self.records:
            QMessageBox.information(self, "No Records", "No records processed yet.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Export All Enriched Results", "websites_all_enriched.xlsx", "Excel Files (*.xlsx);;CSV Files (*.csv)"
        )
        if not save_path:
            return

        try:
            df_out = b2b_engine.export_records_to_dataframe(
                self.records,
                original_df=self.raw_df,
                website_col=self.col_website_combo.currentText()
            )
            if save_path.lower().endswith(".csv"):
                df_out.to_csv(save_path, index=False, encoding="utf-8-sig")
            else:
                self._save_styled_excel(df_out, save_path)

            QMessageBox.information(
                self, "Export Successful", 
                f"Successfully exported {len(df_out)} total leads to:\n{save_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export file:\n{e}")

    def _save_styled_excel(self, df, path):
        import openpyxl
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Filtered Companies")
            ws = writer.sheets["Filtered Companies"]

            header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
            header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            
            for col in range(1, len(df.columns) + 1):
                cell = ws.cell(row=1, column=col)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")

            status_col_idx = None
            for col in range(1, len(df.columns) + 1):
                if ws.cell(row=1, column=col).value in ["Filter Result", "Prospect Status", "ai_verdict"]:
                    status_col_idx = col
                    break

            green_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
            green_font = Font(color="065F46", bold=True)
            
            amber_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
            amber_font = Font(color="92400E", bold=True)
            
            red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
            red_font = Font(color="991B1B", bold=True)

            if status_col_idx:
                for row in range(2, len(df) + 2):
                    cell = ws.cell(row=row, column=status_col_idx)
                    val = str(cell.value)
                    if val == "Qualified":
                        cell.fill = green_fill
                        cell.font = green_font
                    elif val == "Needs Review":
                        cell.fill = amber_fill
                        cell.font = amber_font
                    elif val == "Not Qualified":
                        cell.fill = red_fill
                        cell.font = red_font

            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.value:
                        lines = str(cell.value).split("\n")
                        line_len = max(len(l) for l in lines)
                        max_len = max(max_len, line_len)
                ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)

    # =====================================================================
    # SETTINGS MODAL
    # =====================================================================
    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec_() == QDialog.Accepted:
            self.config = dlg.config
            self.engine.update_settings(**self.engine_settings())
            QMessageBox.information(self, "Settings Saved", "AI settings and configuration updated successfully.")


# =====================================================================
# APPLICATION ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = B2BProspectorApp()
    window.show()
    sys.exit(app.exec_())
