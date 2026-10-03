from pathlib import Path
from unittest.mock import patch
import tools


def test_search_pin_consistent():
    for p in ('requirements.txt', 'requirements-render.txt'):
        assert 'duckduckgo-search==8.1.1' in Path(p).read_text()
    assert 'duckduckgo-search==6.3.5' not in Path('tools.py').read_text()


def test_search_formats_real_results():
    class Search:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def text(self, query, max_results):
            return [{'title': 'Official docs', 'href': 'https://example.invalid/docs', 'body': 'Evidence'}]
    with patch.object(tools, 'DDGS', Search), patch.object(tools, 'HAS_DDG', True):
        out = tools.web_search('test')
        assert out['status'] == 'success'
        assert 'https://example.invalid/docs' in out['output']


def test_search_client_initializes_with_transport():
    from duckduckgo_search import DDGS
    with DDGS() as search:
        assert search.client is not None
