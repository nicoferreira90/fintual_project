import os

# settings.py refuses to import without these when DEBUG is off.
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-secret")
