"""The desktop browser must survive an adapter disconnect and error."""
from contextlib import contextmanager
from types import SimpleNamespace, ModuleType
import sys
import pytest
from scripts.traffic_yandex_worker import attached_yandex

@pytest.mark.parametrize('fail', [False,True])
def test_attach_restores_viewport_without_closing_browser(monkeypatch,fail):
    sizes=[]
    class Page:
        url='https://2gis.ru/p_kamchatskiy'
        viewport_size=None
        def evaluate(self,_):return {'width':1200,'height':800}
        def set_viewport_size(self,size):sizes.append(size)
        def set_default_timeout(self,_):pass
        def screenshot(self,**_):pass
    page=Page()
    class Browser:
        contexts=[SimpleNamespace(pages=[page])]
        def close(self):raise AssertionError('Persistent browser must stay open')
    browser=Browser()
    def connect(endpoint,**_):
        assert endpoint=='http://127.0.0.1:9223'
        return browser
    @contextmanager
    def playwright():yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    package=ModuleType('playwright');module=ModuleType('playwright.sync_api');module.sync_playwright=playwright
    monkeypatch.setitem(sys.modules,'playwright',package);monkeypatch.setitem(sys.modules,'playwright.sync_api',module)
    if fail:
        with pytest.raises(RuntimeError,match='capture failed'):
            with attached_yandex() as attached:
                assert attached is page
                raise RuntimeError('capture failed')
    else:
        with attached_yandex() as attached:assert attached is page
    assert sizes==[{'width':4096,'height':4096},{'width':1200,'height':800}]
