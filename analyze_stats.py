from datetime import timezone
import matplotlib.pyplot as plt
import pandas as pd
import click


def _load_downloads():
    df = pd.read_csv("data/stats.csv", index_col="timestamp", parse_dates=True)
    return df


def _load_firefox():
    df = pd.read_csv("data/firefox-daily-users.csv", index_col="date", parse_dates=True)
    df = df.tz_localize(tz=timezone.utc)
    df.columns = ["Firefox DAU"]
    return df


def _load_chrome():
    df = pd.read_csv("data/chrome-weekly-users.csv", index_col="Date", parse_dates=True)
    df = df.tz_localize(tz=timezone.utc)
    df.columns = ["Chrome WAU"]
    return df


def _load_data():
    df = _load_downloads()
    df = df.resample("1D").mean()
    df = df.merge(_load_chrome(), how="outer", left_index=True, right_index=True)
    df = df.merge(_load_firefox(), how="outer", left_index=True, right_index=True)
    return df


def test_load():
    _load_downloads()
    _load_chrome()
    _load_firefox()


@click.command()
@click.option("--column")
@click.option("--since", type=click.DateTime(["%Y-%m-%d"]))
def main(column=None, since=None):
    n_plots = 3

    df = _load_data()
    df = df.resample("1D").mean()

    if column:
        df = df[column]

    if since:
        df = df.truncate(before=since)

    ax1 = plt.subplot(n_plots, 1, 1)
    df.plot(ax=ax1, title=column)
    ax1.set_title("Cumulative")
    ax1.set_ylim(0)
    ax1.legend()

    if n_plots >= 2:
        ax2 = plt.subplot(n_plots, 1, 2, sharex=ax1)
        df_d = df.diff()
        df_d = df_d[df_d > 0]  # Filter out the crazy outlier
        df_d.plot(ax=ax2)
        ax2.set_title("Per day")
        ax2.set_ylim(0)

    if n_plots >= 3:
        ax = plt.subplot(n_plots, 1, 3, sharex=ax1)
        df_w = df.diff()
        df_w = df_w[df_w > 0]  # Filter out the crazy outlier
        df_w = df_w.rolling("7D").mean() * 7
        df_w.plot(ax=ax)
        ax.set_title("Per week")
        ax.set_ylim(0)

    plt.show()
    # print(df)


if __name__ == "__main__":
    main()
