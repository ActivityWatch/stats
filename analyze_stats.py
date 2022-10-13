from datetime import datetime, timezone

import click
import matplotlib.pyplot as plt
import pandas as pd


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
@click.option("--save")
@click.option("--since", type=click.DateTime(["%Y-%m-%d"]))
@click.option("--per-day", is_flag=True)
@click.option("--resample", default="1D")
@click.option("--title")
def main(
    column: str = None,
    save: str = None,
    since: datetime = None,
    per_day: bool = False,
    resample: str = "1D",
    title: str = None,
):
    n_plots = 2 if per_day else 1

    df = _load_data()
    df = df.resample(resample).mean()
    df = df.interpolate(method="time")  # interpolate missing dates

    if column:
        if column not in df:
            print(f"Error: No such column '{column}', try one of: {list(df.columns)}")
            exit(1)
        df = df[column]

    if since:
        df = df.truncate(before=since)

    gridargs = dict(axis="both", linestyle="--", linewidth=1, alpha=0.4)

    plt.figure(figsize=(8, 2.5 * n_plots))
    ax1 = plt.subplot(n_plots, 1, 1)
    df.plot(ax=ax1, title=column)
    ax1.set_title(title if title is not None else "Cumulative")
    ax1.set_ylim(0)
    ax1.grid(True, which="major", **gridargs)
    ax1.legend()

    if n_plots >= 2:
        ax = plt.subplot(n_plots, 1, 2)  # , sharex=ax1)
        df_w = df.diff()
        df_w = df_w[df_w > 0]  # Filter out the crazy outlier
        df_w = df_w.resample("1D").mean()
        df_w = df_w.rolling("7D").mean() * 7
        df_w.plot(ax=ax)
        ax.set_title("Per week (rolling)")
        ax.set_ylim(0)
        ax.grid(True, which="major", **gridargs)
        ax.legend()

    # if n_plots >= 2:
    #     ax2 = plt.subplot(n_plots, 1, 2, sharex=ax1)
    #     df_d = df.diff()
    #     df_d = df_d[df_d > 0]  # Filter out the crazy outlier
    #     df_d.plot(ax=ax2)
    #     ax2.set_title("Per day")
    #     ax2.set_ylim(0)

    plt.tight_layout()
    # plt.subplots_adjust(hspace=0.10)

    # print(df)

    if save:
        plt.savefig(save)
        # plt.show()
    else:
        plt.show()


if __name__ == "__main__":
    main()
