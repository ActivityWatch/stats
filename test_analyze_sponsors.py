"""Offline tests for analyze_sponsors.py link rendering."""

from __future__ import annotations

import analyze_sponsors
from analyze_sponsors import Sponsor, format_ident

OC_CSV = """\
"datetime","type","amount","currency","oppositeAccountSlug","oppositeAccountName"
"2023-06-06T05:47:17","CREDIT",50,"USD","guest-cf5e5bf5","Martin"
"2023-06-06T05:47:17","DEBIT",-5,"USD","opensource","Open Source Collective"
"2023-06-01T04:03:23","CREDIT",24,"USD","olli-nevalainen","Olli Nevalainen"
"2023-05-01T04:03:23","CREDIT",10,"USD","incognito-77326f02","Incognito"
"""


def _oc_sponsors(tmp_path) -> dict[str, Sponsor]:
    path = tmp_path / "oc.csv"
    path.write_text(OC_CSV)
    sponsors = analyze_sponsors.load_opencollective_csv(str(path))
    return {s.name: s for s in sponsors if s.source == "opencollective"}


def test_opencollective_slug_links_to_opencollective_not_github(tmp_path):
    olli = _oc_sponsors(tmp_path)["Olli Nevalainen"]
    assert olli.github_username is None
    ident = format_ident(olli)
    assert ident == "[Olli Nevalainen](https://opencollective.com/olli-nevalainen)"
    assert "github.com" not in ident


def test_anonymous_opencollective_contributors_get_no_link(tmp_path):
    sponsors = _oc_sponsors(tmp_path)
    assert format_ident(sponsors["Martin"]) == "Martin"
    assert format_ident(sponsors["Incognito"]) == "Incognito"


def test_github_sponsor_still_links_to_github():
    sponsor = Sponsor(name="Justyn Butler", github_username="justyn", source="github")
    assert (
        format_ident(sponsor) == "Justyn Butler ([@justyn](https://github.com/justyn))"
    )
