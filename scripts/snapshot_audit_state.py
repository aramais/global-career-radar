#!/usr/bin/env python3
"""Create a verified read-only audit backup; never prints source content or credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_intake.storage.snapshot import SnapshotError, snapshot_audit_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    try:
        manifest = snapshot_audit_state(options.config, options.output)
    except SnapshotError as error:
        parser.error(str(error))
    except Exception as error:
        # Config/parser exceptions can contain literal values; never echo their messages.
        parser.error(f"Snapshot failed ({type(error).__name__}); verify config and source files")
    print(json.dumps({"output": str(options.output.absolute()), "files": len(manifest["files"]),
                      "row_counts": manifest["verification"]["row_counts"],
                      "quick_check": manifest["verification"]["quick_check"],
                      "restore_verified": manifest["verification"]["restore_verified"]}))


if __name__ == "__main__":
    main()
