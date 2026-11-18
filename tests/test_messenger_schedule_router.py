from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from messenger.callback_codec import encode_callback
from messenger.contracts import NormalizedEvent, PlatformMessage
from messenger.router import RouterDependencies
from messenger.schedule_router import ScheduleMessengerRouter
from messenger.schedule_store import MessengerScheduleStore
from messenger.settings_router import SettingsRecipeStore
from messenger.user_locations import MessengerLocationStore


class Point:
    def __init__(self, lat=55.75, lon=37.62, label="Москва"):
        self.lat, self.lon, self.label, self.source = lat, lon, label, "test"


class Gateway:
    def __init__(self, platform="max"): self.platform=platform; self.calls=[]; self.counter=0
    async def send_text(self, chat_id, text, *, keyboard=None, parse_mode=None):
        self.counter+=1; self.calls.append(("send_text",text,keyboard)); return PlatformMessage(self.platform,chat_id,str(self.counter))
    async def edit_text(self, chat_id, message_id, text, *, keyboard=None, parse_mode=None): return PlatformMessage(self.platform,chat_id,str(message_id))
    async def send_image(self,*a,**k): return PlatformMessage(self.platform,str(a[0]),"i")
    async def send_file(self,*a,**k): return PlatformMessage(self.platform,str(a[0]),"f")
    async def send_animation(self,*a,**k): return PlatformMessage(self.platform,str(a[0]),"a")
    async def answer_callback(self,event,*,text=None): self.calls.append(("answer",text,None))


class ManualExecutor:
    def __init__(self): self.calls=[]
    async def execute_now(self,item,gateway): self.calls.append(item.schedule_id); return True


class ScheduleRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"state.sqlite3"
        locations=MessengerLocationStore(self.path); recipes=SettingsRecipeStore(self.path, locations=locations)
        self.recipe=recipes.record_success("max","42","profile",{"lead":24},Point())
        self.store=MessengerScheduleStore(self.path); self.executor=ManualExecutor()
        self.router=ScheduleMessengerRouter(
            RouterDependencies(geocode=lambda q,n:[Point()]), locations=locations, recipes=recipes,
            schedule_store=self.store, schedule_executor=self.executor, progress_interval_seconds=0.01,
        )
        self.gateway=Gateway("max")

    async def asyncTearDown(self): self.tmp.cleanup()

    def cb(self,eid,payload):
        return NormalizedEvent("max",eid,"CALLBACK","42","chat",callback_payload=payload,callback_id=eid)

    async def test_create_schedule_from_saved_recipe(self):
        await self.router.handle(NormalizedEvent("max","1","COMMAND","42","chat",text="/schedule",command="schedule"),self.gateway)
        await self.router.handle(self.cb("2",encode_callback("schedule","new")),self.gateway)
        await self.router.handle(self.cb("3",encode_callback("schedule","recipe",self.recipe.recipe_id)),self.gateway)
        await self.router.handle(self.cb("4",encode_callback("schedule","freq",1)),self.gateway)
        with patch("messenger.schedule_router.resolve_point_timezone",return_value="Europe/Moscow"):
            await self.router.handle(self.cb("5",encode_callback("schedule","time","06:00")),self.gateway)
        state=self.router.sessions.get("max","42","chat")
        self.assertEqual(state.step,"confirm")
        await self.router.handle(self.cb("6",encode_callback("schedule","save")),self.gateway)
        items=self.store.list_for_user("max","42")
        self.assertEqual(len(items),1)
        self.assertEqual(items[0].product,"profile")
        self.assertEqual(items[0].params,{"lead":24})
        self.assertEqual(items[0].timezone,"Europe/Moscow")

    async def test_run_now_does_not_require_product_router(self):
        from messenger.product_executor import ProductSnapshot
        item=self.store.add("max","42","chat",ProductSnapshot.from_values("profile",Point(),{"lead":24}),"Europe/Moscow","06:00",1)
        await self.router.handle(self.cb("7",encode_callback("schedule","run",item.schedule_id)),self.gateway)
        self.assertEqual(self.executor.calls,[item.schedule_id])

    async def test_schedule_limit_is_per_platform(self):
        from messenger.product_executor import ProductSnapshot
        snap=ProductSnapshot.from_values("profile",Point(),{"lead":24})
        self.store.add("max","42","chat",snap,"Europe/Moscow","06:00",1)
        self.store.add("max","42","chat",snap,"Europe/Moscow","07:00",1)
        self.store.add("vk","42","chat",snap,"Europe/Moscow","08:00",1)
        self.assertEqual(len(self.store.list_for_user("max","42")),2)
        self.assertEqual(len(self.store.list_for_user("vk","42")),1)


if __name__=="__main__": unittest.main()
