#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGV tender scraper entrypoint."""
from __future__ import annotations

from tender_core import ReportProfile, run_profile


AGV_PROFILE = ReportProfile(
    key="agv",
    label="AGV",
    default_keywords=("AGV",),
    default_sites=("all",),
    default_subject_prefix="AGV 招标信息简报",
)


def main() -> None:
    raise SystemExit(run_profile(AGV_PROFILE))


if __name__ == "__main__":
    main()
