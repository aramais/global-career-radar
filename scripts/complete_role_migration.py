#!/usr/bin/env python3
"""Back up and complete the local role-first transition; dry-run unless --apply."""

import argparse
import json

from job_intake.config.settings import load_app_config
from job_intake.role_migration import complete_migration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--apply", action="store_true",
                        help="Back up and update the working database")
    args = parser.parse_args()
    print(json.dumps(complete_migration(load_app_config(args.config), apply=args.apply),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
