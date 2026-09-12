#!/usr/bin/env python3

import argparse
import posixpath
import sys
import unicodedata
from pathlib import Path
import requests

def normalize_name(name):
    """
    Normalize names for deciding whether a match is exact
    This intentionally does NOT do fuzzy matching, as it matters that the match is exact.
    Uses casefold and unicodedata.normalize to throughly normalize artist names
    For example:
    name = "ＳＴＲＡẞＥ"

    name = unicodedata.normalize("NFKC", name)
    Result: STRAẞE

    name = name.casefold()
    Result: strasse
    """
    name = unicodedata.normalize("NFKC", name) #Normalize the name
    return " ".join(name.casefold().split()) #Casefold and join to normalize arbitrary whitespace

class Lidarr:
    def __init__(self, url, api_key):
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        })

        #Cache album lookups because the same album name could
        #be checked against several artist candidates. E.g. "Greatest hits"
        self.album_lookup_cache = {}

    def get(self, endpoint, params=None):
        #Call self.session.get to perform a GET request
        response = self.session.get(
            f"{self.url}/api/v1/{endpoint}",
            params=params,
            timeout=60
        )
        #Throw an error if we get a bad HTTP status
        response.raise_for_status()
        return response.json()

    def post(self, endpoint, payload):
        #Call self.session.post to perform a POST request
        response = self.session.post(
            f"{self.url}/api/v1/{endpoint}",
            json=payload,
            timeout=60
        )
        #Throw an error if we get a bad HTTP status
        response.raise_for_status()
        return response.json()

    def lookup_artist(self, name):
        #Do a GET request on the artist/lookup endpoint to lookup an artist
        return self.get("artist/lookup", {"term": name})

    def lookup_album(self, name):
        """
        Look up an album by title
        Cache results since there may be album collisions between artists. E.g. "Greatest hits"
        """
        key = normalize_name(name)

        if key not in self.album_lookup_cache:
            self.album_lookup_cache[key] = self.get(
                "album/lookup",
                {"term": name}
            )

        return self.album_lookup_cache[key]

    def get_existing_artists(self):
        #Do a GET request on the artist endpoint to get existing artists
        return self.get("artist")

    def get_quality_profiles(self):
        return self.get("qualityprofile")

    def get_metadata_profiles(self):
        return self.get("metadataprofile")

    def search(self, term):
        #Do a GET request on the search endpoint
        return self.get("search", {"term":term})

    def add_artist(self, 
        candidate, 
        artist_folder_name,
        root_folder, 
        quality_profile_id, 
        metadata_profile_id
    ):
        """
        Add an artist to the Lidarr library by doing a POST request
        Params: 
            candidate: A dictionary of artist data
            root_folder: The path to the root folder of the music library
            quality_profile_id: The ID of the desired quality profile
            metadata_profile_id: The 
        """
        artist_path = posixpath.join(
            root_folder.rstrip("/"),
            artist_folder_name
        )

        payload = {
                "artistName": candidate["artistName"],
                "foreignArtistId": candidate["foreignArtistId"],
                "qualityProfileId": quality_profile_id,
                "metadataProfileId": metadata_profile_id,
                "path": artist_path,
                "monitored": False,
                "addOptions": {
                    "monitor": "none",
                    "searchForMissingAlbums": False
                }
        }
        return self.post("artist", payload)

def discover_artist_folders(music_dir):
    """
    Assumes each immediate child directory of music_dir is an artist directory
    Returns a sorted list of artist folder names
    """
    root = Path(music_dir)

    if not root.is_dir():
        raise RuntimeError(f"Music directory does not exist: {root}")

    folders = []
    for directory in root.iterdir():
        if directory.is_dir():
            folders.append(directory)

    return sorted(
            folders,
            key = lambda path: path.name.casefold()
    )

def discover_album_folders(artist_folder):
    albums = []

    for directory in artist_folder.iterdir():
        if directory.is_dir():
            albums.append(directory.name)

    return sorted(
        albums,
        key=str.casefold
    )

def select_profile(profiles, profile_type):
    """
    Display Lidarr profiles and ask the user to choose one
    """
    if not profiles:
        raise RuntimeError(f"Lidarr returned no {profile_type} profiles.")

    print()
    print(f"Available {profile_type} profiles:")
    print("=" * 60)

    #Print out all the available profiles
    for index, profile in enumerate(profiles, start=1):
        print(
            f"[{index}] {profile['name']} "
            f"(Lidarr ID: {profile['id']})"
        )

    while True:
        choice = input(
            f"Select {profile_type} profile: "
        ).strip()

        try:
            index = int(choice) - 1
        except ValueError:
            print("Enter one of the numbers shown above.")
            continue

        if 0 <= index < len(profiles):
            return profiles[index]

        print("Invalid selection.")


def exact_candidate(folder_name, candidates):
    """
    Determine if an exact candidate match is available
    Returns the exact candidate match if one is available
    otherwise returns None
    """
    #Normalize the target's name from the folder name
    target = normalize_name(folder_name)

    #Filter out only exact matches for the current folder
    exact = [
        candidate
        for candidate in candidates
        if normalize_name(candidate.get("artistName", "")) == target
    ]

    #Multiple exact-name artists can exist in MusicBrainz
    #Do not automatically choose between them
    if len(exact) == 1:
        return exact[0]

    return None

def album_result_artist_id(album):
    """
    Extracts the MusicBrainz artist ID from a Lidarr album lookup result
    """
    artist = album.get("artist")

    if not isinstance(artist, dict):
        return None

    return artist.get("foreignArtistId")

def score_candidate_by_albums(lidarr, local_albums, candidate):
    """
    Count exact local-album-title matches belonging to this candidate artist.
    Returns: 
        {
            "match_count": int,
            "local_count": int,
            "matched_albums": [...]
        }
    """
    candidate_id = candidate.get("foreignArtistId")

    matched_albums = []

    #If no candidate ID exists return a zero-match result
    if not candidate_id:
        return {
            "match_count": 0,
            "local_count": len(local_albums),
            "matched_albums": []
        }

    for local_album in local_albums:
        try:
            results = lidarr.lookup_album(local_album)
        except requests.RequestException:
            # Album matching is only supporting evidence for a match
            # A failed album lookup shouldn't kill the entire lookup
            continue

        local_normalized = normalize_name(local_album)

        for album in results:
            #Skip the current album candidate if the normalized names don't match
            if normalize_name(album.get("title", "")) != local_normalized:
                continue

            #Skip the current album candidate if the IDs don't match
            if album_result_artist_id(album) != candidate_id:
                continue

            #Only add the candidate album to matched_albums if both the name and ID match
            matched_albums.append(local_album)
            break
    
    return {
        "match_count": len(matched_albums),
        "local_count": len(local_albums),
        "matched_albums": matched_albums
    }

def score_candidates(lidarr, artist_folder, candidates):
    """
    Add album-match evidence to each artist candidate
    """
    local_albums = discover_album_folders(artist_folder)

    scored = []

    for candidate in candidates:
        album_score = score_candidate_by_albums(
            lidarr,
            local_albums,
            candidate
        )

        scored.append({
            "candidate":candidate,
            "album_match_count": album_score["match_count"],
            "local_album_count": album_score["local_count"],
            "matched_albums": album_score["matched_albums"]
        })

    #Most album matches first
    #Lidarr's original lookup ordering is preserved when counts tie
    scored.sort(
        key=lambda item:
            item["album_match_count"],
        reverse=True
    )

    return scored

def automatic_album_candidate(scored_candidates):
    """
    Automatically accept an album-based match only when the following is true:
        Best candidate has >= 2 exact album matches
        No other candidate has the same number of matches

    One album match is useful evidence, but isn't enough for automatic import
    """
    if not scored_candidates:
        return None

    best = scored_candidates[0]

    #The best album must have at least 2 album matches
    if best["album_match_count"] < 2:
        return None

    if len(scored_candidates) > 1:
        second_best = scored_candidates[1]

        #The best album must have more matches than the next best candidate
        if second_best["album_match_count"] == best["album_match_count"]:
            return None

    return best

def display_candidate(number, scored_candidate):
    """
    Display an artist candidate plus its album-match evidence
    """
    #Gather info from scored_candidate
    candidate = scored_candidate["candidate"]

    #Here, <unknown> is a fallback string if the current candidate doesn't have artistName
    artist_name = candidate.get("artistName", "<unknown>")

    mbid = candidate.get("foreignArtistId", "<unknown>")

    match_count = scored_candidate["album_match_count"]

    local_count = scored_candidate["local_album_count"]

    matched_albums = scored_candidate["matched_albums"]

    #Print info about the candidate
    print(f"[{number}] {artist_name}")
    print(f"\tMBID: {mbid}")
    print(f"\tAlbum matches: {match_count}/{local_count}")

    if matched_albums:
        print(f"\tMatching albums: {', '.join(matched_albums)}")

def choose_candidate(folder_name, candidates):
    """
    Show the user artist candidates for folder_name
    and prompt them to select an artist.
    Returns the selected candidate, otherwise returns None
    """
    print()
    print("=" * 60)
    print(f"No unique exact match for: {folder_name}")
    print("=" * 60)

    if not candidates:
        print("Lidarr returned no candidates.")
        return None

    #Show the candidates for the current folder name
    for i, candidate in enumerate(candidates, start=1):
        display_candidate(i, candidate)

    print()
    print("\t[s] Skip this artist")

    #Loop on the input so that if a bad selection is made
    #the user will be prompted again
    while True:
        choice = input("Choose candidate: ").strip().lower()

        if choice == "s":
            return None

        #Perform validation on the user's input
        try:
            index = int(choice) - 1
        except ValueError:
            print("Enter a candidate number or 's'.")
            continue

        if 0 <= index < len(candidates):
            return candidates[index]

        print("Invalid selection!")

def show_plan(plan, unresolved, quality_profile, metadata_profile):
    print()
    print("=" * 60)
    print("FINAL IMPORT PLAN")
    print("=" * 60)

    print(
        "Quality profile: "
        f"{quality_profile['name']}"
    )

    print(
        "Metadata profile: "
        f"{metadata_profile['name']}"
    )
    
    if plan:
        #Print all the items in the plan
        for item in plan:
            match_type = item["match_type"].upper()
            folder = item["folder"].name
            artist = item["candidate"]["artistName"]
            mbid = item["candidate"]["foreignArtistId"]
            evidence = ""

            if item["album_match_count"] > 0:
                evidence = (
                    f" "
                    f"[{item['album_match_count']}/"
                    f"{item['local_album_count']} "
                    f"albums]"
                )
                
                print(
                    f"[{match_type:6}] "
                    f"{folder} -> {artist} "
                    f"({mbid})"
                    f"{evidence}"
                )
            else:
                print(
                    f"[{match_type:6}] "
                    f"{folder} -> {artist} "
                    f"({mbid})"
                )

    #Show unresolved items too
    if unresolved:
        print()
        print("NOT BEING IMPORTED")
        print("-" * 60)

        for folder in unresolved:
            print(folder.name)

    print()
    print(f"Artists selected: {len(plan)}")
    print(f"Artists skipped/unresolved: {len(unresolved)}")

def build_plan(
        lidarr,
        artist_folders,
        existing_artist_ids,
        preview
):
    """
    Build the complete proposed import plan.
    """
    automatic = []
    manual_needed = []
    unresolved = []

    print()
    print("Looking up artist folders in Lidarr...")
    print()

    for index, folder in enumerate(artist_folders, start=1):
        print(
            f"[{index}/"
            f"{len(artist_folders)}] "
            f"{folder.name}"
        )

        #Attempt to look up candidate artists
        try:
            raw_candidates = (
                lidarr.lookup_artist(folder.name)
            )
        except requests.RequestException as exc:
            print(f"\tArtist lookup failed {exc}")

            unresolved.append(folder)

            continue

        #Remove artists Lidarr already knows about
        candidates = [
            candidate for candidate in raw_candidates
            if candidate.get("foreignArtistId") not in existing_artist_ids
        ]

        already_existing = [
            candidate
            for candidate in raw_candidates
            if candidate.get("foreignArtistId") in existing_artist_ids
        ]

        if already_existing:
            existing_match = exact_candidate(
                folder.name,
                already_existing
            )

            if existing_match is not None:
                print(
                    f"\tSKIP -> "
                    f"{existing_match['artistName']} "
                    f"(already in Lidarr)"
                )
                continue

        if not candidates:
            print("\tNo new artist candidates returned!")

            unresolved.append(folder)
            continue

        #First priority is always unique exact artist-name match
        match = exact_candidate(folder.name, candidates)

        if match is not None:
            album_score = (
                score_candidate_by_albums(lidarr, discover_album_folders(folder), match)
            )

            print(
                f"\tEXACT -> "
                f"{match['artistName']} "
                f"({album_score['match_count']}/"
                f"{album_score['local_count']} albums matched)"
            )

            automatic.append({
                "folder": folder,
                "candidate": match,
                "match_type": "exact",
                "album_match_count": album_score["match_count"],
                "local_album_count": album_score["local_count"]
            })

            continue
        
        #Artist name wasn't an exact unique match
        #Rank candidates using exact album-title matches
        scored_candidates = score_candidates(lidarr, folder, candidates)

        album_match = (automatic_album_candidate(scored_candidates))

        if album_match is not None:
            candidate = album_match["candidate"]

            print(
                f"\tALBUM -> "
                f"{candidate['artistName']} "
                f"({album_match['album_match_count']}/"
                f"{album_match['local_album_count']} albums matched)"
            )
    
            automatic.append({
                "folder": folder,
                "candidate": candidate,
                "match_type": "album",
                "album_match_count": album_match['album_match_count'],
                "local_album_count": album_match['local_album_count']
            })

            continue

        best_count = 0

        if scored_candidates:
            best_count = scored_candidates[0]["album_match_count"]

        print(
            "\tNeeds manual selection "
            "(best candidate has "
            f"{best_count} exact album matches)"
        )

        manual_needed.append({
            "folder": folder,
            "candidates": scored_candidates
        })


    #Preview mode
    if preview:
        print()
        print("=" * 60)
        print("AUTOMATIC MATCHES")
        print("=" * 60)

        for item in automatic:
            print(
                f"[{item['match_type'].upper():6}] "
                f"{item['folder'].name} -> "
                f"{item['candidate']['artistName']}"
            )

        print()
        print("=" * 60)
        print("ARTISTS NEEDING REVIEW")
        print("=" * 60)
        
        for item in manual_needed:
            print()
            print(item["folder"].name)

            #Only show the first five candidates
            for index, scored_candidate in enumerate(item["candidates"][:5], start=1):
                display_candidate(index, scored_candidate)

        
        if unresolved:
            print()
            print("=" * 60)
            print("UNRESOLVED")
            print("=" * 60)

            for folder in unresolved:
                print(folder.name)

        return None, None
    
    #Start the plan with all the automatic imports
    plan = list(automatic)
    
    #Now resolve every ambiguous artist
    for item in manual_needed:
        selected = choose_candidate(item["folder"].name, item["candidates"])

        if selected is None:
            unresolved.append(item["folder"])

            continue

        selected_score = next(
                        scored
                        for scored in item["candidates"]
                        if(scored["candidate"]["foreignArtistId"] == selected["foreignArtistId"])
        )

        plan.append({
            "folder": item["folder"],
            "candidate": selected,
            "match_type": "manual",
            "album_match_count": selected_score["album_match_count"],
            "local_album_count": selected_score["local_album_count"],
        })

    return plan, unresolved

def parse_args():
    parser = argparse.ArgumentParser(description="Import existing artist folders into Lidarr using its API")

    parser.add_argument("--api-key", required=True, help="Lidarr API key")
    parser.add_argument("--lidarr-url", default="http://localhost:8686", help="Lidarr URL (default: http://localhost:8686)")
    parser.add_argument("--music-dir", required=True, help="Path visible to this script containing artist directories")
    parser.add_argument("--root-folder", required=True, help="Music root path as Lidarr sees it")
    parser.add_argument("--preview", action="store_true", help="Show proposed matches without modifying Lidarr")

    return parser.parse_args()

def main():
    args = parse_args()

    lidarr = Lidarr(args.lidarr_url, args.api_key)

    try:
        quality_profiles = lidarr.get_quality_profiles()

        metadata_profiles = lidarr.get_metadata_profiles()

        existing_artists = lidarr.get_existing_artists()

        artist_folders = (discover_artist_folders(args.music_dir))

    except(RuntimeError, requests.RequestException) as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr
        )

        return 1

    print(f"Found {len(artist_folders)} artist directories")
    print(
        f"Lidarr already contains "
        f"{len(existing_artists)} artists."
    )

    quality_profile = select_profile(quality_profiles, "quality")

    metadata_profile = select_profile(metadata_profiles, "metadata")

    existing_artist_ids = {
        artist.get("foreignArtistId")
        for artist in existing_artists
        if artist.get("foreignArtistId")
    }

    plan, unresolved = build_plan(lidarr, artist_folders, existing_artist_ids, args.preview)

    if args.preview:
        print()
        print("Preview complete. No changes were made.")
        return 0

    show_plan(plan, unresolved, quality_profile, metadata_profile)

    if not plan:
        print("Nothing to import.")
        return 0

    print()

    confirmation = input("Type 'yes' to import ALL artists shown above: ")

    if confirmation.lower() != "yes":
        print("Cancelled. No changes were made.")
        return 0

    print()
    print("=" * 60)
    print("IMPORTING TO LIDARR!!!")
    print("=" * 60)

    failures = []

    for index, item in enumerate(plan, start=1):
        candidate = item["candidate"]

        print(
            f"[{index}/{len(plan)}] "
            f"Adding {candidate['artistName']}..."
        )

        try:
            lidarr.add_artist(candidate, item["folder"].name, args.root_folder, quality_profile["id"], metadata_profile["id"])
        except requests.RequestException as exc:
            print(f"\tFAILED: {exc}")

            failures.append(item)
        else:
            print("\tAdded!")

    print()
    print("=" * 60)
    print("LIDARR IMPORT COMPLETE")
    print("=" * 60)

    print(f"Successfully submitted: {len(plan) - len(failures)}")
    print(f"Failed: {len(failures)}")

    if unresolved:
        print()
        print("Artists not imported because no selection was made: ")

        for folder in unresolved:
            print(f"\t{folder.name}")

    if failures:
        print()
        print("Artists that failed during API import: ")

        for item in failures: 
            print(f"\t{item['folder'].name} -> "
                f"{item['candidate']['artistName']}")
            
        return 2

    return 0



if __name__ == "__main__":
    sys.exit(main())
