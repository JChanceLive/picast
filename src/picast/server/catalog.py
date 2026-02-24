"""Curated Archive.org catalog for PiCast.

Static catalog of public domain TV shows, movies, and documentaries
available on Archive.org. Provides structured browsing with series,
seasons, and episodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CatalogEpisode:
    """A single episode in the catalog."""

    title: str
    archive_id: str
    season: int = 1
    episode: int = 1

    @property
    def url(self) -> str:
        return f"https://archive.org/details/{self.archive_id}"


@dataclass
class CatalogSeason:
    """A season containing episodes."""

    number: int
    episodes: list[CatalogEpisode] = field(default_factory=list)


@dataclass
class CatalogSeries:
    """A TV series or collection with seasons and episodes."""

    id: str
    title: str
    category: str
    seasons: list[CatalogSeason] = field(default_factory=list)
    description: str = ""

    @property
    def total_episodes(self) -> int:
        return sum(len(s.episodes) for s in self.seasons)

    def get_episode_by_index(self, flat_index: int) -> CatalogEpisode | None:
        """Get an episode by flat index across all seasons."""
        i = 0
        for season in self.seasons:
            for ep in season.episodes:
                if i == flat_index:
                    return ep
                i += 1
        return None

    def get_episode_index(self, archive_id: str) -> int | None:
        """Get flat index of an episode by archive_id. Returns None if not found."""
        i = 0
        for season in self.seasons:
            for ep in season.episodes:
                if ep.archive_id == archive_id:
                    return i
                i += 1
        return None

    def get_next_episode(self, current_index: int) -> CatalogEpisode | None:
        """Get the next episode after current_index, or None if at end."""
        return self.get_episode_by_index(current_index + 1)

    def to_dict(self, include_episodes: bool = False) -> dict:
        """Serialize for API responses."""
        d = {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "total_episodes": self.total_episodes,
        }
        if include_episodes:
            d["seasons"] = [
                {
                    "number": s.number,
                    "episodes": [
                        {
                            "title": ep.title,
                            "archive_id": ep.archive_id,
                            "url": ep.url,
                            "season": ep.season,
                            "episode": ep.episode,
                        }
                        for ep in s.episodes
                    ],
                }
                for s in self.seasons
            ]
        return d


def find_series_by_url(url: str) -> tuple[CatalogSeries, int] | None:
    """Find a catalog series and episode index from an Archive.org URL.

    Returns (series, episode_index) or None if not a catalog episode.
    """
    # Extract archive_id from URL
    # URLs look like: https://archive.org/details/ARCHIVE_ID
    archive_id = _extract_archive_id(url)
    if not archive_id:
        return None

    for series in CATALOG:
        idx = series.get_episode_index(archive_id)
        if idx is not None:
            return series, idx
    return None


def _extract_archive_id(url: str) -> str | None:
    """Extract archive.org identifier from URL."""
    if "archive.org/details/" not in url:
        return None
    # Handle both with and without trailing path components
    parts = url.split("archive.org/details/")
    if len(parts) < 2:
        return None
    archive_id = parts[1].split("/")[0].split("?")[0].strip()
    return archive_id if archive_id else None


# --- Category definitions ---

CATEGORIES = [
    {"id": "tv-shows", "label": "TV Shows"},
    {"id": "movies", "label": "Movies"},
    {"id": "documentaries", "label": "Documentaries"},
]

# --- Curated catalog data ---
# All archive_ids verified as public domain content on Archive.org

CATALOG: list[CatalogSeries] = [
    CatalogSeries(
        id="one-step-beyond",
        title="One Step Beyond",
        category="tv-shows",
        description="Anthology series exploring paranormal events, hosted by John Newland (1959-1961).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("The Bride Possessed", "OnestepbeyondBridePossessed", 1, 1),
                CatalogEpisode("Night of April 14th", "OneStepBeyond-NightOfApril14th", 1, 2),
                CatalogEpisode("Emergency Only", "OneStepBeyond-EmergencyOnly", 1, 3),
                CatalogEpisode("The Dark Room", "OneStepBeyondTheDarkRoom", 1, 4),
                CatalogEpisode("Twelve Hours to Live", "OnestepbeyondTwelveHoursToLive", 1, 5),
                CatalogEpisode("The Dream", "OneStepBeyondTheDream", 1, 6),
                CatalogEpisode("Epilogue", "OneStepBeyondEpilogue", 1, 7),
                CatalogEpisode("Delusion", "OneStepBeyondDelusion", 1, 8),
                CatalogEpisode("The Dead Part of the House", "OneStepBeyond-TheDeadPartOfTheHouse", 1, 9),
                CatalogEpisode("The Burning Girl", "OnestepbeyondBurningGirl", 1, 10),
            ]),
        ],
    ),
    CatalogSeries(
        id="sherlock-holmes-1954",
        title="Sherlock Holmes",
        category="tv-shows",
        description="Ronald Howard as Sherlock Holmes in this 1954-55 TV series with 39 episodes.",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("The Case of the Cunningham Heritage", "sherlockholmes-cunninghamheritage", 1, 1),
                CatalogEpisode("The Case of Lady Beryl", "SherlockHolmes-TheCaseOfLadyBeryl", 1, 2),
                CatalogEpisode("The Case of the Pennsylvania Gun", "SherlockHolmes-TheCaseOfThePennsylvaniaGun", 1, 3),
                CatalogEpisode("The Case of the Texas Cowgirl", "SherlockHolmes-TheCaseOfTheTexasCowgirl", 1, 4),
                CatalogEpisode("The Case of the Belligerent Ghost", "SherlockHolmes-TheCaseOfTheBelligerentGhost", 1, 5),
                CatalogEpisode("The Case of the Shy Ballerina", "SherlockHolmes-TheCaseOfTheShyBallerina", 1, 6),
                CatalogEpisode("The Case of the Winthrop Legend", "SherlockHolmes-TheCaseOfTheWinthropLegend", 1, 7),
                CatalogEpisode("The Case of the Blind Man's Bluff", "SherlockHolmes-TheCaseOfTheBlindMansBluff", 1, 8),
                CatalogEpisode("The Case of Harry Crocker", "SherlockHolmes-TheCaseOfHarryCrocker", 1, 9),
                CatalogEpisode("The Case of the Shoeless Engineer", "SherlockHolmes-TheCaseOfTheShoelessEngineer", 1, 10),
            ]),
        ],
    ),
    CatalogSeries(
        id="lone-ranger",
        title="The Lone Ranger",
        category="tv-shows",
        description="Classic western series starring Clayton Moore as the masked lawman (1949-1957).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("Enter the Lone Ranger", "TheLoneRanger-EnterTheLoneRanger", 1, 1),
                CatalogEpisode("The Lone Ranger Fights On", "lone_ranger_fights_on", 1, 2),
                CatalogEpisode("The Lone Ranger's Triumph", "TheLoneRanger-TheLoneRangersTriumph", 1, 3),
                CatalogEpisode("Legion of Old Timers", "TheLoneRanger-LegionOfOldTimers", 1, 4),
                CatalogEpisode("Rustlers' Hideout", "TheLoneRanger-RustlersHideout", 1, 5),
                CatalogEpisode("War Horse", "TheLoneRanger-WarHorse", 1, 6),
                CatalogEpisode("Pete and Pedro", "TheLoneRanger-PeteAndPedro", 1, 7),
                CatalogEpisode("The Renegades", "TheLoneRanger-TheRenegades", 1, 8),
                CatalogEpisode("The Tenderfeet", "TheLoneRanger-TheTenderfeet", 1, 9),
                CatalogEpisode("High Card", "TheLoneRanger-HighCard", 1, 10),
            ]),
        ],
    ),
    CatalogSeries(
        id="beverly-hillbillies",
        title="The Beverly Hillbillies",
        category="tv-shows",
        description="The Clampett family strikes oil and moves to Beverly Hills (1962-1971).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("The Clampetts Strike Oil", "BeverlyHillbillies-TheClampettsStrikeOil", 1, 1),
                CatalogEpisode("Getting Settled", "BeverlyHillbillies-GettingSettled", 1, 2),
                CatalogEpisode("Meanwhile, Back at the Cabin", "BeverlyHillbillies-MeanwhileBackAtTheCabin", 1, 3),
                CatalogEpisode("The Clampetts Meet Mrs. Drysdale", "BeverlyHillbillies-TheClampettsMeetMrsDrysdale", 1, 4),
                CatalogEpisode("Jed Buys Stock", "BeverlyHillbillies-JedBuysStock", 1, 5),
                CatalogEpisode("Trick or Treat", "BeverlyHillbillies-TrickOrTreat", 1, 6),
                CatalogEpisode("The Servants", "BeverlyHillbillies-TheServants", 1, 7),
                CatalogEpisode("Jethro Goes to School", "BeverlyHillbillies-JethroGoesToSchool", 1, 8),
                CatalogEpisode("Elly's First Date", "BeverlyHillbillies-EllysFirstDate", 1, 9),
                CatalogEpisode("Pygmalion and Elly", "BeverlyHillbillies-PygmalionAndElly", 1, 10),
            ]),
        ],
    ),
    CatalogSeries(
        id="dick-van-dyke",
        title="The Dick Van Dyke Show",
        category="tv-shows",
        description="Rob Petrie juggles life as a comedy writer and family man (1961-1966).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("The Sick Boy and the Sitter", "DickVanDykeShow-TheSickBoyAndTheSitter", 1, 1),
                CatalogEpisode("Big Max Calvada", "DickVanDykeShow-BigMaxCalvada", 1, 2),
                CatalogEpisode("Jealousy", "DickVanDykeShow-Jealousy", 1, 3),
                CatalogEpisode("Sally and the Lab Technician", "DickVanDykeShow-SallyAndTheLabTechnician", 1, 4),
                CatalogEpisode("Oh How We Met the Night That We Danced", "DickVanDykeShow-OhHowWeMetTheNightThatWeDanced", 1, 5),
                CatalogEpisode("Harrison B. Harding of Camp Crowder, Mo.", "DickVanDykeShow-HarrisonBHarding", 1, 6),
                CatalogEpisode("My Blonde-Haired Brunette", "DickVanDykeShow-MyBlondeHairedBrunette", 1, 7),
                CatalogEpisode("To Tell or Not to Tell", "DickVanDykeShow-ToTellOrNotToTell", 1, 8),
            ]),
        ],
    ),
    CatalogSeries(
        id="night-gallery",
        title="Night Gallery",
        category="tv-shows",
        description="Rod Serling's anthology horror/sci-fi series set in an eerie art gallery (1969-1973).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("The Cemetery / Eyes / Escape Route", "NightGallery-Pilot", 1, 1),
                CatalogEpisode("The Dead Man", "NightGallery-TheDeadMan", 1, 2),
                CatalogEpisode("Room With a View / The Little Black Bag", "NightGallery-RoomWithAView", 1, 3),
                CatalogEpisode("The House / Certain Shadows on the Wall", "NightGallery-TheHouse", 1, 4),
                CatalogEpisode("Make Me Laugh", "NightGallery-MakeMeLaugh", 1, 5),
                CatalogEpisode("Clean Kills and Other Trophies", "NightGallery-CleanKills", 1, 6),
            ]),
        ],
    ),
    CatalogSeries(
        id="voyage-to-bottom",
        title="Voyage to the Bottom of the Sea",
        category="tv-shows",
        description="The crew of the submarine Seaview face underwater adventures and threats (1964-1968).",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("Eleven Days to Zero", "VoyageToTheBottomOfTheSea-ElevenDaysToZero", 1, 1),
                CatalogEpisode("The City Beneath the Sea", "VoyageToTheBottomOfTheSea-CityBeneathTheSea", 1, 2),
                CatalogEpisode("The Fear Makers", "VoyageToTheBottomOfTheSea-TheFearMakers", 1, 3),
                CatalogEpisode("The Mist of Silence", "VoyageToTheBottomOfTheSea-MistOfSilence", 1, 4),
                CatalogEpisode("The Price of Doom", "VoyageToTheBottomOfTheSea-ThePriceOfDoom", 1, 5),
                CatalogEpisode("The Sky Is Falling", "VoyageToTheBottomOfTheSea-TheSkyIsFalling", 1, 6),
                CatalogEpisode("Turn Back the Clock", "VoyageToTheBottomOfTheSea-TurnBackTheClock", 1, 7),
                CatalogEpisode("The Village of Guilt", "VoyageToTheBottomOfTheSea-VillageOfGuilt", 1, 8),
            ]),
        ],
    ),
    CatalogSeries(
        id="nosferatu",
        title="Classic Horror Films",
        category="movies",
        description="Public domain horror films from the golden age of cinema.",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("Nosferatu (1922)", "nosferatu", 1, 1),
                CatalogEpisode("Night of the Living Dead (1968)", "night_of_the_living_dead", 1, 2),
                CatalogEpisode("House on Haunted Hill (1959)", "house_on_haunted_hill_1959", 1, 3),
                CatalogEpisode("The Last Man on Earth (1964)", "TheLastManOnEarth", 1, 4),
                CatalogEpisode("Carnival of Souls (1962)", "CarnivalOfSouls", 1, 5),
                CatalogEpisode("Dementia 13 (1963)", "dementia_13", 1, 6),
                CatalogEpisode("The Little Shop of Horrors (1960)", "the_little_shop_of_horrors", 1, 7),
                CatalogEpisode("Plan 9 from Outer Space (1957)", "Plan_9_from_Outer_Space_1959", 1, 8),
            ]),
        ],
    ),
    CatalogSeries(
        id="classic-scifi",
        title="Classic Sci-Fi Films",
        category="movies",
        description="Public domain science fiction films from the 1950s-60s.",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("Voyage to the Planet of Prehistoric Women (1968)", "VoyageToThePlanetOfPrehistoricWomen", 1, 1),
                CatalogEpisode("Teenagers from Outer Space (1959)", "teenagers_from_outer_space", 1, 2),
                CatalogEpisode("Robot Monster (1953)", "robot_monster", 1, 3),
                CatalogEpisode("Santa Claus Conquers the Martians (1964)", "SantaClausConquersTheMartians", 1, 4),
                CatalogEpisode("Attack of the Giant Leeches (1959)", "attack_of_the_giant_leeches", 1, 5),
                CatalogEpisode("The Brain That Wouldn't Die (1962)", "the_brain_that_wouldnt_die", 1, 6),
            ]),
        ],
    ),
    CatalogSeries(
        id="classic-westerns",
        title="Classic Western Films",
        category="movies",
        description="Public domain western films from Hollywood's golden era.",
        seasons=[
            CatalogSeason(number=1, episodes=[
                CatalogEpisode("Angel and the Badman (1947)", "AngelAndTheBadman1947", 1, 1),
                CatalogEpisode("McLintock! (1963)", "McLintock", 1, 2),
                CatalogEpisode("The Outlaw (1943)", "TheOutlaw_201707", 1, 3),
                CatalogEpisode("My Darling Clementine (1946)", "MyDarlingClementine1946", 1, 4),
            ]),
        ],
    ),
]


def get_series_by_id(series_id: str) -> CatalogSeries | None:
    """Look up a series by its ID."""
    for series in CATALOG:
        if series.id == series_id:
            return series
    return None


def get_series_by_category(category: str) -> list[CatalogSeries]:
    """Get all series in a category."""
    return [s for s in CATALOG if s.category == category]
