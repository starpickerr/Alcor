"""Synthetic registries feed real offline installers and isolated package entries."""
import base64
from hashlib import sha256, sha512
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

from agent.tools.mcp.mcp_client import McpClient
from core.mcp.packages import package_spec, prepare_package
from core.runtime.environments import EnvironmentStore
from core.security.mcp_runtime import environment_identity, launch_identity
from core.security.runtime_trust import RuntimeTrustStore
from core.plugins.caller import caller_origin_scope


@pytest.mark.parametrize('command,args', [('npx',['tool@latest']),('uvx',['tool>=1']),('npx',['--package','tool@1.0.0']),('uvx',['--from','tool==1.0.0']),('npx',['x@1.0.0;touch /tmp/bad'])])
def test_only_exact_structured_package_launchers_are_accepted(command,args):
    with pytest.raises(ValueError):
        package_spec(dict(command=command,args=args))


@pytest.mark.parametrize('launcher',['npx','uvx'])
def test_prepare_exact_package_then_start_offline_without_host_credentials(tmp_path,monkeypatch,launcher):
    from tests.fixtures.runtime_artifacts import seed_runtime_artifacts
    config = dict(name='package-'+launcher,type='stdio',command=launcher,args=['fixture-mcp@1.0.0' if launcher=='npx' else 'fixture-mcp==1.0.0'])
    script = Path('tests/fixtures/mcp_echo_server.py').read_text()
    if launcher == 'uvx':
        seed_runtime_artifacts()
        wheel = tmp_path / 'fixture_mcp-1.0.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel,'w') as archive:
            content = {'fixture_mcp.py':script,
                'fixture_mcp-1.0.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: fixture-mcp\nVersion: 1.0.0\n',
                'fixture_mcp-1.0.0.dist-info/WHEEL':'Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                'fixture_mcp-1.0.0.dist-info/entry_points.txt':'[console_scripts]\nfixture-mcp = fixture_mcp:main\n'}
            for name,value in content.items():
                archive.writestr(name,value)
            archive.writestr('fixture_mcp-1.0.0.dist-info/RECORD','\n'.join(name+',,' for name in content)+'\nfixture_mcp-1.0.0.dist-info/RECORD,,\n')
        monkeypatch.setattr('core.runtime.artifacts.python_wheels',lambda lock:[wheel])
    else:
        script = '''const readline=require('readline');
readline.createInterface({input:process.stdin}).on('line',line=>{let m=JSON.parse(line);if(m.id===undefined)return;
let r=m.method==='initialize'?{protocolVersion:'2024-11-05',capabilities:{tools:{}},serverInfo:{name:'fixture',version:'1'}}:
m.method==='tools/list'?{tools:[{name:'echo',inputSchema:{type:'object'}}]}:{content:[{type:'text',text:'echo:'+m.params.arguments.text}]};
console.log(JSON.stringify({jsonrpc:'2.0',id:m.id,result:r}));});'''
        archive = tmp_path / 'fixture-mcp.tgz'
        with tarfile.open(archive,'w:gz') as tar:
            for name,value in {'package/package.json':json.dumps({'name':'fixture-mcp','version':'1.0.0','bin':{'fixture-mcp':'main.js'},'scripts':{'postinstall':'node -e "process.exit(42)"'}}),'package/main.js':script}.items():
                item=tarfile.TarInfo(name)
                raw=value.encode()
                item.size=len(raw)
                tar.addfile(item,io.BytesIO(raw))
        integrity='sha512-'+base64.b64encode(sha512(archive.read_bytes()).digest()).decode()
        monkeypatch.setattr('core.runtime.artifacts.npm_archives',lambda lock:[archive])
    execute=EnvironmentStore._execute
    network=[]
    def resolver(store,argv,**kwargs):
        if kwargs.get('network'):
            network.append(argv)
            package=kwargs['package']
            if launcher=='npx':
                root=json.loads((package/'package.json').read_text())
                (package/'package-lock.json').write_text(json.dumps({'name':root['name'],'version':'1.0.0','lockfileVersion':3,'packages':{'':root,'node_modules/fixture-mcp':{'version':'1.0.0','resolved':'https://registry.npmjs.org/fixture-mcp/-/fixture-mcp-1.0.0.tgz','integrity':integrity,'bin':{'fixture-mcp':'main.js'},'hasInstallScript':True}}}))
            else:
                (package/'requirements.lock').write_text('fixture-mcp==1.0.0 --hash=sha256:'+sha256(wheel.read_bytes()).hexdigest()+'\n')
            return subprocess.CompletedProcess(argv,0,'','')
        return execute(store,argv,**kwargs)
    monkeypatch.setattr(EnvironmentStore,'_execute',resolver)
    monkeypatch.setenv('CORE_ONLY_SECRET','HOST_SECRET_NOT_FOR_PACKAGES')
    with caller_origin_scope('desktop'):
        before=launch_identity(config)[0]
        client=McpClient(config)
        assert client.initialize() is False  # Runtime may not implicitly install.
        prepared=prepare_package(config)
        assert len(network)==1
        assert launch_identity(config)[0]!=before
        assert not RuntimeTrustStore().capabilities(user_id='local-default',kind='mcp',identifier=config['name'],digest=launch_identity(config)[0])['network']
        monkeypatch.setattr('core.runtime.artifacts.registry_json',lambda *a:pytest.fail('Runtime requested registry metadata'))
        fresh=McpClient(config)
        try:
            assert fresh.initialize()
            assert fresh.call_tool('echo',{'text':'prepared'})=='echo:prepared'
            assert fresh.environment['id']==prepared['id'] and fresh.sandbox_state['mode']=='isolated'
            assert len(network)==1
        finally:
            fresh.shutdown()
        previous=EnvironmentStore().current('mcp',config['name'],'local-default',launch_digest=environment_identity(config))
        def fail(*args,**kwargs):
            raise RuntimeError('synthetic resolver failure')
        monkeypatch.setattr(EnvironmentStore,'_execute',fail)
        with pytest.raises(RuntimeError,match='synthetic resolver failure'):
            prepare_package(config)
        assert EnvironmentStore().current('mcp',config['name'],'local-default',launch_digest=environment_identity(config))==previous
