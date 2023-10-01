"""
Analyze sponsor data to generate a sponsors table on the website
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

Amount = float
Currency = str


@dataclass
class Sponsor:
    name: str
    donated: list[tuple[Amount, Currency, datetime]] = field(default_factory=list)
    github_username: Optional[str] = None
    url: Optional[str] = None
    source: str = "unknown"

    def __repr__(self):
        return f"<Sponsor {self.name} ({self.github_username or self.url}) on {self.source} total_donated={self.total_donated}>"

    @property
    def total_donated(self):
        # group donated by currency, aggregate by sum
        currencies = set([currency for _, currency, _ in self.donated])
        total_donated = {}
        for currency in currencies:
            total_donated[currency] = sum(
                [amount for amount, c, _ in self.donated if c == currency]
            )
        return total_donated

    @property
    def total_donated_usd(self):
        """Tries to approximately convert all currencies to USD"""
        value = 0
        for currency, amount in self.total_donated.items():
            if currency == "USD":
                value += amount
            elif currency == "EUR":
                value += amount * 1.08
            elif currency == "GBP":
                value += amount * 1.25
            else:
                raise ValueError(f"Unknown currency {currency}")
        return value


def load_github_sponsors_csv(filename: str) -> list[Sponsor]:
    """The CSV looks like the following:

    Sponsor Handle,Sponsor Profile Name,Sponsor Public Email,Sponsorship Started On,Is Public?,Is Yearly?,Transaction ID,Tier Name,Tier Monthly Amount,Processed Amount,Is Prorated?,Status,Transaction Date,Metadata,Country,Region,VAT
    snehal-shekatkar,Snehal Shekatkar,,2023-05-07 21:45:50 +0200,true,false,ch_3N5DWnEQsq43iHhX1Svht2g7,$5 a month,$5.00,$4.52,true,settled,2023-05-07 21:46:01 +0200,"",AUT,undefined,
    justyn,Justyn Butler,,2023-02-15 12:14:28 +0100,true,false,ch_3NExsXEQsq43iHhX0Fmfcg6w,$3 a month,$3.00,$3.00,false,settled,2023-06-03 19:04:45 +0200,"",GBR,Kent,
    justyn,Justyn Butler,,2023-02-15 12:14:28 +0100,true,false,ch_3N3ikOEQsq43iHhX1eMXefVn,$3 a month,$3.00,$3.00,false,settled,2023-05-03 18:41:56 +0200,"",GBR,Kent,
    """

    df = pd.read_csv(
        filename, parse_dates=["Sponsorship Started On", "Transaction Date"]
    )
    df = df[df["Is Public?"] == True]  # noqa: E712
    df = df[df["Status"] == "settled"]

    sponsors = []
    handles = df["Sponsor Handle"].unique()
    for handle in handles:
        sponsor = Sponsor(
            name="placeholder",
            github_username=handle,
            donated=[],
            source="github",
        )
        for _, row in df.iterrows():
            if row["Sponsor Handle"] != handle:
                continue
            sponsor.name = row["Sponsor Profile Name"]
            sponsor.donated.append(
                (
                    float(row["Processed Amount"].replace("$", "")),
                    "USD",
                    row["Transaction Date"],
                )
            )

        sponsors.append(sponsor)

    return sponsors


def load_opencollective_csv(filename: str) -> list[Sponsor]:
    """The CSV looks like this:

    "datetime","shortId","shortGroup","description","type","kind","isRefund","isRefunded","shortRefundId","displayAmount","amount","paymentProcessorFee","netAmount","balance","currency","accountSlug","accountName","oppositeAccountSlug","oppositeAccountName","paymentMethodService","paymentMethodType","expenseType","expenseTags","payoutMethodType","merchantId","orderMemo"
    "2023-06-06T05:47:17","d3f98b95","f2238225","Contribution from Martin","CREDIT","CONTRIBUTION","","","","$50.00 USD",50,-2.5,47.5,3059.14,"USD","activitywatch","ActivityWatch","guest-cf5e5bf5","Martin","STRIPE","CREDITCARD",,"",,,
    "2023-06-06T05:47:17","e3c0d1f9","f2238225","Host Fee to Open Source Collective","DEBIT","HOST_FEE","","","","-$5.00 USD",-5,0,-5,3011.64,"USD","activitywatch","ActivityWatch","opensource","Open Source Collective",,,,"",,,
    "2023-06-01T04:03:23","80120e4e","9a189bcd","Yearly contribution from Olli Nevalainen","CREDIT","CONTRIBUTION","","","","$24.00 USD",24,-1.24,22.76,3016.64,"USD","activitywatch","ActivityWatch","olli-nevalainen","Olli Nevalainen","STRIPE","CREDITCARD",,"",,,
    """

    df = pd.read_csv(filename, parse_dates=["datetime"])
    df = df[df["type"] == "CREDIT"]

    sponsors = []
    handles = df["oppositeAccountSlug"].unique()
    for handle in handles:
        sponsor = Sponsor(
            name="placeholder",
            github_username=handle,
            donated=[],
            source="opencollective",
        )
        for _, row in df.iterrows():
            if row["oppositeAccountSlug"] != handle:
                continue
            sponsor.name = row["oppositeAccountName"]
            sponsor.donated.append(
                (
                    row["amount"],
                    row["currency"],
                    row["datetime"].replace(tzinfo=timezone.utc),
                )
            )

        sponsors.append(sponsor)

    # remove 'GitHub Sponsors' from sponsors
    sponsors = [sponsor for sponsor in sponsors if sponsor.name != "GitHub Sponsors"]

    # subtract $3000 from Kerkko (actually 3000 EUR from FUUG.fi)
    for sponsor in sponsors:
        if sponsor.name == "Kerkko Pelttari":
            sponsor.donated.append((-3000, "EUR", datetime.now(tz=timezone.utc)))

    # add FUUG.fi and Ghent University manually
    sponsors.append(
        Sponsor(
            name="FUUG.fi",
            donated=[(3000, "EUR", datetime(2020, 4, 1, tzinfo=timezone.utc))],
            url="https://fuug.fi/",
            source="manual",
        )
    )
    sponsors.append(
        Sponsor(
            name="Ghent University",
            donated=[(500, "EUR", datetime(2023, 5, 1, tzinfo=timezone.utc))],
            url="https://www.ugent.be/",
            source="manual",
        )
    )

    return sponsors


def load_patreon_csv(filename: str) -> list[Sponsor]:
    """The CSV looks like:

    Name,Email,Twitter,Discord,Patron Status,Follows You,Lifetime Amount,Pledge Amount,Charge Frequency,Tier,Addressee,Street,City,State,Zip,Country,Phone,Patronage Since Date,Last Charge Date,Last Charge Status,Additional Details,User ID,Last Updated,Currency,Max Posts,Access Expiration,Next Charge Date
    Karan singh,karan8q@gmail.com,,kraft#9466,Declined patron,No,0.00,1.00,monthly,Time Tracker,,,,,,,,2023-04-08 17:45:28.731953,2023-05-31 07:46:37,Declined,,76783024,2023-05-31 08:01:37.517184,USD,,,2023-05-01 07:00:00
    Dan Thompson,danielrthompsonjr@gmail.com,,,Declined patron,No,0.00,1.00,monthly,Time Tracker,,,,,,,,2023-02-28 20:00:03.756241,2023-03-15 12:03:01,Declined,,46598895,2023-03-15 12:18:01.633900,USD,,,2023-03-01 08:00:00
    """

    df = pd.read_csv(filename, parse_dates=["Patronage Since Date", "Last Charge Date"])

    sponsors = []
    for _, row in df.iterrows():
        sponsor = Sponsor(
            name=row["Name"],
            github_username=None,
            donated=[
                (
                    row["Lifetime Amount"],
                    row["Currency"],
                    row["Last Charge Date"].replace(tzinfo=timezone.utc),
                )
            ],
            source="patreon",
        )
        sponsors.append(sponsor)

    return sponsors


if __name__ == "__main__":
    now = datetime.now(tz=timezone.utc)

    sponsors = []
    sponsors += load_github_sponsors_csv(
        "data/sponsors/ActivityWatch-sponsorships-all-time.csv"
    )
    sponsors += load_github_sponsors_csv(
        "data/sponsors/ErikBjare-sponsorships-all-time.csv"
    )
    sponsors += load_opencollective_csv(
        "data/sponsors/opencollective-activitywatch-transactions.csv"
    )
    sponsors += load_patreon_csv("data/sponsors/patreon-members-866337.csv")
    sponsors = sorted(sponsors, key=lambda s: s.total_donated_usd, reverse=True)

    # filter out sponsors who have donated less than $10
    sponsors = [sponsor for sponsor in sponsors if sponsor.total_donated_usd >= 10]

    # print as markdown table
    print("| Name | Active? | Total Donated |")
    print("| ---- |:-------:| -------------:|")
    for sponsor in sponsors:
        ident = f"{sponsor.name}"
        if sponsor.github_username:
            ident = f"{sponsor.name} ([@{sponsor.github_username}](https://github.com/{sponsor.github_username}))"
        elif sponsor.url:
            ident = f"[{sponsor.name}]({sponsor.url})"

        # active if last donation was less than 3 months ago
        last_donation: datetime = max(timestamp for _, _, timestamp in sponsor.donated)
        is_active = (
            last_donation > now - timedelta(days=90) if sponsor.donated else False
        )
        print(
            f"| {ident} | {'✔️' if is_active else ''} | {sponsor.total_donated_usd:.2f} USD |"
        )
