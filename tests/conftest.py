import pytest
import respx

from hyperlapse.config import Config, SelectionWeights, Settings
from hyperlapse.core.http import CachedHttp
from hyperlapse.server.app import create_app

from . import fixture_data


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        cache_dir=tmp_path / "cache",
        clear_cache_on_quit=True,
        defaults=Settings(),
        selection=SelectionWeights(),
        mapillary_token="MLY|test",
    )


@pytest.fixture
def mocked():
    with respx.mock(assert_all_called=False) as router:
        fixture_data.install(router)
        yield router


@pytest.fixture
def app(cfg, mocked, tmp_path):
    http = CachedHttp(mapillary_token=cfg.mapillary_token, backoff_initial=0.01)
    return create_app(cfg, http=http, web_dist=tmp_path / "no-dist")
