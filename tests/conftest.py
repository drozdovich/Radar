"""Only fake Telegram sources are available to offline tests."""
import os
from pathlib import Path

os.environ["RADAR_SOURCES_FILE"] = str(Path(__file__).parent / "fixtures/sources.json")
