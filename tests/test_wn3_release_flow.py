from __future__ import annotations
import asyncio
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from telegram.ext import ApplicationHandlerStop
from wn3_fixtures import POINT
from test_messenger_weathernext3_router import Gateway
from messenger.contracts import CommonProductResult, NormalizedEvent, Location, ProductAttachment
from messenger.callback_codec import encode_callback
from messenger.router import RouterDependencies
from messenger.weathernext3_router import WeatherNext3MessengerRouter, _lead_keyboard
from messenger.weathernext3_service import DEFAULT_WN3_PARAMS
from messenger.user_recipes import UserRecipeStore
from messenger.product_executor import ProductSnapshot, build_snapshot_result

class ReleaseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.calls=[]
        def builder(point,kind,**params):
            self.calls.append((point,kind,params)); return CommonProductResult('weathernext3','WN3 TEST',[],{})
        self.builder=builder; self.routers=[]
        self.router=self.new_router()
    def new_router(self,**kwargs):
        r=WeatherNext3MessengerRouter(RouterDependencies(geocode=lambda q,n:[POINT]),
            recipes=UserRecipeStore(self.root/'state.db'),wn3_builder=kwargs.pop('builder',self.builder),**kwargs)
        self.routers.append(r);return r
    async def asyncTearDown(self):
        for r in self.routers:await r.shutdown_wn3()
        self.tmp.cleanup()
    def event(self,platform='max',text='/wn3',payload=None,user='42'):
        return NormalizedEvent(platform,None,'CALLBACK' if payload else 'COMMAND' if text.startswith('/') else 'TEXT',user,'1',
            text=text,command=text[1:].split()[0] if text.startswith('/') and not payload else None,
            callback_payload=payload,callback_id='id' if payload else None)
    def last_keyboard(self,gateway):
        return next(call[2] for call in reversed(gateway.calls) if call[2] is not None and hasattr(call[2],'rows'))
    async def test_three_platform_city_then_plus24_and_recipe_step(self):
        for platform in ('telegram','max','vk'):
            with self.subTest(platform=platform):
                gateway=Gateway(platform)
                await self.router.handle(self.event(platform),gateway)
                await self.router.handle(self.event(platform,'Москва +24'),gateway)
                await self.router.wn3_wait_idle()
                self.assertEqual(self.calls[-1][2]['hours'],24)
                recipe=self.router.recipes.latest_for_product(platform,'42','weathernext3')
                self.assertEqual(recipe.params['step'],3);self.assertNotIn('run',recipe.params)
    async def test_mean_and_ensemble_commands_work_on_all_platforms(self):
        for platform in ('telegram', 'max', 'vk'):
            for kind in ('meteogram', 'ensemble'):
                with self.subTest(platform=platform, kind=kind):
                    gateway=Gateway(platform)
                    await self.router.handle(self.event(platform, f'/wn3 Москва kind={kind} days=3'), gateway)
                    await self.router.wn3_wait_idle()
                    self.assertEqual(self.calls[-1][1], kind)
                    self.assertEqual(self.calls[-1][2]['days'], 3)

    async def test_native_geo_and_durable_callback_survive_restart(self):
        gateway=Gateway('max')
        await self.router.handle(self.event(),gateway)
        await self.router.handle(replace(self.event(text='geo'),event_type='LOCATION',location=Location(55.75,37.62)),gateway)
        payload=self.last_keyboard(gateway).rows[0][0].payload
        self.assertTrue(payload.startswith('w3|'));self.assertLessEqual(len(payload.encode()),64)
        fresh=self.new_router();await fresh.handle(self.event(payload=payload),gateway);await fresh.wn3_wait_idle()
        self.assertEqual(len(self.calls),1);self.assertTrue(any(c[0]=='answer' for c in gateway.calls))
        await fresh.handle(self.event(payload=payload,user='43'),gateway)
        self.assertEqual(len(self.calls),1);self.assertTrue(any('другому' in str(c[1]) for c in gateway.calls))
    async def test_ambiguous_place_callback_is_durable(self):
        gateway=Gateway('vk');self.router.deps.geocode=lambda q,n:[POINT,replace(POINT,label='Вторая')]
        await self.router.handle(self.event('vk','/wn3 Город +24'),gateway)
        payload=self.last_keyboard(gateway).rows[0][0].payload
        fresh=self.new_router();await fresh.handle(self.event('vk',payload=payload),gateway);await fresh.wn3_wait_idle()
        self.assertEqual(self.calls[-1][2]['hours'],24)
    async def test_cancel_keeps_capacity_until_worker_finishes_and_no_media(self):
        started=threading.Event(); release=threading.Event()
        path=self.root/'result.png';path.write_bytes(b'png')
        def builder(*args,**kwargs):
            started.set();release.wait(3)
            return CommonProductResult('weathernext3','LATE RESULT',[ProductAttachment('image',path,path.name)],{})
        router=self.new_router(builder=builder);gateway=Gateway('max');event=self.event(text='/wn3 Москва +24')
        try:
            await router.handle(event,gateway)
            self.assertTrue(await asyncio.to_thread(started.wait,2))
            await router.handle(event,gateway)
            self.assertEqual(len(router.wn3_jobs),1)
            await router.handle(self.event(text='/status'),gateway)
            await router.handle(self.event(text='/cancel'),gateway)
            self.assertEqual(len(router.wn3_jobs),1)
        finally: release.set()
        await router.wn3_wait_idle()
        self.assertFalse(any(c[0]=='send_image' for c in gateway.calls));self.assertFalse(path.exists())
        self.assertFalse(router.wn3_jobs)
    async def test_all_leads_end_at360(self):
        keyboard=_lead_keyboard(DEFAULT_WN3_PARAMS,14)
        labels=[b.text for row in keyboard.rows for b in row]
        self.assertIn('+360',labels);self.assertNotIn('+361',labels)
    async def test_telegram_adapter_uses_same_router_and_user_not_bot(self):
        import telegram_weathernext3 as tg
        bot=AsyncMock(); bot.send_message.return_value=SimpleNamespace(message_id=99)
        update=SimpleNamespace(effective_user=SimpleNamespace(id=42),effective_chat=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text='/wn3 Москва +24',message_id=1,location=None,from_user=SimpleNamespace(id=999)),
            callback_query=None,update_id=101)
        context=SimpleNamespace(bot=bot,user_data={})
        with patch.object(tg,'get_router',return_value=self.router),patch('telegram_user_state.get_active_location',return_value=None),patch('telegram_user_state.remember_location'):
            with self.assertRaises(ApplicationHandlerStop):await tg.wn3_update(update,context)
            await self.router.wn3_wait_idle()
        self.assertEqual(len(self.calls),1)
        self.assertIsNotNone(self.router.recipes.latest_for_product('telegram','42','weathernext3'))
        self.assertIsNone(self.router.recipes.latest_for_product('telegram','999','weathernext3'))

class ReleaseApiTests(unittest.TestCase):
    def setUp(self):
        from messenger.weathernext3_api import router
        app=FastAPI();app.include_router(router);self.client=TestClient(app)
    def test_disabled_and_unauthorized_never_compute(self):
        with patch('messenger.weathernext3_api.build_weathernext3_product_result') as builder:
            with patch.dict(os.environ,{'WEATHERNEXT3_API_KEY':''}):
                self.assertEqual(self.client.post('/api/wn3/product',json={'lat':1,'lon':2}).status_code,503)
            with patch.dict(os.environ,{'WEATHERNEXT3_API_KEY':'correct-key'}):
                self.assertEqual(self.client.post('/api/wn3/product',json={'lat':1,'lon':2}).status_code,403)
            builder.assert_not_called()
    def test_authorized_zip_and_strict_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'point.csv';path.write_text('T_C\n10\n')
            result=CommonProductResult('weathernext3','TEST MODEL',[ProductAttachment('file',path,'point.csv')],{'model':'WeatherNext 3'})
            with patch.dict(os.environ,{'WEATHERNEXT3_API_KEY':'test-key'}),patch('messenger.weathernext3_api.build_weathernext3_product_result',return_value=result):
                response=self.client.post('/api/wn3/product',headers={'x-api-key':'test-key'},json={'lat':55.75,'lon':37.62})
                self.assertEqual(response.status_code,200)
                with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
                    self.assertEqual(set(bundle.namelist()),{'metadata.json','01_point.csv'})
                self.assertFalse(path.exists())
                bad=self.client.post('/api/wn3/product',headers={'x-api-key':'test-key'},json={'lat':55.75,'lon':37.62,'params':{'typo':1}})
                self.assertEqual(bad.status_code,422)
