"""Test configuration — set dummy env vars before app imports."""

import os

# Must be set before app.config.Settings() runs at import time
os.environ.setdefault("LIVEKIT_URL", "wss://test.livekit.cloud")
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret")
