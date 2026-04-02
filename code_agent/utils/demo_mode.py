# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

"""Temporary demo-recording toggles.

NOTE:
- These switches are intentionally temporary and should be turned back off
  after recording the demo.
- The real logic is still kept in place and guarded by these flags.
"""

from __future__ import annotations

# TEMP: set back to False after the demo video is recorded.
DEMO_RECORDING_MODE = True

# TEMP: speed up UI animations for recording.
DEMO_LIVE_FRAME_DELAY = 0.02
DEMO_PROGRESS_DELAY = 0.015
DEMO_TYPEWRITER_DELAY = 0.004

# TEMP: speed up bash polling so command results appear faster in the demo.
DEMO_BASH_OUTPUT_DELAY = 0.03
