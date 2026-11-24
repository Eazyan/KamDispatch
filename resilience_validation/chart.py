"""Render load quantiles and resource observations from the saved run."""
import json
from pathlib import Path
import re

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent / "evidence"
DATA = json.loads((HERE / "experiments.json").read_text())


def memory(value):
    amount, unit = re.fullmatch(r"([0-9.]+)([a-zA-Z]+)", value.strip()).groups()
    return float(amount) * {"B": 1 / 1024**2, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024,
                            "kB": 1 / 1024, "MB": 1, "GB": 1024}[unit]


def resource(stage, field):
    samples = []
    for sample in stage["resources"]:
        if sample["containers"]:
            samples.append(sum(field(container) for container in sample["containers"]))
    return max(samples, default=float("nan"))


stages = DATA["load"]
users = [stage["measurements"]["users"] for stage in stages]
fig, axes = plt.subplots(2, 2, figsize=(10, 6.5), layout="constrained")
fig.suptitle("KamDispatch · синтетическая локальная нагрузка", fontweight="bold")
latency, throughput, cpu, ram = axes.flat
for key, label in (("p50_ms", "p50"), ("p95_ms", "p95"), ("p99_ms", "p99")):
    latency.plot(users, [stage["measurements"]["by_path"]["/api/v1/dashboard"][key] for stage in stages],
                 marker="o", label=label)
latency.set(yscale="log", ylabel="мс, логарифмическая шкала", title="Задержка dashboard")
latency.legend()
throughput.plot(users, [s["measurements"]["requests_per_second"] for s in stages], marker="o", color="#246656")
throughput.set(ylabel="ответов/с, включая ошибки", title="Пропускная способность смеси")
cpu.plot(users, [resource(s, lambda row: float(row["CPUPerc"].rstrip("%"))) for s in stages], marker="o", color="#813a5e")
cpu.set(ylabel="% одного ядра, сумма", title="CPU · максимум по сэмплам")
ram.plot(users, [resource(s, lambda row: memory(row["MemUsage"].split("/")[0])) for s in stages], marker="o", color="#26607a")
ram.set(ylabel="МиБ, сумма", title="RAM · максимум по сэмплам")
for axis in axes.flat:
    axis.set(xscale="log", xticks=users, xticklabels=users, xlabel="параллельных клиентов")
    axis.grid(alpha=.2)
fig.savefig(HERE / "load.png", dpi=160)
