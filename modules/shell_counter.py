"""宽松格式的 Shell 消费者：统计 stdin 字节里的行数、字符数与词频。"""

import collections
import json
import os
import sys


def main():
    params = json.loads(os.environ.get("DATAFLOW_PARAMS", "{}"))
    top_n = int(params.get("top_n", 5))

    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    words = [w for w in text.replace("\t", " ").split() if w]
    counter = collections.Counter(words)

    out = [
        "[shell_counter] runtime = shell / subprocess",
        "bytes        = %d" % len(raw),
        "lines        = %d" % len(lines),
        "chars        = %d" % len(text),
        "words        = %d" % len(words),
        "---- top tokens ----",
    ]
    for token, times in counter.most_common(top_n):
        out.append("%-16s %d" % (token, times))
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))


main()
