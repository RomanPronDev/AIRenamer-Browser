"""Browser provisioning regression fixtures: no network or installed-user writes."""
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock
import urllib.error
import zipfile

import pytest
import browser_ffmpeg as ff
import browser_native
import browser_platform
import config
import utils


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    ff._windows_validated.clear()
    monkeypatch.setattr(ff.shutil, 'which', lambda _: None)
    monkeypatch.setattr(browser_platform, 'is_macos', lambda: False)


def archive(monkeypatch, *, digest=None):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as package:
        package.writestr('ffmpeg/bin/ffmpeg.exe',b'working FFmpeg')
    content=buffer.getvalue()
    sha=digest or hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(ff,'_windows_source',lambda _:('https://example.invalid/ffmpeg.zip',sha))
    def response(*args,**kwargs):
        stream=io.BytesIO(content);stream.headers={'Content-Length':str(len(content))};return stream
    download=Mock(side_effect=response)
    monkeypatch.setattr(ff.urllib.request,'urlopen',download)
    monkeypatch.setattr(utils,'validate_ffmpeg_executable',lambda path: Path(path).is_file() and Path(path).read_bytes()==b'working FFmpeg')
    return download


def test_local_or_path_ffmpeg_wins_without_network(tmp_path,monkeypatch):
    local=tmp_path/'ffmpeg.exe';local.write_bytes(b'working FFmpeg')
    monkeypatch.setattr(utils,'validate_ffmpeg_executable',lambda path: Path(path)==local)
    monkeypatch.setattr(ff,'_windows_source',lambda _:pytest.fail('local tool must prevent download'))
    assert ff.provision(str(local))==str(local)
    local2=tmp_path/'elsewhere.exe';local2.write_bytes(b'working FFmpeg')
    monkeypatch.setattr(utils,'validate_ffmpeg_executable',lambda path: Path(path)==local2)
    monkeypatch.setattr(ff.shutil,'which',lambda _:str(local2))
    assert ff.provision(str(tmp_path/'Tools/ffmpeg.exe'))==str(local2)
    assert ff.tool_status(tmp_path/'Tools/ffmpeg.exe')['state']=='ready'


def test_api_throttling_uses_official_checksum_fallback(monkeypatch):
    monkeypatch.setattr(utils,'_load_release_metadata',Mock(side_effect=urllib.error.HTTPError('api',403,'rate limit',{},None)))
    digest='a'*64
    read=Mock(return_value=(digest+'  '+ff.WINDOWS_ASSET+'\n').encode())
    monkeypatch.setattr(utils,'_read_url_bytes',read)
    assert ff._windows_source(60)==(ff.WINDOWS_RELEASE+ff.WINDOWS_ASSET,digest)
    assert read.call_args.args[0].endswith('/checksums.sha256')
    read.return_value=(digest+'  unrelated.zip\n').encode()
    with pytest.raises(utils.FFmpegResolutionError,match='lacks'):
        ff._windows_source(60)


def test_verified_download_installs_once_and_reports_progress(tmp_path,monkeypatch):
    target=tmp_path/'Tools/ffmpeg.exe';download=archive(monkeypatch)
    assert ff.provision(str(target))==str(target)
    assert ff.status(target)['state']=='ready'
    assert ff.provision(str(target))==str(target)
    assert download.call_count==1
    assert not list(target.parent.glob('.ffmpeg-install-*'))


def test_transient_failure_retries_and_later_attempts_are_not_session_poisoned(tmp_path,monkeypatch):
    target=tmp_path/'Tools/ffmpeg.exe';download=archive(monkeypatch)
    response=download.side_effect
    download.side_effect=[urllib.error.URLError('connection reset'),response()]
    monkeypatch.setattr(ff.time,'sleep',lambda _:None)
    assert ff.provision(str(target))==str(target)
    assert download.call_count==2
    other=tmp_path/'other/ffmpeg.exe'
    download.side_effect=urllib.error.URLError('offline')
    with pytest.raises(utils.FFmpegResolutionError,match='offline'):
        ff.provision(str(other))
    assert ff.status(other)['state']=='error'
    download.side_effect=response
    assert ff.provision(str(other))==str(other)


def test_bad_digest_never_commits_and_can_be_retried(tmp_path,monkeypatch):
    target=tmp_path/'Tools/ffmpeg.exe';download=archive(monkeypatch,digest='0'*64)
    monkeypatch.setattr(ff.time,'sleep',lambda _:None)
    with pytest.raises(utils.FFmpegResolutionError,match='SHA-256'):
        ff.provision(str(target))
    assert not target.exists();assert download.call_count==3
    assert not list(target.parent.glob('.ffmpeg-install-*'))
    archive(monkeypatch)
    assert ff.provision(str(target))==str(target)


def test_windows_settings_setup_detaches_and_reuses_active_or_ready_state(tmp_path,monkeypatch):
    target=tmp_path/'Tools/ffmpeg.exe';monkeypatch.setattr(config,'FFMPEG_PATH',str(target))
    host=browser_native.BrowserHost.__new__(browser_native.BrowserHost)
    state={'state':'missing'}
    monkeypatch.setattr(ff,'tool_status',lambda _:state)
    launch=Mock();monkeypatch.setattr(browser_native.subprocess,'Popen',launch)
    assert host.ffmpeg_setup({})['started'] is True
    assert '--install-ffmpeg' in launch.call_args.args[0]
    assert host.ffmpeg_status({})['state']=='missing'
    state={'state':'downloading'};host.ffmpeg_setup({});assert launch.call_count==1
    state={'state':'ready','path':str(target)};assert host.ffmpeg_setup({})['started'] is False
    state={'state':'error'};launch.side_effect=OSError('launch blocked')
    with pytest.raises(OSError,match='blocked'):host.ffmpeg_setup({})
    assert ff.status(target)['state']=='error'


def test_abandoned_checking_status_allows_retry(tmp_path):
    target=tmp_path/'ffmpeg.exe'
    ff.status_path(target).write_text(json.dumps({'state':'checking','updatedAt':0}))
    assert ff.tool_status(target)['state']=='error'


def test_mac_ready_status_without_the_executable_does_not_block_retry(tmp_path,monkeypatch):
    monkeypatch.setattr(browser_platform,'is_macos',lambda:True)
    target=tmp_path/'ffmpeg'
    ff.status_path(target).write_text(json.dumps({'state':'ready'}))
    assert ff.tool_status(target)['state']=='missing'
