"""随机序列数据源 —— 多个顶层函数，节点面板可任选一个作为入口。

参数名与 Manifest 的 params key 同名时会自动注入参数面板里的值；
def execute(inputs) 约定入口也保留，方便不指定函数时直接跑。
"""
import math
import random


def random_series(count=8, seed=42, low=0, high=100):
    """随机整数序列：seed 固定时结果可复现"""
    rng = random.Random(seed)
    seq = [rng.randint(int(low), int(high)) for _ in range(int(count))]
    return seq


def wave_series(count=12, amplitude=10, noise=0, seed=42):
    """正弦波序列，可叠加噪声（输出 float）"""
    rng = random.Random(seed)
    seq = []
    for i in range(int(count)):
        value = float(amplitude) * math.sin(i * math.pi / 6)
        if noise:
            value += rng.uniform(-float(noise), float(noise))
        seq.append(round(value, 4))
    return seq


def mixed_series(count=6, seed=7):
    """混合类型序列：变量树里能看到 int / str 交替的逐元素类型标注"""
    rng = random.Random(seed)
    seq = []
    for i in range(int(count)):
        seq.append(i + 1 if i % 2 == 0 else f"item-{rng.randint(10, 99)}")
    return seq


def execute(inputs):
    """兼容约定入口：默认等价于 random_series(参数面板的值)"""
    return random_series(
        count=int(PARAMS.get("count", 8)),
        seed=int(PARAMS.get("seed", 42)),
        low=int(PARAMS.get("low", 0)),
        high=int(PARAMS.get("high", 100)),
    )
