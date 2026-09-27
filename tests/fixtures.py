"""Hand-built MLB Stats API payloads. No network in the test suite."""

from __future__ import annotations

from typing import Any, Optional


def split(
    season: str,
    sport_id: int,
    team: str = "Some Club",
    pa: Optional[int] = 500,
    bb: Optional[int] = 50,
    so: Optional[int] = 125,
    ab: Optional[int] = 440,
    hits: Optional[int] = 120,
    tb: Optional[int] = 210,
    hbp: Optional[int] = 4,
    sf: Optional[int] = 6,
    ops: Optional[str] = ".812",
    slg: Optional[str] = ".477",
    drop: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One yearByYear hitting split; `drop` removes fields to mimic API gaps."""
    stat = {
        "plateAppearances": pa,
        "baseOnBalls": bb,
        "strikeOuts": so,
        "atBats": ab,
        "hits": hits,
        "totalBases": tb,
        "hitByPitch": hbp,
        "sacFlies": sf,
        "ops": ops,
        "slg": slg,
    }
    for key in drop:
        stat.pop(key, None)
    return {
        "season": season,
        "stat": stat,
        "team": {"id": 999, "name": team},
        "sport": {"id": sport_id},
    }


def people_payload(splits: list[dict[str, Any]], person_id: int = 682829) -> dict[str, Any]:
    return {
        "people": [
            {
                "id": person_id,
                "fullName": "Test Player",
                "primaryPosition": {"abbreviation": "SS"},
                "currentTeam": {"name": "Test Club"},
                "stats": [
                    {
                        "type": {"displayName": "yearByYear"},
                        "group": {"displayName": "hitting"},
                        "splits": splits,
                    }
                ],
            }
        ]
    }


PERSON_ONLY = {
    "people": [
        {
            "id": 682829,
            "fullName": "Elly De La Cruz",
            "primaryPosition": {"abbreviation": "SS"},
            "currentTeam": {"name": "Cincinnati Reds"},
            "mlbDebutDate": "2023-06-06",
        }
    ]
}

ROSTER = {
    "people": [
        {"id": 1, "fullName": "Aaron Judge", "primaryPosition": {"abbreviation": "RF"}},
        {"id": 2, "fullName": "Will Smith", "primaryPosition": {"abbreviation": "C"}},
        {"id": 3, "fullName": "Will Smith", "primaryPosition": {"abbreviation": "P"}},
    ]
}
