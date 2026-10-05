"""Keep tests away from the user's real log folder and app settings."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ts-tests-")
# Must be set before transcript_studio.logs is imported (LOG_DIR is computed at import)
# and before QSettings is first used (it reads XDG_CONFIG_HOME on Linux).
os.environ["XDG_STATE_HOME"] = os.path.join(_tmp, "state")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_tmp, "config")
