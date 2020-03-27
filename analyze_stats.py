import matplotlib.pyplot as plt
import pandas as pd


def load_data():
    df = pd.read_csv(
        "stats.csv",
        index_col="timestamp",
        names=["timestamp", "downloads", "stars"],
        parse_dates=True,
    )
    return df


if __name__ == "__main__":
    df = load_data()
    df = df.resample("1D").mean()
    ax1 = plt.subplot(3, 1, 1)
    ax2 = plt.subplot(3, 1, 2, sharex=ax1)
    ax3 = plt.subplot(3, 1, 3, sharex=ax1)

    ax1.set_title("Cumulative")
    df.plot(ax=ax1)
    ax1.set_ylim(0)

    ax2.set_title("Per day")
    df_d = df.diff()
    df_d = df_d[df_d > 0]  # Filter out the crazy outlier
    df_d.plot(ax=ax2)
    ax2.set_ylim(0)

    ax3.set_title("Per week")
    df_w = df.diff()
    df_w = df_w[df_w > 0]  # Filter out the crazy outlier
    df_w = df_w.rolling("7D").mean() * 7
    df_w.plot(ax=ax3)
    ax3.set_ylim(0)

    plt.show()
    # print(df)
