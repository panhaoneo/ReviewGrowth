"""数据抓取编排：kline → events → earnings → valuation → reports

用法:
  python3 scripts/fetch_all.py --code 300750 [--force] [--only fetch_earnings]
"""

import os
import subprocess
import sys
from argparse import ArgumentParser

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tools"))

from common import log  # noqa: E402

STEPS = ["fetch_kline", "fetch_events", "fetch_earnings", "fetch_valuation", "fetch_reports"]


def main():
    ap = ArgumentParser(description="ReviewGrowth 数据抓取编排")
    ap.add_argument("--code", required=True, help="股票代码，如 300750")
    ap.add_argument("--force", action="store_true", help="忽略缓存全量刷新")
    ap.add_argument("--only", choices=STEPS, help="只运行某一步")
    args = ap.parse_args()

    steps = [args.only] if args.only else STEPS
    for step in steps:
        log(f"═══ {step} ═══")
        cmd = [sys.executable, os.path.join(_REPO, "scripts", f"{step}.py"), "--code", args.code]
        if args.force:
            cmd.append("--force")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            log(f"✗ {step} 失败（exit {r.returncode}），中止")
            sys.exit(1)
    log("全部数据抓取完成 ✓")


if __name__ == "__main__":
    main()
