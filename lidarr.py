import time
import posixpath
import unicodedata

#3rd party libs
import requests

#Local imports
from utils import *


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

    def get(self, endpoint, params=None, max_attempts=3):
        url = f"{self.url}/api/v1/{endpoint}"

        for attempt in range(1, max_attempts + 1):
            response = self.session.get(
                url,
                params=params,
                timeout=30
            )

            if response.status_code == 503 and attempt < max_attempts:
                term = params.get("term") if params else None

                description = endpoint
                if term:
                    description += f" '{term}'"

                wait_time = attempt * 3

                print(
                    f"\t503 during {description} "
                    f"retrying in {wait_time}s "
                    f"(attempt {attempt + 1}/{max_attempts})"
                )

                time.sleep(wait_time)
                continue

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
            time.sleep(0.25)
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

