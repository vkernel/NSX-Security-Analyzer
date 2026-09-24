"""Release identity baked into the image, independent of database snapshots."""
import json
from pathlib import Path

VERSION = "0.1.0"
metadata = Path(__file__).with_name("_build.json")
BUILD = json.loads(metadata.read_text()) if metadata.exists() else {"revision": "source", "built_at": "Not recorded"}


def application_version(request):
    return {"application_version": VERSION, "application_build": BUILD["revision"][:12],
            "application_revision": BUILD["revision"], "application_built_at": BUILD["built_at"]}
