import matplotlib.pyplot as plt
from uscope.util import add_bool_arg
import json


def run(in_fn=None, show=True):
    if in_fn is None:
        in_fn = "data/profile.jl"
    header = None
    ys_virtual_memory = []
    ys_rss = []
    x0 = None
    xs = []
    for l in open(in_fn):
        j = json.loads(l)
        if j["type"] == "profile.header":
            header = j
        elif j["type"] == "profile.poll":
            if x0 is None:
                x0 = j["time"]
            xs.append((j["time"] - x0) / 60)
            ys_virtual_memory.append(
                j["host"]["psutil.virtual_memory"]["percent"])
            ys_rss.append(j["argus"]["process"]["rss"] / 1e6)

    _fig, ax1 = plt.subplots()

    plt.xlabel("Minutes", color="red")

    #ax1.set_ylabel("virtual_mem %")
    ax1.plot(xs, ys_virtual_memory, label="virtual_mem", color="blue")
    ax1.set_ylabel("System memory %", color="blue")
    ax1.tick_params(axis='y', labelcolor="blue")

    ax2 = ax1.twinx()
    ax2.plot(xs, ys_rss, label="rss", color="red")
    ax2.set_ylabel("Process MB", color="red")
    ax2.tick_params(axis='y', labelcolor="red")
    plt.legend()
    plt.show()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run test")
    add_bool_arg(parser, "--show", default=False)
    parser.add_argument("--file", default=None)
    args = parser.parse_args()

    run(in_fn=args.file, show=args.show)


if __name__ == "__main__":
    main()
