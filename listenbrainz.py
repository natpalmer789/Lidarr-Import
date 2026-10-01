#Builtins
import time

#3rd party libs
import requests

class ListenBrainz:
    def __init__(self):
        self.url = "https://api.listenbrainz.org"
        self.session = requests.Session()

        self.popular_album_cache = {}

    def get(self, endpoint, params=None, max_attempts=3):
        """
        Performs a GET request on the specified listenbrainz endpoint
        """
        url = f"{self.url}/{endpoint}"

        
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=30
                )

            except requests.RequestException:
                if attempt >= max_attempts:
                    raise

                wait_time = attempt * 3

                print(
                    f"\tListenBrainz request failed. Retrying in {wait_time}s. "
                    f"(attempt {attempt + 1}/{max_attempts})"
                )

                time.sleep(wait_time)
                continue

            #Retry on a 503 error code so we still get album names from listenbrainz
            if response.status_code == 503 and attempt < max_attempts:
                wait_time = attempt * 3

                print(
                    f"\t503 during candidate album query. Retrying in {wait_time}s. "
                    f"(attempt {attempt+1}/{max_attempts})"
                )

                time.sleep(wait_time)
                continue

            #Throw an error if we get a bad HTTP status
            response.raise_for_status()
            return response.json()
    
    def get_popular_albums(self, artist_mbid, count=3):
        """
        Get the most popular album releases for an artist
        """
        if artist_mbid in self.popular_album_cache:
            return self.popular_album_cache[artist_mbid]

        results = self.get(f"1/popularity/top-release-groups-for-artist/{artist_mbid}")

        albums = []

        for result in results:
            release_group = result.get("release_group", {})

            #Here we want to get actual albums rather than singles, etc.
            if release_group.get("type") != "Album":
                continue

            name = release_group.get("name")

            if name:
                albums.append(name)

            if len(albums) >= count:
                break

        self.popular_album_cache[artist_mbid] = albums

        return albums





