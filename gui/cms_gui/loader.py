from PySide6.QtCore import QThread, Signal
from . import core as core_mod

class LoaderThread(QThread):
    progress = Signal(str)
    finished_inventory = Signal(object) # core_mod.Inventory
    error = Signal(Exception)

    def __init__(self, core_instance, parent=None):
        super().__init__(parent)
        self.core = core_instance

    def run(self):
        try:
            self.progress.emit("Checking core configuration...")
            if not self.core.is_configured():
                self.error.emit(RuntimeError("core not configured"))
                return

            self.progress.emit("Reading describe payload from backend...")
            payload = self.core.describe()

            self.progress.emit("Parsing inventory...")
            inventory = core_mod.Inventory(payload)
            
            self.finished_inventory.emit(inventory)
        except Exception as exc:
            self.error.emit(exc)
