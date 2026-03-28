#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU tender scraper entrypoint."""
from __future__ import annotations

from tender_core import ReportProfile, run_profile


GPU_PROFILE = ReportProfile(
    key="gpu",
    label="GPU",
    default_keywords=(
        "GPU",
        "GPU服务器",
        "GPU算力",
        "算力服务",
        "GPU租赁",
        "算力租赁",
        "智算服务",
    ),
    default_sites=(
        "ccgp",
        "ggzy",
        "zcygov",
        "zycg",
        "szecp",
        "cebpub",
        "cebpubservice",
        "mofcom",
        "ebnew",
        "chinabidding",
        "365trade",
        "ecsg",
        "ygcgfw",
    ),
    default_subject_prefix="GPU 招标信息简报",
)


def main() -> None:
    raise SystemExit(run_profile(GPU_PROFILE))


if __name__ == "__main__":
    main()
