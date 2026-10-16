from scripts.native_chrome import profile_path,stop_browser


def test_cleanup_never_terminates_unrelated_browser(monkeypatch):
    import psutil
    site={'url':'https://example.test/shop'}
    calls=[]
    class Process:
        def __init__(self,name,command,label): self.info={'name':name,'cmdline':command};self.label=label
        def terminate(self): calls.append(self.label)
        def wait(self,timeout): pass
    owned='--user-data-dir='+str(profile_path(site).resolve())
    processes=[Process('chrome.exe',[owned],'owned'),Process('chrome.exe',['--user-data-dir=C:/daily-profile'],'daily'),
               Process('python.exe',[owned],'python'),Process('chrome.exe',None,'hidden')]
    monkeypatch.setattr(psutil,'process_iter',lambda _:processes)
    stop_browser(site)
    assert calls==['owned']
