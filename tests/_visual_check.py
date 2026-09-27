"""Dev-only: boot the app with stubbed API data and screenshot each page.

Not part of the test suite. Run with: python tests/_visual_check.py
"""

from __future__ import annotations

import threading
import time

import uvicorn

from app import main
from app.services.models import MLBAPIError, PlayerMatch
from tests.fixtures import people_payload, split
from tests.test_compare import levels
from tests.test_projection import league_split, payloads_from

PLAYER = PlayerMatch(
    person_id=682829,
    full_name="Elly De La Cruz",
    position="SS",
    current_team="Cincinnati Reds",
    mlb_debut_date="2023-06-06",
)


class Stub:
    def find_players(self, query, season=None):
        if "boom" in query.lower():
            raise MLBAPIError("HTTPSConnectionPool(host='statsapi.mlb.com'): Read timed out.")
        if "smith" in query.lower():
            return [
                PlayerMatch(person_id=2, full_name="Will Smith", position="C", current_team="LAD"),
                PlayerMatch(person_id=3, full_name="Will Smith", position="P", current_team="SF"),
            ]
        if "zzz" in query.lower():
            return []
        return [PLAYER]

    def get_person(self, person_id):
        return PLAYER

    # --- Compare tab -------------------------------------------------
    ROSTER = {
        "Elly De La Cruz": (1, "Elly De La Cruz", (600, 62, 190, 540, 140, 250), (169, 17, 48, 145, 44, 91)),
        "Bobby Witt Jr.": (2, "Bobby Witt Jr.", (690, 45, 120, 620, 190, 340), (280, 22, 60, 250, 78, 140)),
        "Corbin Carroll": (3, "Corbin Carroll", (620, 70, 130, 530, 145, 265), (320, 40, 70, 275, 90, 165)),
        "Jackson Chourio": (4, "Jackson Chourio", (600, 30, 140, 560, 155, 265), (95, 8, 25, 85, 25, 45)),
        "Jasson Dominguez": (5, "Jasson Dominguez", (58, 6, 18, 50, 13, 24), (410, 55, 110, 350, 95, 175)),
        "Wyatt Langford": (6, "Wyatt Langford", (580, 55, 140, 510, 130, 230), None),
        "Julio Rodriguez": (7, "Julio Rodriguez", (640, 50, 160, 570, 155, 275), (300, 28, 66, 265, 85, 160)),
        "Gunnar Henderson": (8, "Gunnar Henderson", (610, 70, 145, 520, 140, 265), (360, 45, 80, 305, 95, 180)),
        "Anthony Volpe": (9, "Anthony Volpe", (540, 45, 150, 480, 105, 195), (420, 50, 105, 360, 100, 190)),
        "Colt Keith": (10, "Colt Keith", (520, 40, 110, 470, 120, 205), (240, 26, 52, 210, 70, 130)),
        "Evan Carter": (11, "Evan Carter", (280, 35, 80, 240, 60, 105), (330, 48, 70, 275, 90, 165)),
        "Masyn Winn": (12, "Masyn Winn", (560, 38, 100, 505, 135, 215), (390, 30, 75, 350, 105, 185)),
        "Jordan Lawlar": (13, "Jordan Lawlar", (150, 12, 55, 133, 28, 45), (290, 34, 88, 248, 72, 140)),
    }

    def bulk_find_players(self, queries):
        out = {}
        for q in queries:
            if "smith" in q.lower():
                out[q] = [
                    PlayerMatch(person_id=90, full_name="Will Smith", position="C", current_team="Los Angeles Dodgers"),
                    PlayerMatch(person_id=91, full_name="Will Smith", position="P", current_team="Kansas City Royals"),
                ]
            elif q in self.PROSPECTS:
                pid = self.PROSPECTS[q][0]
                out[q] = [PlayerMatch(person_id=pid, full_name=q, position="OF")]
            elif q in self.ROSTER:
                pid, name, _, _ = self.ROSTER[q]
                out[q] = [PlayerMatch(person_id=pid, full_name=name, position="OF")]
            else:
                out[q] = []
        return out, []

    def bulk_levels_hitting(self, person_ids, sport_ids):
        prospects = {v[0]: (k, v[1], v[2]) for k, v in self.PROSPECTS.items()}
        roster = {v[0]: v for v in self.ROSTER.values()}
        sport_of = {"aaa": 11, "aa": 12, "aplus": 13}
        payloads = {}
        for pid in person_ids:
            if pid in prospects:
                _name, by_level, mlb = prospects[pid]
                kwargs = {}
                for key, entries in by_level.items():
                    kwargs[key] = [
                        league_split(2023 + i, sport_of[key], league,
                                     pa=pa, bb=bb, so=so, ab=ab, hits=h, tb=tb)
                        for i, (league, pa, bb, so, ab, h, tb) in enumerate(entries)
                    ]
                if mlb:
                    kwargs["mlb"] = [league_split(2026, 1, "American League",
                                                  pa=mlb[0], bb=mlb[1], so=mlb[2],
                                                  ab=mlb[3], hits=mlb[4], tb=mlb[5])]
                payloads[pid] = payloads_from(**kwargs)
            else:
                _, _, mlb, aaa = roster[pid]
                payloads[pid] = levels(mlb=mlb, aaa=aaa)
        return payloads, []

    # --- Projection tab ----------------------------------------------
    # id -> {level: [(league, pa, bb, k, ab, h, tb), ...]}, plus the MLB line
    PROSPECTS = {
        "Marcelo Mayer": (22, {
            "aaa": [("International League", 300, 33, 70, 258, 78, 132)],
            "aa": [("Eastern League", 480, 48, 118, 420, 118, 205)],
            "aplus": [("South Atlantic League", 350, 40, 80, 300, 90, 150)],
        }, None),
        "Roman Anthony": (21, {
            "aaa": [("Pacific Coast League", 430, 62, 92, 360, 112, 196)],
            "aa": [("Eastern League", 310, 44, 70, 265, 82, 145)],
        }, (410, 44, 98, 355, 96, 158)),
        "Coby Mayo": (23, {
            "aaa": [("International League", 560, 66, 160, 480, 130, 260)],
            "aa": [("Eastern League", 420, 50, 112, 360, 104, 205)],
            "aplus": [("South Atlantic League", 290, 34, 70, 250, 76, 132)],
        }, (180, 14, 62, 162, 34, 58)),
        "Dylan Crews": (24, {
            # split a level between two leagues: the bigger stint wins
            "aaa": [("International League", 300, 33, 68, 258, 78, 132),
                    ("Pacific Coast League", 150, 15, 36, 130, 39, 66)],
            "aa": [("Texas League", 380, 38, 86, 330, 98, 165)],
        }, (520, 40, 130, 470, 115, 190)),
        "Jackson Merrill": (25, {
            "aa": [("Texas League", 350, 22, 70, 318, 100, 155)],
            "aplus": [("Midwest League", 400, 30, 84, 355, 112, 178)],
        }, (620, 35, 140, 570, 165, 290)),
        "Colt Keith": (26, {
            "aaa": [("International League", 190, 20, 40, 165, 52, 90)],
            "aa": [("Southern League", 410, 45, 78, 350, 115, 190)],
        }, (500, 42, 110, 450, 115, 185)),
        "Kyle Teel": (27, {
            "aa": [("Southern League", 330, 42, 78, 280, 88, 148)],
            "aplus": [("South Atlantic League", 270, 36, 66, 225, 70, 118)],
        }, None),
        "Justin Crawford": (29, {
            "aaa": [("International League", 260, 20, 60, 236, 74, 104)],
            "aplus": [("Northwest League", 330, 28, 74, 295, 95, 135)],
        }, None),
        "Carson Benge": (31, {"aaa": [("Mexican League", 300, 30, 70, 260, 78, 130)]}, None),
        "Brady House": (32, {"aaa": [("International League", 60, 4, 18, 55, 14, 22)],
                             "aa": [("Eastern League", 420, 36, 96, 375, 112, 186)]}, None),
    }

    def all_levels_hitting(self, person_id, sport_ids=None):
        return {
            1: people_payload(
                [
                    split("2025", 1, "Cincinnati Reds", pa=690, bb=62, so=190, ab=600, hits=160, tb=290),
                    split("2024", 1, "Cincinnati Reds", pa=670, bb=71, so=218, ab=571, hits=147, tb=273),
                    split("2023", 1, "Cincinnati Reds", pa=427, bb=31, so=144, ab=384, hits=90, tb=161),
                ]
            ),
            11: people_payload(
                [split("2023", 11, "Louisville Bats", pa=169, bb=17, so=48, ab=145, hits=44, tb=91)]
            ),
            12: people_payload(
                [split("2023", 12, "Chattanooga Lookouts", pa=284, bb=25, so=76, ab=246, hits=74, tb=147)]
            ),
            13: people_payload(
                [split("2022", 13, "Dayton Dragons", pa=536, bb=41, so=141, ab=473, hits=142, tb=270)]
            ),
            16: people_payload(
                [split("2021", 16, "ACL Reds", pa=99, bb=9, so=31, ab=86, hits=25, tb=44)]
            ),
        }, [14]


def main_() -> None:
    main.client = Stub()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=8123, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2)

    from playwright.sync_api import sync_playwright

    shots = {
        "home": "http://127.0.0.1:8123/",
        "matches": "http://127.0.0.1:8123/search?q=will+smith",
        "empty": "http://127.0.0.1:8123/search?q=zzzz",
        "error": "http://127.0.0.1:8123/search?q=boom",
        "results": "http://127.0.0.1:8123/player/682829",
    }
    compare_form = {
        "players": "Elly De La Cruz\nBobby Witt Jr.\nCorbin Carroll\nJackson Chourio\nJasson Dominguez\nWyatt Langford\nJulio Rodriguez\nGunnar Henderson\nAnthony Volpe\nColt Keith\nEvan Carter\nMasyn Winn\nJordan Lawlar\nWill Smith\nNobody At All",
        "pair": "mlb-aaa",
        "min_pa": "120",
        "exclude": "on",
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        for name, url in shots.items():
            page.goto(url, wait_until="networkidle")
            page.screenshot(path=f"/tmp/shot-{name}.png", full_page=True)
            print("captured", name)
        page.goto("http://127.0.0.1:8123/compare", wait_until="networkidle")
        page.screenshot(path="/tmp/shot-compare-form.png", full_page=True)
        print("captured compare-form")
        page.fill("#players", compare_form["players"])
        page.select_option("#pair", compare_form["pair"])
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/shot-compare.png", full_page=True)
        print("captured compare")
        page.goto("http://127.0.0.1:8123/projection", wait_until="networkidle")
        page.screenshot(path="/tmp/shot-projection-form.png", full_page=True)
        print("captured projection-form")
        page.fill("#players", "\n".join(list(Stub.PROSPECTS) + ["Will Smith", "Nobody At All"]))
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        page.screenshot(path="/tmp/shot-projection.png", full_page=True)
        print("captured projection")

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto("http://127.0.0.1:8123/translations", wait_until="networkidle")
        page.wait_for_timeout(900)
        page.screenshot(path="/tmp/shot-translations.png", full_page=True)
        print("captured translations")
        page.set_viewport_size({"width": 400, "height": 900})
        page.screenshot(path="/tmp/shot-compare-mobile.png", full_page=True)
        print("captured compare-mobile")
        browser.close()
    server.should_exit = True


if __name__ == "__main__":
    main_()
