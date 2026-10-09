from tv_mgmt_pure.media import describe, media_from, media_key, show_of


def test_nothing_playing():
    assert media_from("idle", {"media_title": "Bluey"}) is None
    assert media_from("standby", {}) is None
    assert media_from("playing", {}) is None


def test_series():
    media = media_from("playing", {
        "media_title": "Hammerbarn", "media_series_title": "Bluey", "media_season": "2", "media_episode": 14,
    })
    assert media == {"title": "Hammerbarn", "series": "Bluey", "season": 2, "episode": 14}
    assert show_of(media) == "Bluey"
    assert describe(media) == "Bluey, Season 2, Episode 14: Hammerbarn"


def test_episode_in_artist_like_prime_video():
    media = media_from("paused", {
        "media_title": "Genevieve's Playhouse",
        "media_artist": "Season 2, Ep. 14 Learn Vehicle Names with Transforming Robots!",
    })
    assert media == {
        "series": "Genevieve's Playhouse", "season": 2, "episode": 14,
        "title": "Learn Vehicle Names with Transforming Robots!",
    }
    assert show_of(media) == "Genevieve's Playhouse"
    assert media_from("playing", {"media_title": "Show", "media_artist": "S1 E3"})["title"] == "Show"


def test_movie_and_song():
    movie = media_from("playing", {"media_title": "Moana"})
    assert show_of(movie) == "Moana" and describe(movie) == "Moana"
    song = media_from("playing", {"media_title": "How Far I'll Go", "media_artist": "Auliʻi Cravalho", "media_album_name": "Moana"})
    assert describe(song) == "How Far I'll Go by Auliʻi Cravalho"
    assert show_of(song) == "How Far I'll Go"


def test_key_changes_per_episode():
    a = media_from("playing", {"media_title": "A", "media_series_title": "Bluey", "media_episode": 1})
    b = media_from("paused", {"media_title": "A", "media_series_title": "Bluey", "media_episode": 1})
    c = media_from("playing", {"media_title": "B", "media_series_title": "Bluey", "media_episode": 2})
    assert media_key(a) == media_key(b) != media_key(c)
    assert media_key(None) is None
