import os

import anyio

from cratere.settings import Settings


def test_alternate_hosts():
    os.environ["CRATERE_HOST"] = "foo.example.com"
    os.environ["CRATERE_ALTERNATE_HOSTS"] = '["foobar", "bar", "baz"]'
    os.environ["CRATERE_INDEX"] = "hejho"
    os.environ["CRATERE_CACHE"] = "buzz"

    settings = Settings()

    assert settings.host == "foo.example.com"
    assert settings.alternate_hosts == ["foobar", "bar", "baz"]
    assert settings.index == anyio.Path("hejho")
    assert settings.cache == anyio.Path("buzz")
