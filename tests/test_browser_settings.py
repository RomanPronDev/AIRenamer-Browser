"""Process-level checks: Browser writes must never touch the desktop store."""
import json
import os
from pathlib import Path
import subprocess
import sys
import io
import struct
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import browser_native

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_protocol_keeps_diagnostic_prints_off_its_binary_channel():
    request = json.dumps({'id': 1, 'type': 'ping'}).encode()
    incoming = io.BytesIO(struct.pack('<I', len(request)) + request)
    outgoing, diagnostic, accidental_stdout = io.BytesIO(), io.StringIO(), io.StringIO()
    host = Mock()
    def dispatch(message):
        print('helper diagnostic during operation')
        return {'version': '0.27.7'}
    host.dispatch.side_effect = dispatch
    with patch.object(browser_native, 'BrowserHost', return_value=host), \
         redirect_stdout(accidental_stdout), redirect_stderr(diagnostic):
        browser_native.run(incoming, outgoing)
    wire = outgoing.getvalue()
    size = struct.unpack('<I', wire[:4])[0]
    assert len(wire) == 4 + size
    assert json.loads(wire[4:])['result']['version'] == '0.27.7'
    assert accidental_stdout.getvalue() == ''
    assert 'helper diagnostic' in diagnostic.getvalue()
    host.close.assert_called_once()


def child(local, code):
    env = dict(os.environ, LOCALAPPDATA=str(local))
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def desktop_store(local):
    child(local, """
import config
config.setup_directories()
config.save_projects({'DESKTOP':r'X:\\DesktopOnly'}, project_modes={'DESKTOP':'shots'})
config.add_ignored_folder('desktop_ignore')
settings=config.read_json_file(config.SETTINGS_FILE,{})
settings['filename_template']='desktop_{shot}_{type}_v{version}_{subversion}{format}'
config.atomic_write_json(config.SETTINGS_FILE,settings)
""")
    desktop = local / "MediaRenamer"
    return desktop, {str(path.relative_to(desktop)): path.read_bytes()
                     for directory in (desktop / "Config", desktop / "State")
                     for path in directory.rglob("*") if path.is_file()}


def unchanged(desktop, before):
    after = {str(path.relative_to(desktop)): path.read_bytes()
             for directory in (desktop / "Config", desktop / "State")
             for path in directory.rglob("*") if path.is_file()}
    assert after == before


def test_fresh_browser_ignores_desktop_settings_and_writes_only_its_own_store(tmp_path):
    desktop, before = desktop_store(tmp_path)
    browser = desktop / "Browser"
    browser.mkdir()
    (browser / "installation.json").write_text(json.dumps({"version": "0.27.7", "legacySharedSettings": False}))
    child(tmp_path, """
import config,browser_native
host=browser_native.BrowserHost()
assert config.get_projects()=={}
assert config.FILENAME_TEMPLATE==config.DEFAULT_FILENAME_TEMPLATE
assert 'desktop_ignore' not in config.get_machine_ignored_folders()
defaults=host.get_structure({})['defaults']
assert (defaults['targetPrefix'],defaults['imageFolder'],defaults['videoFolder'])==('genai','KEYFRAMES','VIDEO')
assert config.STATE_DIR.endswith(r'Browser\\State')
config.save_projects({'BROWSER':r'X:\\BrowserOnly'},project_modes={'BROWSER':'sequences'})
host.set_ignored_names({'names':['browser_ignore']})
assert config.get_projects()=={'BROWSER':r'X:\\BrowserOnly'}
assert config.FFMPEG_PATH.endswith(r'MediaRenamer\\Tools\\ffmpeg.exe')
host.close()
""")
    unchanged(desktop, before)
    child(tmp_path, """
import config,browser_native
h=browser_native.BrowserHost()
assert config.get_projects()=={'BROWSER':r'X:\\BrowserOnly'}
assert config.get_machine_ignored_folders()==['browser_ignore']
h.close()
""")
    unchanged(desktop, before)


def test_old_browser_shortcuts_are_copied_once_without_following_desktop_changes(tmp_path):
    desktop, before = desktop_store(tmp_path)
    browser = desktop / "Browser"
    browser.mkdir()
    (browser / "installation.json").write_text(json.dumps({"version":"0.27.7","legacySharedSettings":True}))
    child(tmp_path, """
import config,browser_native
h=browser_native.BrowserHost()
assert config.get_projects()=={'DESKTOP':r'X:\\DesktopOnly'}
assert config.get_machine_ignored_folders()==['desktop_ignore']
assert config.FILENAME_TEMPLATE==config.DEFAULT_FILENAME_TEMPLATE
config.save_projects({'LOCAL':r'X:\\Own'})
h.close()
""")
    unchanged(desktop, before)
    child(tmp_path, "import config;config.save_projects({'NEW_DESKTOP':r'X:\\NewDesktop'})")
    child(tmp_path, """
import config,browser_native
h=browser_native.BrowserHost()
assert config.get_projects()=={'LOCAL':r'X:\\Own'}
h.close()
""")
    assert json.loads((browser / "settings-isolation.json").read_text())["legacyProjectsCopied"] is True


def test_existing_browser_settings_and_projects_are_not_overwritten(tmp_path):
    desktop, before = desktop_store(tmp_path)
    child(tmp_path, """
import config,browser_settings
from pathlib import Path
root=Path(config.get_local_base_dir())/'Browser'
config.atomic_write_json(str(root/'installation.json'),{'version':'0.27.5'})
config.atomic_write_json(str(root/'State/Projects'/(config.get_machine_id()+'.json')),
 {'projects':{'OWN':r'X:\\Own'},'project_modes':{},'ignored_folders':[]})
settings=config.default_settings();settings['filename_template']='own_{shot}_{type}_v{version}_{subversion}{format}'
config.atomic_write_json(str(root/'Config/settings.json'),settings)
browser_settings.initialize()
assert config.get_projects()=={'OWN':r'X:\\Own'}
assert config.FILENAME_TEMPLATE.startswith('own_')
""")
    unchanged(desktop, before)


def test_corrupt_legacy_shared_projects_do_not_block_browser_project_setup(tmp_path):
    desktop, _ = desktop_store(tmp_path)
    source = next((desktop / 'State/Projects').glob('*.json'))
    source.write_bytes(b'corrupt legacy state')
    browser = desktop / 'Browser'
    browser.mkdir()
    (browser/'installation.json').write_text(json.dumps({'version':'0.27.5'}))
    child(tmp_path, """
import config,browser_native
h=browser_native.BrowserHost()
assert config.get_projects()=={}
config.save_projects({'OWN':r'X:\\Own'})
assert config.get_projects()=={'OWN':r'X:\\Own'}
h.close()
""")
    assert source.read_bytes() == b'corrupt legacy state'
    assert json.loads((browser/'settings-isolation.json').read_text())['migrationError']


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell installer')
def test_optional_ffmpeg_launch_cancellation_does_not_abort_setup(tmp_path):
    script = tmp_path / "test-installer.ps1"
    script.write_text(r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$tree=[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Installer parse error'}
$fn=$tree.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-OptionalFFmpegSetup'},$true)
Invoke-Expression $fn.Extent.Text
function Start-Process { throw [System.ComponentModel.Win32Exception]::new(1223) }
Start-OptionalFFmpegSetup -Executable 'C:\Example\host.exe' -InstallDirectory $args[1]
Write-Output 'SETUP-CONTINUES'
''', encoding='utf-8')
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(script),
                             str(ROOT/'Install-BrowserNative.ps1'),str(tmp_path)],
                            capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SETUP-CONTINUES' in result.stdout
    log = (tmp_path/'setup-ffmpeg-launch.log').read_text()
    assert 'host.exe' in log and 'Win32Exception' in log


def test_browser_naming_settings_drive_real_main_and_targeted_subversions_after_restart(tmp_path):
    desktop, before = desktop_store(tmp_path)
    child(tmp_path, r"""
import config,browser_native
from pathlib import Path
h=browser_native.BrowserHost();base=Path(config.get_local_base_dir())
project=base/'DEMO';(project/'vfx/shots/SH010/genai').mkdir(parents=True)
config.save_projects({'DEMO':str(project)},project_modes={'DEMO':'shots'})
source=base/'source.png';source.write_bytes(b'original-image')
target={'project':'DEMO','shot':'SH010'}
settings={'filename_template':'take-{shot}-{type}-{version}-r{subversion}{format}',
'image_type_suffix':'STILL','video_type_suffix':'CLIP','subversion_enabled':'Yes'}
result=h.set_preferences({'settings':settings,'names':['ignore-me']})
assert result['settings']['filename_template']==settings['filename_template']
assert config.get_projects()=={'DEMO':str(project)}
assert config.get_machine_ignored_folders()==['ignore-me']
preview=h.preview_naming({'settings':settings,'sequence':'','shot':'SH010'})
first=h.import_file({**target,'source':str(source)})
assert first['name']=='take-SH010-STILL-001-r00.png'
assert first['name']==preview['imageMain']
second=h.import_file({**target,'source':str(source)})
latest=h.import_file({**target,'source':str(source),'subversion':True})
assert latest['name']=='take-SH010-STILL-002-r01.png'
older=h.import_file({**target,'source':str(source),'targetExisting':first['name'],'targetCategory':'keyframes'})
assert older['name']=='take-SH010-STILL-001-r01.png'
assert source.read_bytes()==b'original-image'
records=h.files(target)['files'];assert {tuple((f['version'],f['subversion'])) for f in records}=={(1,0),(2,0),(2,1),(1,1)}
h.close()
""")
    child(tmp_path, r"""
import config,browser_native
from pathlib import Path
h=browser_native.BrowserHost();base=Path(config.get_local_base_dir())
assert h.preferences()['settings']['image_type_suffix']=='STILL'
target={'project':'DEMO','shot':'SH010'}
source=base/'source.png'
new=h.import_file({**target,'source':str(source),'subversion':True})
assert new['name']=='take-SH010-STILL-002-r02.png'
h.set_preferences({'settings':{'subversion_enabled':'No','filename_template':'{shot}_{type}_{version}{format}'}})
new=h.import_file({**target,'source':str(source),'subversion':True,'targetExisting':'does-not-exist.png'})
assert new['name']=='SH010_STILL_001.png'  # New template/context, prior names are not silently renamed.
next_file=h.import_file({**target,'source':str(source),'subversion':True})
assert next_file['name']=='SH010_STILL_002.png'
assert new['name'] in {f['name'] for f in h.files(target)['files']}
h.close()
""")
    unchanged(desktop, before)


def test_browser_preferences_validate_before_mutating_and_preserve_custom_folders(tmp_path):
    child(tmp_path, r"""
import config,browser_native
from pathlib import Path
h=browser_native.BrowserHost()
h.set_default_structure({'scenePrefix':'vfx/shots','shotPrefix':'','targetPrefix':'custom-ai','imageFolder':'STILLS','videoFolder':'CLIPS','defaultLayout':'auto'})
settings_path=Path(config.SETTINGS_FILE);before=settings_path.read_bytes()
for settings,names in [({'filename_template':'../{version}'},[]),({'filename_template':'{shot}_{version}'},[]),({'image_extensions':['.png'],'video_extensions':['.png']},[]),({'image_type_suffix':'GOOD'},['../ignored'])]:
    try:h.set_preferences({'settings':settings,'names':names})
    except (browser_native.HostError,ValueError):pass
    else:raise AssertionError('Invalid settings accepted')
    assert settings_path.read_bytes()==before
try:h.set_preferences({'settings':{'additional_categories':[{'id':'custom','folder':'STILLS','media_type':'image','type_suffix':'X'}]}})
except browser_native.HostError:pass
else:raise AssertionError('Folder collision accepted')
result=h.set_preferences({'settings':{'image_type_suffix':'CUSTOM'}})
assert h.get_structure({})['defaults']['imageFolder']=='STILLS'
assert h.get_structure({})['defaults']['targetPrefix']=='custom-ai'
assert result['settings']['image_type_suffix']=='CUSTOM'
h.close()
""")


def test_browser_additional_categories_extensions_and_transfer_subversion_options(tmp_path):
    child(tmp_path, r"""
import base64,config,browser_native
from pathlib import Path
h=browser_native.BrowserHost();base=Path(config.get_local_base_dir())
project=base/'DEMO';(project/'vfx/shots/SH010').mkdir(parents=True)
config.save_projects({'DEMO':str(project)},project_modes={'DEMO':'shots'})
settings=h.preferences()['settings'];settings['image_extensions'].append('.heic')
settings['additional_categories']=[{'id':'reference','folder':'REFERENCES','media_type':'image','type_suffix':'REF'}]
h.set_preferences({'settings':settings})
target={'project':'DEMO','shot':'SH010','category':'reference'}
source=base/'source.heic';source.write_bytes(b'heic-fixture')
first=h.import_file({**target,'source':str(source)})
assert Path(first['path']).parent==project/'vfx/shots/SH010/genai/REFERENCES'
assert 'REF' in first['name']
transfer=h.transfer_begin({**target,'name':'new.heic','subversion':True,'targetExisting':first['name'],'targetCategory':'reference'})
h.transfer_chunk({'transferId':transfer['transferId'],'data':base64.b64encode(b'next-image').decode()})
result=h.transfer_finish({'transferId':transfer['transferId']})
assert result['name'].endswith('_v001_01.heic')
assert Path(result['path']).read_bytes()==b'next-image'
assert len(h.files(target)['files'])==2
assert '.heic' in h.projects()['preferences']['imageExtensions']
h.close()
""")
