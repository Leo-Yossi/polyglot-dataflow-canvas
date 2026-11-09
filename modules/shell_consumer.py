"""Shell 消费者：以子进程方式运行，只通过 stdin/stdout 字节与外界通信。

ShellAdapter 用 subprocess.run(command, input=<bytes>) 启动；
配置参数通过环境变量 DATAFLOW_PARAMS 传入（shell 没有函数参数概念）。
"""

import json
import os
import sys


def main():
    params = json.loads(os.environ.get("DATAFLOW_PARAMS", "{}"))
    keyword = str(params.get("keyword", ""))

    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0] if lines else ""
    body = lines[1:] if len(lines) > 1 else lines
    if keyword:
        body = [ln for ln in body if keyword in ln]

    out = [
        "[shell_consumer] runtime = shell / subprocess",
        "stdin_bytes  = %d" % len(raw),
        "total_lines  = %d" % len(lines),
        "header       = %s" % header,
        "data_rows    = %d" % len(body),
        "keyword      = %s" % (keyword or "<none>"),
        "---- first 5 rows ----",
    ]
    out.extend(body[:5])
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))


main()
