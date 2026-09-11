"""Default-off regressions: cold runtime, old controls, providers and saved state."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from feature_flags import (
    DISABLED_SOURCE_MESSAGE, FeatureDisabledError, disabled_input, is_weathernext3,
    product_available, require_product, weathernext3_enabled,
)

ROOT = Path(__file__).resolve().parents[1]
POINT = {'lat': 55.75, 'lon': 37.62, 'label': 'Тест', 'source': 'test'}


class DisabledPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'WEATHERNEXT3_ENABLED': '0'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_absent_switch_stays_off_even_with_credentials(self):
        with patch.dict(os.environ, {'WEATHERNEXT3_BIGQUERY_PROJECT': 'test', 'WEATHERNEXT3_API_KEY': 'test-key'}):
            os.environ.pop('WEATHERNEXT3_ENABLED', None)
            self.assertFalse(weathernext3_enabled())
        for value in ('', 'false', 'auto', 'garbage', '0'):
            with patch.dict(os.environ, {'WEATHERNEXT3_ENABLED': value}):
                self.assertFalse(weathernext3_enabled())
        with patch.dict(os.environ, {'WEATHERNEXT3_ENABLED': '1'}):
            self.assertTrue(weathernext3_enabled())

    def test_all_aliases_rejected_by_model_registry(self):
        from meteogram_models import source_for_id, sources_by_kind, MeteogramError
        for name in ('wn3','wn3_mean','wn3_ensemble','weathernext3','weathernext3_mean','weather-next3','weather_next_3','weathernext'):
            with self.subTest(name=name):
                with self.assertRaises(MeteogramError): source_for_id(name)
                self.assertFalse(product_available('meteogram', {'source_id': name}))
        for ensemble in (False, True):
            self.assertTrue(sources_by_kind(ensemble))
            self.assertFalse(any(is_weathernext3(s.source_id) for s in sources_by_kind(ensemble)))
        self.assertEqual(source_for_id('gfs').source_id, 'gfs')

    def test_commands_and_all_callback_generations_are_rejected(self):
        for text in ('/wn3', '/weathernext3 Москва +24', '/WN3@mybot Москва', '/meteogram Москва source=wn3_mean', '/meteogram Москва ensemble=weathernext3'):
            self.assertTrue(disabled_input(text), text)
        for payload in ('home:wn3','wn3:run','w3|owner-card|run','v1|wn3|run','v1|product|open|weathernext3','meteo:source:weathernext3_mean','v1|meteo|source|wn3'):
            self.assertTrue(disabled_input(callback=payload), payload)
        self.assertFalse(disabled_input('/meteogram Москва source=gfs'))
        self.assertFalse(disabled_input('/start'))
        self.assertFalse(disabled_input(callback='home:map'))

    def test_default_requirements_do_not_install_cloud_packages(self):
        base=(ROOT/'requirements.txt').read_text()
        for name in ('google-cloud-', 'zarr>=', 'obstore>=', '-r requirements-weathernext3.txt'):
            self.assertNotIn(name, base)
        optional=(ROOT/'requirements-weathernext3.txt').read_text()
        self.assertIn('google-cloud-bigquery', optional)
        self.assertIn('WEATHERNEXT3_ENABLED=0', (ROOT/'.env.telegram.example').read_text())

    def test_builders_and_clients_fail_before_any_io(self):
        from messenger.product_executor import ProductSnapshot, build_snapshot_result
        from messenger.weathernext3_service import build_weathernext3_product_result
        from weathernext3_provider import WeatherNext3Provider, GoogleBigQueryExecutor
        from weathernext3_zarr import WeatherNext3ZarrProvider
        from weathernext3_meteogram import fetch_weathernext3_meteogram
        from geocode import GeoPoint
        executor=Mock(); opener=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            provider=WeatherNext3Provider(project='test-project', dataset='test', executor=executor, cache_dir=tmp)
            upper=WeatherNext3ZarrProvider(candidates=opener, opener=opener, cache_dir=tmp)
            calls=[lambda: provider.point_series('T',55,37,1), lambda: provider.latest_run(55,37,1),
                   lambda: provider.map_frames('T',55,37,[1]), lambda: WeatherNext3Provider.from_env(),
                   lambda: upper.profiles(55,37,[6]), lambda: upper.runs(), lambda: upper.open('test'),
                   lambda: GoogleBigQueryExecutor(billing_project='test-project',client=executor).query('SELECT 1',{}),
                   lambda: build_weathernext3_product_result(GeoPoint(55,37,'T','test'),provider=provider),
                   lambda: fetch_weathernext3_meteogram('T',55,37,1,provider=provider)]
            for func in calls:
                with self.assertRaises(FeatureDisabledError): func()
            for product,params in [('weathernext3',{}),('meteogram',{'source':'wn3'}),('meteogram',{'source_id':'weathernext3_mean'})]:
                with self.assertRaises(FeatureDisabledError): ProductSnapshot.from_values(product,POINT,params)
                with self.assertRaises(FeatureDisabledError): build_snapshot_result(ProductSnapshot(product,POINT,params))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(executor.mock_calls,[]); self.assertEqual(opener.mock_calls,[])

    def test_local_check_is_off_and_live_attempt_has_no_cloud_import(self):
        from weathernext3_status import status
        from weathernext3_check import main
        self.assertFalse(status()['enabled'])
        with patch('builtins.print'):
            self.assertEqual(main([]),0)
            self.assertEqual(main(['--live']),2)

    def test_saved_common_recipes_and_schedules_hidden_and_preserved(self):
        from messenger.user_recipes import UserRecipeStore
        from messenger.schedule_store import MessengerScheduleStore
        from messenger.product_executor import ProductSnapshot
        now=datetime(2026,9,1,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            recipes=UserRecipeStore(Path(tmp)/'state.db');store=MessengerScheduleStore(recipes.path)
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                wn3=recipes.record_success('max','1','weathernext3',{},POINT)
                mean=recipes.record_success('max','1','meteogram',{'source':'weathernext3_mean'},POINT)
                items=[store.add('max','1','1',ProductSnapshot.from_values(p,POINT,params),'UTC','01:00',1,now_utc=now)
                       for p,params in [('weathernext3',{}),('meteogram',{'source':'wn3'})]]
            store.add('max','1','1',ProductSnapshot.from_values('profile',POINT,{}),'UTC','01:00',1,now_utc=now)
            gfs=recipes.record_success('max','1','profile',{},POINT)
            self.assertEqual([r.recipe_id for r in recipes.list('max','1')],[gfs.recipe_id])
            self.assertIsNone(recipes.get('max','1',wn3.recipe_id));self.assertIsNone(recipes.get('max','1',mean.recipe_id))
            self.assertEqual([s.product for s in store.list_for_user('max','1')],['profile'])
            due,skipped=store.claim_due(now_utc=now+timedelta(hours=1))
            self.assertEqual([s.product for s in due],['profile']);self.assertEqual(skipped,[])
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                self.assertEqual(len(recipes.list('max','1')),3)
                self.assertEqual(len(store.list_for_user('max','1')),3)
                self.assertIsNone(store.get('max','1',items[0].schedule_id).last_started_utc)

    def test_native_telegram_preferences_and_json_schedules_hidden(self):
        from telegram_user_state import save_product_selection, get_product_preference
        from telegram_schedules import ScheduleStore
        now=datetime(2026,9,1,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.db'; store=ScheduleStore(Path(tmp)/'schedules.json')
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                save_product_selection(1,'meteogram',{'source_id':'wn3'},POINT,db_path=db)
                item=store.add(user_id=1,chat_id=1,username=None,product='meteogram',point=POINT,
                    params={'source_id':'wn3_mean'},timezone_name='UTC',local_time='01:00',every_days=1,now_utc=now)
            before=store.path.read_bytes()
            self.assertIsNone(get_product_preference(1,'meteogram',db_path=db))
            self.assertEqual(store.list_for_user(1),[]);self.assertIsNone(store.get(item.schedule_id))
            self.assertEqual(store.claim_due(now+timedelta(hours=1)),([],[]))
            self.assertEqual(store.path.read_bytes(),before)
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                self.assertEqual(len(store.list_for_user(1)),1)
                self.assertIsNotNone(get_product_preference(1,'meteogram',db_path=db))

    def test_usable_recipe_limits_do_not_prune_hidden_records(self):
        from messenger.user_recipes import UserRecipeStore
        with tempfile.TemporaryDirectory() as tmp:
            store=UserRecipeStore(Path(tmp)/'state.db')
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                hidden=store.record_success('max','1','weathernext3',{},POINT)
            with patch('messenger.user_recipes.MAX_RECIPES_PER_USER',2):
                for lead in (6,12,24):
                    store.record_success('max','1','profile',{'lead':lead},POINT)
            self.assertEqual(len(store.list('max','1')),2)
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                self.assertIsNotNone(store.get('max','1',hidden.recipe_id))

    def test_native_latest_and_quick_fall_back_to_available_successes(self):
        from telegram_user_state import record_product_success, get_last_success_preference, get_quick_preferences
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.db'
            record_product_success(1,'profile',{'lead':24},POINT,db_path=db)
            with patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'1'}):
                record_product_success(1,'meteogram',{'source_id':'wn3'},POINT,db_path=db)
            self.assertEqual(get_last_success_preference(1,db_path=db).product,'profile')
            self.assertEqual([p.product for p in get_quick_preferences(1,limit=1,db_path=db)],['profile'])

    def test_cold_start_with_imports_blocked_and_invalid_wn3_config(self):
        script=r'''
import asyncio, importlib.abc, json, os, sys
class NoWeatherNext(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if 'weathernext' in fullname or fullname.split('.')[0] in {'google','zarr','obstore'}:
            raise AssertionError('Disabled provider imported: '+fullname)
sys.meta_path.insert(0,NoWeatherNext())
import messenger_runtime as runtime
import telegram_bot, telegram_personal_ux, telegram_concise_ux, telegram_commands
from meteogram_models import SOURCES
from fastapi.testclient import TestClient
assert type(runtime.ROUTER).__name__=='ScheduleMessengerRouter'
assert 'wn3' not in [c.command for c in telegram_commands.BOT_COMMANDS]
assert not any('weathernext' in s.source_id for s in SOURCES)
for value in (telegram_personal_ux.home_text(1),telegram_personal_ux.home_keyboard(1).to_dict(),
              telegram_concise_ux.home_text(),telegram_concise_ux.help_text(),
              telegram_personal_ux._settings_keyboard(1).to_dict()):
    assert 'weathernext' not in str(value).lower() and '/wn3' not in str(value), value
app=telegram_bot.build_application()
assert not any(set(getattr(h,'commands',())) & {'wn3','weathernext3'} for group in app.handlers.values() for h in group)
assert all('weathernext' not in getattr(h.callback,'__module__','') for group in app.handlers.values() for h in group)
with TestClient(runtime.app) as client:
    assert client.get('/ready').status_code==200
    health=client.get('/health').json()
    assert health['status']=='ok',health
    assert len(health['products'])==7
    assert 'weathernext' not in json.dumps(health).lower()
    assert not any('/wn3' in p for p in client.get('/openapi.json').json()['paths'])
    assert client.post('/api/wn3/product',headers={'x-api-key':'old-key'},json={'lat':55,'lon':37}).status_code==404
    assert runtime.SCHEDULER.gateways()['telegram'] is None
assert not any('weathernext' in n for n in sys.modules)
print('Cold runtime without WeatherNext/cloud imports: OK')
'''
        with tempfile.TemporaryDirectory() as tmp:
            env={**os.environ,'WEATHERNEXT3_ENABLED':'0','WEATHERNEXT3_BIGQUERY_PROJECT':'invalid project!',
                 'WEATHERNEXT3_BIGQUERY_DATASET':'old','WEATHERNEXT3_API_KEY':'old-key',
                 'MAX_CONCURRENT_WEATHERNEXT3':'not-an-integer','WEATHERNEXT3_MAP_PIXEL_SIZE':'bad',
                 'TELEGRAM_ENABLED':'0','MAX_ENABLED':'0','VK_ENABLED':'0','TELEGRAM_BOT_TOKEN':'123456:fake-token-not-used',
                 'GFS_CACHE_DIR':tmp+'/cache','MESSENGER_PREFERENCES_DB':tmp+'/messenger.db',
                 'TELEGRAM_PREFERENCES_DB':tmp+'/telegram.db','TELEGRAM_ADMIN_DB':tmp+'/admin.db'}
            result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,text=True,capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertFalse((Path(tmp)/'cache/weathernext3').exists())


class DisabledFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        switch=patch.dict(os.environ,{'WEATHERNEXT3_ENABLED':'0'});switch.start();self.addCleanup(switch.stop)

    async def test_telegram_old_buttons_and_commands_are_acknowledged_without_tasks(self):
        from telegram_feature_guard import guard
        from telegram.ext import ApplicationHandlerStop
        for callback,text in [('home:wn3',''),('w3|old|run',''),('meteo:source:wn3',''),('', '/wn3 Москва +24')]:
            message=SimpleNamespace(text=text,reply_text=AsyncMock())
            query=SimpleNamespace(data=callback,answer=AsyncMock()) if callback else None
            update=SimpleNamespace(effective_message=message,callback_query=query)
            with self.assertRaises(ApplicationHandlerStop): await guard(update,SimpleNamespace(user_data={}))
            if query: query.answer.assert_awaited_once()
            else: message.reply_text.assert_awaited_once()
        context=SimpleNamespace(user_data={'weathernext3_wizard':{'step':'await_point'}})
        await guard(SimpleNamespace(effective_message=SimpleNamespace(text='/start'),callback_query=None),context)
        self.assertEqual(context.user_data,{})

    async def test_common_home_and_rejection_on_telegram_max_vk(self):
        from messenger.schedule_router import ScheduleMessengerRouter
        from messenger.router import RouterDependencies
        from messenger.contracts import NormalizedEvent, PlatformMessage
        from messenger.user_recipes import UserRecipeStore
        with tempfile.TemporaryDirectory() as tmp:
            geocode=Mock(side_effect=AssertionError('Geocoder must not run for disabled input'))
            router=ScheduleMessengerRouter(RouterDependencies(geocode=geocode),recipes=UserRecipeStore(Path(tmp)/'state.db'))
            for platform in ('telegram','max','vk'):
                gateway=SimpleNamespace(platform=platform,send_text=AsyncMock(return_value=PlatformMessage(platform,'1','1')),answer_callback=AsyncMock())
                await router.handle(NormalizedEvent(platform,None,'COMMAND','1','1',text='/start',command='start'),gateway)
                home=gateway.send_text.call_args
                self.assertNotIn('weathernext',str(home).lower())
                for text,payload in [('/wn3 Москва +24',None),('', 'w3|old|run'),('', 'v1|product|open|weathernext3'),('', 'v1|meteo|source|weathernext3_mean')]:
                    event=NormalizedEvent(platform,None,'CALLBACK' if payload else 'COMMAND','1','1',
                        text=text,command='wn3' if text else None,callback_payload=payload)
                    await router.handle(event,gateway)
                    self.assertEqual(gateway.send_text.call_args.args[1],DISABLED_SOURCE_MESSAGE)
                self.assertFalse(hasattr(router,'wn3_jobs'))
            geocode.assert_not_called()

    async def test_scheduler_never_starts_disabled_snapshot_or_sends_failure_spam(self):
        from messenger.scheduler import MessengerScheduler, ScheduleExecutor
        item=SimpleNamespace(product='meteogram',params={'source':'wn3'},platform='max')
        gateway=SimpleNamespace(send_text=AsyncMock())
        store=Mock();store.claim_due.return_value=([item],[])
        executor=SimpleNamespace(execute=AsyncMock())
        scheduler=MessengerScheduler(store=store,executor=executor,gateways=lambda:{'max':gateway})
        self.assertEqual(await scheduler.run_once(),(0,0))
        executor.execute.assert_not_awaited();gateway.send_text.assert_not_awaited()
        with self.assertRaises(FeatureDisabledError): await ScheduleExecutor().execute(item,gateway)

    async def test_registration_clears_legacy_scopes(self):
        from telegram_commands import register_bot_commands
        application=SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock(),delete_my_commands=AsyncMock()))
        await register_bot_commands(application)
        self.assertEqual(application.bot.set_my_commands.await_count,4)
        self.assertEqual(application.bot.delete_my_commands.await_count,2)
        for call in application.bot.set_my_commands.call_args_list:
            self.assertNotIn('wn3',[c.command for c in call.args[0]])


    async def test_command_registration_preserves_startup_hooks_when_network_fails(self):
        from telegram_commands import install_command_registration
        previous=AsyncMock()
        app=SimpleNamespace(post_init=previous)
        install_command_registration(app)
        with patch('telegram_commands.register_bot_commands',new=AsyncMock(side_effect=RuntimeError('offline'))):
            with self.assertLogs('telegram_commands',level='WARNING'):
                await app.post_init(app)
        previous.assert_awaited_once_with(app)
