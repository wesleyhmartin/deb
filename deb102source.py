import sys
import os
import json
import re
import unicodedata as emoji
import subprocess
import webbrowser
import importlib

# Attempt to import the keyboard module and provide a friendly error if it fails
try:
    import keyboard
except ImportError:
    print("Error: The 'keyboard' module is not installed. Please run: pip install keyboard")
    print("The auto-focus feature will be disabled.")
    keyboard = None
except Exception as e:
    print(f"Warning: Could not import the 'keyboard' module ({e}).")
    print("Auto-focus feature may not work and might require administrative privileges (run with sudo/as Admin).")
    keyboard = None


from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QListWidget, QTextEdit, QLabel,
    QMessageBox, QDialog, QDialogButtonBox, QStatusBar, QProgressBar
)
from PyQt6.QtCore import QThread, pyqtSignal, QObject, Qt, QTimer

# --- Configuration and Setup ---
APPDATA_DIR = os.getenv('APPDATA')
if not APPDATA_DIR:
    print("Could not find AppData directory. Exiting.")
    sys.exit(1)

CONFIG_DIR = os.path.join(APPDATA_DIR, 'DEB_PyQt')
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

# --- Global Variables ---
gemini_module = None
model = None
api_key = None

# --- Custom Widget for Delete/Enter Key Press ---
class CustomListWidget(QListWidget):
    deleteKeyPressed = pyqtSignal()

    def keyPressEvent(self, event):
        # Propagate the event to the parent class first
        super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Delete:
            self.deleteKeyPressed.emit()

# --- Core Logic Functions (Unchanged) ---
def load_api_key():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f).get('api_key')
        except json.JSONDecodeError:
            os.remove(CONFIG_FILE)
    return None

def save_api_key(key):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'api_key': key}, f)

def get_gemini_model():
    global gemini_module, model, api_key
    if model: return model
    if not gemini_module: gemini_module = importlib.import_module('google.generativeai')
    gemini_module.configure(api_key=api_key)
    model = gemini_module.GenerativeModel('gemini-1.5-flash')
    return model

def process_gemini_response(text_response):
    processed_text = re.sub(r'^(\s*)\* ', r'\1• ', text_response, flags=re.MULTILINE)
    return processed_text.replace('*', '')

# --- Worker for Threading ---
class Worker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn; self.args = args; self.kwargs = kwargs
    def run(self):
        try:
            self.finished.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.error.emit(str(e))

# --- API Key Dialog (Unchanged) ---
class ApiKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter API Key")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Welcome to DEB!\nPlease enter your Gemini API key."))
        self.apiKeyInput = QLineEdit()
        self.apiKeyInput.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.apiKeyInput)
        help_button = QPushButton("Get an API Key (Help)")
        help_button.clicked.connect(lambda: webbrowser.open_new_tab('https://wesleymartin.net/deb-extras/get-gemini-api-key'))
        layout.addWidget(help_button)
        buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)
    def get_key(self):
        return self.apiKeyInput.text()

# --- Main Application Window ---
class EmojiDescriberApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DEB - Emoji Describer")
        self.setGeometry(100, 100, 750, 600)
        self.identified_emojis = []
        self.thread = None
        self.worker = None
        self.initial_identification_done = False

        if not self.check_api_key():
            sys.exit(0)
        
        self.init_ui()
        self.check_dependencies()

    def check_api_key(self):
        global api_key
        api_key = load_api_key()
        if not api_key:
            dialog = ApiKeyDialog(self)
            if dialog.exec():
                user_key = dialog.get_key()
                if user_key:
                    api_key = user_key
                    save_api_key(api_key)
                    QMessageBox.information(self, "Success", "API key saved!")
                    return True
                else:
                    QMessageBox.warning(self, "Warning", "API key cannot be empty.")
                    return self.check_api_key()
            return False
        return True

    def check_dependencies(self):
        try:
            importlib.import_module('google.generativeai')
        except ImportError:
            # Handle dependency check as before
            pass 

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Input
        input_layout = QHBoxLayout()
        self.emoji_input = QLineEdit()
        self.emoji_input.setPlaceholderText("Enter emojis here...")
        self.emoji_input.returnPressed.connect(self.identify_emojis_action)
        input_layout.addWidget(self.emoji_input)
        
        self.identify_button = QPushButton("Identify")
        self.identify_button.clicked.connect(self.identify_emojis_action)
        self.identify_button.setDefault(True) # Make this the default button
        input_layout.addWidget(self.identify_button)
        main_layout.addLayout(input_layout)

        # --- HIDDEN CONTENT AREA ---
        self.content_widget = QWidget()
        content_layout = QHBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 10, 0, 0)
        
        # List Widget
        self.list_widget_container = QWidget()
        list_layout = QVBoxLayout(self.list_widget_container)
        list_layout.addWidget(QLabel("Identified Characters:"))
        self.emoji_listbox = CustomListWidget()
        self.emoji_listbox.itemSelectionChanged.connect(self.update_button_states)
        self.emoji_listbox.itemActivated.connect(self.describe_one_action) # Enter/Double-click
        self.emoji_listbox.deleteKeyPressed.connect(self.delete_selected_item) # Delete key
        list_layout.addWidget(self.emoji_listbox)
        content_layout.addWidget(self.list_widget_container, 1)

        # Results Widget
        self.results_widget_container = QWidget()
        results_layout = QVBoxLayout(self.results_widget_container)
        results_layout.addWidget(QLabel("Description:"))
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        results_layout.addWidget(self.results_text)
        content_layout.addWidget(self.results_widget_container, 2)
        
        main_layout.addWidget(self.content_widget)
        self.content_widget.setVisible(False) # Initially hidden

        # Actions
        actions_layout = QHBoxLayout()
        self.desc_one_button = QPushButton("Describe Selected")
        self.desc_one_button.clicked.connect(self.describe_one_action)
        self.desc_all_button = QPushButton("Describe All")
        self.desc_all_button.clicked.connect(self.describe_all_action)
        self.clear_button = QPushButton("Clear All")
        self.clear_button.clicked.connect(self.clear_all)
        
        actions_layout.addWidget(self.desc_one_button)
        actions_layout.addWidget(self.desc_all_button)
        actions_layout.addStretch()
        actions_layout.addWidget(self.clear_button)
        main_layout.addLayout(actions_layout)

        # Status Bar
        self.setStatusBar(QStatusBar(self))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,0) # Indeterminate
        self.statusBar().addPermanentWidget(self.progress_bar)
        
        self.clear_all()

    def showEvent(self, event):
        """Override showEvent to trigger actions after the window is visible."""
        super().showEvent(event)
        # Use QTimer to ensure the action happens after the event loop starts
        QTimer.singleShot(100, self.simulate_tab_press)

    def simulate_tab_press(self):
        """Simulates a tab press to focus the input field."""
        if keyboard:
            try:
                keyboard.press_and_release('tab')
            except Exception as e:
                self.statusBar().showMessage(f"Auto-focus failed: {e}", 5000)
        else:
            self.emoji_input.setFocus() # Fallback for when keyboard module fails

    def set_controls_enabled(self, enabled):
        """Enable or disable all interactive controls."""
        self.emoji_input.setEnabled(enabled)
        self.identify_button.setEnabled(enabled)
        self.emoji_listbox.setEnabled(enabled)
        self.update_button_states()
        if not enabled:
             self.desc_one_button.setEnabled(False)
             self.desc_all_button.setEnabled(False)
             self.clear_button.setEnabled(False)

    def update_button_states(self):
        is_busy = self.progress_bar.isVisible()
        has_items = self.emoji_listbox.count() > 0
        has_selection = len(self.emoji_listbox.selectedItems()) > 0
        self.desc_one_button.setEnabled(not is_busy and has_selection)
        self.desc_all_button.setEnabled(not is_busy and has_items)
        self.clear_button.setEnabled(not is_busy)

    def identify_emojis_action(self):
        characters_input = self.emoji_input.text()
        self.emoji_listbox.clear(); self.results_text.clear(); self.identified_emojis.clear()

        if not characters_input:
            self.statusBar().showMessage("Input is empty.", 3000); return

        for char in characters_input:
            try:
                self.identified_emojis.append({'character': char, 'name': emoji.name(char)})
                self.emoji_listbox.addItem(f"{char} - {emoji.name(char)}")
            except ValueError: pass
        
        if self.identified_emojis:
            if not self.initial_identification_done:
                self.content_widget.setVisible(True)
                self.initial_identification_done = True
            self.statusBar().showMessage(f"Found {len(self.identified_emojis)} character(s).")
        else:
            self.statusBar().showMessage("No recognizable characters found.")
        self.update_button_states()

    def delete_selected_item(self):
        """Deletes the currently selected item from the list."""
        for item in self.emoji_listbox.selectedItems():
            row = self.emoji_listbox.row(item)
            self.emoji_listbox.takeItem(row)
            del self.identified_emojis[row]
        self.update_button_states()

    def clear_all(self):
        self.emoji_input.clear()
        self.emoji_listbox.clear()
        self.results_text.clear()
        self.identified_emojis.clear()
        self.content_widget.setVisible(False)
        self.initial_identification_done = False
        self.statusBar().showMessage("Ready. Enter emojis and click 'Identify'.")
        self.update_button_states()

    def describe_one_action(self):
        selection = self.emoji_listbox.selectedItems()
        if not selection: return
        
        index = self.emoji_listbox.row(selection[0])
        emoji_item = self.identified_emojis[index]
        
        prompt = f'Describe the {emoji_item["character"]} emoji ({emoji_item["name"]}) visually, for a blind person, in one concise sentence.'
        self.run_gemini_task(prompt, f"Description for {emoji_item['character']}:\n\n")

    def describe_all_action(self):
        if not self.identified_emojis: return
        parts = [f'{e["character"]} ({e["name"]})' for e in self.identified_emojis]
        prompt = "For each of the following, provide a one-sentence visual description for a blind person: " + ", ".join(parts)
        self.run_gemini_task(prompt, "Descriptions:\n\n")

    def run_gemini_task(self, prompt, prefix):
        self.set_controls_enabled(False); self.progress_bar.setVisible(True)
        self.statusBar().showMessage("Getting description from Gemini API...")
        
        self.thread = QThread()
        self.worker = Worker(self.gemini_api_call, prompt)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(lambda result: self.on_task_finished(result, prefix))
        self.worker.error.connect(self.on_task_error)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    def gemini_api_call(self, prompt):
        """The actual blocking API call."""
        current_model = get_gemini_model()
        response = current_model.generate_content(prompt)
        return process_gemini_response(response.text)

    def on_task_finished(self, description, prefix):
        self.results_text.setText(f"{prefix}{description}\n\n---\nAI-Generated. Verify important information.")
        self.statusBar().showMessage("Description loaded successfully.", 4000)
        self.progress_bar.setVisible(False)
        self.set_controls_enabled(True)

    def on_task_error(self, error_message):
        QMessageBox.critical(self, "API Error", f"An error occurred: {error_message}")
        if "API key not valid" in error_message and os.path.exists(CONFIG_FILE):
             os.remove(CONFIG_FILE)
             QMessageBox.information(self, "API Key Removed", "Your invalid API key has been removed. Please restart the application.")
        self.statusBar().showMessage("API Error. Please restart.", 5000)
        self.progress_bar.setVisible(False)
        self.set_controls_enabled(True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EmojiDescriberApp()
    window.show()
    sys.exit(app.exec())