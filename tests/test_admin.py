import json
import sqlite3

import pytest
from dotenv import dotenv_values

from kurumibot.admin_config import ensure_login, read_env, save_env
from kurumibot.library import Library
from kurumibot.pricing import estimate
from conftest import authenticate


async def test_private_endpoints_and_files_require_login(admin_client, admin_fixture):
    for path in ('/api/overview','/api/settings','/api/materials','/api/conversations','/api/usage',
                 '/api/materials/'+admin_fixture.ids[0]+'/image','/api/conversations/1/image'):
        response = await admin_client.get(path)
        assert response.status == 401
    for path in ('/.env','/static/../.env','/static/not-allowed'):
        response = await admin_client.get(path)
        assert response.status == 404
    assert (await (await admin_client.get('/api/session')).json()) == {'authenticated':False}


async def test_login_cookie_logout_and_csrf(admin_client):
    wrong = await admin_client.post('/api/login', json={'username':'admin','password':'wrong'})
    assert wrong.status == 401
    headers = await authenticate(admin_client)
    cookie = next(cookie for cookie in admin_client.session.cookie_jar if cookie.key=='kurumi_session')
    assert cookie['httponly'] and cookie['samesite']=='Strict'
    assert (await admin_client.get('/api/overview')).status == 200
    assert (await admin_client.post('/api/settings',json={'CODEX_OAUTH_PROXY_MODEL':'gpt-6-luna-low'})).status == 403
    assert (await admin_client.post('/api/settings',headers={**headers,'Origin':'https://other.example'},json={})).status == 403
    assert (await admin_client.post('/api/logout',headers=headers,json={})).status == 200
    assert (await admin_client.get('/api/overview')).status == 401


async def test_login_throttled(admin_client):
    for _ in range(10):
        assert (await admin_client.post('/api/login',json={'username':'admin','password':'wrong'})).status == 401
    assert (await admin_client.post('/api/login',json={'username':'admin','password':'wrong'})).status == 429


async def test_expired_session(admin_fixture, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    import kurumibot.admin as admin
    monkeypatch.setattr(admin,'SESSION_SECONDS',-1)
    async with TestClient(TestServer(admin.create_app(admin_fixture.config,admin_fixture.env))) as client:
        await authenticate(client)
        assert (await client.get('/api/overview')).status == 401


async def test_masked_settings_and_blank_preserve(admin_client, admin_fixture):
    headers = await authenticate(admin_client)
    result = await (await admin_client.get('/api/settings')).json()
    text = json.dumps(result)
    assert all(secret not in text for secret in ('test-api-key','test-qq-secret','test-admin-password','test-app'))
    response = await admin_client.post('/api/settings',headers=headers,json={
        'CODEX_OAUTH_PROXY_MODEL':'gpt-6-sol-low','CODEX_OAUTH_PROXY_API_KEY':'','QQ_APP_ID':'','QQ_APP_SECRET':'',
    })
    assert (await response.json())['restart_required'] is True
    values = read_env(admin_fixture.env)
    assert values['CODEX_OAUTH_PROXY_API_KEY']=='test-api-key'
    assert values['QQ_APP_SECRET']=='test-qq-secret'
    assert values['CODEX_OAUTH_PROXY_MODEL']=='gpt-6-sol-low'
    assert values['UNRELATED']=='preserve me'
    assert '# Keep this comment' in admin_fixture.env.read_text()
    assert admin_fixture.env.stat().st_mode & 0o777 == 0o600
    assert admin_fixture.config.model=='gpt-6-luna-low'


@pytest.mark.parametrize('payload',[
    {'CODEX_OAUTH_PROXY_MODEL':''}, {'CODEX_OAUTH_PROXY_MODEL':'invalid model'}, {'CODEX_OAUTH_PROXY_API_KEY':'key\nOTHER=secret'},
    {'KURUMI_ADMIN_PASSWORD':'replacement'}, {'QQ_APP_ID':123},
])
async def test_invalid_settings_never_written(admin_client,admin_fixture,payload):
    headers=await authenticate(admin_client)
    before=admin_fixture.env.read_bytes()
    assert (await admin_client.post('/api/settings',headers=headers,json=payload)).status==400
    assert admin_fixture.env.read_bytes()==before


async def test_material_changes_immediately_visible_to_bot(admin_client,admin_fixture):
    headers=await authenticate(admin_client)
    library=Library(admin_fixture.config.library)
    try:
        assert len(library.candidates())==3
        material_id=admin_fixture.ids[0]
        response=await admin_client.patch('/api/materials/'+material_id,headers=headers,json={'enabled':False})
        assert response.status==200
        assert len(library.candidates())==2
        assert library.selected_image(material_id,{material_id}) is None
        response=await admin_client.patch('/api/materials/'+material_id,headers=headers,json={
            'enabled':True,'quote_traditional':'人工繁體台詞','quote_simplified':'人工简体台词',
            'emotion_tags':['焦虑','自嘲'],'meaning':'新的含义','scenarios':'新的日常场景',
        })
        assert response.status==200
        updated=next(item for item in library.candidates() if item['id']==material_id)
        assert updated['quote_simplified']=='人工简体台词'
        assert updated['emotion_tags']==['焦虑','自嘲']
        assert library.selected_image(material_id,{material_id}).is_file()
    finally:
        library.close()


@pytest.mark.parametrize('payload',[{'image_path':'/etc/passwd'},{'enabled':1},{'emotion_tags':[]},{'quote_simplified':''}])
async def test_invalid_material_edit(admin_client,admin_fixture,payload):
    headers=await authenticate(admin_client)
    assert (await admin_client.patch('/api/materials/'+admin_fixture.ids[0],headers=headers,json=payload)).status==400


async def test_material_search_pagination_and_preview(admin_client,admin_fixture):
    await authenticate(admin_client)
    response=await admin_client.get('/api/materials?q=辞职&size=1')
    data=await response.json()
    assert data['total']==1 and len(data['items'])==1
    assert data['items'][0]['id']==admin_fixture.ids[-1]
    response=await admin_client.get('/api/materials/'+admin_fixture.ids[-1]+'/image')
    assert response.status==200 and response.content_type=='image/png'
    assert (await admin_client.get('/api/materials?page=0')).status==400
    assert (await admin_client.get('/api/materials?size=10000')).status==400


async def test_history_usage_and_startup_preserves_pending(admin_client,admin_fixture):
    await authenticate(admin_client)
    data=await (await admin_client.get('/api/conversations?q=加班')).json()
    assert data['total']==1
    item=data['items'][0]
    assert item['scene']=='加班烦躁' and item['has_image']
    assert 'image_path' not in item
    assert (await admin_client.get('/api/conversations/1/image')).status==200
    usage=await (await admin_client.get('/api/usage')).json()
    assert usage['items'][0]['estimated_cost']==0.001376
    overview=await (await admin_client.get('/api/overview')).json()
    assert overview['materials']['enabled']==3
    assert overview['conversations']['sent']==1
    assert overview['ai']['tokens']==1020
    with sqlite3.connect(admin_fixture.config.history) as db:
        assert db.execute('SELECT status FROM conversations WHERE id=?',(admin_fixture.pending,)).fetchone()[0]=='processing'


async def test_history_image_path_cannot_expose_env(admin_client,admin_fixture):
    await authenticate(admin_client)
    with sqlite3.connect(admin_fixture.config.history) as db:
        db.execute('UPDATE conversations SET image_path=? WHERE id=1',(str(admin_fixture.env),))
    assert (await admin_client.get('/api/conversations/1/image')).status==404


def test_dotenv_quotes_and_generated_credentials(tmp_path):
    env=tmp_path/'.env'
    env.write_text("KEEP='original'\n")
    username,password=ensure_login(env)
    assert username=='admin' and len(password)>=20
    assert ensure_login(env)==(username,password)
    save_env({'DEEPSEEK_API_KEY':"literal'$with\\slash"},env)
    assert dotenv_values(env,interpolate=False)['DEEPSEEK_API_KEY']=="literal'$with\\slash"
    assert read_env(env)['KEEP']=='original'


def test_cost_estimate_cache_and_unknown():
    assert estimate('deepseek-flash',{'prompt_tokens':1000,'completion_tokens':20,'prompt_cache_hit_tokens':400})==0.001376
    assert estimate('deepseek-flash',{}) is None
    assert estimate('unknown-model',{'prompt_tokens':1000,'completion_tokens':20}) is None
