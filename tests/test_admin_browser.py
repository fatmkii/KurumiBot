import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, expect

from kurumibot.admin_config import read_env
from kurumibot.library import Library
from kurumibot.history import History, masked
from conftest import authenticate

pytestmark = pytest.mark.browser


@pytest_asyncio.fixture
async def browser_page(admin_client):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page = await browser.new_page(viewport={'width':1440,'height':1000}, locale='zh-CN')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(str(admin_client.make_url('/')))
        await page.get_by_label('用户名',exact=True).fill('admin')
        await page.get_by_label('密码',exact=True).fill('test-admin-password')
        await page.get_by_role('button',name='进入工作室').click()
        await expect(page.get_by_role('heading',name='运行概览',exact=True)).to_be_visible()
        await expect(page.get_by_role('heading',name='最近的一次接话')).to_be_visible()
        yield page
        await browser.close()
        assert not errors, errors


async def test_history_preview_usage_and_logout(browser_page):
    page=browser_page
    await page.get_by_role('navigation').get_by_role('link',name='对话记录').click()
    await expect(page.get_by_role('heading',name='对话记录',exact=True)).to_be_visible()
    row=page.get_by_role('row').filter(has_text='老板又让我加班，烦死了')
    await row.get_by_role('button',name='详情').click()
    dialog=page.get_by_role('dialog')
    await expect(dialog.get_by_role('heading',name='对话 #1')).to_be_visible()
    await expect(dialog.get_by_text('想辞职的共鸣',exact=True)).to_be_visible()
    await expect(dialog.get_by_role('img')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(dialog).to_be_hidden()
    await page.get_by_role('navigation').get_by_role('link',name='AI 用量').click()
    await expect(page.get_by_role('cell',name='¥ 0.0014',exact=True)).to_be_visible()
    await page.get_by_role('link',name='跳到主要内容').focus()
    await page.keyboard.press('Enter')
    await expect(page.get_by_role('main')).to_be_focused()
    assert page.url.endswith('#usage')
    await page.get_by_role('button',name='退出登录',exact=True).click()
    await expect(page.get_by_label('密码',exact=True)).to_be_visible()
    await expect(page.get_by_role('main')).to_be_hidden()


async def test_group_conversation_label_and_preview(browser_page,admin_fixture):
    history=History(admin_fixture.config.history)
    try:
        inbound=SimpleNamespace(chat_scope='group',chat_id='test-group',user_id='test-member',message_id='group-message')
        conversation_id=history.claim(inbound,'群里亏麻了')
        image=admin_fixture.config.library/'images'/f'{admin_fixture.ids[0]}.png'
        history.finish(conversation_id,status='sent',image_path=str(image),material_id=admin_fixture.ids[0],attempts=1)
    finally:
        history.close()
    page=browser_page
    await page.get_by_role('navigation').get_by_role('link',name='对话记录').click()
    row=page.get_by_role('row').filter(has_text='群里亏麻了')
    await expect(row).to_contain_text('群 @ · '+masked('test-group')[-6:])
    await row.get_by_role('button',name='详情').click()
    dialog=page.get_by_role('dialog')
    await expect(dialog.get_by_text('群 @ · '+masked('test-group')[-6:],exact=True)).to_be_visible()
    await expect(dialog.get_by_role('img')).to_be_visible()


async def test_edit_and_toggle_material(browser_page,admin_fixture):
    page=browser_page
    await page.get_by_role('navigation').get_by_role('link',name='台词素材库').click()
    await page.get_by_label('搜索台词或场景').fill('辞职')
    await page.get_by_role('button',name='查找素材').click()
    await expect(page.get_by_role('article')).to_have_count(1)
    await page.get_by_role('button',name='修正',exact=True).click()
    dialog=page.get_by_role('dialog')
    await dialog.get_by_label('繁体原台词').fill('加班想辭職')
    await dialog.get_by_label('简体检索台词').fill('加班想辞职')
    await dialog.get_by_label('情绪标签').fill('自嘲，委屈')
    await dialog.get_by_label('适用场景').fill('工作加班')
    await dialog.get_by_role('checkbox').uncheck()
    await dialog.get_by_role('button',name='保存修正').click()
    await expect(dialog).to_be_hidden()
    await expect(page.get_by_text('加班想辞职',exact=True)).to_be_visible()
    switch=page.get_by_role('switch')
    await expect(switch).to_have_attribute('aria-checked','false')
    library=Library(admin_fixture.config.library)
    try:
        assert admin_fixture.ids[-1] not in {item['id'] for item in library.candidates()}
        await switch.click()
        await expect(switch).to_have_attribute('aria-checked','true')
        candidate=next(item for item in library.candidates() if item['id']==admin_fixture.ids[-1])
        assert candidate['quote_simplified']=='加班想辞职'
        assert candidate['emotion_tags']==['自嘲','委屈']
    finally:
        library.close()


async def test_settings_blank_preserve_and_restart_notice(browser_page,admin_fixture):
    page=browser_page
    await page.get_by_role('navigation').get_by_role('link',name='连接与配置').click()
    await page.get_by_label('AI 模型',exact=True).fill('gpt-6-sol-low')
    await expect(page.get_by_label('Codex OAuth Proxy API 密钥')).to_be_empty()
    await page.get_by_role('button',name='保存配置').click()
    await expect(page.get_by_text('请重启 Bot 服务使修改生效。',exact=True)).to_be_visible()
    values=read_env(admin_fixture.env)
    assert values['CODEX_OAUTH_PROXY_API_KEY']=='test-api-key'
    assert values['CODEX_OAUTH_PROXY_MODEL']=='gpt-6-sol-low'
    assert admin_fixture.config.model=='gpt-6-luna-low'
    await page.get_by_label('Codex OAuth Proxy API 密钥').fill('replacement-test-api-key')
    await page.get_by_role('button',name='保存配置').click()
    await expect(page.get_by_label('Codex OAuth Proxy API 密钥')).to_be_empty()
    assert read_env(admin_fixture.env)['CODEX_OAUTH_PROXY_API_KEY']=='replacement-test-api-key'
    assert 'replacement-test-api-key' not in await page.get_by_role('main').inner_text()


async def test_material_text_is_not_executed(browser_page,admin_client,admin_fixture):
    headers=await authenticate(admin_client)
    text='<img src=x onerror="window.__xss=true">'
    response=await admin_client.patch('/api/materials/'+admin_fixture.ids[0],headers=headers,json={'quote_simplified':text})
    assert response.status==200
    page=browser_page
    await page.get_by_role('navigation').get_by_role('link',name='台词素材库').click()
    await expect(page.get_by_text(text,exact=True)).to_be_visible()
    assert await page.evaluate('Boolean(window.__xss)') is False


@pytest.mark.parametrize('width,height',[(375,812),(768,1024),(1440,1000),(812,375)])
async def test_responsive_pages_without_body_overflow(browser_page,tmp_path,width,height):
    page=browser_page
    await page.set_viewport_size({'width':width,'height':height})
    await page.emulate_media(reduced_motion='reduce')
    for link,title in [('运行概览','运行概览'),('台词素材库','台词素材库'),('连接与配置','连接与配置')]:
        await page.get_by_role('navigation').get_by_role('link',name=link).click()
        await expect(page.get_by_role('heading',name=title,exact=True)).to_be_visible()
        await expect(page.get_by_role('button',name='↻ 刷新数据')).to_be_enabled()
        overflow=await page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        assert not overflow, (link,width)
        if title=='台词素材库':
            await expect(page.get_by_role('article')).to_have_count(3)
            await page.get_by_role('img').evaluate_all('(images)=>Promise.all(images.map(image=>image.decode()))')
        await page.screenshot(path=str(tmp_path/f'{title}-{width}.png'),full_page=True)
    if width==375:
        await page.get_by_role('button',name='退出',exact=True).click()
        await expect(page.get_by_label('密码',exact=True)).to_be_visible()
