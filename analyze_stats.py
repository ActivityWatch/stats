from datetime import datetime, timedelta, timezone

import click
import matplotlib.pyplot as plt
import pandas as pd


def _load_downloads():
    df = pd.read_csv("data/stats.csv", index_col="timestamp", parse_dates=True)
    df.index = pd.to_datetime(df.index, format="ISO8601", utc=True)
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


def _load_android():
    df = pd.read_csv("data/android/installed.csv", index_col="Date", parse_dates=True)
    df = df.tz_localize(tz=timezone.utc)
    df.drop(columns=["Notes"], inplace=True)
    col_name = "Android installed devices"
    df.columns = [col_name]
    return df


def _load_assets():
    """Raw per-asset download counts over time (long format).

    Columns: timestamp, tag, asset, platform, downloads. The collector logs an
    asset only when its count changes, so this is sparse — see _asset_series()
    for the forward-filled reconstruction.
    """
    df = pd.read_csv("data/stats-assets.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _asset_series(df=None):
    """Wide per-(tag, asset) download counts, forward-filled over time.

    Reconstructs each asset's running total at every observed timestamp from
    the change-only log.
    """
    df = _load_assets() if df is None else df
    return (
        df.pivot_table(index="timestamp", columns=["tag", "asset"], values="downloads")
        .sort_index()
        .ffill()
    )


def _asset_meta(df):
    """Per-(tag, asset) metadata (platform), aligned to _asset_series columns."""
    meta = df.drop_duplicates(["tag", "asset"]).set_index(["tag", "asset"])[["platform"]]
    return meta.reindex(_asset_series(df).columns)


def platform_totals(df=None):
    """Time series of total downloads per platform."""
    df = _load_assets() if df is None else df
    wide = _asset_series(df)
    plat = _asset_meta(df)["platform"]
    totals = wide.T.groupby(plat.values).sum().T
    totals.columns.name = "platform"
    return totals


def version_platform_totals(df=None):
    """Time series of downloads per (version, platform)."""
    df = _load_assets() if df is None else df
    meta = _asset_meta(df)
    wt = _asset_series(df).T
    wt.index = pd.MultiIndex.from_arrays(
        [meta.index.get_level_values("tag"), meta["platform"].values],
        names=["tag", "platform"],
    )
    return wt.groupby(level=["tag", "platform"]).sum().T


def _load_data():
    df = _load_downloads()
    df = df.resample("1D").mean()
    df = df.merge(_load_chrome(), how="outer", left_index=True, right_index=True)
    df = df.merge(_load_firefox(), how="outer", left_index=True, right_index=True)
    df = df.merge(_load_android(), how="outer", left_index=True, right_index=True)
    return df


def test_load():
    _load_downloads()
    _load_chrome()
    _load_firefox()
    _load_android()
    _load_assets()
    platform_totals()
    version_platform_totals()


def test_load_all():
    _load_data()


@click.command()
@click.option("--column")
@click.option("--save")
@click.option("--since", type=click.DateTime(["%Y-%m-%d"]))
@click.option("--per-day", is_flag=True)
@click.option("--per-week", is_flag=True)
@click.option("--resample", default="1D")
@click.option("--title")
def main(
    column: str | None = None,
    save: str | None = None,
    since: datetime | None = None,
    per_day: bool = False,
    per_week: bool = False,
    resample: str = "1D",
    title: str | None = None,
):
    n_plots = 2 if per_day or per_week else 1

    df = _load_data()
    df = df.resample(resample).mean()
    df = df.interpolate(method="time")  # interpolate missing dates

    if column:
        if column not in df:
            print(f"Error: No such column '{column}', try one of: {list(df.columns)}")
            exit(1)
        df = df[column]

    if since:
        df = df.truncate(before=since.astimezone(timezone.utc))

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
        # Always smooth out the day-to-day noise; per-week is the same daily
        # rate scaled up by 7, not a different (jagged) series.
        df_w = df_w.rolling("7D").mean()
        if per_week:
            df_w = df_w * 7
        df_w.plot(ax=ax)
        ax.set_title(f"Per {'week' if per_week else 'day'} (7d rolling mean)")
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


def calculate_goal_date(df, goal, sample_days=90):
    # Get the last date and its corresponding downloads
    last_date = df.index[-1]
    last = df.iloc[-1]

    # Calculate the daily download rate based on the last `sample_days` days
    daily_rate = df[-sample_days:].diff().mean()

    # Calculate the number of days until we reach the goal
    days_until_goal = (goal - last) / daily_rate

    # Calculate the date when we'll reach the goal
    date_goal = last_date + timedelta(days=days_until_goal)

    return date_goal


if __name__ == "__main__":
    df = _load_data()
    print(
        f"Estimated date to reach 1,000,000 downloads: {calculate_goal_date(df['downloads'], 1000000)}"
    )
    print(
        f"Estimated date to reach 10,000 stars: {calculate_goal_date(df['stars'], 10000)}"
    )
    main()
