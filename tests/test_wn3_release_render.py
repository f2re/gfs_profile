from __future__ import annotations
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from wn3_fixtures import POINT, upper_provider, map_frames
from test_weathernext3_provider import FakeExecutor
from weathernext3_provider import WeatherNext3Provider, MAP_KINDS, WeatherNext3Error
from weathernext3_map import write_weathernext3_map_png, write_weathernext3_animation
from messenger.weathernext3_service import build_weathernext3_product_result
from messenger.profile_service import cleanup_product_result

class ReleaseRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.basemap=patch('weathernext3_map.local_basemap_overlay',return_value={})
        self.basemap.start();self.addCleanup(self.basemap.stop)
    def check_png(self,path):
        with Image.open(path) as image:
            self.assertEqual(image.format,'PNG'); self.assertGreaterEqual(image.width,640)
        self.assertGreater(path.stat().st_size,1024)
    def test_every_surface_map_kind_renders(self):
        for kind in MAP_KINDS:
            with self.subTest(kind=kind):
                self.check_png(write_weathernext3_map_png(map_frames(kind)[0],self.root/f'{kind}.png',pixel_size=640))
    def test_upper_profile_aero_windgram_use_common_renderers(self):
        for kind in ('profile','aero','windgram'):
            with self.subTest(kind=kind):
                result=build_weathernext3_product_result(POINT,kind,hours=6,to=24,step=6,upper_provider=upper_provider())
                try:
                    self.check_png(result.attachments[0].path)
                    text=result.attachments[1].path.read_text(encoding='utf-8-sig')
                    self.assertIn('pressure_hpa',text); self.assertIn('WeatherNext 3',text)
                    self.assertIn('Google GCS/Zarr',result.summary); self.assertNotIn('GFS',result.summary)
                    self.assertEqual(result.metadata['model'],'WeatherNext 3')
                finally: cleanup_product_result(result)
    def test_surface_timeseries_and_meteogram_reports(self):
        provider=WeatherNext3Provider(project='test-project',dataset='linked',executor=FakeExecutor(),cache_dir=self.root)
        for kind,fmt in (('cloudgram','png'),('precip_compare','png'),('meteogram','png'),('meteogram','pdf'),('meteogram','docx'),('ensemble','png'),('ensemble','pdf'),('ensemble','docx')):
            with self.subTest(kind=kind,fmt=fmt):
                result=build_weathernext3_product_result(POINT,kind,days=1,format=fmt,provider=provider)
                try:
                    first=result.attachments[0].path
                    if fmt=='png': self.check_png(first)
                    elif fmt=='pdf': self.assertEqual(first.read_bytes()[:4],b'%PDF')
                    else: self.assertEqual(first.read_bytes()[:2],b'PK')
                    self.assertEqual(result.attachments[-1].mime_type,'text/csv')
                    self.assertNotIn('64/64',result.summary)
                finally: cleanup_product_result(result)
    def test_gif_fallback_and_mixed_run_rejection(self):
        frames=map_frames()
        with patch('weathernext3_map.shutil.which',return_value=None):
            path=write_weathernext3_animation(frames,self.root/'map.mp4',pixel_size=640)
        self.assertEqual(path.suffix,'.gif')
        with Image.open(path) as image:self.assertEqual(image.n_frames,2)
        with self.assertRaises(WeatherNext3Error):
            write_weathernext3_animation([frames[1],frames[0]],self.root/'bad.mp4',pixel_size=640)
    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg not installed')
    def test_native_h264_animation(self):
        path=write_weathernext3_animation(map_frames(),self.root/'map.mp4',pixel_size=640)
        self.assertEqual(path.suffix,'.mp4')
        self.assertEqual(path.read_bytes()[4:8],b'ftyp')
