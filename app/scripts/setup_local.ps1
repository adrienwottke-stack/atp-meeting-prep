param([switch]$SkipStart, [switch]$SwitchFromCloud)
$ErrorActionPreference = 'Stop'
$applicationRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $applicationRoot
try {
    $pythonPath = Join-Path $applicationRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Zuerst die virtuelle Umgebung gemäß README einrichten.'
    }
    $activeEnvironment = Join-Path $applicationRoot '.env'
    if ((Test-Path -LiteralPath $activeEnvironment) -and
        (Select-String -LiteralPath $activeEnvironment -Pattern '^SUPABASE_URL\s*=.*https://' -Quiet)) {
        if (-not $SwitchFromCloud) {
            throw 'Aktive Cloud-Konfiguration: lokales Setup nur bewusst mit -SwitchFromCloud ausführen. Für lokale Regressionen scripts/local_regression.py verwenden.'
        }
        New-Item -ItemType Directory -Force -Path 'output\setup' | Out-Null
        Copy-Item -LiteralPath $activeEnvironment -Destination (Join-Path $applicationRoot ('output\setup\cloud-env-before-local-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.env'))
    }
    if (-not $SkipStart) {
        & npx --yes supabase@2.121.0 start --help | Out-Null
        # Output can contain local keys. Keep it in the ignored setup folder.
        New-Item -ItemType Directory -Force -Path 'output\setup' | Out-Null
        $ErrorActionPreference = 'Continue'
        & npx --yes supabase@2.121.0 start -x realtime,imgproxy,studio,edge-runtime,logflare,vector,supavisor *> 'output\setup\supabase-start.log'
        $startExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($startExit -ne 0) { throw 'Supabase-Start fehlgeschlagen; output/setup/supabase-start.log prüfen.' }
    }
    New-Item -ItemType Directory -Force -Path 'output\setup' | Out-Null
    & npx --yes supabase@2.121.0 db push --help | Out-Null
    $ErrorActionPreference = 'Continue'
    & npx --yes supabase@2.121.0 db push --local --yes *> 'output\setup\supabase-migrations.log'
    $migrationExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($migrationExit -ne 0) { throw 'ATP-Migrationen fehlgeschlagen; output/setup/supabase-migrations.log prüfen.' }
    & npx --yes supabase@2.121.0 status --help | Out-Null
    $ErrorActionPreference = 'Continue'
    $statusText = & npx --yes supabase@2.121.0 status -o json 2>$null
    $statusExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($statusExit -ne 0) { throw 'Lokaler ATP-Supabase-Stack nicht erreichbar.' }
    $status = ($statusText -join "`n") | ConvertFrom-Json
    if ($status.API_URL -ne 'http://127.0.0.1:55431') { throw 'Abbruch: Nicht der isolierte lokale ATP-Stack.' }
    New-Item -ItemType Directory -Force -Path 'output\setup' | Out-Null
    $statusFile = Join-Path $applicationRoot 'output\setup\supabase-status.json'
    [IO.File]::WriteAllText($statusFile, ($status | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
    $env:ATP_SETUP_STATUS = $statusFile
    $pythonSource = @'
import hashlib, json, os, secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
import httpx, psycopg
from psycopg import sql
from dotenv import dotenv_values

root=Path.cwd()
status=json.loads(Path(os.environ['ATP_SETUP_STATUS']).read_text(encoding='utf-8-sig'))
if status['API_URL']!='http://127.0.0.1:55431': raise RuntimeError('Wrong development stack')
setup=root/'output'/'setup'
credentials_path=setup/'local-users.json'
previous=json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
org_id=previous.get('org_id','a7100000-0000-4000-8000-000000000001')
users=previous.get('users',[{'email':'demo-a@atp.example','password':'ATP-Lokal-Test-2026!'},
                            {'email':'demo-b@atp.example','password':'ATP-Lokal-Test-2026!'}])
web_password=previous.get('web_password') or secrets.token_urlsafe(36)
worker_password=previous.get('worker_password') or secrets.token_urlsafe(36)
service=status['SERVICE_ROLE_KEY']
headers={'apikey':service,'Authorization':'Bearer '+service}
with httpx.Client(base_url=status['API_URL']+'/auth/v1/',headers=headers,timeout=20,trust_env=False) as client:
    existing=client.get('admin/users',params={'per_page':1000})
    existing.raise_for_status()
    existing={u['email']:u['id'] for u in existing.json()['users']}
    for user in users:
        if user['email'] in existing:
            user['id']=existing[user['email']]
        else:
            response=client.post('admin/users',json={'email':user['email'],'password':user['password'],'email_confirm':True})
            response.raise_for_status()
            user['id']=response.json()['id']
with psycopg.connect(status['DB_URL']) as db:
    exists=db.execute("select to_regclass('atp.meetings')").fetchone()[0]
    if not exists: raise RuntimeError('Migration missing: run npx --yes supabase@2.121.0 db push --local first')
    for role,password in [('atp_web',web_password),('atp_worker',worker_password)]:
        db.execute(sql.SQL('alter role {} login password {}').format(sql.Identifier(role),sql.Literal(password)))
    for index,user in enumerate(users):
        db.execute('insert into atp.memberships(user_id,org_id,role) values(%s,%s,%s) on conflict(user_id,org_id) do nothing',
                   (user['id'],org_id,'admin' if index==0 else 'member'))
    profile=json.loads((root/'src'/'atp'/'profiles'/'ATP-2026-10-15.2.json').read_text(encoding='utf-8'))
    digest=hashlib.sha256(json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    # Explicit development activation for synthetic local fixtures, not ATP approval.
    current=db.execute('select content_hash from atp.profile_versions where org_id=%s and version=%s',(org_id,profile['profile_id'])).fetchone()
    if current and current[0]!=digest: raise RuntimeError('Profile version hash conflict')
    db.execute('update atp.profile_versions set active=false where org_id=%s and active',(org_id,))
    db.execute('insert into atp.profile_versions(org_id,version,content_hash,content,active,activated_by) values(%s,%s,%s,%s,true,%s) on conflict(org_id,version) do update set active=true,activated_by=excluded.activated_by',
               (org_id,profile['profile_id'],digest,psycopg.types.json.Jsonb(profile),users[0]['id']))
    db.execute('insert into atp.profile_activations(org_id,version,content_hash,activated_by,reason) values(%s,%s,%s,%s,%s)',
               (org_id,profile['profile_id'],digest,users[0]['id'],'Lokaler synthetischer Entwicklungsstand; fachliche ATP-Freigabe offen'))

environment=dict(dotenv_values(root/'.env'))
environment.update({'APP_MODE':'local','APP_BASE_URL':'http://127.0.0.1:8000','FIXTURE_MODE':'true',
                    'STORAGE_BACKEND':'local','SUPABASE_SECRET_KEY':'',
                    'SUPABASE_URL':status['API_URL'],'SUPABASE_ANON_KEY':status['ANON_KEY'],
                    'SUPABASE_SERVICE_ROLE_KEY':service,'SUPABASE_JWT_SECRET':status['JWT_SECRET'],
                    'SUPABASE_JWT_ALGORITHM':'ES256','SUPABASE_JWT_ISSUER':status['API_URL']+'/auth/v1',
                    'AUTH_VERIFY_SESSION':'true',
                    'DATABASE_URL':'postgresql://atp_web:'+web_password+'@127.0.0.1:55432/postgres',
                    'WORKER_DATABASE_URL':'postgresql://atp_worker:'+worker_password+'@127.0.0.1:55432/postgres'})
# Do not import any host credentials. A missing OpenAI key remains missing.
lines=[key+'='+json.dumps(str(value),ensure_ascii=False) for key,value in environment.items() if value is not None]
(root/'.env').write_text('\n'.join(lines)+'\n',encoding='utf-8')
credentials={'org_id':org_id,'users':users,'web_password':web_password,'worker_password':worker_password}
credentials_path.write_text(json.dumps(credentials,indent=2),encoding='utf-8')
print('Lokale Supabase Auth, zwei synthetische Nutzer, getrennte DB-Rollen und app/.env eingerichtet.')
print('Anmeldedaten: app/output/setup/local-users.json (nur lokal, von Git ausgeschlossen).')
'@
    $pythonSource | & $pythonPath -
    if ($LASTEXITCODE -ne 0) { throw 'Lokale Einrichtung fehlgeschlagen.' }
} finally {
    Remove-Item Env:\ATP_SETUP_STATUS -ErrorAction SilentlyContinue
    Pop-Location
}
